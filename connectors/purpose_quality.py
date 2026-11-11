"""Quality check for a model-written project purpose ("Suggested" purpose).

Pure functions, standard library only. `check_purpose_suggestion(text, evidence,
project_name)` decides whether an answer of the local model may be offered to the user
as a suggested `SolarSystem.purpose`. It never edits the text; it only accepts or
rejects it with a reason code and a short English explanation (`QualityResult.detail`,
shown e.g. as "No suggestion: the answer did not match the evidence").

Checks, in this order (the first failing one wins; see `REASONS`):

=============  ==============================================================================
reason         rejected when
=============  ==============================================================================
empty          the answer is blank or only punctuation / symbols
unknown        the model said it does not know: ``UNKNOWN`` (any case, markdown) or an obvious
               variant ("I don't know", "not enough information", "cannot determine",
               "the evidence does not say", Turkish "bilinmiyor", "yeterli bilgi yok", ...)
too_long       more than `MAX_SENTENCES` sentences or more than `MAX_CHARS` characters
               (abbreviations such as e.g. / i.e. / etc. / vs., decimals, version numbers,
               file names such as main.py and URLs do not end a sentence)
name_only      nothing meaningful is left once the project name, the generic vocabulary,
               hedges and stopwords are removed, and the answer does mention the name
               ("NOVA is the NOVA project.")
generic        fewer than `MIN_CONTENT_WORDS` meaningful words are left once template
               words, the generic vocabulary (`GENERIC_WORDS`), hedges (`HEDGE_WORDS`),
               words that only talk about the evidence (`META_WORDS`) and stopwords are
               removed ("A software project that helps users manage data.", "Based on the
               evidence, this is an application.")
no_overlap     no content word of the answer matches a content word of the evidence
=============  ==============================================================================

Word matching (`words_match`) uses `purpose_evidence.word_key` (Turkish-aware lower
case: I -> ı, İ -> i, accents and ı ş ç ğ ö ü folded to ASCII) and is lenient: two words
match when they are equal, when one is a prefix of the other, or when both are at least
4 letters long and share their first 4 letters ("Turkish" ~ "Türkçe", "quiz" ~
"Quizzes"); doubled letters count once ("assistant" ~ "asistanısın"). The evidence side (`evidence_words`) leaves out what would make a match
trivial: the evidence labels written by the importer ("Header comment (file.py):",
"Names in code:", ...), file names, the importer's own boilerplate (entry-point
explanations, "(class)", "code files", language names), the project name, stopwords and
the generic vocabulary. Identifiers are split into words (`computeDiceTotal()` ->
compute, dice, total; `odak_modu` -> odak, modu).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Set, Tuple

from connectors.purpose_evidence import SESSION_EVIDENCE_LABEL, meaningful_words, word_key

MAX_SENTENCES = 2
MAX_CHARS = 400
MIN_CONTENT_WORDS = 2
PREFIX_MATCH_LEN = 4

#: Reason code -> short English explanation (used as `QualityResult.detail`).
REASONS: Dict[str, str] = {
    "empty": "the answer was empty",
    "unknown": "the model could not tell what the project is for",
    "too_long": "the answer was longer than 2 sentences / 400 characters",
    "name_only": "the answer only repeated the project name",
    "generic": "the answer was too generic to say what the project is for",
    "no_overlap": "the answer did not match the evidence",
}


@dataclass
class QualityResult:
    ok: bool
    reason: str = ""
    detail: str = ""
    #: Answer words that matched evidence words, as "answer~evidence" (informational).
    matched: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Word lists (compared by `word_key`; see `_is_in`)
# ---------------------------------------------------------------------------
_GENERIC_RAW = """
project projects software application applications app apps program programs programme tool tools
system systems code codebase codes various variety features feature functionality functionalities
function functions user users people person someone helps help helping helpful provides provide provided
providing offers offer offering allows allow allowing enables enable enabling lets let designed design
used use uses using usable manage manages managing management manager data files file tasks task things
thing stuff way ways set simple basic small several many number multiple different certain specific
related based purpose purposes aims aim aimed intended goal includes include including support supports
supporting create creates created creating make makes making made build builds built building written
work works working run runs running launch launches launching solution solutions service services
module modules component components library libraries framework frameworks script scripts website
platform interface information content kind type types like such primarily mainly mostly especially
generally general overall able well also both while within does doing done gives give giving
python javascript typescript php java html css node react nodejs
proje projesi projedir yazılım yazilim uygulama uygulaması uygulamasi uygulamalar program programı araç araci aracı sistem sistemi
kullanıcı kullanici kullanıcılar kullanıcıların kullanıcıya sağlar saglar sağlayan saglayan yardımcı yardimci
yardım yönetmek yonetmek yönetim yonetim veri veriler dosyalar görevler gorevler işler isler çeşitli cesitli
özellikler ozellikler özellik ozellik amaç amac amacı amaci amaçlı amacli sunar sunan yapan yapar eden
"""
_HEDGE_RAW = """
likely possibly possible potentially potential appears appear apparently seems seem seemingly probably
probable might perhaps maybe presumably suggests suggest suggesting suggested could would
muhtemelen galiba sanırım sanirim olabilir görünüyor gorunuyor görünmektedir belki
"""
_META_RAW = """
evidence analysis analyzed analysed static shows show showed shown indicates indicate indicating
according mentioned given lines line names name comments comment header describes described description
listed list collected found
kanıt kanit kanıtlar analiz gösteriyor gosteriyor belirtilen verilen
"""
_TEMPLATE_RAW = "this that which there here is are was it its it's an the a"
#: Words the importer itself writes into evidence lines (never a meaningful match).
_BOILERPLATE_RAW = """
code files file other pages page root plus more folder template ignored unmodified block imported
class interface type types enum struct trait reference function functions method methods
python javascript typescript php java html css react node nodejs shell powershell batch
meta description heading title window
"""
_EXTRA_STOP_RAW = "does did doing its it's which that what who whose whom been being into onto"


def _keys(raw: str) -> Set[str]:
    return {word_key(w) for w in raw.split()}


GENERIC_WORDS: Set[str] = _keys(_GENERIC_RAW)
HEDGE_WORDS: Set[str] = _keys(_HEDGE_RAW)
META_WORDS: Set[str] = _keys(_META_RAW)
TEMPLATE_WORDS: Set[str] = _keys(_TEMPLATE_RAW)
BOILERPLATE_WORDS: Set[str] = _keys(_BOILERPLATE_RAW)
_EXTRA_STOP: Set[str] = _keys(_EXTRA_STOP_RAW)
_NOT_CONTENT: Set[str] = GENERIC_WORDS | HEDGE_WORDS | META_WORDS | TEMPLATE_WORDS | _EXTRA_STOP


def _is_in(key: str, words: Set[str]) -> bool:
    """`key` is one of `words`, or a word of 5+ letters plus a short suffix
    ("applications", "kullanıcıların", "managed")."""
    if key in words:
        return True
    for n in range(len(key) - 1, max(4, len(key) - 6), -1):
        if key[:n] in words:
            return True
    return False


def _squeeze(key: str) -> str:
    """Doubled letters collapsed ("assistant" -> "asistant", "address" -> "adres"), so
    English words match their Turkish loanwords ("asistan", "adres")."""
    return re.sub(r"(.)\1+", r"\1", key)


def words_match(a: str, b: str) -> bool:
    """Lenient match of two comparison keys (see the module docstring)."""
    if not a or not b:
        return False
    a, b = _squeeze(a), _squeeze(b)
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    return len(a) >= PREFIX_MATCH_LEN and len(b) >= PREFIX_MATCH_LEN and a[:PREFIX_MATCH_LEN] == b[:PREFIX_MATCH_LEN]


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_APOSTROPHE_SUFFIX_RE = re.compile(r"(?<=[^\W\d_])['’][^\W\d_]+")
_IDENT_RE = re.compile(r"[^\W\d]+")  # letters and underscores (a whole identifier)
_CAMEL_RE = re.compile(r"[A-ZÇĞİÖŞÜ]+(?![a-zçğıöşü])|[A-ZÇĞİÖŞÜ]?[a-zçğıöşü]+|[^\W\d_]+")
_FILE_NAME_RE = re.compile(r"\b[\w.-]+\.[A-Za-z][A-Za-z0-9]{0,4}\b")


def name_keys(project_name: str) -> Set[str]:
    """Comparison keys of the project name: the whole name and its parts
    ("dice-server" -> dice, server, diceserver; "TaleWeave" -> taleweave, tale, weave)."""
    out: Set[str] = set()
    tokens = re.findall(r"[^\W\d_]+", project_name or "")
    for tok in tokens:
        out.add(word_key(tok))
        out.update(word_key(p) for p in _CAMEL_RE.findall(tok))
    if len(tokens) > 1:
        out.add(word_key("".join(tokens)))
    return {k for k in out if len(k) >= 3}


def _is_name(key: str, names: Set[str]) -> bool:
    # "NOVA'nın" loses its suffix earlier; "NOVAnın" / "novas" start with the name.
    return any(key == n or (len(n) >= 4 and key.startswith(n) and len(key) - len(n) <= 4) for n in names)


def _content_words(text: str, names: Set[str], drop: Set[str], split_identifiers: bool) -> List[Tuple[str, str]]:
    """Meaningful (word, key) pairs of `text` (at least 3 letters, unique by key) without
    the project name, stopwords and the words in `drop`. With `split_identifiers`,
    identifiers are split into camelCase / snake_case parts, and an identifier that is
    the project name ("TaleWeave") is dropped with all its parts."""
    cleaned = _APOSTROPHE_SUFFIX_RE.sub("", _URL_RE.sub(" ", text or ""))
    pairs: List[Tuple[str, str]] = []
    if split_identifiers:
        for ident in _IDENT_RE.findall(cleaned):
            if _is_name(word_key(ident.replace("_", "")), names):
                continue
            for chunk in filter(None, ident.split("_")):
                pairs.extend((p, word_key(p)) for p in _CAMEL_RE.findall(chunk))
    else:
        pairs = [(w, word_key(w)) for w in meaningful_words(cleaned)]
    seen: Set[str] = set()
    result: List[Tuple[str, str]] = []
    for w, k in pairs:
        if len(k) < 3 or k in seen or k in _STOP or _is_in(k, drop) or _is_name(k, names):
            continue
        seen.add(k)
        result.append((w, k))
    return result


# Stopwords of `purpose_evidence.meaningful_words`, as keys (it has no public set).
_STOP: Set[str] = {word_key(w) for w in "the and for with from this that are was were been has have had not but you your our its his her their them they what which who when where why how all any each some more most other such only own same than too very can will just should would could may might must also here there then now about after before again use used using see via etc per one two".split()} | _EXTRA_STOP | {
    word_key(w) for w in "ve ile için icin bir bu şu o da de ki mi ya veya ama gibi kadar daha çok cok en her hem ise ne nasıl nasil var yok olan olarak olur şey sey ben sen biz siz proje projeyi project projects dosya file files kod code main".split()
}


# ---------------------------------------------------------------------------
# Evidence side
# ---------------------------------------------------------------------------
#: Labels the importer writes at the start of an evidence line (`codebase_importer._collect_purpose`).
EVIDENCE_LABELS: Tuple[str, ...] = (
    SESSION_EVIDENCE_LABEL,
    "Own description",
    "README intro",
    "README title",
    "Docstring",
    "Header comment",
    "Text in code",
    "Titles shown to users",
    "Command-line help",
    "Capabilities implied by libraries",
    "Project shape",
    "Entry point",
    "Names in code",
)
_LABEL_RE = re.compile(
    r"^(?:" + "|".join(re.escape(lbl) for lbl in sorted(EVIDENCE_LABELS, key=len, reverse=True)) + r")"
    r"(?:\s*\((?P<paren>[^()]*(?:\([^()]*\)[^()]*)*)\))?\s*:\s*"
)
_TEMPLATE_README_RE = re.compile(r"^README \([^)]*\) is the unmodified .* template \(ignored\)\.?$")
_PARENS_RE = re.compile(r"\([^()]*\)")
_QUOTED_RE = re.compile(r'"([^"]*)"')
_TITLE_KIND_RE = re.compile(r'\b(?:page title|meta description|heading|window title)\s+(?=")', re.IGNORECASE)


def _strip_parens(text: str) -> str:
    prev = None
    while prev != text:
        prev, text = text, _PARENS_RE.sub(" ", text)
    return text


def evidence_text(evidence: str) -> str:
    """The evidence with labels, label parentheses (file names, constant names),
    importer explanations and file names removed: only the quoted / described content."""
    out: List[str] = []
    for line in (evidence or "").splitlines():
        line = line.strip()
        if not line or _TEMPLATE_README_RE.match(line):
            continue
        m = _LABEL_RE.match(line)
        label = ""
        if m:
            label = line[: m.start("paren") if m.group("paren") is not None else m.end()].split("(")[0].strip(" :")
            # A README intro may carry `titled "..."` in its parentheses: that title is content.
            titled = _QUOTED_RE.findall(m.group("paren") or "")
            line = " ".join(titled + [line[m.end():]])
        if label in ("Entry point", "Project shape"):
            line = _strip_parens(line)  # explanations written by the importer
        elif label in ("Titles shown to users", "Command-line help"):
            line = _TITLE_KIND_RE.sub("", line)
            line = re.sub(r"\(\s*[^()\s]+\.[A-Za-z0-9]{1,5}\s*\)", " ", line)  # (index.php)
        elif label == "Names in code":
            line = re.sub(r"\((?:class|interface|type|enum|struct|trait)\)", " ", line)
        line = _URL_RE.sub(" ", line)
        line = _FILE_NAME_RE.sub(lambda fm: " " + fm.group(0).rsplit(".", 1)[0] + " ", line)
        out.append(line)
    return "\n".join(out)


def evidence_words(evidence: str, project_name: str = "") -> List[Tuple[str, str]]:
    """Content (word, key) pairs of the evidence that count for the overlap check."""
    return _content_words(evidence_text(evidence), name_keys(project_name),
                          GENERIC_WORDS | BOILERPLATE_WORDS, split_identifiers=True)


# ---------------------------------------------------------------------------
# Answer side
# ---------------------------------------------------------------------------
_MARKDOWN_RE = re.compile(r"[*_`#>]+")
_LEAD_IN_RE = re.compile(
    r"^\s*(?:sorry|unfortunately|üzgünüm|maalesef|based on (?:the |this )?(?:provided |given |available )?"
    r"(?:evidence|information|analysis)|from (?:the )?evidence|according to (?:the )?evidence|"
    r"kanıtlara göre|verilen bilgilere göre)[,:;.!\s-]*",
    re.IGNORECASE,
)
_UNKNOWN_RE = re.compile(
    r"^(?:unknown\b|i (?:do not|don['’]t) know|(?:there is |there['’]s )?(?:not enough|insufficient|too little) "
    r"(?:information|evidence|context|details|data)|(?:it is |it['’]s )?(?:not (?:clear|possible)|unclear|impossible) "
    r"(?:what|to (?:tell|say|determine))|(?:i |we )?(?:cannot|can not|can['’]t|am unable to|are unable to|unable to) "
    r"(?:determine|tell|say|infer|know)|(?:this |it )?cannot be determined|"
    r"the (?:evidence|information) (?:does not|doesn['’]t|is not enough|is insufficient|is too thin)|"
    r"bilinmiyor|bilmiyorum|bilinmemektedir|belirsiz|yeterli (?:bilgi|kanıt|kanit|veri) yok|"
    r"(?:bilgi|kanıt|kanit) yetersiz|belirlenemiyor|belirlenemedi|anlaşılamıyor|anlasilamiyor|söylemek mümkün değil)",
    re.IGNORECASE,
)


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", _MARKDOWN_RE.sub(" ", text or "")).strip()


def is_unknown(text: str) -> bool:
    """True for UNKNOWN (any case / markdown) and obvious "I can't tell" variants."""
    plain = _plain(text)
    first = re.sub(r"[^\w\s]", " ", plain).split()
    if first and first[0].upper() == "UNKNOWN":
        return True
    return bool(_UNKNOWN_RE.match(_LEAD_IN_RE.sub("", plain)))


_ABBREV_RE = re.compile(
    r"\b(?:e\.g|i\.e|etc|vs|cf|approx|incl|esp|resp|dr|mr|mrs|ms|prof|no|nr|ca|örn|orn|vb|bkz|yy|sn)\.", re.IGNORECASE
)
_INNER_DOT_RE = re.compile(r"(?<=[\w])[.](?=[\w])")  # 1.5, v2.0.1, main.py, e.g (inner dot)
_SENTENCE_RE = re.compile(r"[^.!?…\n]*[^\W_][^.!?…\n]*(?:[.!?…]+|$)", re.MULTILINE)


def count_sentences(text: str) -> int:
    """Sentences in `text`; abbreviations, decimals, versions, file names and URLs do not
    end one. A line break also ends a sentence (bullet lists count per line)."""
    s = _URL_RE.sub("URL", text or "")
    s = _ABBREV_RE.sub(lambda m: m.group(0)[:-1].replace(".", ""), s)
    s = _INNER_DOT_RE.sub("", s)
    return sum(1 for m in _SENTENCE_RE.finditer(s) if re.search(r"[^\W_]", m.group(0)))


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------
def _fail(reason: str) -> QualityResult:
    return QualityResult(ok=False, reason=reason, detail=REASONS[reason])


def check_purpose_suggestion(text: str, evidence: str, project_name: str) -> QualityResult:
    """Accepts or rejects a model-written purpose (see the module docstring for the rules)."""
    plain = _plain(text)
    if not re.search(r"[^\W_]", plain):
        return _fail("empty")
    if is_unknown(plain):
        return _fail("unknown")
    if len(plain) > MAX_CHARS or count_sentences(_MARKDOWN_RE.sub(" ", text or "")) > MAX_SENTENCES:
        return _fail("too_long")
    names = name_keys(project_name)
    body = _LEAD_IN_RE.sub("", plain)
    content = _content_words(body, names, _NOT_CONTENT, split_identifiers=False)
    if not content:
        mentions_name = any(_is_name(word_key(w), names) for w in re.findall(r"[^\W\d_]+", _APOSTROPHE_SUFFIX_RE.sub("", body)))
        return _fail("name_only" if mentions_name else "generic")
    if len(content) < MIN_CONTENT_WORDS:
        return _fail("generic")
    ev = evidence_words(evidence, project_name)
    matched: List[str] = []
    for word, key in content:
        hit = next((ev_word for ev_word, ev_key in ev if words_match(key, ev_key)), None)
        if hit is not None:
            matched.append(f"{word}~{hit}")
    if not matched:
        return _fail("no_overlap")
    return QualityResult(ok=True, matched=tuple(matched))


__all__: Sequence[str] = (
    "QualityResult", "REASONS", "check_purpose_suggestion", "count_sentences", "evidence_text",
    "evidence_words", "is_unknown", "name_keys", "words_match", "GENERIC_WORDS", "HEDGE_WORDS",
    "META_WORDS", "BOILERPLATE_WORDS", "EVIDENCE_LABELS", "MAX_CHARS", "MAX_SENTENCES",
)
