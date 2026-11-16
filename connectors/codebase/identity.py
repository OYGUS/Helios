"""Identity line (`SolarSystem.identity`): every scan writes one short fact line, "what the
project is" (`identity_line`): kind + languages, 2–3 external libraries, the first entry
point, joined with " · " and capped at `IDENTITY_MAX_CHARS`. The kind word comes only from
the explicit signals of `IDENTITY_KINDS` (ambiguous / none -> no kind word); nothing comes
from the LLM. A re-scan replaces it (`merge_identity`); `stored_identity` derives the same
line for display from stored facts of systems scanned before the field existed.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.models import SolarSystem
from connectors.codebase.constants import _STDLIB_MODULES
from connectors.codebase.lang_common import _clip
from connectors.codebase.purpose_text import CAPABILITIES_LABEL
from connectors.codebase.libraries import TECH_FINGERPRINTS, _Manifests, _STDLIB_CAPABILITY_NAMES, _norm_dep
from connectors.codebase.content import _Analysis, _plural


# ---------------------------------------------------------------------------
# Identity line (`SolarSystem.identity`): what the project IS, from code facts only
# ---------------------------------------------------------------------------
IDENTITY_MAX_CHARS = 90  # libraries are dropped first, then the entry point
IDENTITY_MAX_LIBS = 3
IDENTITY_SEP = " · "
IDENTITY_TOOLTIP = "Derived from the code analysis"
#: Marker signals (not library names) that the analysis turns into kind signals.
MARKER_ROOT_INDEX_PHP = "marker:index.php"  # index.php at the root or in public/
MARKER_CLI = "marker:cli"  # argparse/click/typer in a file with a `__main__` block; commander/yargs + package.json bin
MARKER_AIOHTTP_WEB = "marker:aiohttp.web"  # `aiohttp.web` imported (aiohttp alone is also an HTTP client)
MARKER_UNITY = "marker:unity"  # ProjectSettings/ProjectVersion.txt, or Assets/ + ProjectSettings/
MARKER_GODOT = "marker:godot"  # project.godot at the root

#: The kind table: (kind words, tier, signals). A signal is a library name as imported or
#: declared (matched after `_norm_dep`; entries ending in "/" are scope prefixes) or a
#: marker above. Only the lowest tier with a hit counts; inside it the web kinds collapse
#: (website > web app > web server), and if two different kinds are still left the
#: project is ambiguous and gets no kind word. Without any hit: no kind word either.
IDENTITY_KINDS: Tuple[Tuple[str, int, Tuple[str, ...]], ...] = (
    ("game", 1, (MARKER_UNITY, MARKER_GODOT)),
    ("desktop app", 1, ("electron", "tauri", "@tauri-apps/")),
    ("desktop app", 2, ("pyside6", "pyside2", "pyqt6", "pyqt5", "pyqt4", "tkinter", "customtkinter", "wx", "wxpython", "kivy", "dearpygui")),
    ("game", 2, ("pygame", "pyglet", "arcade", "ursina", "panda3d", "phaser")),
    ("bot", 2, ("discord", "discord.py", "py-cord", "nextcord", "disnake", "discord.js", "eris", "aiogram", "telegram",
                "python-telegram-bot", "telebot", "pytelegrambotapi", "telegraf", "node-telegram-bot-api", "grammy",
                "whatsapp-web.js", "baileys", "@whiskeysockets/baileys")),
    ("mobile app", 2, ("react-native", "expo")),
    ("web app", 2, ("next", "react-dom", "vue", "nuxt", "svelte", "@sveltejs/", "@angular/core", "vite", "preact", "solid-js",
                    "django", "laravel/framework", "symfony/framework-bundle")),
    ("web server", 2, ("express", "fastify", "koa", "@hapi/hapi", "hapi", "@nestjs/core", "flask", "fastapi", MARKER_AIOHTTP_WEB,
                       "sanic", "tornado", "starlette", "bottle", "slim/slim")),
    ("website", 2, (MARKER_ROOT_INDEX_PHP,)),
    ("command-line tool", 3, (MARKER_CLI,)),
)
_WEB_KINDS = ("website", "web app", "web server")  # collapse order inside one tier

#: Development-only tools never listed as libraries (normalised; "x-" / "@x/" are prefixes).
IDENTITY_DEV_TOOLS: Set[str] = {
    "pytest", "pytest-", "mypy", "black", "ruff", "flake8", "pylint", "isort", "coverage", "tox", "pre-commit", "nox",
    "setuptools", "wheel", "pip", "hatchling", "poetry",
    "eslint", "eslint-", "@eslint/", "@typescript-eslint/", "prettier", "prettier-", "typescript", "@types/", "jest",
    "jest-", "@jest/", "vitest", "@vitest/", "mocha", "chai", "nodemon", "ts-node", "tsx", "@testing-library/",
    "@babel/", "babel-", "webpack", "webpack-", "vite", "@vitejs/", "postcss", "postcss-", "autoprefixer", "rollup",
    "@rollup/", "esbuild", "husky", "lint-staged", "concurrently", "cross-env", "phpunit/phpunit",
}
_IDENTITY_MARKUP_LANGS = {"HTML", "CSS", "SCSS", "SQL", "Markdown / Wiki", "C/C++ Header"}


def _is_dev_tool(name: str) -> bool:
    n = _norm_dep(name)
    return any(n == t or ((t.endswith("-") or t.endswith("/")) and n.startswith(t)) for t in IDENTITY_DEV_TOOLS)


def _signal_matches(name: str, signal: str) -> bool:
    if name.startswith("marker:") or signal.startswith("marker:"):
        return name == signal
    n, s = _norm_dep(name), _norm_dep(signal)
    return n.startswith(s) if s.endswith("/") else n == s


def identity_kind(signals: Iterable[str]) -> Tuple[str, List[str]]:
    """(kind words, signals that gave it) from `IDENTITY_KINDS`, or ("", []) when there is
    no signal or the signals are ambiguous (see the table)."""
    names = [s for s in dict.fromkeys(str(x).strip() for x in signals) if s]
    for tier in sorted({t for _k, t, _s in IDENTITY_KINDS}):
        hits: Dict[str, List[str]] = {}
        for kind, t, sigs in IDENTITY_KINDS:
            if t != tier:
                continue
            for n in names:
                if any(_signal_matches(n, s) for s in sigs) and n not in hits.get(kind, []):
                    hits.setdefault(kind, []).append(n)
        if not hits:
            continue
        web = [k for k in _WEB_KINDS if k in hits]
        if len(web) > 1:
            keep = web[0]
            merged = [n for k in web for n in hits.pop(k)]
            hits[keep] = list(dict.fromkeys([n for n in merged]))
        if len(hits) != 1:
            return "", []
        kind, why = next(iter(hits.items()))
        return kind, why
    return "", []


def identity_languages(languages: Sequence[Tuple[str, int]]) -> List[str]:
    """The 1–2 main code languages from (language label, file count) pairs: labels are
    reduced to the language ("TypeScript (React)" -> "TypeScript"), markup / style / SQL
    only count when nothing else is there, and a second language needs at least 2 files
    and 15% of the first one's files (unknown counts = 0 skip that test)."""
    counts: Dict[str, int] = {}
    for label, n in languages:
        base = re.sub(r"\s*\(.*\)\s*$", "", str(label or "")).strip()
        if base:
            counts[base] = counts.get(base, 0) + int(n or 0)
    if not counts:
        return []
    code = {k: v for k, v in counts.items() if k not in _IDENTITY_MARKUP_LANGS} or counts
    order = list(code)  # insertion order = caller's order (ties)
    ranked = sorted(code.items(), key=lambda kv: (-kv[1], order.index(kv[0])))
    out = [ranked[0][0]]
    if len(ranked) > 1:
        first_n, (second, second_n) = ranked[0][1], ranked[1]
        if (first_n == 0 and second_n == 0) or (second_n >= 2 and second_n >= 0.15 * first_n):
            out.append(second)
    return out


def identity_line(
    languages: Sequence[Tuple[str, int]] = (),
    libraries: Sequence[str] = (),
    entry: str = "",
    signals: Iterable[str] = (),
    code_files: Optional[int] = None,
    file_count: int = 0,
    max_chars: int = IDENTITY_MAX_CHARS,
) -> str:
    """The project's identity line, from analysis facts only, joined with " · ":

    1. kind + languages ("Python desktop app"; kind words only from `IDENTITY_KINDS`,
       otherwise just the languages, "Python + JavaScript");
    2. up to `IDENTITY_MAX_LIBS` external `libraries` (caller's order = importance; dev
       tools and the standard library skipped; the libraries that gave the kind first);
    3. "entry: <first entry point>".

    Unknown parts are left out. `code_files == 0` gives a factual "No code files" /
    "N files, no code". Longer than `max_chars`: libraries are dropped first, then the
    entry point."""
    if code_files == 0:
        return f"{_plural(file_count, 'file')}, no code" if file_count > 0 else "No code files"
    signal_list = list(signals) + list(libraries)
    kind, why = identity_kind(signal_list)
    langs = identity_languages(languages)
    lang_txt = " + ".join(langs)
    head = f"{lang_txt} {kind}" if (lang_txt and kind) else (lang_txt or kind[:1].upper() + kind[1:])

    libs: List[str] = []
    seen: Set[str] = set()
    kind_keys = {_norm_dep(w) for w in why}
    # The libraries that gave the kind come first: the imported ones, else the first
    # declared one (e.g. `electron`, which the code itself rarely imports).
    candidates = [n for n in libraries if _norm_dep(n) in kind_keys]
    if not candidates:
        candidates = [w for w in why if not w.startswith("marker:")][:1]
    candidates += list(libraries)
    for name in candidates:
        name = str(name or "").strip()
        key = _norm_dep(name)
        if not name or key in seen or name.startswith("marker:") or name.endswith("\\"):
            continue
        if _is_dev_tool(name) or name in _STDLIB_MODULES or name.lower() in _STDLIB_MODULES:
            continue
        # A declared distribution next to its imported name (pyside6-essentials / PySide6).
        if any(key.startswith(s + "-") for s in seen):
            continue
        seen.add(key)
        libs.append(name)
        if len(libs) >= IDENTITY_MAX_LIBS:
            break
    entry = str(entry or "").strip()

    def build(n_libs: int, with_entry: bool) -> str:
        parts = [p for p in (head, ", ".join(libs[:n_libs]), f"entry: {entry}" if (entry and with_entry) else "") if p]
        return IDENTITY_SEP.join(parts)

    for with_entry in (True, False):
        for n in range(len(libs), -1, -1):
            text = build(n, with_entry)
            if len(text) <= max_chars:
                return text
    return _clip(build(0, False), max_chars)


def _identity_library_order(externals: Sequence[Tuple[str, int]]) -> List[str]:
    """Library names by importing-file count, recognised libraries (`TECH_FINGERPRINTS`)
    first among equal counts, then the given order."""
    known = {_norm_dep(e) for t in TECH_FINGERPRINTS.values() for e in t.exact}
    indexed = list(enumerate(externals))
    indexed.sort(key=lambda it: (-int(it[1][1] or 0), _norm_dep(it[1][0]) not in known, it[0]))
    return [name for _i, (name, _n) in indexed]


def _analysis_identity(an: "_Analysis", manifests: "_Manifests") -> str:
    """`identity_line` for one scan (facts of `_Analysis` + the manifests + a few root markers)."""
    code = [f for f in an.files if f.is_code]
    if not code:
        return identity_line(code_files=0, file_count=len(an.files))
    lang_counts = Counter(f.lang for f in code)
    signals: List[str] = [e.name for e in an.externals] + sorted(manifests.names)
    cli_libs = {"argparse", "click", "typer"}
    for f in code:
        signals.extend(sorted(n for n in f.external_imports if n in _STDLIB_CAPABILITY_NAMES | {"tkinter"}))
        if f.ext == ".py" and f.layer != "testing" and (f.has_main_block or f.name == "__main__.py") and f.external_imports & cli_libs:
            signals.append(MARKER_CLI)
        if f.ext == ".py" and any(m == "aiohttp.web" or (m == "aiohttp" and "web" in names) for _lvl, m, names in f.py_imports):
            signals.append(MARKER_AIOHTTP_WEB)
    if any(isinstance(d.get("bin"), (str, dict)) and d.get("bin") for _p, d in manifests.packages) and manifests.names & {"commander", "yargs", "cac", "meow"}:
        signals.append(MARKER_CLI)
    if "index.php" in an.by_rel or "public/index.php" in an.by_rel:
        signals.append(MARKER_ROOT_INDEX_PHP)
    folder = an.folder
    try:
        if (folder / "project.godot").is_file():
            signals.append(MARKER_GODOT)
        if (folder / "ProjectSettings" / "ProjectVersion.txt").is_file() or ((folder / "Assets").is_dir() and (folder / "ProjectSettings").is_dir()):
            signals.append(MARKER_UNITY)
    except OSError:
        pass
    libraries = _identity_library_order([(e.name, len(e.importers)) for e in an.externals if not e.name.endswith("\\")])
    return identity_line(
        languages=lang_counts.most_common(),
        libraries=libraries,
        entry=an.entries[0][0] if an.entries else "",
        signals=signals,
        code_files=len(code),
        file_count=len(an.files),
    )


def merge_identity(existing: SolarSystem, fresh: SolarSystem) -> None:
    """A re-scan always replaces the identity line (it is not user-editable)."""
    existing.identity = fresh.identity or ""


_SHAPE_RE = re.compile(r"^(?P<langs>.+?), (?P<n>\d+) code files?\b")
_PAREN_NAMES_RE = re.compile(r"\(([^()]*)\)")


def stored_identity(system: SolarSystem) -> str:
    """The identity line to show for `system`: `system.identity` when a scan wrote one;
    otherwise a best-effort value derived with the same rules from what is stored —
    the `purpose_evidence` facts ("Project shape: …", "Entry point: …", "Capabilities
    implied by libraries: …"), the generated core plan ("Languages: …", "Entry point: …")
    and the External Libraries module. Nothing is invented: "" when nothing is known, and
    for manual projects that were never scanned."""
    current = " ".join(str(getattr(system, "identity", "") or "").split())
    if current:
        return current
    evidence = str(getattr(system, "purpose_evidence", "") or "")
    plan = "" if system.is_user_modified else str(system.core_plan or "")
    ext_planet = next((p for p in system.planets if p.kind == "external"), None)
    if system.source == "manual" and not evidence.strip() and ext_planet is None:
        return ""
    languages: List[Tuple[str, int]] = []
    code_files: Optional[int] = None
    signals: List[str] = []
    entry = ""
    for line in evidence.splitlines():
        if line.startswith("Project shape:"):
            body = line[len("Project shape:"):].strip()
            first, _sep, rest = body.partition("; ")
            m = _SHAPE_RE.match(first)
            if m:
                code_files = int(m.group("n"))
                if m.group("langs").strip() != "no code":
                    languages = [(lang.strip(), 0) for lang in m.group("langs").split(" + ")]
            for fact in rest.split("; ") if rest else []:
                if fact.startswith("PHP website: index.php"):
                    signals.append(MARKER_ROOT_INDEX_PHP)
                elif fact.startswith("command-line tool ("):
                    signals.append(MARKER_CLI)
                else:
                    for grp in _PAREN_NAMES_RE.findall(fact):
                        signals.extend(n.strip() for n in grp.split(","))
        elif line.startswith(CAPABILITIES_LABEL + ":"):
            for grp in _PAREN_NAMES_RE.findall(line[len(CAPABILITIES_LABEL) + 1:]):
                signals.extend(n.strip() for n in grp.split(",") if n.strip() and n.strip() != "…" and " in " not in n)
        elif line.startswith("Entry point:") and not entry:
            entry = line[len("Entry point:"):].strip().split(" (", 1)[0].split("; ", 1)[0].strip()
    for line in plan.splitlines():
        if line.startswith("Languages:"):
            parsed = [(m.group(1).strip(), int(m.group(2))) for m in re.finditer(r"([^,(]+(?:\([^)]*\)[^,(]*)?) \((\d+)\)", line[len("Languages:"):])]
            # "TypeScript (React) (12)" -> label "TypeScript (React)"
            if parsed:
                languages = [(lbl, n) for lbl, n in parsed if lbl != "Markdown / Wiki"]
                if not languages and code_files is None:
                    code_files = 0
        elif line.startswith("Entry point:") and not entry and not line.startswith("Entry point: none found"):
            entry = line[len("Entry point:"):].strip().split(" — ", 1)[0].strip()
    externals: List[Tuple[str, int]] = []
    if ext_planet is not None:
        for sat in ext_planet.satellites:
            if sat.kind not in ("", "package"):
                continue
            m = re.search(r"Imported by \((\d+)\)", sat.content or "")
            externals.append((sat.title.strip(), int(m.group(1)) if m else 0))
    if not languages and code_files is None and not externals and not entry:
        return ""
    return identity_line(
        languages=languages,
        libraries=_identity_library_order(externals),
        entry=entry,
        signals=signals,
        code_files=code_files if code_files == 0 else None,
    )
