"""Purpose (`SolarSystem.purpose`, `purpose_source`, `purpose_evidence`): every scan writes
`purpose_evidence`, a labelled text of at most `PURPOSE_EVIDENCE_BUDGET` characters built
only from what the walk already read, in this priority order: the project's own
description (package.json, pyproject.toml, composer.json, Cargo.toml, setup.py,
setup.cfg); the README intro (`readme_intro`: the descriptive paragraph under the H1 or
an About / Overview / Hakkında / ... heading; badges, code, lists, install / usage text
and imperative instructions are skipped; scaffold templates such as create-next-app are
discarded and recorded as a fact); header comments / docstrings of entry and
settings-like files; user-facing strings (descriptive module-level string constants,
argparse texts, window / page titles, meta descriptions, `<h1>`) — never values of
key/token/secret/password-like names or anything that looks like a credential, and
`.env` files are never read; the capabilities implied by libraries (`TECH_FINGERPRINTS`,
the single library table); and shape facts (kind, entry point, telling names).
`connectors.agent_importer` adds the first session requests as the FIRST line (every
compose / merge puts it on top; old evidence with it last is read the same way).
Header comments and docstrings are filtered line by line
(`purpose_evidence.clean_header_text`): encoding notes, shebangs, license text, lint /
tool directives, `/// <reference>`, modelines, "generated / do not edit" notes,
metadata and separators are dropped, and a block left with fewer than 3 meaningful
words gives way to the next comment block in the first 40 lines.
`purpose_evidence.assess_purpose_evidence` scores the stored evidence (descriptions,
README, descriptive headers / user-facing text / session requests count; code facts do
not); only strong evidence is sent to Ollama, and its answer is only a suggestion
(`SolarSystem.purpose_suggestion`, see `connectors.purpose_suggestion`). `purpose` is set
deterministically only from the project's own words (`purpose_source="readme"`, which
also clears a pending suggestion); a "user" purpose is never touched, an accepted "ai"
suggestion survives a re-scan without own words (`merge_purpose`). The core plan opens
with a `Purpose:` line (`purpose_line`, never a suggestion); when no purpose is known it
says "Not written yet" plus the capabilities. README text other than
the intro is not copied into the core plan.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set, Tuple

from core.models import SolarSystem
from connectors.purpose_evidence import SESSION_EVIDENCE_LABEL, session_line_of
from connectors.codebase.fileinfo import _FileInfo
from connectors.codebase.lang_common import _clip
from connectors.codebase.purpose_text import (
    CAPABILITIES_LABEL,
    PURPOSE_EVIDENCE_BUDGET,
    PURPOSE_HEADER_MAX,
    PURPOSE_NAMES_MAX,
    PURPOSE_SESSION_RESERVE,
    PURPOSE_STRING_CAP,
    PURPOSE_STRING_MAX,
    SESSION_REQUESTS_MAX,
    SESSION_REQUEST_CHARS,
    _PURPOSE_NAME_RE,
    _PURPOSE_STEMS,
    _docstring_description,
    _first_sentences,
    _looks_secret,
    _valid_description,
    readme_intro,
)
from connectors.codebase.libraries import TECH_FINGERPRINTS, _Manifests, _STDLIB_CAPABILITY_NAMES, _norm_dep
from connectors.codebase.content import _Analysis, _capped, _plural


# ---------------------------------------------------------------------------
# Purpose evidence: assembly (uses only what the single walk already read)
# ---------------------------------------------------------------------------
_GENERIC_NAMES = {
    "main", "init", "run", "setup", "start", "stop", "helper", "helpers", "util", "utils", "get", "set", "handle",
    "handler", "callback", "update", "render", "index", "app", "process", "load", "save", "test", "tests", "foo",
    "bar", "do", "go", "exec", "execute", "wrapper", "inner", "default", "config", "configure", "create", "new",
    "home", "page", "data", "item", "items", "value", "values", "result", "results", "check", "parse", "read",
    "write", "open", "close", "on", "off", "log", "debug", "error", "print", "show", "hide",
}
_PHP_INCLUDE_STEMS = {
    "config", "db", "database", "functions", "function", "header", "footer", "init", "bootstrap", "helpers",
    "helper", "connect", "connection", "conn", "settings", "autoload", "common", "layout", "nav", "navbar",
    "sidebar", "session", "auth", "constants", "env",
}
_SHAPE_CAPABILITIES = (
    ("desktop GUI", "desktop GUI application"),
    ("Discord bot", "Discord bot"),
    ("Telegram bot", "Telegram bot"),
    ("WhatsApp bot", "WhatsApp bot"),
    ("web server / API", "web server / API"),
)


@dataclass
class _PurposeInfo:
    """Deterministic purpose evidence of one scan."""

    items: List[str] = field(default_factory=list)  # labelled evidence lines, priority order
    purpose: str = ""  # the project's own words (description / README intro), or ""
    purpose_from: str = ""  # e.g. "the project's own description in package.json"
    origin: str = ""  # "package" (own description) or "readme" (README intro); see SolarSystem.purpose_origin
    capabilities: str = ""  # "speech-to-text (faster_whisper), LLM / AI model (ollama)"
    facts: List[str] = field(default_factory=list)  # core-plan facts ("README is the unmodified ... template")
    evidence: str = ""  # assembled text (code evidence only)


def _capability_groups(an: "_Analysis", manifests: "_Manifests") -> List[Tuple[str, List[str]]]:
    """(capability, matched library names) in table order, from third-party packages
    actually imported (External Libraries), declared dependency names, a few standard
    library modules (tkinter, sqlite3, argparse, smtplib) and PHP PDO/mysqli usage."""
    names: Dict[str, str] = {}
    for e in an.externals:
        if e.name and not e.name.endswith("\\"):
            names.setdefault(_norm_dep(e.name), e.name)
    for f in an.files:
        for n in sorted(f.external_imports):
            if n in _STDLIB_CAPABILITY_NAMES:
                names.setdefault(n, n)
        if f.uses_php_db:
            names.setdefault(f.uses_php_db.lower(), f"{f.uses_php_db} in PHP")
    for n in sorted(manifests.names):
        if n:
            names.setdefault(n, n)
    groups: Dict[str, List[str]] = {}
    for tech in TECH_FINGERPRINTS.values():
        if not tech.capability:
            continue
        exact = {_norm_dep(e) for e in tech.exact}
        hits = sorted(k for k in names if k in exact or (tech.prefixes and any(k.startswith(p) for p in tech.prefixes)))
        if hits:
            bucket = groups.setdefault(tech.capability, [])
            for k in hits:
                if names[k] not in bucket:
                    bucket.append(names[k])
    return list(groups.items())


def _format_capabilities(groups: List[Tuple[str, List[str]]], cap: int = 380) -> str:
    parts = [f"{c} ({', '.join(libs[:3])}{', …' if len(libs) > 3 else ''})" for c, libs in groups]
    return _clip(", ".join(parts), cap) if parts else ""


def _purpose_file_order(an: "_Analysis") -> List[_FileInfo]:
    """Candidate files in a stable order: entry points, then files named like
    settings/config/main/app/index, then the most-imported ones."""
    entry_rels = [r for r, _ in an.entries]
    cands = [f for f in an.files if f.purpose_candidate]
    def rank(f: _FileInfo) -> Tuple[int, int, int, str]:
        stem = posixpath.splitext(f.name)[0].lower()
        return (
            entry_rels.index(f.rel) if f.rel in entry_rels else len(entry_rels),
            0 if stem in _PURPOSE_STEMS else 1,
            -len(f.importers),
            f.rel,
        )
    return sorted(cands, key=rank)


def _header_of(f: _FileInfo) -> Tuple[str, str]:
    """(label, text) of a file's module docstring or first comment block."""
    if f.ext == ".py":
        para = _docstring_description(f.doc)
        if para:
            return "Docstring", _clip(para, 300)
        if f.lead_comment:
            return "Header comment", _clip(f.lead_comment, 300)
        return "", ""
    for kind, _n, text in f.purpose_bits:
        if kind == "header comment":
            return "Header comment", _clip(text, 300)
    return "", ""


def _names_line(an: "_Analysis", ordered: List[_FileInfo]) -> str:
    seen: Set[str] = set()
    names: List[str] = []
    files = ordered + sorted((f for f in an.files if f.is_code and not f.purpose_candidate and f.layer != "testing"),
                             key=lambda f: (-len(f.importers), f.rel))
    for f in files:
        for d in f.defs:
            n = d.name
            if not n or n.startswith("_") or n.lower() in _GENERIC_NAMES or n in seen or len(n) < 3:
                continue
            seen.add(n)
            names.append(f"{n}()" if d.kind == "function" else f"{n} ({d.keyword or d.kind})")
            if len(names) >= PURPOSE_NAMES_MAX:
                break
        if len(names) >= PURPOSE_NAMES_MAX:
            break
    return ", ".join(names)


def _shape_line(an: "_Analysis", groups: List[Tuple[str, List[str]]]) -> str:
    files = an.files
    code = [f for f in files if f.is_code]
    langs = Counter(f.lang for f in code if f.ext not in (".css", ".scss"))
    lang_txt = " + ".join(k for k, _ in langs.most_common(2)) or "no code"
    facts: List[str] = [f"{lang_txt}, {_plural(len(code), 'code file')}"]
    root_php = sorted(f.name for f in code if f.ext == ".php" and "/" not in f.rel)
    index_php = "index.php" in root_php or "public/index.php" in an.by_rel
    if index_php:
        pages = [posixpath.splitext(n)[0] for n in root_php if n != "index.php" and posixpath.splitext(n)[0].lower() not in _PHP_INCLUDE_STEMS]
        txt = "PHP website: index.php"
        if pages:
            txt += f" plus {_plural(len(pages), 'other page')} at the root ({_capped(pages, 8)})"
        facts.append(txt)
    caps = dict(groups)
    for cap, label in _SHAPE_CAPABILITIES:
        if cap in caps:
            facts.append(f"{label} ({', '.join(caps[cap][:2])})")
    cli_files = [f.rel for f in code if f.ext == ".py" and "argparse" in f.external_imports and f.has_main_block]
    if cli_files:
        facts.append(f"command-line tool (argparse in {cli_files[0]})")
    if not index_php and "index.html" in an.by_rel and not any(f.ext in (".py", ".php") for f in code) and "web frontend" not in caps:
        facts.append("static web page (index.html at the root)")
    return "; ".join(facts)


def _collect_purpose(an: "_Analysis", manifests: "_Manifests") -> _PurposeInfo:
    """Builds the deterministic purpose evidence (see `SolarSystem.purpose_evidence`) in
    priority order: the project's own description (manifests), the README intro, header
    comments / docstrings of entry and settings files, user-facing strings and titles,
    capabilities implied by libraries, and shape facts (kind, entry point, telling
    names). Each line is labelled with where it came from; secrets are never quoted."""
    info = _PurposeInfo()
    name = an.project_name
    # 1. own description
    own = next(((rel, d) for rel, d in manifests.own_descriptions if _valid_description(d, name)), None)
    if own:
        info.items.append(f"Own description ({own[0]}): {_clip(own[1], 350)}")
    # 2. README intro
    readme = next((f for f in sorted(an.files, key=lambda x: x.rel) if f.readme_text), None)
    rel, text = (readme.rel, readme.readme_text) if readme else an.extras.readme_alt
    intro = ""
    if rel and text:
        intro, title, tool = readme_intro(text)
        if tool:
            fact = f"README ({rel}) is the unmodified {tool} template (ignored)"
            info.facts.append(fact)
            info.items.append(fact + ".")
        elif intro:
            titled = f', titled "{_clip(title, 80)}"' if title and title.strip().lower() != name.strip().lower() else ""
            info.items.append(f"README intro ({rel}{titled}): {intro}")
        elif title and title.strip().lower() != name.strip().lower() and not _looks_secret(title):
            info.items.append(f"README title ({rel}): {_clip(title, 120)}")
    if own:
        info.purpose = _first_sentences(own[1])
        info.purpose_from = f"the project's own description in {own[0]}"
        info.origin = "package"
    elif intro:
        info.purpose = _first_sentences(intro)
        info.purpose_from = f"the README intro in {rel}"
        info.origin = "readme"
    # 3. header comments / docstrings
    ordered = _purpose_file_order(an)
    headers = 0
    seen_text: Set[str] = set()
    for f in ordered:
        label, h = _header_of(f)
        if h and h not in seen_text and not _looks_secret(h):
            seen_text.add(h)
            info.items.append(f"{label} ({f.rel}): {h}")
            headers += 1
            if headers >= PURPOSE_HEADER_MAX:
                break
    # 4. user-facing strings and titles
    titles: List[str] = []
    cli: List[str] = []
    strings: List[Tuple[int, int, int, str]] = []
    for pos, f in enumerate(ordered):
        for kind, bname, btext in f.purpose_bits:
            if kind in ("page title", "meta description", "heading", "window title"):
                entry = f'{kind} "{_clip(btext, 150)}" ({f.rel})'
                if len(titles) < 4 and not any(f'"{_clip(btext, 150)}"' in t for t in titles):
                    titles.append(entry)
            elif kind == "cli":
                if len(cli) < 2:
                    cli.append(f'{bname} "{_clip(btext, 200)}" ({f.rel})')
            elif kind == "string" and btext not in seen_text:
                hint = 0 if _PURPOSE_NAME_RE.search(bname) else 1
                strings.append((hint, -len(btext), pos, f'Text in code ({f.rel}, {bname}): "{_clip(btext, PURPOSE_STRING_CAP)}"'))
    if titles:
        info.items.append("Titles shown to users: " + "; ".join(titles))
    if cli:
        info.items.append("Command-line help: " + "; ".join(cli))
    for _h, _l, _p, line in sorted(strings)[:PURPOSE_STRING_MAX]:
        info.items.append(line)
    # 5. capabilities, 6. shape
    groups = _capability_groups(an, manifests)
    info.capabilities = _format_capabilities(groups)
    if info.capabilities:
        info.items.append(f"{CAPABILITIES_LABEL}: {info.capabilities}")
    info.items.append(f"Project shape: {_shape_line(an, groups)}")
    if an.entries:
        info.items.append("Entry point: " + "; ".join(f"{r} ({ev})" for r, ev in an.entries))
    names = _names_line(an, ordered)
    if names:
        info.items.append(f"Names in code: {names}")
    if an.extras.backup_dirs:
        info.facts.append(
            f"Skipped {_plural(len(an.extras.backup_dirs), 'backup folder')} ({_capped(sorted(an.extras.backup_dirs), 5)}): "
            "folders named backup / backups / *-backup / *_backup / *.bak are not analysed"
        )
    info.evidence = compose_purpose_evidence(info.items)
    return info


_ESSENTIAL_PREFIXES = (CAPABILITIES_LABEL, "Project shape:", "Entry point:", "Names in code:")


def compose_purpose_evidence(items: Sequence[str], session_line: str = "", budget: int = PURPOSE_EVIDENCE_BUDGET) -> str:
    """Joins labelled evidence lines within `budget` characters. The first-session
    requests line comes FIRST (what the user asked for is the best hint of what the
    project is for); `session_line` defaults to a session line found among `items`, so a
    re-compose of stored evidence in the old order moves it to the top. The compact facts
    (capabilities, shape, entry point, names) and the session line always keep their
    room; the descriptive lines between them are clipped / dropped from the end."""
    session_line = session_line or session_line_of(items)
    session_line = _clip(session_line, PURPOSE_SESSION_RESERVE) if session_line else ""
    essentials = [_clip(it, 420) for it in items if it.startswith(_ESSENTIAL_PREFIXES)]
    others = [it for it in items if not it.startswith(_ESSENTIAL_PREFIXES) and not it.startswith(SESSION_EVIDENCE_LABEL)]
    room = budget - sum(len(x) + 1 for x in essentials) - (len(session_line) + 1 if session_line else 0)
    kept: List[str] = []
    for it in others:
        if len(it) + 1 <= room:
            kept.append(it)
            room -= len(it) + 1
        elif room > 120:
            kept.append(_clip(it, room - 1))
            room = 0
    lines = ([session_line] if session_line else []) + kept + essentials
    return "\n".join(lines)


def format_session_requests(requests: Sequence[str]) -> str:
    """The session line of the purpose evidence: up to `SESSION_REQUESTS_MAX` quoted
    requests, each truncated to `SESSION_REQUEST_CHARS`; requests that look like secrets
    are dropped. Returns "" if nothing is left."""
    quoted: List[str] = []
    for r in requests:
        r = re.sub(r"\s+", " ", r or "").strip()
        if not r or _looks_secret(r):
            continue
        q = f'"{_clip(r, SESSION_REQUEST_CHARS)}"'
        if q not in quoted:
            quoted.append(q)
        if len(quoted) >= SESSION_REQUESTS_MAX:
            break
    return f"{SESSION_EVIDENCE_LABEL}: " + " | ".join(quoted) if quoted else ""


def _purpose_origin(evidence: str) -> str:
    for line in (evidence or "").splitlines():
        m = re.match(r"^(Own description|README intro) \(([^,)]+)", line)
        if m:
            what = "the project's own description" if m.group(1) == "Own description" else "the README intro"
            return f"{what} in {m.group(2)}"
    return "the project's own words"


def _evidence_capabilities(evidence: str) -> str:
    prefix = CAPABILITIES_LABEL + ": "
    for line in (evidence or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def purpose_line(purpose: str, source: str, evidence: str = "", origin: str = "") -> str:
    """First line of a generated core plan: the purpose and how it was obtained."""
    text = re.sub(r"\s+", " ", purpose or "").strip()
    if text:
        how = {
            "user": "entered by the user",
            "ai": "suggested by the local AI model from the collected evidence and accepted by the user",
            "readme": f"from {origin or _purpose_origin(evidence)}",
        }.get(source, "")
        return f"Purpose: {text}" + (f" ({how})" if how else "")
    caps = _evidence_capabilities(evidence) or "no recognisable signals"
    return f"Purpose: Not written yet — based on the code this project involves: {caps}"


def update_core_plan_purpose(system: SolarSystem) -> None:
    """Rewrites the `Purpose:` line of a generated core plan from `system.purpose`
    (no-op when the user edited the core plan or it has no such line)."""
    plan = system.core_plan or ""
    if system.is_user_modified or not plan.startswith("Purpose:"):
        return
    rest = plan[plan.find("\n"):] if "\n" in plan else ""
    system.core_plan = purpose_line(system.purpose, system.purpose_source, system.purpose_evidence) + rest


def apply_purpose_text(system: SolarSystem, text: str, source: str = "user") -> bool:
    """Sets `system.purpose` and updates the core plan's `Purpose:` line. Never
    overwrites a purpose the user typed unless `source == "user"`. Text from the local
    model is never a purpose: `source="ai"` is refused (returns False); AI text goes
    through `purpose_suggestion.set_purpose_suggestion` and becomes an "ai" purpose only
    via `accept_purpose_suggestion`. Returns True if the system changed."""
    if source == "ai":
        return False
    if system.purpose_source == "user" and source != "user":
        return False
    text = re.sub(r"\s+", " ", text or "").strip()
    system.purpose = text
    system.purpose_source = source if text else ""
    update_core_plan_purpose(system)
    return True


def merge_purpose(existing: SolarSystem, fresh: SolarSystem) -> None:
    """Applies a re-scan's purpose results to an existing system. The evidence is
    replaced (session requests already in it are kept when the scan had none). The
    purpose: a "user" purpose is never touched; else the project's own words
    ("readme") are taken when found; else an "ai" purpose is kept; else it is cleared."""
    evidence = fresh.purpose_evidence or ""
    if not session_line_of(evidence):
        old_line = session_line_of(existing.purpose_evidence or "")
        if old_line:
            evidence = compose_purpose_evidence(evidence.splitlines(), old_line)  # session line first
    existing.purpose_evidence = evidence
    if existing.purpose_source == "user":
        return
    if fresh.purpose_source == "readme" and fresh.purpose.strip():
        existing.purpose, existing.purpose_source = fresh.purpose, "readme"
        existing.purpose_origin = fresh.purpose_origin
        # The project's own words win; a pending AI suggestion is no longer needed.
        existing.purpose_suggestion = existing.purpose_suggestion_key = ""
    elif existing.purpose_source == "ai" and existing.purpose.strip():
        return
    else:
        existing.purpose, existing.purpose_source = "", ""
