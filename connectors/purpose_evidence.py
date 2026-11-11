"""Purpose evidence helpers: comment-noise filtering and evidence strength.

Pure functions (standard library only), shared by `connectors.codebase_importer` (which
builds `SolarSystem.purpose_evidence`), `connectors.ollama_enhancer` (which only asks the
local model for a purpose when the evidence is strong enough) and the UI (which shows
the strength in the "based on" dialog).

Noise filter (`is_noise_comment_line`, `clean_header_text`): header comments and
docstrings often start with lines that say nothing about what a project is for. Each
line is checked against `NOISE_PATTERNS` (one table, one category per row): encoding
notes, shebangs, license / copyright text, lint / tool directives, TypeScript
`/// <reference>` directives, editor modelines, "generated / do not edit" notes, pure
metadata (author, date, version, ...) and separator lines. What remains must have at
least `HEADER_MIN_WORDS` meaningful words, otherwise the header counts as empty and the
importer may use the next comment block.

Evidence strength (`assess_purpose_evidence`): a small points score computed from the
stored `purpose_evidence` text alone (so it also works for old galaxy.json files):

* own description / README intro: 2 points each (strong by themselves);
* README title: 1;
* each docstring / header comment that still passes the noise filter and has at least
  4 meaningful words: 1;
* each user-facing title / meta description / heading / command-line help with at least
  2 meaningful words: 1 (at most 2 in total);
* each "Text in code" string with at least 4 meaningful words: 1;
* each first-session request with at least 4 meaningful words (URLs, stopwords, the
  project name and filler such as "devam et" / "başlat" / "bakar mısın" removed): 1 (at
  most 2 in total);
* capabilities, project shape, entry point and names in code: 0 (code facts, not
  descriptions).

The evidence is strong when the score is at least `PURPOSE_STRONG_SCORE`. Only then is
the local model asked to write a purpose.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, List, Pattern, Sequence, Set, Tuple

SESSION_EVIDENCE_LABEL = "From the first sessions (what the user asked for)"

PURPOSE_STRONG_SCORE = 2
HEADER_MIN_WORDS = 3  # a cleaned header with fewer meaningful words is dropped
DESCRIPTIVE_MIN_WORDS = 4  # header / text / session line that earns a point
TITLE_MIN_WORDS = 2
TITLE_POINTS_MAX = 2
SESSION_POINTS_MAX = 2

# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------
_STOPWORDS_RAW = """
the and for with from this that these those into onto over under are was were been being has have had
not but you your our its his her their them they what which who whom when where why how all any each
some more most other such only own same than too very can will just should would could may might must
also here there then now about after before again very use used using see via etc per one two
ve ile için icin bir bu şu şunu bunu bunu o da de ki mi mı mu mü ya yada veya ama fakat ancak gibi kadar
daha çok cok az en her hiç hic hem ise ne neden nasıl nasil nerde nerede nedir niye hangi hangisi kim
var yok olan olarak olur oldu olsun olmak şey sey şöyle böyle zaten artık sonra önce şimdi simdi
ben sen biz siz onlar beni seni bana sana bize benim senin bizim iki üç bi iyi kötü tamam evet hayır
lütfen lutfen bak baksana bakar mısın misin yap yapar yapalım yapmak et eder edelim
proje projeyi projeye projem projemi projenin project projects dosya dosyayı dosyası dosyalar file files
kod code main
"""
_FILLER_RE = re.compile(
    r"\b(?:devam\s+et\w*|yapmaya\s+devam|başlat\w*|baslat\w*|çalıştır\w*|calistir\w*|bakar\s+m[ıi]s[ıi]n|"
    r"bir\s+bak\w*|bak\w*\s+m[ıi]s[ıi]n|düzelt\w*|duzelt\w*|keep\s+going|go\s+on|continue|please|"
    r"start\s+it|run\s+it|fix\s+it)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


def _tr_lower(text: str) -> str:
    return (text or "").replace("İ", "i").replace("I", "ı").lower()


def word_key(word: str) -> str:
    """Comparison key of a word: Turkish-aware lower case, accents removed, ı → i."""
    s = unicodedata.normalize("NFKD", _tr_lower(word))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.replace("ı", "i")


_STOPWORDS: Set[str] = {word_key(w) for w in _STOPWORDS_RAW.split()}


def _is_path_token(tok: str) -> bool:
    if not re.search(r"[/\\]", tok):
        return False
    return bool(
        re.match(r"^(?:[A-Za-z]:|\.{1,2}|~)?[/\\]", tok)
        or tok.count("/") + tok.count("\\") >= 2
        or re.search(r"\.\w{1,5}[)\]\"',.;:]*$", tok)
    )


def meaningful_words(text: str, exclude: Iterable[str] = ()) -> List[str]:
    """Words of at least 3 letters that are not stopwords (small EN + TR list), not part
    of a URL or a path, not numbers and not in `exclude` (e.g. the project name)."""
    excluded = {word_key(w) for w in exclude}
    text = _URL_RE.sub(" ", text or "")
    kept_tokens = [t for t in text.split() if not _is_path_token(t)]
    out: List[str] = []
    for w in _WORD_RE.findall(" ".join(kept_tokens)):
        k = word_key(w)
        if len(k) >= 3 and k not in _STOPWORDS and k not in excluded:
            out.append(w)
    return out


def _name_words(project_name: str) -> List[str]:
    return _WORD_RE.findall(project_name or "")


# ---------------------------------------------------------------------------
# Noise filter for header comments / docstrings
# ---------------------------------------------------------------------------
_ENC = r"(?:utf-?(?:8|16|32)|charset|kodlama|karakter\s+kodlamas\w*|code\s?page|chcp|\bbom\b|byte\s+order\s+mark)"
_ENC_SETUP = (
    r"(?:encoding|kodlama)\b.*\b(?:set|setup|fix|configure|reconfigure|stdout|stderr|console|terminal|ayar\w*)"
    r"|\b(?:set|setup|fix|configure|reconfigure|stdout|stderr|console|terminal|ayar\w*)\b.*\b(?:encoding|kodlama)"
)

#: (category, pattern): a comment line matching any pattern is not a description.
NOISE_PATTERNS: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("encoding", re.compile(r"-\*-.*coding|\bcoding[:=]|" + _ENC + "|" + _ENC_SETUP, re.IGNORECASE)),
    ("shebang", re.compile(r"^#!|^!?/?usr/(?:local/)?bin/|^!?/bin/|^!?/usr/bin/env\b|^env\s+(?:python|node|bash|sh|php|deno)\b", re.IGNORECASE)),
    ("license", re.compile(
        r"spdx-license-identifier|copyright|\(c\)|©|licen[sc]ed\s+under|\b(?:mit|apache|bsd|isc|mozilla)\s+licen[sc]e|"
        r"\bl?gplv?\d?\b|gnu\s+(?:general|lesser|affero)|all\s+rights\s+reserved|permission\s+is\s+hereby\s+granted|"
        r"https?://\S*(?:licen[sc]e|opensource\.org)|\blisans|tüm\s+hakları\s+saklıdır|tum\s+haklari\s+saklidir|"
        r"without\s+warranty|provided\s+\"?as\s+is",
        re.IGNORECASE)),
    ("directive", re.compile(
        r"eslint(?:-disable|-enable|-env)?\b|\bpylint:|\bnoqa\b|\btype:\s*ignore|\bmypy:|\bflake8\b|@ts-(?:nocheck|check|ignore|expect-error)|"
        r"prettier-ignore|\bjshint\b|\bjslint\b|\bphpcs:?|\bistanbul\s+ignore|\bc8\s+ignore|\bfmt:\s*(?:off|on)\b|\bisort:|"
        r"^['\"]?use\s+(?:strict|client|server)['\"]?;?$|^@(?:flow|format|jsx|jsxImportSource|refresh|vitest-environment)\b",
        re.IGNORECASE)),
    ("reference", re.compile(r"^/*\s*<(?:reference|amd-module|amd-dependency)\b", re.IGNORECASE)),
    ("modeline", re.compile(r"^(?:vim?|ex):|\bvim?:\s*set\b|-\*-.*-\*-|\blocal\s+variables:|\bemacs\b", re.IGNORECASE)),
    ("generated", re.compile(
        r"auto-?generated|generated\s+(?:by|with|from|automatically|on)|this\s+file\s+(?:is|was|has\s+been)\s+(?:auto(?:matically)?[- ]?)?generated|"
        r"do\s+not\s+(?:edit|modify|change)|don'?t\s+(?:edit|modify)|(?:should|must)\s+not\s+be\s+(?:edited|modified|changed)|"
        r"otomatik\s+(?:olarak\s+)?(?:oluşturul|üretil|olusturul|uretil)\w*|düzenlemeyin|duzenlemeyin|değiştirmeyin|degistirmeyin",
        re.IGNORECASE)),
    ("metadata", re.compile(
        r"^@(?!(?:fileoverview|overview|description|desc|summary|brief)\b)\w+|"
        r"^(?:author|authors|created(?:\s+(?:on|by|at))?|date|last\s+(?:modified|updated|edited)|updated|modified|version|"
        r"maintainer|email|e-mail|contact|since|revision|rev|id|file|filename|package|module|yazar|tarih|sürüm|surum|"
        r"oluşturulma(?:\s+tarihi)?|güncelleme)\s*[:=]|^(?:created|written|last\s+(?:modified|updated))\s+(?:on|at|by)\b",
        re.IGNORECASE)),
    ("separator", re.compile(r"^[\W_]+$")),
)

_DESC_TAG_RE = re.compile(r"^@(?:fileoverview|overview|description|desc|summary|brief)\b\s*", re.IGNORECASE)


def _strip_comment_markers(line: str) -> str:
    s = (line or "").strip()
    s = re.sub(r"^(?:<!--|/\*+|\*+(?!/)|//+|#+|--)\s*", "", s)
    s = re.sub(r"\s*(?:\*+/|-->)$", "", s)
    return s.strip()


def noise_category(line: str) -> str:
    """The `NOISE_PATTERNS` category of a comment line ("" if it may be a description).
    Comment markers (`#`, `//`, `/*`, `*`, `<!--`) may still be present."""
    raw = (line or "").strip()
    if raw.startswith("#!"):
        return "shebang"
    if raw.startswith("///") and "<" in raw:
        return "reference"
    s = _strip_comment_markers(raw)
    if not s:
        return "separator" if re.search(r"[^\W_]", raw) is None and raw else ""
    for category, rx in NOISE_PATTERNS:
        if rx.search(s):
            return category
    return ""


def is_noise_comment_line(line: str) -> bool:
    """True if a comment line is not a description (see `NOISE_PATTERNS`)."""
    return bool(noise_category(line))


def clean_header_text(text: str, min_words: int = HEADER_MIN_WORDS) -> str:
    """A header comment / docstring with the noise lines removed (line by line), joined
    with spaces. Returns "" when fewer than `min_words` meaningful words remain."""
    kept: List[str] = []
    for raw in (text or "").splitlines():
        if not raw.strip() or is_noise_comment_line(raw):
            continue
        s = _DESC_TAG_RE.sub("", _strip_comment_markers(raw)).strip()
        if s:
            kept.append(s)
    out = re.sub(r"\s+", " ", " ".join(kept)).strip()
    return out if len(meaningful_words(out)) >= min_words else ""


def _clean_stored_header(text: str) -> str:
    """Stored headers are already one line: check each sentence on its own."""
    parts = re.split(r"(?<=[.!?])\s+", text or "")
    return clean_header_text("\n".join(parts))


# ---------------------------------------------------------------------------
# Evidence order
# ---------------------------------------------------------------------------
def session_line_of(evidence_or_lines) -> str:
    """The first-session requests line of an evidence text (or list of lines), wherever it is."""
    lines = evidence_or_lines.splitlines() if isinstance(evidence_or_lines, str) else list(evidence_or_lines or ())
    return next((ln for ln in lines if ln.startswith(SESSION_EVIDENCE_LABEL)), "")


def session_line_first(evidence: str) -> str:
    """`evidence` with its session line moved to the top (old galaxy.json files stored it
    last); other lines keep their order."""
    lines = (evidence or "").splitlines()
    line = session_line_of(lines)
    if not line or lines[0] == line:
        return evidence or ""
    return "\n".join([line] + [ln for ln in lines if ln != line])


def session_requests(line: str) -> List[str]:
    """The quoted requests of a session line (`"a" | "b"`)."""
    if not line.startswith(SESSION_EVIDENCE_LABEL):
        return []
    body = line[len(SESSION_EVIDENCE_LABEL):].lstrip(": ").strip()
    if not body:
        return []
    if body.startswith('"'):
        body = body[1:]
    if body.endswith('"'):
        body = body[:-1]
    return [r.strip() for r in body.split('" | "') if r.strip()]


# ---------------------------------------------------------------------------
# Evidence strength
# ---------------------------------------------------------------------------
@dataclass
class EvidenceStrength:
    strong: bool
    score: int
    needed: int = PURPOSE_STRONG_SCORE
    signals: List[str] = field(default_factory=list)
    explanation: str = ""


_HEADER_LINE_RE = re.compile(r"^(Docstring|Header comment) \((.+?)\): (.*)$")
_TEXT_LINE_RE = re.compile(r"^Text in code \((.+?), ([^,()]*)\): \"?(.*?)\"?$")
_OWN_RE = re.compile(r"^(Own description|README intro) \(([^,)]+)")
_README_TITLE_RE = re.compile(r"^README title \(([^)]+)\): ")
_ITEM_RE = re.compile(r'^(.*?)\s"(.*)"\s\((.*)$')
#: A quoted string that is a query, not prose (e.g. a SQL WHERE clause).
_CODE_TEXT_RE = re.compile(r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|WHERE|FROM|JOIN|CREATE|ALTER|DROP)\b", re.IGNORECASE)


def _short(text: str, limit: int = 40) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def assess_purpose_evidence(evidence: str, project_name: str = "") -> EvidenceStrength:
    """Scores how much the evidence says about what the project is for (see the module
    docstring for the rules). Pure; works on old stored evidence in any line order."""
    name = _name_words(project_name)
    score = 0
    signals: List[str] = []
    title_points = session_points = 0

    def add(points: int, what: str) -> None:
        nonlocal score
        score += points
        signals.append(what)

    for line in (evidence or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _OWN_RE.match(line)
        if m:
            add(2, ("own description" if m.group(1) == "Own description" else "README intro") + f" in {m.group(2)}")
            continue
        m = _README_TITLE_RE.match(line)
        if m:
            add(1, f"README title in {m.group(1)}")
            continue
        m = _HEADER_LINE_RE.match(line)
        if m:
            cleaned = _clean_stored_header(m.group(3))
            if len(meaningful_words(cleaned, name)) >= DESCRIPTIVE_MIN_WORDS:
                add(1, f"{m.group(1).lower()} in {m.group(2)}")
            continue
        m = _TEXT_LINE_RE.match(line)
        if m:
            if not _CODE_TEXT_RE.match(m.group(3)) and len(meaningful_words(m.group(3), name)) >= DESCRIPTIVE_MIN_WORDS:
                add(1, f"text in {m.group(1)}")
            continue
        if line.startswith(("Titles shown to users:", "Command-line help:")):
            rest = line.split(":", 1)[1]
            for piece in re.split(r"\)\s*;\s*", rest):
                im = _ITEM_RE.match(piece.strip())
                if not im or title_points >= TITLE_POINTS_MAX:
                    continue
                if len(meaningful_words(im.group(2), name)) >= TITLE_MIN_WORDS:
                    title_points += 1
                    add(1, f"{im.group(1).strip()} in {im.group(3).rstrip(')').strip()}")
            continue
        if line.startswith(SESSION_EVIDENCE_LABEL):
            for req in session_requests(line):
                if session_points >= SESSION_POINTS_MAX:
                    break
                if len(meaningful_words(_FILLER_RE.sub(" ", req), name)) >= DESCRIPTIVE_MIN_WORDS:
                    session_points += 1
                    add(1, f'session request "{_short(req)}"')
            continue
        # capabilities, project shape, entry point, names in code, facts: 0 points
    strong = score >= PURPOSE_STRONG_SCORE
    counts: dict = {}
    for sig in signals:
        counts[sig] = counts.get(sig, 0) + 1
    shown = [f"{sig} (×{n})" if n > 1 else sig for sig, n in counts.items()]
    pts = f"{score} point{'' if score == 1 else 's'}, {PURPOSE_STRONG_SCORE} needed"
    if signals:
        explanation = f"{'Strong' if strong else 'Weak'} evidence ({pts}): " + "; ".join(shown)
    else:
        explanation = (f"Weak evidence ({pts}): no description, header comment, user-facing text "
                       "or descriptive session request")
    return EvidenceStrength(strong=strong, score=score, needed=PURPOSE_STRONG_SCORE, signals=signals,
                            explanation=explanation)


def purpose_evidence_is_strong(evidence: str, project_name: str = "") -> bool:
    return bool((evidence or "").strip()) and assess_purpose_evidence(evidence, project_name).strong


def evidence_key(evidence: str) -> str:
    """A short fingerprint of `purpose_evidence` (12 hex chars of the SHA-1 of the
    whitespace-normalised text; "" for empty evidence). Records which evidence an AI
    suggestion was made from, or was dismissed for (`SolarSystem.purpose_suggestion_key`,
    `purpose_dismissed_key`), so a re-scan that changes the evidence is noticed."""
    norm = " ".join((evidence or "").split())
    if not norm:
        return ""
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


__all__: Sequence[str] = (
    "SESSION_EVIDENCE_LABEL", "evidence_key", "PURPOSE_STRONG_SCORE", "NOISE_PATTERNS", "EvidenceStrength",
    "assess_purpose_evidence", "purpose_evidence_is_strong", "clean_header_text", "is_noise_comment_line",
    "noise_category", "meaningful_words", "session_line_first", "session_line_of", "session_requests", "word_key",
)
