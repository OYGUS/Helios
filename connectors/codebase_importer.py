"""Codebase, Understand-Anything (.ua), and Markdown Wiki Analysis Engine.

1. Imports `.ua/knowledge-graph.json` or `.understand-anything/knowledge-graph.json`
   directly if present (the graph is converted, not re-analysed).
2. Otherwise `build_code_map()` walks the directory once (pruning dependency/build/VCS
   folders), reads every analysable file exactly once and fills a `SolarSystem` in one
   of two modes.

   **File-level map** — for small or flat projects: the layout is flat (fewer than two
   directories with files, or almost everything at the root) OR the project has at most
   `FILE_LEVEL_MAX_FILES` analysable code files (Markdown does not count as code).

   * Each code file is one module (`kind="file"`, `file_path` = the file). Title = file
     name, or the relative path when two files share a name. `layer` comes from
     `_classify_file_layer()` (path/name tokens).
   * Its sub-components are the definitions found in it (`kind` "function" / "class",
     with `signature`, `line`, `file_path`). Python classes additionally get their
     public methods as their own sub-components (`kind="method"`, title `Foo.bar()`)
     when the file has few definitions: a class with at most
     `METHOD_SATELLITE_MAX_PUBLIC` public methods is expanded, in source order, while the
     module's sub-component count stays at most `METHOD_SATELLITES_MAX_TOTAL`; otherwise
     methods stay listed inside the class sub-component. A Python file without
     definitions gets one `kind="constants"` sub-component with its top-level names.
     Module-level code that calls project definitions gets a `(module code)`
     sub-component. At most `SATELLITE_CAP` per module; truncation is stated.
   * Links are file-to-file `depends_on` links, one per file pair, from resolved
     imports / includes / references. Labels name what is used when known:
     `imports islemler — calls gorev_yurut(), odak_modu()`.
   * Markdown and small config/data files are listed (not parsed, except Markdown
     headings) in one "Docs & Data" module (`kind="layer"`, layer `docs`).
   * At most `MAX_PLANETS` modules (including the Docs and External modules): the most
     significant files keep their own module (entry points first), the rest are listed
     in one "Other files" module (`kind="layer"`) whose sub-components are files.

   **Directory map** — for larger projects with real directories: modules are the real
   top-level directories (`kind="dir"`; root-level files form "Project Root"; a
   dominant directory such as `src/` is descended into; the smallest are folded into
   "Other"). Sub-components are the most significant files (`kind="file"`). File
   imports are aggregated into module-to-module links labelled with the import count.

   **External Libraries** — one extra module (`kind="external"`, layer `integration`)
   when the project imports third-party packages: one `kind="package"` sub-component per
   package actually imported (Python: top-level module names that are neither standard
   library — `sys.stdlib_module_names`, small fallback list — nor project modules;
   JS/TS: bare specifiers reduced to the package name incl. `@scope/pkg`, Node built-ins
   and `node:`/other `scheme:` specifiers excluded; PHP: Composer `require` packages from
   `composer.json` plus `use`d namespaces mapped to a package through
   `vendor/composer/autoload_psr4.php`). Each lists the importing files and whether and
   where it is declared (requirements*.txt, pyproject.toml, Pipfile, setup.py
   `install_requires`, package.json, composer.json) with the version spec. Python import
   names are matched to distribution names by normalisation plus a small alias table
   (`yaml` = PyYAML, `cv2` = opencv-python, ...). No links are drawn to this module.

   **Entry points** (`PlanetNode.is_entry`, at most 3; on the directory module that
   contains the file in directory maps), by evidence: a Python file with an
   `if __name__ == "__main__":` block or a package `__main__.py` (test files excluded;
   `main.py`/`__main__.py`/`app.py`/`manage.py` preferred; files imported by other
   project files are used only when no un-imported candidate exists); package.json
   `main` / `bin` / `scripts.start` targets that exist; `index.php` at the root or in
   `public/`; `index.html` at the root or next to a package.json. The evidence is
   written into the module summary and the core plan.

What is resolved (links come only from these; nothing is guessed):

   * Python (`ast`): absolute imports against the project's own packages / script
     directories and relative imports against the file's package; every statement of
     the tree is visited, so imports inside functions or `if` blocks count.
   * JS/TS (`.js .jsx .ts .tsx .mjs .cjs .mts .cts`, plus the `<script>` blocks of
     `.vue`/`.svelte`): specifiers of `import`, `export ... from`, `import()` and
     `require()` (comments are blanked first), relative or root-absolute, with
     extension lookup across those extensions, `.js`→`.ts` style substitution and
     `index.*`; `compilerOptions.paths` + `baseUrl` of `tsconfig.json`/`jsconfig.json`
     files (applied to files under the config's directory; `extends` is not followed);
     and the Vite alias `@/` → the nearest ancestor `src/` directory that exists. An alias
     only produces a link when the target file exists.
   * PHP: `include`, `include_once`, `require`, `require_once` whose path is built only
     from string literals, `__DIR__`, `dirname(__FILE__)`, `dirname(__DIR__[, n])` and
     `$_SERVER['DOCUMENT_ROOT']` (= the project root). A plain relative path is tried
     relative to the including file first, then relative to the project root (PHP's
     include_path behaviour with "." usually being the site root); anything containing a
     variable, constant or function call is counted as unresolved. `use Foo\\Bar;`
     statements resolve to the single project file that declares a class / interface /
     trait / enum with that fully-qualified name, or failing that that short name;
     ambiguous names are skipped. Only code inside `<?php ... ?>` / `<?= ... ?>` blocks is
     scanned (comments and string contents are blanked before matching definitions).
   * HTML (and the HTML parts of PHP files): relative or root-absolute
     `<script src="...">` and `<link href="...">` references to project files (a
     root-absolute `/x` is tried against the project root, then the HTML file's own
     folder, e.g. a Vite root); external URLs and template expressions are ignored.
   * Path lookups for JS/TS, PHP and HTML fall back to a case-insensitive match (Windows
     file systems are case-insensitive) when the exact path does not exist and the
     lower-cased path is unique. Python imports are matched exactly.
   * Other languages (Go, Rust, C#, Java, Kotlin, Ruby, C/C++, SQL, CSS) get
     pattern-matched definitions only and never produce links.

Call relations (`SatelliteNode.calls` / `called_by`, references
`"<module title>::<sub-component title>"`, deduplicated, sorted, at most
`CALL_REF_CAP` in the list; the text says "+N more"):

   * Python (`ast`), for every top-level function, every method, every class body and
     the module body: `name()` resolving to a function/class of the same module or one
     imported with `from m import name [as alias]` (re-exports in a package
     `__init__.py` are followed up to 3 levels); `mod.name()` / `pkg.mod.name()` /
     `alias.name()` through `import` statements of project modules;
     `self.method()` / `cls.method()` inside a method of the same class (methods of
     project base classes are searched too); `ClassName(...)` constructor calls and
     `ClassName.method()`; `obj.method()` only when `obj` is a local variable that is
     assigned exclusively from one project class constructor in the same function (or
     in the module body for module code). Imports anywhere in a file count for the
     whole file. Parameters, loop/with/except targets and other assignments make a name
     local and unresolvable. Calls through `super()`, attributes, return values,
     decorators and default values are not analysed. Calls to builtins, the standard
     library or third-party packages are never recorded, and no call is ever matched
     by bare method name across the project.
   * JS/TS (pattern-based, best-effort): calls `a(...)` / `new A(...)` of names imported
     by name or as default from a resolved project file (`import {a, b as c} from`,
     `import d from`, `const {a} = require()`), and `ns.f(...)` for
     `import * as ns` / `const ns = require()`, when the target file defines a
     top-level function or class of that name (the default export for default imports).
     The caller is the pattern-found top-level function / class whose brace-delimited
     body contains the call (comments and string contents are blanked before braces are
     counted; template-literal expressions are not scanned). Calls to functions of the
     same file, method calls and JSX usage are not resolved.
   * PHP (pattern-based, best-effort): calls `f(...)` to a function that is declared in
     exactly one project file which the calling file includes directly (function names
     compared case-insensitively). Same-file calls, methods, static calls and `new` are
     not resolved.
   * Directory maps (and the "Other files" module) have file sub-components: their
     `calls`/`called_by` reference the file sub-components of the files they call into,
     and the text names the called functions.
   * Guards: call analysis is skipped for files larger than
     `CALL_ANALYSIS_MAX_FILE_BYTES` and entirely for projects with more than
     `CALL_ANALYSIS_MAX_CODE_FILES` code files; the core plan says so when it happens.

Python classes record the signature (`class Foo(Base1, Base2)`), bases resolved to
project classes where possible, decorators, the docstring's first line, dataclass /
NamedTuple / TypedDict fields, Enum members, Protocol / ABC notes, and every method's
signature with `@staticmethod` / `@classmethod` / `@property` markers and its visibility
(public, private `_x`, dunder). Functions record their signature (annotations, defaults
abbreviated, `async`), decorators, docstring first line (or the comment above) and line.
JS/TS and PHP classes record `extends` / `implements`, method names (PHP with
visibility) and whether a JS definition is exported; pattern matching can miss or
misidentify definitions and all generated text says how a fact was derived. Signatures,
decorators and class details are only built when a file-level map is expected (directory
maps show files, not definitions), which keeps large scans fast. Files that fail to
parse (syntax errors, odd encodings decoded with replacement characters) still appear,
with a note.

Backup folders: while walking, directories named `backup`, `backups`, `*-backup`,
`*_backup` or `*.bak` (case-insensitive) are skipped like dependency folders (they only
duplicate the project's files); the core plan says "Skipped N backup folders (...)".

Purpose (`SolarSystem.purpose`, `purpose_source`, `purpose_evidence`): every scan writes
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

Identity line (`SolarSystem.identity`): every scan writes one short fact line, "what the
project is" (`identity_line`): kind + languages, 2–3 external libraries, the first entry
point, joined with " · " and capped at `IDENTITY_MAX_CHARS`. The kind word comes only from
the explicit signals of `IDENTITY_KINDS` (ambiguous / none -> no kind word); nothing comes
from the LLM. A re-scan replaces it (`merge_identity`); `stored_identity` derives the same
line for display from stored facts of systems scanned before the field existed.

Ignored folders: every scan entry point skips a folder that equals or is inside a path
of the store setting `ignored_folders` (`is_ignored_folder`), counted in
`LAST_SCAN_STATS["codebase"]["skipped_ignored"]` (+ `_paths`).

Project identity: one project per folder, whatever tool saw it. `analyze_directory()`
looks for an existing non-manual system whose normalised `workspace_path` is the same
folder (case-insensitive for Windows paths) under ANY source, consolidates stale
duplicates left by older versions (`consolidate_folder_systems()`), and refreshes that
system in place: it keeps its `source`, adds `codebase` to `sources`, keeps user-modified
modules / sub-components / links, positions and colours of user-modified nodes, the
`ai_summary` of same-title modules, the Ollama block of the core plan and the
"Work History" module written by `connectors.agent_importer`.
"""

from __future__ import annotations

import ast
import bisect
import copy
import json
import logging
import os
import posixpath
import re
import sys
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

try:  # Python 3.11+
    import tomllib as _tomllib  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - Python 3.10 fallback
    _tomllib = None

from core.models import LAYER_DEFINITIONS, SOURCE_CATEGORIES, GalaxyStore, PlanetNode, SolarSystem
from connectors.purpose_evidence import (  # noqa: F401  (re-exported for callers of this module)
    PURPOSE_STRONG_SCORE,
    SESSION_EVIDENCE_LABEL,
    EvidenceStrength,
    assess_purpose_evidence,
    clean_header_text,
    is_noise_comment_line,
    purpose_evidence_is_strong,
    session_line_of,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MAX_PLANETS = 12  # modules shown on the canvas (smallest folded into "Other")
SATELLITE_CAP = 30  # files / definitions shown as sub-components per module
FILE_LEVEL_MAX_FILES = 14  # projects with at most this many code files get a file-level map
DATA_FILE_MAX_BYTES = 1_000_000  # larger config/data files are not listed in "Docs & Data"
DATA_FILE_LIST_CAP = 200  # config/data files collected per project
DOCS_PLANET_TITLE = "Docs & Data"
OTHER_FILES_PLANET_TITLE = "Other files"
HISTORY_PLANET_TITLE = "Work History"  # written by connectors.agent_importer
#: Non-code files listed (not parsed) in the "Docs & Data" module of file-level maps.
DATA_EXTENSIONS: Set[str] = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".txt", ".csv", ".xml", ".env.example"}
DATA_FILE_NAMES: Set[str] = {"dockerfile", "makefile", "procfile"}
_LOCK_FILE_NAMES: Set[str] = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "pipfile.lock",
    "composer.lock", "cargo.lock", "npm-shrinkwrap.json", "bun.lockb",
}
#: Sources whose systems are produced by `connectors.agent_importer`.
AGENT_SOURCES: Tuple[str, ...] = ("antigravity", "codex", "claude_code")
#: Used only when `sys.stdlib_module_names` is missing (Python < 3.10).
_STDLIB_FALLBACK: Set[str] = {
    "abc", "argparse", "array", "ast", "asyncio", "base64", "binascii", "bisect", "builtins", "calendar", "cmath",
    "codecs", "collections", "concurrent", "configparser", "contextlib", "copy", "csv", "ctypes", "dataclasses",
    "datetime", "decimal", "difflib", "email", "enum", "errno", "fnmatch", "fractions", "functools", "gc", "getpass",
    "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "locale", "logging", "math", "mimetypes", "msvcrt", "multiprocessing", "numbers", "operator", "os",
    "pathlib", "pickle", "platform", "pprint", "queue", "random", "re", "secrets", "select", "shlex", "shutil",
    "signal", "socket", "sqlite3", "ssl", "statistics", "string", "struct", "subprocess", "sys", "tempfile",
    "textwrap", "threading", "time", "timeit", "tkinter", "traceback", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "venv", "warnings", "weakref", "webbrowser", "winreg", "winsound", "xml", "zipfile", "zlib",
    "zoneinfo", "_thread",
}
_STDLIB_MODULES: Set[str] = (set(getattr(sys, "stdlib_module_names", ())) or _STDLIB_FALLBACK) | {"__future__"}
SUMMARY_FILE_LIST_CAP = 400  # file names listed in a module summary
MAX_PARSE_BYTES = 1_000_000  # larger files are counted but not parsed
MAX_PARSED_FILES = 20_000  # files beyond this are counted but not parsed
DOMINANT_DIR_SHARE = 0.70  # descend into a directory holding >= this share of files
MAX_DESCEND_DEPTH = 5
FLAT_ROOT_SHARE = 0.75  # root holding more than this share => flat project
OTHER_PLANET_TITLE = "Other"
ROOT_PLANET_TITLE = "Project Root"
OLLAMA_MARKER = "[OLLAMA VERIFIED ARCHITECTURE ANALYSIS"
EXTERNAL_PLANET_TITLE = "External Libraries"
MODULE_CODE_TITLE = "(module code)"
CALL_REF_CAP = 40  # call references stored per sub-component (the text says "+N more")
METHOD_SATELLITE_MAX_PUBLIC = 10  # classes with at most this many public methods may show them as sub-components
METHOD_SATELLITES_MAX_TOTAL = 12  # ... while the file module has at most this many sub-components
CALL_ANALYSIS_MAX_FILE_BYTES = 300_000  # larger files get no call analysis
CALL_ANALYSIS_MAX_CODE_FILES = 4_000  # projects with more code files get no call analysis
_CONFIG_NAMES: Set[str] = {"tsconfig.json", "jsconfig.json", "package.json", "composer.json"}

#: Failures collected by the last `scan_parent_projects_directory` call: (folder, error).
LAST_SCAN_FAILURES: List[Tuple[str, str]] = []

IGNORED_DIRS: Set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "bower_components",
    "jspm_packages",
    ".venv",
    "venv",
    "env",
    "virtualenv",
    "site-packages",
    "dist-packages",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".parcel-cache",
    ".cache",
    ".idea",
    ".vscode",
    ".vs",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".eggs",
    "htmlcov",
    "coverage",
    ".coverage",
    ".nyc_output",
    "vendor",
    ".gradle",
    ".terraform",
    "__MACOSX",
    "Pods",
    "DerivedData",
    ".dart_tool",
    ".expo",
}
_IGNORED_DIRS_LOWER = {d.lower() for d in IGNORED_DIRS}
IGNORED_DIR_SUFFIXES: Tuple[str, ...] = (".egg-info", ".dist-info")
#: `bin`/`obj` are pruned only when they look like .NET build output.
_DOTNET_PROJECT_SUFFIXES: Tuple[str, ...] = (".csproj", ".fsproj", ".vbproj", ".sln")

CODE_EXTENSIONS: Dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".js": "JavaScript",
    ".jsx": "JavaScript (React)",
    ".mjs": "JavaScript (ESM)",
    ".cjs": "JavaScript (CommonJS)",
    ".mts": "TypeScript (ESM)",
    ".cts": "TypeScript (CommonJS)",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".java": "Java",
    ".kt": "Kotlin",
    ".php": "PHP",
    ".rb": "Ruby",
    ".cpp": "C++",
    ".hpp": "C++",
    ".c": "C",
    ".h": "C/C++ Header",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".md": "Markdown / Wiki",
}

_JS_EXTS: Tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte")
_JS_EXT_SET: Set[str] = set(_JS_EXTS)
#: `./x.js` may point at `x.ts` (TypeScript ESM convention), `.mjs` at `.mts`, ...
_JS_TS_SUBST: Dict[str, Tuple[str, ...]] = {
    ".js": (".ts", ".tsx", ".jsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",),
}
_NODE_BUILTINS: Set[str] = {
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto", "dgram",
    "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
}
_NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*$", re.IGNORECASE)
_LAYER_PRIORITY: Tuple[str, ...] = ("testing", "ui", "integration", "core", "architecture", "research", "docs")

# ---------------------------------------------------------------------------
# Layer classification
# ---------------------------------------------------------------------------
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _split_tokens(text: str) -> List[str]:
    """Splits a name into lowercase tokens on `_`, `-`, `.`, whitespace, other
    non-word characters and camelCase boundaries (`MainWindow` -> main, window)."""
    tokens: List[str] = []
    for chunk in re.split(r"[\W_]+", text):
        if not chunk:
            continue
        for part in _CAMEL_RE.split(chunk):
            if part:
                tokens.append(part.lower())
    return tokens


_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "__mocks__", "test_data", "testdata"}
_TEST_STEM_FIRST = {"test", "tests"}
_TEST_STEM_LAST = {"test", "tests", "spec", "specs"}

_LAYER_DIR_WORDS: Dict[str, Set[str]] = {
    "ui": {
        "ui", "gui", "view", "views", "component", "components", "page", "pages", "frontend",
        "widget", "widgets", "template", "templates", "style", "styles", "screen", "screens",
        "layout", "layouts", "public", "static", "css", "theme", "themes", "client", "web",
    },
    "integration": {
        "connector", "connectors", "api", "apis", "route", "routes", "router", "routers",
        "controller", "controllers", "clients", "bridge", "bridges", "adapter", "adapters",
        "plugin", "plugins", "skill", "skills", "agent", "agents", "integration", "integrations",
        "handler", "handlers", "endpoint", "endpoints", "webhook", "webhooks", "bot", "bots",
        "gateway", "sdk", "cli",
    },
    "core": {
        "core", "model", "models", "domain", "entity", "entities", "db", "database", "store",
        "stores", "service", "services", "engine", "lib", "data", "schema", "schemas",
        "repository", "repositories", "state", "logic", "kernel", "common", "shared", "utils",
        "util", "helpers",
    },
    "architecture": {
        "script", "scripts", "tool", "tools", "config", "configs", "conf", "settings", "deploy",
        "deployment", "ci", "infra", "docker", "bootstrap", "setup",
    },
    "docs": {"doc", "docs", "documentation", "wiki", "guide", "guides", "manual"},
    "research": {"research", "notebook", "notebooks", "experiment", "experiments", "prototype", "prototypes", "poc", "sandbox", "playground"},
}

_LAYER_STEM_WORDS: Dict[str, Set[str]] = {
    "ui": {
        "window", "windows", "canvas", "view", "views", "widget", "widgets", "dialog", "dialogs",
        "ui", "gui", "style", "styles", "theme", "dashboard", "page", "screen", "component",
        "layout", "panel", "modal", "button", "navbar", "sidebar", "menu", "toolbar", "render",
        "renderer", "template", "frontend",
    },
    "integration": {
        "connector", "importer", "bridge", "client", "api", "webhook", "bot", "scraper", "fetch",
        "fetcher", "http", "rest", "graphql", "grpc", "socket", "websocket", "route", "routes",
        "router", "controller", "endpoint", "adapter", "sdk", "crawler", "mcp", "cli", "gateway",
        "integration", "plugin", "enhancer", "sync", "exporter", "upload", "download",
    },
    "core": {
        "model", "models", "schema", "schemas", "store", "db", "database", "engine", "service",
        "manager", "analyzer", "repository", "repo", "entity", "entities", "types", "state",
        "reducer", "dao", "orm", "migration", "migrations", "core", "domain", "logic",
    },
    "architecture": {
        "main", "app", "index", "config", "settings", "setup", "build", "manage", "wsgi", "asgi",
        "conf", "makefile", "gulpfile", "webpack", "vite", "rollup", "babel", "entry",
        "bootstrap", "server", "deploy", "noxfile", "tasks",
    },
    "docs": set(),
    "research": {"research", "experiment", "benchmark", "prototype", "poc"},
}
_ENTRY_STEMS = {"main", "app", "index", "__main__"}
_ARCH_MD_STEMS = {"readme", "claude", "agents", "architecture", "plan", "roadmap", "design", "gemini"}
_EXT_LAYER_BONUS: Dict[str, Tuple[str, int]] = {
    ".css": ("ui", 4),
    ".scss": ("ui", 4),
    ".html": ("ui", 4),
    ".htm": ("ui", 4),
    ".vue": ("ui", 4),
    ".svelte": ("ui", 4),
    ".tsx": ("ui", 1),
    ".jsx": ("ui", 1),
    ".sql": ("core", 2),
}


def _classify_file_layer(rel_path) -> str:
    """Determines the architectural layer of a file from its relative path.

    Matching is done on whole directory names / directory-name tokens and on whole
    tokens of the file stem (so `build.py` is not "ui" and `capital.ts` is not
    "integration"). Tests are checked first; `scripts/` is tooling, not testing.
    Remaining layers are scored (stem token 3, directory 2, extension bonus) and the
    highest score wins; ties follow `_LAYER_PRIORITY`. Default: `core`.
    """
    rel = PurePosixPath(str(rel_path).replace("\\", "/"))
    dirs = [d.lower() for d in rel.parts[:-1]]
    suffix = rel.suffix.lower()
    stem = rel.stem
    stem_tokens = _split_tokens(stem)

    if suffix == ".md":
        if any(d in _LAYER_DIR_WORDS["research"] for d in dirs):
            return "research"
        if stem.lower() in _ARCH_MD_STEMS and not dirs:
            return "architecture"
        return "docs"

    if any(d in _TEST_DIRS for d in dirs) or stem.lower() == "conftest":
        return "testing"
    if stem_tokens and (stem_tokens[0] in _TEST_STEM_FIRST or stem_tokens[-1] in _TEST_STEM_LAST):
        return "testing"

    scores: Counter = Counter()
    for d in dirs:
        d_tokens = set(_split_tokens(d)) | {d}
        for layer, words in _LAYER_DIR_WORDS.items():
            if d_tokens & words:
                scores[layer] += 2
    for tok in stem_tokens:
        for layer, words in _LAYER_STEM_WORDS.items():
            if tok in words:
                weight = 3
                if layer == "architecture" and tok in _ENTRY_STEMS and len(dirs) > 1:
                    weight = 1  # `components/button/index.ts` is not an entry point
                scores[layer] += weight
    if suffix in _EXT_LAYER_BONUS:
        layer, bonus = _EXT_LAYER_BONUS[suffix]
        scores[layer] += bonus

    if not scores:
        return "core"
    best = max(scores.values())
    for layer in _LAYER_PRIORITY:
        if scores.get(layer) == best:
            return layer
    return "core"


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------
Chain = Tuple[str, ...]


@dataclass
class _Method:
    name: str
    lineno: int
    signature: str = ""
    flags: List[str] = field(default_factory=list)  # "staticmethod", "classmethod", "property", "async", "static", ...
    vis: str = ""  # "public", "private", "protected", "dunder"
    doc: str = ""


@dataclass
class _Def:
    kind: str  # "function", "class" (Python) or "type" (pattern matched class-like)
    name: str
    lineno: int
    keyword: str = ""  # e.g. "class", "struct", "interface", "def", "fn"
    signature: str = ""
    doc: str = ""
    doc_source: str = ""  # "docstring" or "comment above"
    methods: List[str] = field(default_factory=list)  # public method names
    method_infos: List[_Method] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    bases: List[str] = field(default_factory=list)  # base classes / `extends` as written
    implements: List[str] = field(default_factory=list)
    class_notes: List[str] = field(default_factory=list)  # "dataclass", "Enum", ...
    fields: List[str] = field(default_factory=list)  # dataclass fields / enum members
    exported: Optional[bool] = None  # JS/TS only
    is_default: bool = False  # JS/TS `export default`
    is_async: bool = False
    body: Tuple[int, int] = (0, 0)  # character range of the body (brace languages)

    @property
    def title(self) -> str:
        if self.kind == "function":
            return f"{self.name}()"
        return f"{self.keyword or 'class'} {self.name}"

    @property
    def key(self) -> str:
        return ("f:" if self.kind == "function" else "c:") + self.name


@dataclass
class _FileInfo:
    rel: str  # POSIX-style path relative to the project root
    path: Path
    ext: str
    lang: str
    size: int
    lines: int = 0
    method: str = ""  # "ast", "regex", "markdown", "" (not parsed)
    note: str = ""  # why it was not parsed / parse problems
    doc: str = ""
    classes: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    def_count: int = 0
    headings: List[str] = field(default_factory=list)
    wikilinks: List[str] = field(default_factory=list)
    py_imports: List[Tuple[int, str, List[str]]] = field(default_factory=list)
    js_specs: List[str] = field(default_factory=list)
    external_imports: Set[str] = field(default_factory=set)
    builtin_imports: Set[str] = field(default_factory=set)  # Node built-ins (`fs`, `node:path`)
    deps: Set[str] = field(default_factory=set)
    importers: Set[str] = field(default_factory=set)
    layer: str = "core"
    lead_comment: str = ""  # first comment block of the file (Python without docstring)
    defs: List[_Def] = field(default_factory=list)  # top-level definitions, source order
    top_names: List[Tuple[str, int]] = field(default_factory=list)  # top-level assigned names
    #: resolved target rel -> (module text as written, imported names)
    dep_labels: Dict[str, Tuple[str, List[str]]] = field(default_factory=dict)
    dep_verbs: Dict[str, str] = field(default_factory=dict)  # target rel -> "imports" / "includes" / ...
    # --- entry point evidence ------------------------------------------------
    has_main_block: bool = False
    # --- Python call analysis (raw, resolved later) --------------------------
    py_binds: Dict[str, tuple] = field(default_factory=dict)  # local name -> import binding
    py_top: Dict[str, str] = field(default_factory=dict)  # top-level name -> "function" / "class"
    py_scopes: Dict[str, Set[Chain]] = field(default_factory=dict)  # scope key -> called chains
    py_locals: Dict[str, Dict[str, Optional[Chain]]] = field(default_factory=dict)
    py_self: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # method scope -> (self name, class)
    py_class_bases: Dict[str, List[Chain]] = field(default_factory=dict)
    py_class_methods: Dict[str, Set[str]] = field(default_factory=dict)
    # --- JS/TS / PHP call analysis -------------------------------------------
    js_binds: List[Tuple[str, str, str]] = field(default_factory=list)  # (local, imported | "default" | "*", spec)
    name_calls: Dict[str, Set[str]] = field(default_factory=dict)  # scope -> called identifiers
    member_calls: Dict[str, Set[Tuple[str, str]]] = field(default_factory=dict)  # scope -> (namespace, member)
    spec_hits: Dict[str, str] = field(default_factory=dict)  # JS specifier -> resolved rel
    php_includes: List[Tuple[str, str, str]] = field(default_factory=list)  # (base, path, as written)
    php_uses: List[str] = field(default_factory=list)
    php_namespace: str = ""
    unresolved_includes: int = 0
    html_refs: List[str] = field(default_factory=list)
    calls_note: str = ""  # why call analysis was skipped for this file
    # --- purpose evidence (only for shallow "candidate" files, see _is_purpose_candidate) ---
    purpose_candidate: bool = False
    #: (kind, name, text): kind is "string", "cli", "window title", "page title",
    #: "meta description", "heading" or "header comment"
    purpose_bits: List[Tuple[str, str, str]] = field(default_factory=list)
    uses_php_db: str = ""  # "PDO" / "mysqli" when the PHP code opens a database connection
    readme_text: str = ""  # raw text of a root-level README.md (kept for the intro parser)

    @property
    def name(self) -> str:
        return posixpath.basename(self.rel)

    @property
    def dir_parts(self) -> Tuple[str, ...]:
        return tuple(self.rel.split("/")[:-1])

    @property
    def is_code(self) -> bool:
        return self.ext != ".md"

    def add_dep(self, target: str, module_text: str, names: Sequence[str] = (), verb: str = "imports") -> None:
        if target == self.rel:
            return
        self.deps.add(target)
        mod, existing = self.dep_labels.get(target, (module_text, []))
        for n in names:
            if n and n != "*" and n not in existing:
                existing.append(n)
        self.dep_labels[target] = (mod, existing)
        self.dep_verbs.setdefault(target, verb)

def _first_line(text: str, limit: int = 70) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:limit]


def _iter_statements(tree: ast.AST) -> Iterable[ast.stmt]:
    """Yields every statement in the tree, including those nested in functions,
    classes, if/try/with/loop/match bodies. Imports are statements, so this finds the
    same imports as `ast.walk` while skipping expression nodes (several times faster)."""
    stack: List[ast.stmt] = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        yield node
        for attr in ("body", "orelse", "finalbody"):
            sub = getattr(node, attr, None)
            if isinstance(sub, list):
                stack.extend(x for x in sub if isinstance(x, ast.stmt))
        for handler in getattr(node, "handlers", None) or ():
            stack.extend(handler.body)
        for case in getattr(node, "cases", None) or ():
            stack.extend(case.body)


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - malformed trees
        return "…"


def _clean_comment_line(line: str) -> str:
    s = line.strip()
    s = s.lstrip("#/*!-<").rstrip("*/->").strip()
    s = re.sub(r"^[=\-*~#_]{3,}$", "", s)
    return s.strip(" =-*~")


def _first_comment_block(lines: List[str], max_lines: int = 40) -> str:
    """Text of the first block of consecutive `#` comment lines in the first `max_lines`
    lines that is a description. Noise lines (shebang, encoding, license, directives,
    "generated" notes, metadata, separators: `purpose_evidence.NOISE_PATTERNS`) are
    removed; a block left with too few meaningful words is skipped and the next block
    within `max_lines` is tried."""
    block: List[str] = []

    def flush() -> str:
        text = clean_header_text("\n".join(block))
        block.clear()
        return text if len(re.findall(r"\w", text)) >= 10 else ""

    for raw in lines[:max_lines]:
        s = raw.strip()
        if s.startswith("#"):
            if not is_noise_comment_line(s):
                cleaned = _clean_comment_line(s)
                if cleaned:
                    block.append(cleaned)
            continue
        if block:
            text = flush()
            if text:
                return text[:300]
    return flush()[:300] if block else ""


def _comment_above(lines: List[str], node: ast.AST) -> str:
    """Comment lines directly above a definition (above its decorators, if any)."""
    first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    idx = first - 2  # 0-based index of the line above
    block: List[str] = []
    while idx >= 0 and lines[idx].strip().startswith("#"):
        cleaned = _clean_comment_line(lines[idx])
        if cleaned:
            block.insert(0, cleaned)
        idx -= 1
    return " ".join(block)[:200]


def _set_def_doc(d: "_Def", docstring: str, lines: List[str], node: ast.AST) -> None:
    if docstring.strip():
        d.doc, d.doc_source = _first_line(docstring, 200), "docstring"
        return
    above = _comment_above(lines, node)
    if above:
        d.doc, d.doc_source = above, "comment above"


def _abbrev(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fmt_arg(arg: ast.arg, default: Optional[ast.AST]) -> str:
    s = arg.arg
    if arg.annotation is not None:
        s += ": " + _abbrev(_safe_unparse(arg.annotation), 30)
    if default is not None:
        s += (" = " if arg.annotation is not None else "=") + _abbrev(_safe_unparse(default), 20)
    return s


def _fmt_args(a: ast.arguments) -> str:
    """Arguments as in source (annotations and defaults abbreviated)."""
    out: List[str] = []
    positional = list(a.posonlyargs) + list(a.args)
    defaults: List[Optional[ast.AST]] = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    for i, (arg, dflt) in enumerate(zip(positional, defaults)):
        out.append(_fmt_arg(arg, dflt))
        if a.posonlyargs and i == len(a.posonlyargs) - 1:
            out.append("/")
    if a.vararg is not None:
        out.append("*" + _fmt_arg(a.vararg, None))
    elif a.kwonlyargs:
        out.append("*")
    for arg, dflt in zip(a.kwonlyargs, a.kw_defaults):
        out.append(_fmt_arg(arg, dflt))
    if a.kwarg is not None:
        out.append("**" + _fmt_arg(a.kwarg, None))
    return ", ".join(out)


def _fmt_def(node, with_def: bool = True) -> str:
    is_async = isinstance(node, ast.AsyncFunctionDef)
    head = ("async def " if is_async else "def ") if with_def else ("async " if is_async else "")
    sig = f"{head}{node.name}({_fmt_args(node.args)})"
    if node.returns is not None:
        sig += " -> " + _abbrev(_safe_unparse(node.returns), 30)
    return sig


def _call_chain(func: ast.AST) -> Optional[Chain]:
    """`a.b.c` -> ("a", "b", "c") when the expression is a plain dotted name."""
    parts: List[str] = []
    node = func
    while type(node) is ast.Attribute:
        parts.append(node.attr)
        node = node.value
        if len(parts) > 6:
            return None
    if type(node) is ast.Name:
        parts.append(node.id)
        parts.reverse()
        return tuple(parts)
    return None


def _decorator_name(node: ast.AST) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    chain = _call_chain(target)
    return chain[-1] if chain else ""


def _note_local(locals_: Dict[str, Optional[Chain]], name: str, ctor: Optional[Chain]) -> None:
    if name in locals_:
        if locals_[name] != ctor:
            locals_[name] = None
    else:
        locals_[name] = ctor


def _bind_targets(target: ast.AST, locals_: Dict[str, Optional[Chain]]) -> None:
    stack = [target]
    while stack:
        t = stack.pop()
        tt = type(t)
        if tt is ast.Name:
            _note_local(locals_, t.id, None)
        elif tt is ast.Tuple or tt is ast.List:
            stack.extend(t.elts)
        elif tt is ast.Starred:
            stack.append(t.value)


def _bind_params(args: ast.arguments, locals_: Dict[str, Optional[Chain]]) -> None:
    for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        _note_local(locals_, a.arg, None)
    for a in (args.vararg, args.kwarg):
        if a is not None:
            _note_local(locals_, a.arg, None)


_PY_SKIP_TYPES = {ast.Name, ast.Constant, ast.Load, ast.Store, ast.Del, ast.alias, ast.arg, ast.Pass, ast.Break, ast.Continue}
for _base in (ast.operator, ast.cmpop, ast.unaryop, ast.boolop, ast.expr_context):
    _PY_SKIP_TYPES.update(_base.__subclasses__())
#: Fields that never hold child statements/expressions worth visiting.
_PY_SCALAR_FIELDS = {"ctx", "id", "attr", "name", "asname", "module", "level", "type_comment", "kind", "is_async",
                     "conversion", "arg", "op", "ops", "decorator_list", "returns", "type_params", "simple"}
_PY_CHILD_FIELDS: Dict[type, Tuple[str, ...]] = {}


def _child_fields(t: type) -> Tuple[str, ...]:
    fields = _PY_CHILD_FIELDS.get(t)
    if fields is None:
        fields = _PY_CHILD_FIELDS[t] = tuple(f for f in t._fields if f not in _PY_SCALAR_FIELDS)
    return fields
_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _collect_scope(stmts: Iterable[ast.AST], calls: Set[Chain], locals_: Dict[str, Optional[Chain]], self_name: str,
                   sink: Optional[List[ast.stmt]] = None) -> None:
    """Collects called dotted names and local-name bindings of one scope (nested
    functions / lambdas / comprehensions are part of the scope). Import statements and
    nested definitions are appended to `sink` (so the tree is walked only once)."""
    stack: List[ast.AST] = list(stmts)
    pop, push = stack.pop, stack.append
    skip = _PY_SKIP_TYPES
    child_fields = _PY_CHILD_FIELDS
    AST = ast.AST
    while stack:
        node = pop()
        t = type(node)
        if t is ast.Call:
            ch = _call_chain(node.func)
            if ch is not None:
                calls.add(ch)
        elif t is ast.Assign:
            v = node.value
            ctor = _call_chain(v.func) if type(v) is ast.Call else None
            for tgt in node.targets:
                if type(tgt) is ast.Name:
                    _note_local(locals_, tgt.id, ctor)
                else:
                    _bind_targets(tgt, locals_)
        elif t is ast.AnnAssign:
            v = node.value
            if type(node.target) is ast.Name:
                _note_local(locals_, node.target.id, _call_chain(v.func) if type(v) is ast.Call else None)
        elif t is ast.AugAssign or t is ast.For or t is ast.AsyncFor or t is ast.comprehension or t is ast.NamedExpr:
            _bind_targets(node.target, locals_)
        elif t is ast.withitem:
            if node.optional_vars is not None:
                _bind_targets(node.optional_vars, locals_)
        elif t is ast.ExceptHandler:
            if node.name:
                _note_local(locals_, node.name, None)
        elif t is ast.Import or t is ast.ImportFrom:
            if sink is not None:
                sink.append(node)
            continue
        elif t is ast.FunctionDef or t is ast.AsyncFunctionDef:
            if sink is not None:
                sink.append(node)
            _note_local(locals_, node.name, None)
            _bind_params(node.args, locals_)
            stack.extend(node.body)  # decorators / defaults are not analysed
            continue
        elif t is ast.Lambda:
            _bind_params(node.args, locals_)
        elif t is ast.ClassDef:
            if sink is not None:
                sink.append(node)
            _note_local(locals_, node.name, None)
            if self_name:
                inner: Set[Chain] = set()
                _collect_scope(node.body, inner, {}, "", sink)
                calls.update(c for c in inner if c[0] != self_name)
                continue
        fields = child_fields.get(t) or _child_fields(t)
        for f in fields:
            v = getattr(node, f, None)
            if v is None:
                continue
            if v.__class__ is list:
                for x in v:
                    if type(x) not in skip and isinstance(x, AST):
                        push(x)
            elif type(v) not in skip and isinstance(v, AST):
                push(v)


def _is_main_test(test: ast.AST) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left, test.comparators[0]]
    names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
    consts = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
    return bool(names and consts)


_ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}


def _py_class_def(node: ast.ClassDef, lines: List[str], detail: bool = True) -> _Def:
    if not detail:  # directory maps show files, not class details: keep it cheap
        d = _Def("class", node.name, node.lineno, keyword="class", signature=f"class {node.name}")
        d.methods = [n.name for n in node.body if isinstance(n, _FUNC_TYPES) and not n.name.startswith("_")]
        _set_def_doc(d, ast.get_docstring(node) or "", lines, node)
        return d
    bases = [_safe_unparse(b) for b in node.bases]
    kws = [f"{k.arg}={_safe_unparse(k.value)}" for k in node.keywords if k.arg]
    sig = f"class {node.name}({', '.join(bases + kws)})" if (bases or kws) else f"class {node.name}"
    d = _Def("class", node.name, node.lineno, keyword="class", signature=_clip(sig, 160), bases=bases)
    d.decorators = ["@" + _abbrev(_safe_unparse(x), 60) for x in node.decorator_list]
    deco_names = {_decorator_name(x) for x in node.decorator_list}
    base_last = {b.split("[")[0].split(".")[-1] for b in bases}
    if "dataclass" in deco_names:
        d.class_notes.append("dataclass")
    if "NamedTuple" in base_last:
        d.class_notes.append("NamedTuple")
    if "TypedDict" in base_last:
        d.class_notes.append("TypedDict")
    if base_last & _ENUM_BASES:
        d.class_notes.append("Enum")
    if "Protocol" in base_last:
        d.class_notes.append("Protocol")
    if "ABC" in base_last or any(k.arg == "metaclass" and _safe_unparse(k.value).endswith("ABCMeta") for k in node.keywords):
        d.class_notes.append("abstract base class")
    want_fields = bool({"dataclass", "NamedTuple", "TypedDict"} & set(d.class_notes))
    for n in node.body:
        if isinstance(n, _FUNC_TYPES):
            flags: List[str] = []
            for deco in n.decorator_list:
                dn = _decorator_name(deco)
                if dn in ("staticmethod", "classmethod", "property", "abstractmethod", "cached_property"):
                    flags.append(dn)
                elif dn in ("setter", "getter", "deleter"):
                    flags.append("property " + dn)
            if isinstance(n, ast.AsyncFunctionDef):
                flags.append("async")
            if n.name.startswith("__") and n.name.endswith("__"):
                vis = "dunder"
            elif n.name.startswith("_"):
                vis = "private"
            else:
                vis = "public"
            m = _Method(n.name, n.lineno, signature=_fmt_def(n, with_def=False), flags=flags, vis=vis)
            m.doc = _first_line(ast.get_docstring(n) or "", 120)
            d.method_infos.append(m)
        elif want_fields and isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            ann = _safe_unparse(n.annotation)
            if ann.startswith(("ClassVar", "typing.ClassVar")):
                continue
            entry = f"{n.target.id}: {_abbrev(ann, 30)}"
            if n.value is not None:
                entry += " = " + _abbrev(_safe_unparse(n.value), 20)
            d.fields.append(entry)
        elif "Enum" in d.class_notes and isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                    d.fields.append(tgt.id)
    d.methods = [m.name for m in d.method_infos if m.vis == "public"]
    _set_def_doc(d, ast.get_docstring(node) or "", lines, node)
    return d


def _analyze_python_source(text: str, info: _FileInfo, analyze_calls: bool = True, detail: bool = True) -> None:
    """Fills `info` from Python source using `ast` (regex fallback on syntax errors)."""
    if text.startswith("﻿"):
        text = text[1:]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # e.g. SyntaxWarning for invalid escapes
            tree = ast.parse(text)
    except Exception as exc:  # SyntaxError, ValueError (null bytes), RecursionError
        info.note = (
            f"Python AST parsing failed ({type(exc).__name__}); definitions found by pattern matching, "
            "no call analysis."
        )
        _analyze_with_patterns(text, info, _LANG_PATTERNS["python_fallback"])
        for m in re.finditer(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+([\w\s,]+)", text, flags=re.MULTILINE):
            names = [n.strip() for n in m.group(3).split(",") if n.strip()]
            info.py_imports.append((len(m.group(1)), m.group(2), names))
        for m in re.finditer(r"^\s*import\s+([\w.]+)", text, flags=re.MULTILINE):
            info.py_imports.append((0, m.group(1), []))
        if re.search(r"^if\s+__name__\s*==\s*['\"]__main__['\"]", text, flags=re.MULTILINE):
            info.has_main_block = True
        return

    info.method = "ast"
    info.doc = (ast.get_docstring(tree) or "").strip()
    lines = text.splitlines()
    if not info.doc or not _docstring_description(info.doc):
        info.lead_comment = _first_comment_block(lines)
    module_stmts: List[ast.stmt] = []
    seen_names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            d = _py_class_def(node, lines, detail)
            desc = f"class {node.name}"
            if d.methods:
                desc += f" ({', '.join(d.methods[:4])}{', …' if len(d.methods) > 4 else ''})"
            if d.doc and d.doc_source == "docstring":
                desc += f" — {_first_line(d.doc)}"
            info.classes.append(desc)
            info.defs.append(d)
            info.py_top[node.name] = "class"
        elif isinstance(node, _FUNC_TYPES):
            f_doc = ast.get_docstring(node) or ""
            if not node.name.startswith("_"):
                desc = f"def {node.name}()"
                if f_doc:
                    desc += f" — {_first_line(f_doc)}"
                info.functions.append(desc)
            d = _Def("function", node.name, node.lineno, keyword="async def" if isinstance(node, ast.AsyncFunctionDef) else "def")
            d.is_async = isinstance(node, ast.AsyncFunctionDef)
            if detail:
                d.signature = _clip(_fmt_def(node), 160)
                d.decorators = ["@" + _abbrev(_safe_unparse(x), 60) for x in node.decorator_list]
            else:
                d.signature = f"{d.keyword} {node.name}(…)"
            _set_def_doc(d, f_doc, lines, node)
            info.defs.append(d)
            info.py_top[node.name] = "function"
        else:
            module_stmts.append(node)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name) and n.id not in seen_names:
                            seen_names.add(n.id)
                            info.top_names.append((n.id, node.lineno))
            elif isinstance(node, ast.If) and _is_main_test(node.test):
                info.has_main_block = True

    if info.purpose_candidate:
        _python_purpose_bits(tree, text, info)

    if analyze_calls:
        sink: List[ast.stmt] = []
        _collect_python_calls(tree, module_stmts, info, sink)
        nodes: Iterable[ast.stmt] = sink
        info.def_count = len(tree.body) - len(module_stmts)
        info.def_count += sum(1 for n in tree.body if isinstance(n, ast.ClassDef) for x in n.body if isinstance(x, _FUNC_TYPES))
    else:
        nodes = _iter_statements(tree)
    imports = []
    for node in nodes:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            info.def_count += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
    imports.sort(key=lambda n: (n.lineno, n.col_offset))
    for node in imports:
        if isinstance(node, ast.Import):
            for alias in node.names:
                info.py_imports.append((0, alias.name, []))
                if alias.asname:
                    info.py_binds.setdefault(alias.asname, ("mod", alias.name, True))
                else:
                    head = alias.name.split(".")[0]
                    info.py_binds.setdefault(head, ("mod", head, False))
        elif isinstance(node, ast.ImportFrom):
            info.py_imports.append((node.level or 0, node.module or "", [a.name for a in node.names]))
            for a in node.names:
                if a.name != "*":
                    info.py_binds.setdefault(a.asname or a.name, ("from", node.level or 0, node.module or "", a.name))



def _collect_python_calls(tree: ast.Module, module_stmts: List[ast.stmt], info: _FileInfo, sink: List[ast.stmt]) -> None:
    """Call analysis: one scope per top-level function, method, class body and the module body."""
    def store(scope: str, calls: Set[Chain], locals_: Dict[str, Optional[Chain]]) -> None:
        if calls:
            info.py_scopes[scope] = calls
            ctor_locals = {k: v for k, v in locals_.items() if k in {c[0] for c in calls}}
            if ctor_locals:
                info.py_locals[scope] = ctor_locals

    calls: Set[Chain] = set()
    locals_: Dict[str, Optional[Chain]] = {}
    _collect_scope(module_stmts, calls, locals_, "", sink)
    store("mod", calls, locals_)
    for node in tree.body:
        if isinstance(node, _FUNC_TYPES):
            calls, locals_ = set(), {}
            _bind_params(node.args, locals_)
            _collect_scope(node.body, calls, locals_, "", sink)
            store("f:" + node.name, calls, locals_)
        elif isinstance(node, ast.ClassDef):
            cname = node.name
            info.py_class_bases[cname] = [c for c in (_call_chain(b) for b in node.bases) if c]
            info.py_class_methods[cname] = {n.name for n in node.body if isinstance(n, _FUNC_TYPES)}
            calls, locals_ = set(), {}
            _collect_scope([n for n in node.body if not isinstance(n, _FUNC_TYPES)], calls, locals_, "", sink)
            store("c:" + cname, calls, locals_)
            for n in node.body:
                if not isinstance(n, _FUNC_TYPES):
                    continue
                calls, locals_ = set(), {}
                deco = {_decorator_name(x) for x in n.decorator_list}
                params = list(n.args.posonlyargs) + list(n.args.args)
                self_name = params[0].arg if params and "staticmethod" not in deco else ""
                _bind_params(n.args, locals_)
                locals_.pop(self_name, None)
                _collect_scope(n.body, calls, locals_, self_name, sink)
                scope = f"m:{cname}.{n.name}"
                store(scope, calls, locals_)
                if self_name and scope in info.py_scopes:
                    info.py_self[scope] = (self_name, cname)


# Per-language (type patterns, function patterns). Each pattern's group 1 is the name;
# optional named group `recv` is a Go receiver.
_LANG_PATTERNS: Dict[str, Tuple[List[str], List[str]]] = {
    "rust": (
        [r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|union|type)\s+([A-Za-z_]\w*)"],
        [r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?fn\s+([A-Za-z_]\w*)"],
    ),
    "go": (
        [r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b", r"^\s*type\s+([A-Za-z_]\w*)\s+(?!struct|interface)[\w\[\]*.]+"],
        [r"^\s*func\s+(?:\((?P<recv>[^)]*)\)\s*)?([A-Za-z_]\w*)\s*[\[(]"],
    ),
    "csharp": (
        [r"\b(?:class|interface|struct|enum|record)\s+([A-Za-z_]\w*)"],
        [
            r"^\s*(?:\[[^\]]*\]\s*)*(?:(?:public|private|protected|internal|static|virtual|override|async|abstract|sealed|extern|unsafe|new|partial|readonly)\s+)+"
            r"[\w<>\[\],.? \t]*?[ \t]([A-Za-z_]\w*)[ \t]*(?:<[^>\n]*>)?[ \t]*\("
        ],
    ),
    "java": (
        [r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)"],
        [
            r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)+"
            r"(?:<[^>\n]*>[ \t]*)?[\w<>\[\],.?]+[ \t]+([A-Za-z_]\w*)[ \t]*\("
        ],
    ),
    "kotlin": (
        [r"\b(?:class|interface|object)\s+([A-Za-z_]\w*)"],
        [r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\("],
    ),
    "js": (
        [
            r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
            r"^\s*(?:export\s+)?(?:declare\s+)?interface\s+([A-Za-z_$][\w$]*)",
            r"^\s*(?:export\s+)?(?:declare\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)",
            r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*(?:<[^=]*>)?\s*=",
        ],
        [
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)",
            r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)",
        ],
    ),
    "php": (
        [r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+([A-Za-z_]\w*)"],
        [r"\bfunction\s+&?([A-Za-z_]\w*)\s*\("],
    ),
    "ruby": (
        [r"^\s*(?:class|module)\s+([A-Z]\w*)"],
        [r"^\s*def\s+(?:self\.)?([\w?!]+)"],
    ),
    "c": (
        [r"^\s*(?:typedef\s+)?(?:class|struct|enum|union)\s+([A-Za-z_]\w*)\s*(?:final\s*)?[{:]"],
        [r"^[A-Za-z_][\w \t\*&:<>,]*?\b([A-Za-z_]\w*)[ \t]*\([^;{}()]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{"],
    ),
    "sql": (
        [r"\bcreate\s+(?:or\s+replace\s+)?(?:table|view)\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([\w.]+)"],
        [r"\bcreate\s+(?:or\s+replace\s+)?(?:function|procedure|trigger)\s+[`\"\[]?([\w.]+)"],
    ),
    "python_fallback": (
        [r"^\s*class\s+([A-Za-z_]\w*)"],
        [r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"],
    ),
}
_EXT_TO_PATTERN_KEY: Dict[str, str] = {
    ".rs": "rust",
    ".go": "go",
    ".cs": "csharp",
    ".java": "java",
    ".kt": "kotlin",
    ".ts": "js",
    ".tsx": "js",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".mts": "js",
    ".cts": "js",
    ".vue": "js",
    ".svelte": "js",
    ".php": "php",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "c",
    ".hpp": "c",
    ".sql": "sql",
}
_C_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "catch", "else", "do"}


def _analyze_with_patterns(text: str, info: _FileInfo, patterns: Tuple[List[str], List[str]]) -> None:
    type_pats, fn_pats = patterns
    flags = re.MULTILINE | (re.IGNORECASE if info.ext == ".sql" else 0)

    def line_of(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    def line_text(pos: int) -> str:
        start = text.rfind("\n", 0, pos) + 1
        end = text.find("\n", pos)
        return _clip(text[start : end if end != -1 else len(text)], 160)

    seen: Set[str] = set()
    for pat in type_pats:
        for m in re.finditer(pat, text, flags=flags):
            name = m.group(1)
            if name and name not in seen:
                seen.add(name)
                info.classes.append(f"type {name}")
                kw = re.search(r"\b(class|interface|struct|enum|record|trait|union|type|object|module|table|view)\b", m.group(0), flags=re.IGNORECASE)
                info.defs.append(
                    _Def("type", name, line_of(m.start(1)), keyword=(kw.group(1).lower() if kw else "type"), signature=line_text(m.start(1)))
                )
    seen_fn: Set[str] = set()
    for pat in fn_pats:
        for m in re.finditer(pat, text, flags=flags):
            name = m.group(m.lastindex) if "recv" not in m.groupdict() else m.group(2)
            if not name or name in _C_KEYWORDS:
                continue
            recv = (m.groupdict().get("recv") or "").strip()
            label = name
            if recv:
                recv_type = recv.split()[-1] if recv.split() else recv
                label = f"({recv_type}) {name}"
            if label in seen_fn:
                continue
            seen_fn.add(label)
            info.functions.append(f"func {label}()" if info.ext == ".go" else f"fn {label}()")
            pos = m.start(m.lastindex) if "recv" not in m.groupdict() else m.start(2)
            info.defs.append(_Def("function", label, line_of(pos), signature=line_text(pos)))
    info.defs.sort(key=lambda d: d.lineno)
    info.def_count = len(info.classes) + len(info.functions)

_JS_IMPORT_PATTERNS = [
    re.compile(r"""\bimport\s+(?:type\s+)?(?:[\w$*{}\s,]+?\s+from\s+)?['"]([^'"\n]+)['"]"""),
    re.compile(r"""\bexport\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s+from\s+['"]([^'"\n]+)['"]"""),
    re.compile(r"""\bimport\(\s*['"]([^'"\n]+)['"]\s*\)"""),
    re.compile(r"""\brequire\(\s*['"]([^'"\n]+)['"]\s*\)"""),
]

# ---------------------------------------------------------------------------
# Comment / string masking (positions and line numbers are preserved)
# ---------------------------------------------------------------------------
_JS_TOKEN_RE = re.compile(
    r"//[^\n]*|/\*(?:[^*]|\*(?!/))*(?:\*/|\Z)|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`"
)
_PHP_TOKEN_RE = re.compile(
    r"#[^\n]*|//[^\n]*|/\*(?:[^*]|\*(?!/))*(?:\*/|\Z)|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\""
)
_NON_NL_RE = re.compile(r"[^\n]")


def _blank(s: str) -> str:
    return " " * len(s) if "\n" not in s else _NON_NL_RE.sub(" ", s)


def _mask_both(text: str, token_re: "re.Pattern[str]") -> Tuple[str, str]:
    """(text with comments blanked, text with comments AND string contents blanked).
    Both have the same length and line structure as `text`."""
    cm: List[str] = []
    fm: List[str] = []
    last = 0
    for m in token_re.finditer(text):
        s, e = m.span()
        if s > last:
            seg = text[last:s]
            cm.append(seg)
            fm.append(seg)
        tok = m.group(0)
        if tok[0] in "'\"`":
            cm.append(tok)
            fm.append(tok[0] + _blank(tok[1:-1]) + tok[-1] if len(tok) >= 2 else tok)
        else:
            b = _blank(tok)
            cm.append(b)
            fm.append(b)
        last = e
    if last < len(text):
        cm.append(text[last:])
        fm.append(text[last:])
    return "".join(cm), "".join(fm)


_SCRIPT_BLOCK_RE = re.compile(r"(<script\b[^>]*>)(.*?)(</script\s*>)", re.IGNORECASE | re.DOTALL)


def _script_blocks(text: str) -> str:
    """Same-length text keeping only the contents of `<script>` blocks (Vue/Svelte)."""
    out: List[str] = []
    last = 0
    for m in _SCRIPT_BLOCK_RE.finditer(text):
        out.append(_blank(text[last : m.start(2)]))
        out.append(m.group(2))
        last = m.end(2)
    out.append(_blank(text[last:]))
    return "".join(out)


_PHP_OPEN_RE = re.compile(r"<\?(?:php\b|=|(?=\s))", re.IGNORECASE)


def _php_code_regions(text: str) -> str:
    """Same-length text keeping only `<?php ... ?>` / `<?= ... ?>` code (HTML blanked)."""
    out: List[str] = []
    pos, n = 0, len(text)
    while pos < n:
        m = _PHP_OPEN_RE.search(text, pos)
        if not m:
            out.append(_blank(text[pos:]))
            break
        out.append(_blank(text[pos : m.end()]))
        close = text.find("?>", m.end())
        if close == -1:
            out.append(text[m.end() :])
            break
        out.append(text[m.end() : close])
        out.append("  ")
        pos = close + 2
    return "".join(out)


_BRACE_RE = re.compile(r"[{}]")
_PAREN_RE = re.compile(r"[()]")
_BODY_START_RE = re.compile(r"[{;]")


def _brace_pairs(masked: str) -> Dict[int, int]:
    pairs: Dict[int, int] = {}
    stack: List[int] = []
    for m in _BRACE_RE.finditer(masked):
        if m.group() == "{":
            stack.append(m.start())
        elif stack:
            pairs[stack.pop()] = m.start()
    for o in stack:
        pairs[o] = len(masked)
    return pairs


def _match_paren(masked: str, open_pos: int) -> int:
    depth = 0
    for m in _PAREN_RE.finditer(masked, open_pos):
        depth += 1 if m.group() == "(" else -1
        if depth == 0:
            return m.start()
    return -1


def _body_after_params(masked: str, paren_pos: int, pairs: Dict[int, int]) -> Tuple[int, int]:
    close = _match_paren(masked, paren_pos)
    if close < 0:
        return (0, 0)
    m = _BODY_START_RE.search(masked, close, close + 300)
    if not m or m.group() != "{":
        return (0, 0)
    return (m.start(), pairs.get(m.start(), m.start()))


def _body_from(masked: str, pos: int, pairs: Dict[int, int]) -> Tuple[int, int]:
    m = _BODY_START_RE.search(masked, pos, pos + 400)
    if not m or m.group() != "{":
        return (0, 0)
    return (m.start(), pairs.get(m.start(), m.start()))


class _Lines:
    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)

    def line_text(self, pos: int, limit: int = 160) -> str:
        i = bisect.bisect_right(self.starts, pos) - 1
        start = self.starts[i]
        end = self.text.find("\n", pos)
        return _clip(self.text[start : end if end != -1 else len(self.text)], limit)


def _nest_defs(defs: List[_Def], positions: Dict[int, int]) -> Dict[int, Optional[_Def]]:
    """Parent definition (by body containment) of each definition, keyed by id()."""
    parent: Dict[int, Optional[_Def]] = {}
    stack: List[_Def] = []
    for d in sorted(defs, key=lambda x: positions[id(x)]):
        pos = positions[id(d)]
        while stack and stack[-1].body[1] <= pos:
            stack.pop()
        parent[id(d)] = stack[-1] if stack else None
        if d.body[1] > d.body[0]:
            stack.append(d)
    return parent


def _direct_member_checker(fm: str, body: Tuple[int, int], opens_sorted: List[int], pairs: Dict[int, int]):
    """Returns a predicate: is `pos` directly inside the block `body` (not nested)?"""
    o, c = body
    i = bisect.bisect_right(opens_sorted, o)
    nested: List[Tuple[int, int]] = []
    while i < len(opens_sorted) and opens_sorted[i] < c:
        b_open = opens_sorted[i]
        b_close = pairs[b_open]
        nested.append((b_open, b_close))
        i = bisect.bisect_right(opens_sorted, b_close)
    starts = [s for s, _ in nested]

    def direct(pos: int) -> bool:
        if not (o < pos < c):
            return False
        k = bisect.bisect_right(starts, pos) - 1
        return k < 0 or pos > nested[k][1]

    return direct


def _scope_finder(top: List[_Def]):
    """Returns pos -> scope key of the top-level definition whose body contains pos ("mod" otherwise)."""
    bodies = sorted((d.body[0], d.body[1], d.key) for d in top if d.body[1] > d.body[0])
    starts = [b[0] for b in bodies]

    def find(pos: int) -> str:
        k = bisect.bisect_right(starts, pos) - 1
        if k >= 0 and bodies[k][0] < pos < bodies[k][1]:
            return bodies[k][2]
        return "mod"

    return find


def _split_names(text: str) -> List[str]:
    return [p.strip() for p in re.split(r",(?![^<]*>)", text) if p.strip()]


# --- JavaScript / TypeScript ---------------------------------------------------
_JS_FUNC_DECL_RE = re.compile(
    r"^[ \t]*(?P<exp>export\s+(?:default\s+)?)?(?:declare\s+)?(?P<async>async\s+)?function\s*\*?\s*"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^>\n]*>)?\s*(?P<paren>\()",
    re.MULTILINE,
)
_JS_FUNC_VAR_RE = re.compile(
    r"^[ \t]*(?P<exp>export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?P<async>async\s+)?"
    r"(?:(?P<fn>function\b\s*\*?\s*[\w$]*\s*\()|(?P<arrow>(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=>))",
    re.MULTILINE,
)
_JS_CLASS_RE = re.compile(
    r"^[ \t]*(?P<exp>export\s+(?:default\s+)?)?(?:declare\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)(?P<rest>[^{\n]*)",
    re.MULTILINE,
)
_JS_TYPE_RE = re.compile(
    r"^[ \t]*(?P<exp>export\s+)?(?:declare\s+)?(?:const\s+)?(?P<kw>interface|enum|type)\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?P<tail>(?:<[^=\n{]*>)?\s*[={]|extends\b)",
    re.MULTILINE,
)
_JS_METHOD_RE = re.compile(
    r"^[ \t]*(?P<mods>(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set|declare)\s+)*)\*?\s*"
    r"(?P<name>#?[A-Za-z_$][\w$]*)\s*(?:<[^>\n]*>)?\s*\(",
    re.MULTILINE,
)
_JS_ARROW_PROP_RE = re.compile(
    r"^[ \t]*(?P<mods>(?:(?:public|private|protected|static|readonly)\s+)*)(?P<name>#?[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*"
    r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    re.MULTILINE,
)
_JS_NOT_METHODS = {"if", "for", "while", "switch", "catch", "return", "function", "typeof", "new", "super", "else", "with", "do", "await"}
_JS_IMPORT_CLAUSE_RE = re.compile(r"""\bimport\s+(?!type\s)(?P<clause>[\w$*{}\s,]+?)\s+from\s+['"](?P<spec>[^'"\n]+)['"]""")
_JS_REQUIRE_DESTRUCT_RE = re.compile(r"""\b(?:const|let|var)\s*\{(?P<names>[^}]*)\}\s*=\s*require\(\s*['"](?P<spec>[^'"\n]+)['"]\s*\)""")
_JS_REQUIRE_WHOLE_RE = re.compile(r"""\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*require\(\s*['"](?P<spec>[^'"\n]+)['"]\s*\)""")
_JS_EXPORT_DEFAULT_NAME_RE = re.compile(r"\bexport\s+default\s+([A-Za-z_$][\w$]*)\s*;?\s*$", re.MULTILINE)
_JS_CJS_EXPORTS_RE = re.compile(r"\bmodule\.exports\s*=\s*\{([^}]*)\}")
_JS_CJS_EXPORT_NAME_RE = re.compile(r"\b(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=")


def _parse_import_clause(clause: str) -> List[Tuple[str, str]]:
    """`a, { b, c as d }` / `* as ns` -> [(local, imported | "default" | "*")]."""
    out: List[Tuple[str, str]] = []
    m = re.search(r"\{([^}]*)\}", clause)
    if m:
        for part in m.group(1).split(","):
            p = part.strip()
            if not p or p.startswith("type "):
                continue
            if " as " in p:
                imp, loc = [x.strip() for x in p.split(" as ", 1)]
            else:
                imp = loc = p
            if re.fullmatch(r"[\w$]+", loc) and re.fullmatch(r"[\w$]+", imp):
                out.append((loc, imp))
        clause = clause[: m.start()] + clause[m.end() :]
    for part in clause.split(","):
        p = part.strip()
        if not p:
            continue
        ns = re.fullmatch(r"\*\s*as\s+([\w$]+)", p)
        if ns:
            out.append((ns.group(1), "*"))
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", p):
            out.append((p, "default"))
    return out


def _analyze_js(text: str, info: _FileInfo, analyze_calls: bool) -> None:
    src = _script_blocks(text) if info.ext in (".vue", ".svelte") else text
    cm, fm = _mask_both(src, _JS_TOKEN_RE)
    lines = _Lines(cm)
    for pat in _JS_IMPORT_PATTERNS:
        for m in pat.finditer(cm):
            spec = m.group(1).strip()
            if spec and spec not in info.js_specs:
                info.js_specs.append(spec)
    for m in _JS_IMPORT_CLAUSE_RE.finditer(cm):
        for loc, imp in _parse_import_clause(m.group("clause")):
            info.js_binds.append((loc, imp, m.group("spec").strip()))
    for m in _JS_REQUIRE_DESTRUCT_RE.finditer(cm):
        for part in m.group("names").split(","):
            p = part.strip()
            if not p:
                continue
            imp, _, loc = p.partition(":")
            imp, loc = imp.strip(), (loc.strip() or imp.strip())
            if re.fullmatch(r"[\w$]+", imp) and re.fullmatch(r"[\w$]+", loc):
                info.js_binds.append((loc, imp, m.group("spec").strip()))
    for m in _JS_REQUIRE_WHOLE_RE.finditer(cm):
        info.js_binds.append((m.group("name"), "*", m.group("spec").strip()))

    pairs = _brace_pairs(fm)
    positions: Dict[int, int] = {}
    defs: List[_Def] = []

    def add(d: _Def, pos: int) -> None:
        positions[id(d)] = pos
        defs.append(d)

    for m in _JS_FUNC_DECL_RE.finditer(fm):
        pos = m.start("name")
        d = _Def("function", m.group("name"), lines.line_of(pos), signature=lines.line_text(pos))
        d.exported = bool(m.group("exp"))
        d.is_default = bool(m.group("exp") and "default" in m.group("exp"))
        d.is_async = bool(m.group("async"))
        d.body = _body_after_params(fm, m.start("paren"), pairs)
        add(d, pos)
    for m in _JS_FUNC_VAR_RE.finditer(fm):
        pos = m.start("name")
        d = _Def("function", m.group("name"), lines.line_of(pos), signature=lines.line_text(pos))
        d.exported = bool(m.group("exp"))
        d.is_async = bool(m.group("async"))
        if m.group("fn"):
            d.body = _body_after_params(fm, m.end("fn") - 1, pairs)
        else:
            j = m.end()
            while j < len(fm) and fm[j] in " \t":
                j += 1
            if j < len(fm) and fm[j] == "{":
                d.body = (j, pairs.get(j, j))
            else:
                end = fm.find("\n", j)
                d.body = (m.end(), end if end != -1 else len(fm))
        add(d, pos)
    for m in _JS_CLASS_RE.finditer(fm):
        pos = m.start("name")
        d = _Def("type", m.group("name"), lines.line_of(pos), keyword="class", signature=lines.line_text(pos))
        rest = m.group("rest")
        ext = re.search(r"\bextends\s+([\w$.]+(?:<[^>]*>)?)", rest)
        if ext:
            d.bases = [ext.group(1)]
        imp = re.search(r"\bimplements\s+(.+)$", rest)
        if imp:
            d.implements = _split_names(imp.group(1))
        d.exported = bool(m.group("exp"))
        d.is_default = bool(m.group("exp") and "default" in m.group("exp"))
        d.body = _body_from(fm, m.end("name"), pairs)
        add(d, pos)
    for m in _JS_TYPE_RE.finditer(fm):
        pos = m.start("name")
        d = _Def("type", m.group("name"), lines.line_of(pos), keyword=m.group("kw"), signature=lines.line_text(pos))
        d.exported = bool(m.group("exp"))
        if m.group("kw") != "type":
            d.body = _body_from(fm, m.end("name"), pairs)
        add(d, pos)

    parent = _nest_defs(defs, positions)
    top = [d for d in defs if parent[id(d)] is None]
    uniq: Dict[str, _Def] = {}
    for d in sorted(top, key=lambda x: positions[id(x)]):
        kept = uniq.get(d.key)
        if kept is None:
            uniq[d.key] = d
        elif kept.body[1] <= kept.body[0] and d.body[1] > d.body[0]:
            d.exported = d.exported or kept.exported
            uniq[d.key] = d  # TS overload signatures: keep the implementation
    top = sorted(uniq.values(), key=lambda x: positions[id(x)])

    # exports (ES default by name, CommonJS)
    cjs: Set[str] = set()
    for m in _JS_CJS_EXPORTS_RE.finditer(fm):
        for part in m.group(1).split(","):
            name = part.split(":")[0].strip()
            if re.fullmatch(r"[\w$]+", name):
                cjs.add(name)
    cjs.update(m.group(1) for m in _JS_CJS_EXPORT_NAME_RE.finditer(fm))
    default_names = {m.group(1) for m in _JS_EXPORT_DEFAULT_NAME_RE.finditer(fm)}
    for d in top:
        if d.name in cjs:
            d.exported = True
        if d.name in default_names:
            d.is_default = True
            d.exported = True

    opens_sorted = sorted(pairs)
    for d in top:
        if d.keyword == "class" and d.body[1] > d.body[0]:
            direct = _direct_member_checker(fm, d.body, opens_sorted, pairs)
            seen_m: Set[str] = set()
            for pat in (_JS_METHOD_RE, _JS_ARROW_PROP_RE):
                for m in pat.finditer(fm, d.body[0] + 1, d.body[1]):
                    name = m.group("name")
                    if name in _JS_NOT_METHODS or name in seen_m or not direct(m.start("name")):
                        continue
                    seen_m.add(name)
                    mods = m.group("mods") or ""
                    vis = "private" if ("private" in mods or name.startswith("#")) else ("protected" if "protected" in mods else "public")
                    flags = [w for w in ("static", "async", "get", "set", "abstract") if re.search(rf"\b{w}\b", mods)]
                    d.method_infos.append(_Method(name, lines.line_of(m.start("name")), signature=lines.line_text(m.start("name"), 120), flags=flags, vis=vis))
            d.method_infos.sort(key=lambda x: x.lineno)
            d.methods = [mi.name for mi in d.method_infos if mi.vis == "public"]

    for d in top:
        if d.kind == "function":
            info.functions.append(f"fn {d.name}()")
        else:
            info.classes.append(f"type {d.name}")
    info.defs.extend(top)
    info.defs.sort(key=lambda d: d.lineno)
    info.def_count = len(info.defs)

    if not analyze_calls or not info.js_binds:
        return
    find_scope = _scope_finder(top)
    named = sorted({loc for loc, imp, _ in info.js_binds if imp != "*"})
    spaces = sorted({loc for loc, imp, _ in info.js_binds if imp == "*"})
    if named:
        pat = re.compile(r"(?<![\w$.])(" + "|".join(re.escape(n) for n in named) + r")\s*\(")
        for m in pat.finditer(fm):
            before = fm[max(0, m.start() - 12) : m.start()]
            if re.search(r"\bfunction\s*\*?\s*$", before):
                continue
            close = _match_paren(fm, m.end() - 1)
            if close > 0:
                after = fm[close + 1 : close + 40].lstrip()
                if after.startswith("{") or after.startswith(":") and "{" in after[:30] and "=>" not in after[:30]:
                    continue  # a declaration `name(...) {`, not a call
            info.name_calls.setdefault(find_scope(m.start()), set()).add(m.group(1))
    if spaces:
        pat = re.compile(r"(?<![\w$.])(" + "|".join(re.escape(n) for n in spaces) + r")\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
        for m in pat.finditer(fm):
            info.member_calls.setdefault(find_scope(m.start()), set()).add((m.group(1), m.group(2)))


# --- PHP -----------------------------------------------------------------------
_PHP_CLASS_RE = re.compile(
    r"^[ \t]*(?:(?:abstract|final|readonly)\s+)*(?P<kw>class|interface|trait|enum)\s+(?P<name>[A-Za-z_]\w*)(?P<rest>[^{;]{0,300})",
    re.MULTILINE | re.IGNORECASE,
)
_PHP_FUNC_RE = re.compile(r"(?<![\w$>:\\])function\s+&?\s*(?P<name>[A-Za-z_]\w*)\s*(?P<paren>\()", re.IGNORECASE)
_PHP_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z_\\][\w\\]*)\s*[;{]", re.MULTILINE | re.IGNORECASE)
_PHP_USE_RE = re.compile(r"^\s*use\s+(?!function\b|const\b)(?P<body>\\?[A-Za-z_][\w\\]*(?:\s*\{[^}]*\})?(?:\s+as\s+\w+)?(?:\s*,\s*\\?[A-Za-z_][\w\\]*(?:\s+as\s+\w+)?)*)\s*;", re.MULTILINE | re.IGNORECASE)
_PHP_INCLUDE_RE = re.compile(r"(?<![\w$>:])(?P<kw>include_once|include|require_once|require)\b\s*(?P<expr>[^;\n]+)", re.IGNORECASE)
_PHP_CALL_RE = re.compile(r"(?<![\w$>:\\])([A-Za-z_]\w*)\s*\(")
_PHP_NOT_CALLS = {
    "if", "elseif", "while", "for", "foreach", "switch", "array", "list", "isset", "empty", "unset", "echo", "print",
    "return", "function", "fn", "match", "catch", "include", "include_once", "require", "require_once", "exit", "die",
    "eval", "new", "and", "or", "declare", "use", "static", "self", "parent", "clone", "yield",
}
_PHP_DIRNAME_RE = re.compile(r"dirname\s*\(\s*(__DIR__|__FILE__)\s*(?:,\s*(\d+)\s*)?\)", re.IGNORECASE)
_PHP_DOCROOT_RE = re.compile(r"\$_SERVER\s*\[\s*['\"]DOCUMENT_ROOT['\"]\s*\]")


def _split_concat(expr: str) -> List[str]:
    """Splits a PHP expression on top-level `.` concatenation operators."""
    parts: List[str] = []
    depth = 0
    quote = ""
    cur: List[str] = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(expr):
                cur.append(expr[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif ch in "([":
            depth += 1
            cur.append(ch)
        elif ch in ")]":
            depth -= 1
            cur.append(ch)
        elif ch == "." and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts]


def _php_string_literal(part: str) -> Optional[str]:
    if len(part) >= 2 and part[0] == part[-1] == "'":
        return part[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    if len(part) >= 2 and part[0] == part[-1] == '"' and "$" not in part and "{" not in part:
        return part[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return None


def _php_include_target(expr: str) -> Optional[Tuple[str, str]]:
    """(base, path) of a static include expression; base is "dir" (the including file's
    directory), "root" (document root = project root) or "" (plain relative path).
    None for dynamic expressions."""
    e = expr.strip()
    while e.startswith("(") and _match_paren(e, 0) == len(e) - 1:
        e = e[1:-1].strip()
    base, path = "", ""
    for i, part in enumerate(_split_concat(e)):
        while part.startswith("(") and part.endswith(")") and _match_paren(part, 0) == len(part) - 1:
            part = part[1:-1].strip()
        lit = _php_string_literal(part)
        if lit is not None:
            path += lit
            continue
        if i == 0:
            if part == "__DIR__":
                base = "dir"
                continue
            dm = _PHP_DIRNAME_RE.fullmatch(part)
            if dm:
                levels = int(dm.group(2) or 1)
                up = levels - 1 if dm.group(1).upper() == "__FILE__" else levels
                base, path = "dir", "/" + "/".join([".."] * up) if up else ""
                continue
            if _PHP_DOCROOT_RE.fullmatch(part):
                base = "root"
                continue
        return None
    if not path:
        return None
    return base, path


def _analyze_php(text: str, info: _FileInfo, analyze_calls: bool) -> None:
    code = _php_code_regions(text)
    cm, fm = _mask_both(code, _PHP_TOKEN_RE)
    lines = _Lines(cm)
    ns = _PHP_NAMESPACE_RE.search(fm)
    if ns:
        info.php_namespace = ns.group(1).strip("\\")
    for m in _PHP_USE_RE.finditer(cm):
        body = m.group("body")
        group = re.match(r"\\?([\w\\]+?)\\?\s*\{([^}]*)\}", body)
        if group:
            prefix = group.group(1).strip("\\")
            for item in group.group(2).split(","):
                name = item.strip().split()[0] if item.strip() else ""
                if name:
                    info.php_uses.append(f"{prefix}\\{name.strip(chr(92))}")
            continue
        for item in body.split(","):
            name = item.strip().split()[0].strip("\\") if item.strip() else ""
            if name:
                info.php_uses.append(name)
    for m in _PHP_INCLUDE_RE.finditer(cm):
        expr = m.group("expr").strip()
        target = _php_include_target(expr)
        if target is None:
            info.unresolved_includes += 1
        else:
            info.php_includes.append((target[0], target[1], f"{m.group('kw').lower()} {_clip(expr, 60)}"))

    pairs = _brace_pairs(fm)
    positions: Dict[int, int] = {}
    defs: List[_Def] = []
    for m in _PHP_CLASS_RE.finditer(fm):
        pos = m.start("name")
        d = _Def("type", m.group("name"), lines.line_of(pos), keyword=m.group("kw").lower(), signature=lines.line_text(pos))
        rest = m.group("rest")
        ext = re.search(r"\bextends\s+([\\\w]+(?:\s*,\s*[\\\w]+)*)", rest, flags=re.IGNORECASE)
        if ext:
            d.bases = _split_names(ext.group(1))
        imp = re.search(r"\bimplements\s+([\\\w\s,]+)", rest, flags=re.IGNORECASE)
        if imp:
            d.implements = _split_names(imp.group(1))
        d.body = _body_from(fm, m.end("name"), pairs)
        positions[id(d)] = pos
        defs.append(d)
    for m in _PHP_FUNC_RE.finditer(fm):
        pos = m.start("name")
        d = _Def("function", m.group("name"), lines.line_of(pos), signature=lines.line_text(pos))
        d.body = _body_after_params(fm, m.start("paren"), pairs)
        line_start = fm.rfind("\n", 0, m.start()) + 1
        mods = fm[line_start : m.start()].lower()
        d.class_notes = [w for w in ("public", "protected", "private", "static", "abstract", "final") if re.search(rf"\b{w}\b", mods)]
        positions[id(d)] = pos
        defs.append(d)
    parent = _nest_defs(defs, positions)
    top: List[_Def] = []
    for d in sorted(defs, key=lambda x: positions[id(x)]):
        par = parent[id(d)]
        if par is None:
            top.append(d)
        elif d.kind == "function" and par.kind == "type" and parent[id(par)] is None:
            vis = next((w for w in ("private", "protected", "public") if w in d.class_notes), "public")
            flags = [w for w in ("static", "abstract", "final") if w in d.class_notes]
            par.method_infos.append(_Method(d.name, d.lineno, signature=d.signature, flags=flags, vis=vis))
    for d in top:
        if d.kind == "function":
            d.class_notes = []
            info.functions.append(f"fn {d.name}()")
        else:
            d.methods = [mi.name for mi in d.method_infos if mi.vis == "public"]
            info.classes.append(f"type {d.name}")
    info.defs.extend(top)
    info.defs.sort(key=lambda d: d.lineno)
    info.def_count = len(info.defs)

    if not analyze_calls:
        return
    find_scope = _scope_finder(top)
    for m in _PHP_CALL_RE.finditer(fm):
        name = m.group(1)
        low = name.lower()
        if low in _PHP_NOT_CALLS:
            continue
        before = fm[max(0, m.start() - 10) : m.start()].lower()
        if re.search(r"\b(?:function|new)\s*&?\s*$", before):
            continue
        info.name_calls.setdefault(find_scope(m.start()), set()).add(low)


# --- HTML references -------------------------------------------------------------
_HTML_REF_RES = (
    re.compile(r"""<script\b[^>]*?\bsrc\s*=\s*["']([^"'<>]+)["']""", re.IGNORECASE),
    re.compile(r"""<link\b[^>]*?\bhref\s*=\s*["']([^"'<>]+)["']""", re.IGNORECASE),
)


def _collect_html_refs(text: str, info: _FileInfo) -> None:
    for pat in _HTML_REF_RES:
        for m in pat.finditer(text):
            ref = m.group(1).strip()
            if not ref or re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", ref, flags=re.IGNORECASE):
                continue
            if any(x in ref for x in ("{{", "<?", "$", "{%")):
                continue
            if ref not in info.html_refs:
                info.html_refs.append(ref)


def _analyze_generic_source(text: str, info: _FileInfo, analyze_calls: bool = True) -> None:
    key = _EXT_TO_PATTERN_KEY.get(info.ext)
    info.method = "regex"
    if key == "js":
        _analyze_js(text, info, analyze_calls)
    elif key == "php":
        _analyze_php(text, info, analyze_calls)
    elif key:
        _analyze_with_patterns(text, info, _LANG_PATTERNS[key])
    if info.ext in (".html", ".htm", ".php"):
        _collect_html_refs(text, info)
    # leading comment as a description
    for ln in [ln.strip() for ln in text.splitlines() if ln.strip()][:5]:
        if ln.startswith(("//", "/*", "#", "*", "<!--", "--")) and not ln.startswith(("#include", "#!", "#pragma")):
            if is_noise_comment_line(ln):
                continue
            cand = ln.lstrip("/*#!-< ").rstrip("*/-> ").strip()
            if len(cand) > 10:
                info.doc = cand[:300]
                break


def _js_package_name(spec: str) -> str:
    parts = spec.split("/")
    if spec.startswith("@") and len(parts) > 1:
        return "/".join(parts[:2])
    return parts[0]


def _analyze_markdown_text(text: str) -> Tuple[str, List[str], List[str]]:
    """Extracts a summary paragraph, headings and `[[wikilinks]]` from Markdown text."""
    headings = re.findall(r"^#{1,3}\s+(.+)$", text, flags=re.MULTILINE)
    wikilinks = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)
    in_code = False
    paragraphs: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s or s.startswith(("#", "!", "<", "---", "|", "[!")):
            continue
        paragraphs.append(s)
        if len(paragraphs) >= 3:
            break
    summary = "\n".join(paragraphs)[:450]
    return summary, headings[:12], list(dict.fromkeys(wikilinks))[:20]


def _analyze_markdown_file(file_path: Path) -> Tuple[str, List[str], List[str]]:
    """Backward-compatible helper: analyses one Markdown file on disk."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", [], []
    return _analyze_markdown_text(text)


# ---------------------------------------------------------------------------
# Purpose evidence: per-file extraction during the single walk
# ---------------------------------------------------------------------------
PURPOSE_EVIDENCE_BUDGET = 2400  # characters of `SolarSystem.purpose_evidence` in total
PURPOSE_SESSION_RESERVE = 760  # ... of which at most this much goes to session requests
PURPOSE_CANDIDATE_CAP = 60  # files per project scanned for header comments / user-facing text
PURPOSE_MAX_CHARS = 350  # deterministic `purpose` taken from the project's own words
README_TEXT_CAP = 40_000  # characters of a root README kept for the intro parser
README_INTRO_CAP = 600
PURPOSE_STRING_CAP = 300  # characters per quoted user-facing string
PURPOSE_STRING_MAX = 3
PURPOSE_HEADER_MAX = 3
PURPOSE_NAMES_MAX = 12
SESSION_REQUESTS_MAX = 4
SESSION_REQUEST_CHARS = 160
CAPABILITIES_LABEL = "Capabilities implied by libraries"
_README_ALT_NAMES = {"readme.rst", "readme.txt", "readme"}
_PURPOSE_DIRS = {"src", "app", "public", "lib", "bin", "cmd", "pages", "www", "htdocs", "web"}
#: File stems whose header comment / strings say most about a project.
_PURPOSE_STEMS = {
    "main", "app", "index", "__main__", "__init__", "settings", "config", "configuration", "conf",
    "server", "bot", "cli", "home",
}
_PURPOSE_EXTS = {".py", ".php", ".html", ".htm"} | set(_JS_EXTS)
_PURPOSE_NAME_RE = re.compile(
    r"prompt|message|mesaj|title|baslik|başlık|description|desc$|aciklama|açıklama|welcome|greeting|about|"
    r"help|system|sistem|intro|tagline|slogan|banner|summary|ozet|özet|hosgeldin|hoşgeldin",
    re.IGNORECASE,
)
#: Values of names like these are never quoted (even when they look harmless).
_SECRET_NAME_RE = re.compile(
    r"key|token|secret|passw|passwd|pwd|auth|credential|dsn|connection|conn_?str|cookie|private|salt|webhook|bearer",
    re.IGNORECASE,
)
_SECRET_VALUE_RES = (
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),  # JWT
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),  # hex keys / hashes
    re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),  # URL with credentials
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[abpr]|AKIA|AIza|glpat|hf|pat)[-_][A-Za-z0-9_-]{12,}"),  # vendor keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-+/=]{23,}[A-Za-z0-9=]"),  # long unbroken token (checked below)
)


def _looks_secret(text: str) -> bool:
    """True if `text` contains something that looks like a credential: a JWT, a long hex
    key, a URL with user:password, a vendor key prefix, a private key, or a long
    unbroken high-entropy token (letters and digits mixed)."""
    for rx in _SECRET_VALUE_RES[:-1]:
        if rx.search(text):
            return True
    for m in _SECRET_VALUE_RES[-1].finditer(text):
        tok = m.group(0)
        if any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok) and len(set(tok)) >= 12:
            if not re.fullmatch(r"[a-z_]+[0-9]*|[A-Z_]+[0-9]*", tok) and "/" not in tok.strip("/"):
                return True
            if sum(c.isdigit() for c in tok) >= 4 and any(c.isupper() for c in tok) and any(c.islower() for c in tok):
                return True
    return False


def _is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name or ""))


def _is_natural_text(text: str, min_len: int = 80) -> bool:
    """A multi-sentence natural-language string (not code, a path, a URL or a format)."""
    t = text.strip()
    if len(t) < min_len:
        return False
    words = re.findall(r"[^\W\d_]{2,}", t)
    if len(words) < 10:
        return False
    letters = sum(c.isalpha() or c.isspace() for c in t)
    if letters / max(len(t), 1) < 0.75:
        return False
    return len(re.findall(r"[.!?:](?:\s|$)", t)) >= 1


def _is_purpose_candidate(rel: str, ext: str, layer: str = "") -> bool:
    if ext not in _PURPOSE_EXTS or layer == "testing":
        return False
    parts = rel.split("/")
    stem = posixpath.splitext(parts[-1])[0].lower()
    if len(parts) == 1:
        return True
    if len(parts) == 2:
        return parts[0].lower() in _PURPOSE_DIRS or stem in _PURPOSE_STEMS
    return len(parts) == 3 and parts[0].lower() in _PURPOSE_DIRS and stem in _PURPOSE_STEMS


def _read_small_text(path: str, limit: int = 200_000) -> str:
    try:
        if os.path.islink(path) or os.path.getsize(path) > limit:
            return ""
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _add_bit(info: _FileInfo, kind: str, name: str, text: str) -> None:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) < 3 or len(info.purpose_bits) >= 16 or _looks_secret(text) or _is_secret_name(name):
        return
    if any(k == kind and t == text for k, _n, t in info.purpose_bits):
        return
    info.purpose_bits.append((kind, name, text))


def _py_static_str(node: ast.AST, consts: Dict[str, str], depth: int = 0) -> Optional[str]:
    """The value of a string expression built only from literals, f-strings and names of
    earlier module-level string constants (other placeholders are written as `{…}`)."""
    if depth > 20:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out: List[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                inner = v.value
                if isinstance(inner, ast.Name) and inner.id in consts and not _is_secret_name(inner.id):
                    out.append(consts[inner.id])
                else:
                    out.append("{…}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _py_static_str(node.left, consts, depth + 1)
        right = _py_static_str(node.right, consts, depth + 1)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):  # "-" * 40
        base, times = node.left, node.right
        if isinstance(base, ast.Constant) and isinstance(base.value, int):
            base, times = times, base
        if isinstance(times, ast.Constant) and isinstance(times.value, int) and 0 <= times.value <= 200:
            val = _py_static_str(base, consts, depth + 1)
            if val is not None and len(val) * times.value <= 20_000:
                return val * times.value
    return None


_PY_TITLE_CALLS = {"setWindowTitle", "setApplicationName", "setApplicationDisplayName", "title", "wm_title", "set_title", "set_caption"}


def _python_purpose_bits(tree: ast.Module, text: str, info: _FileInfo) -> None:
    """User-facing text of a Python candidate file, from the already-parsed tree:
    module-level string constants with descriptive names (or long natural-language
    values), argparse descriptions / help texts and window titles. Secrets are skipped."""
    consts: Dict[str, str] = {}
    found: List[Tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value_node = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name, value_node = node.target.id, node.value
        else:
            continue
        value = _py_static_str(value_node, consts)
        if value is None:
            continue
        if _is_secret_name(name) or _looks_secret(value):
            continue
        if len(value) <= 400:
            consts[name] = value
        if (_PURPOSE_NAME_RE.search(name) and len(value.strip()) >= 20) or _is_natural_text(value):
            found.append((name, value))
    for name, value in found:
        _add_bit(info, "string", name, value)
    if "ArgumentParser" not in text and "itle" not in text and "add_argument" not in text:
        return
    helps = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if fname == "ArgumentParser":
                for kw in node.keywords:
                    if kw.arg in ("description", "epilog", "prog"):
                        val = _py_static_str(kw.value, consts)
                        if val:
                            _add_bit(info, "cli", f"argparse {kw.arg}", val)
            elif fname in ("add_argument", "add_parser") and helps < 4:
                for kw in node.keywords:
                    if kw.arg == "help":
                        val = _py_static_str(kw.value, consts)
                        if val and len(val) >= 12:
                            _add_bit(info, "cli", "argparse help", val)
                            helps += 1
            elif fname in _PY_TITLE_CALLS and node.args:
                val = _py_static_str(node.args[0], consts)
                if val and len(val.strip()) >= 3:
                    _add_bit(info, "window title", fname, val)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Attribute) and t.attr == "title":
                val = _py_static_str(node.value, consts)
                if val and len(val.strip()) >= 3:
                    _add_bit(info, "window title", "title", val)


_JS_STR_CONST_RE = re.compile(
    r"""(?:^|[\s;])(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*string\s*)?=\s*(["'`])((?:\\.|(?!\2)[^\\]){20,3000}?)\2""",
    re.MULTILINE | re.DOTALL,
)
_PHP_STR_CONST_RE = re.compile(
    r"""(?:\$([A-Za-z_]\w*)\s*=\s*|define\(\s*['"]([A-Za-z_]\w*)['"]\s*,\s*|\bconst\s+([A-Za-z_]\w*)\s*=\s*)(["'])((?:\\.|(?!\4)[^\\]){20,3000}?)\4""",
    re.DOTALL,
)
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_HTML_META_DESC_RES = (
    re.compile(r"""<meta\s[^>]*name\s*=\s*["']description["'][^>]*content\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL),
    re.compile(r"""<meta\s[^>]*content\s*=\s*(["'])(.*?)\1[^>]*name\s*=\s*["']description["']""", re.IGNORECASE | re.DOTALL),
)
_HTML_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1\s*>", re.IGNORECASE | re.DOTALL)
_JS_DOC_TITLE_RE = re.compile(r"""\bdocument\.title\s*=\s*(["'`])([^"'`\n]{3,200})\1""")
_PHP_DB_RES = (("PDO", re.compile(r"\bnew\s+\\?PDO\s*\(")), ("mysqli", re.compile(r"\bmysqli_connect\s*\(|\bnew\s+\\?mysqli\s*\(", re.IGNORECASE)))
_TEMPLATE_EXPR_RE = re.compile(r"<\?(?:php|=)?.*?(?:\?>|$)|\{\{.*?\}\}|\{%.*?%\}|\$\{[^}]*\}", re.DOTALL)


def _php_db_usage(text: str, info: _FileInfo) -> None:
    for label, rx in _PHP_DB_RES:
        if rx.search(text):
            info.uses_php_db = label
            return


def _strip_markup(fragment: str) -> str:
    import html as _html

    text = _TEMPLATE_EXPR_RE.sub(" ", fragment)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _unescape_js(value: str) -> str:
    return value.replace("\\n", " ").replace("\\t", " ").replace("\\'", "'").replace('\\"', '"').replace("\\`", "`")


#: Code lines allowed above a header comment (they are not "code before the header").
_DIRECTIVE_CODE_RE = re.compile(
    r"""^(?:['"]use\s+(?:strict|client|server)['"];?|declare\s*\(\s*strict_types\s*=\s*1\s*\)\s*;|<\?php\b.*|#!.*)$""",
    re.IGNORECASE,
)
_HEADER_SCAN_LINES = 40
_HASH_NON_COMMENT = ("#include", "#pragma", "#!", "#define", "#if", "#endif", "#import")


def _comment_blocks(lines: List[str]) -> Iterable[Tuple[int, List[str]]]:
    """(start line, raw lines) of each comment block (`/* … */`, `<!-- … -->`, a run of
    `//` or `#` lines) in `lines`, in order."""
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        handled = False
        for opener, closer in (("/*", "*/"), ("<!--", "-->")):
            if s.startswith(opener):
                start, body = i, []
                rest = s[len(opener):]
                while True:
                    if closer in rest:
                        body.append(rest[: rest.index(closer)])
                        break
                    body.append(rest)
                    i += 1
                    if i >= len(lines):
                        break
                    rest = lines[i]
                yield start, body
                handled = True
                break
        if not handled and s.startswith(("//", "#")) and not s.startswith(_HASH_NON_COMMENT):
            start, body = i, []
            while i < len(lines) and lines[i].strip().startswith(("//", "#")) and not lines[i].strip().startswith(_HASH_NON_COMMENT):
                body.append(lines[i].strip())
                i += 1
            yield start, body
            continue
        i += 1


def _generic_header_comment(text: str) -> str:
    """The header comment (`/** … */`, `//` lines, `#` lines or `<!-- … -->`) of a
    non-Python file: the first comment block at the top (after `<?php`, a shebang or a
    directive such as `'use strict'`), with noise lines removed
    (`purpose_evidence.clean_header_text`). When that block is only noise (license,
    encoding, directives, `/// <reference>`, "generated" notes, ...), the next comment
    block within the first 40 lines is used instead."""
    lines = text[:8000].lstrip("\ufeff").splitlines()[:_HEADER_SCAN_LINES]
    first_code = next(
        (n for n, ln in enumerate(lines)
         if ln.strip() and not ln.strip().startswith(("/*", "*", "//", "#", "<!--", "-->"))
         and not _DIRECTIVE_CODE_RE.match(ln.strip())),
        None,
    )
    for n, (start, body) in enumerate(_comment_blocks(lines)):
        if n == 0 and first_code is not None and first_code < start:
            return ""  # code before the first comment: the file has no header comment
        kept = [_clean_comment_line(ln) for ln in body if not is_noise_comment_line(ln)]
        out = clean_header_text("\n".join(ln for ln in kept if ln))
        if len(re.findall(r"\w", out)) >= 10:
            return out[:300]
    return ""


def _text_purpose_bits(text: str, info: _FileInfo) -> None:
    """User-facing text of a JS/TS/PHP/HTML candidate file by cautious patterns:
    descriptive string constants, `document.title`, and for HTML/PHP pages the
    `<title>`, `<meta name="description">` and first `<h1>`. Also notes whether PHP code
    opens a database connection (PDO / mysqli). Secrets are skipped."""
    header = _generic_header_comment(text)
    if header:
        _add_bit(info, "header comment", "", header)
    if info.ext in (".html", ".htm", ".php"):
        m = _HTML_TITLE_RE.search(text)
        if m:
            _add_bit(info, "page title", "<title>", _strip_markup(m.group(1))[:200])
        for rx in _HTML_META_DESC_RES:
            m = rx.search(text)
            if m:
                _add_bit(info, "meta description", "<meta description>", _strip_markup(m.group(2))[:300])
                break
        m = _HTML_H1_RE.search(text)
        if m:
            _add_bit(info, "heading", "<h1>", _strip_markup(m.group(1))[:200])
    if info.ext == ".php":
        _php_db_usage(text, info)
        for m in _PHP_STR_CONST_RE.finditer(_php_code_regions(text) if "<?" in text else text):
            name = m.group(1) or m.group(2) or m.group(3) or ""
            value = _unescape_js(m.group(5))
            if "$" in value and m.group(4) == '"':
                value = re.sub(r"\{?\$[A-Za-z_][\w\[\]'\"->]*\}?", "{…}", value)
            if (_PURPOSE_NAME_RE.search(name) and len(value) >= 20) or _is_natural_text(value):
                _add_bit(info, "string", name, value)
    elif info.ext in _JS_EXT_SET:
        m = _JS_DOC_TITLE_RE.search(text)
        if m:
            _add_bit(info, "window title", "document.title", m.group(2))
        for m in _JS_STR_CONST_RE.finditer(text):
            name, value = m.group(1), _unescape_js(m.group(3))
            if m.group(2) == "`":
                value = re.sub(r"\$\{[^}]*\}", "{…}", value)
            if (_PURPOSE_NAME_RE.search(name) and len(value) >= 20) or _is_natural_text(value):
                _add_bit(info, "string", name, value)


# ---------------------------------------------------------------------------
# README intro
# ---------------------------------------------------------------------------
#: (tool, pattern) — a README matching one of these is the unmodified scaffold template.
_README_BOILERPLATE: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("create-next-app", re.compile(r"bootstrapped with \[?`?create-next-app", re.IGNORECASE)),
    ("Create React App", re.compile(r"bootstrapped with \[?Create React App", re.IGNORECASE)),
    ("Vite", re.compile(
        r"This template provides a minimal setup to get \w+ working in Vite|"
        r"^#\s*(?:React|Vue(?: 3)?|Svelte|Preact|Lit|Vanilla|Solid)(?:\s*\+\s*TypeScript)?\s*\+\s*Vite\b",
        re.IGNORECASE | re.MULTILINE)),
    ("npm init / README", re.compile(r"TODO: (?:Explain|Give a short introduction|Describe)", re.IGNORECASE)),
    ("Laravel", re.compile(r"Laravel is a web application framework with expressive, elegant syntax", re.IGNORECASE)),
    ("Django", re.compile(r"(?:generated|created) (?:by|with|using) [`']?django-admin startproject", re.IGNORECASE)),
    ("Angular CLI", re.compile(r"This project was generated (?:with|using) \[Angular CLI\]", re.IGNORECASE)),
    ("Expo", re.compile(r"This is an \[Expo\]\(https://expo\.dev\) project created with", re.IGNORECASE)),
    ("SvelteKit", re.compile(r"Everything you need to build a Svelte project, powered by", re.IGNORECASE)),
    ("Nuxt", re.compile(r"Look at the \[Nuxt(?: 3)? documentation\]", re.IGNORECASE)),
)
_DESC_HEADING_RE = re.compile(
    r"^(?:about|about (?:this|the) (?:project|app|tool)|overview|description|what is|what's|whats|what it does|"
    r"introduction|intro|summary|purpose|hakk[ıi]nda|proje hakk[ıi]nda|nedir|a[çc][ıi]klama|[öo]zet|genel bak[ıi][şs]|"
    r"tan[ıi]t[ıi]m|giri[şs]|ama[çc])\b",
    re.IGNORECASE,
)
_INSTR_HEADING_RE = re.compile(
    r"(?:install|setup|set up|usage|how to (?:use|run|start|install)|running|run\b|requirements|prerequisites|"
    r"quick ?start|getting started|build|deploy|configuration|config\b|contributing|license|licence|changelog|"
    r"credits|acknowledg|table of contents|contents|toc\b|learn more|available scripts|scripts|commands|"
    r"development|testing|tests?\b|faq|troubleshoot|support|roadmap|todo|"
    r"kurulum|kullan[ıi]m|[çc]al[ıi][şs]t[ıi]rma|gereksinim|ba[şs]larken|lisans|katk[ıi]|i[çc]indekiler|y[üu]kleme|nas[ıi]l)",
    re.IGNORECASE,
)
_IMPERATIVE_WORDS = {
    "run", "install", "double-click", "doubleclick", "open", "first", "first,", "clone", "download", "execute", "start",
    "launch", "type", "copy", "navigate", "go", "cd", "use", "create", "set", "edit", "add", "make", "put", "click",
    "press", "call", "build", "deploy", "configure", "enter", "place", "rename", "visit", "follow", "check", "npm",
    "pip", "python", "yarn", "pnpm", "git", "node", "php", "composer", "docker", "then", "next", "finally", "step",
    "çalıştır", "çalıştırın", "çalıştırmak", "kur", "kurun", "yükle", "yükleyin", "çift", "aç", "açın", "önce",
    "indir", "indirin", "tıkla", "tıklayın", "başlat", "başlatın", "yaz", "yazın", "gir", "girin", "kopyala",
    "kopyalayın", "ilk", "sonra", "adım",
}
_COMMAND_RE = re.compile(r"(?:^|\s)(?:npm|pnpm|yarn|npx|pip3?|python3?|py|node|php|composer|git|cd|docker|cargo|go)\s+[\w./-]+")
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")


def _clean_md_inline(text: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)  # links -> text
    t = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])", r"\1", t)
    t = re.sub(r"``?([^`]+)``?", r"\1", t)  # rst / md inline code -> text
    return re.sub(r"\s+", " ", t).strip()


def _is_instruction_paragraph(raw: str, cleaned: str) -> bool:
    first = re.split(r"\s+", cleaned.strip(), maxsplit=1)[0].strip(".,:;!?()\"'").lower() if cleaned.strip() else ""
    first = first.replace("İ", "i")
    if first in _IMPERATIVE_WORDS or cleaned.lower().startswith(("double click", "double-click", "çift tıkla")):
        return True
    low = cleaned.lower()[:120]
    if "double-click" in low or "double click" in low or "çift tıkla" in low or "cift tikla" in low:
        return True
    if raw.lstrip().startswith(("$ ", "> ", "C:\\", "PS ")):
        return True
    code_chars = sum(len(m.group(0)) for m in re.finditer(r"`[^`]+`", raw))
    if code_chars > 0.3 * max(len(raw), 1):
        return True
    return bool(_COMMAND_RE.search(cleaned)) and len(cleaned.split()) < 25


def _readme_sections(text: str) -> List[Tuple[int, str, List[str]]]:
    """Splits Markdown / reStructuredText into (heading level, heading text, raw
    paragraphs); level 0 is the text before the first heading. Code blocks, HTML
    comments and indented blocks are dropped."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: List[Tuple[int, str, List[str]]] = [(0, "", [])]
    para: List[str] = []
    fence = ""
    in_comment = False

    def flush() -> None:
        if para:
            if not all(ln.startswith(("    ", "\t")) for ln in para):
                sections[-1][2].append("\n".join(para))
            para.clear()

    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            i += 1
            continue
        if fence:
            if s.startswith(fence):
                fence = ""
            i += 1
            continue
        if s.startswith(("```", "~~~")):
            flush()
            fence = s[:3]
            i += 1
            continue
        if s.startswith("<!--"):
            flush()
            in_comment = "-->" not in s
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", s)
        if m:
            flush()
            sections.append((len(m.group(1)), _clean_md_inline(m.group(2)), []))
            i += 1
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if s and not para and re.fullmatch(r"(=+|-+|~+|\^+|\*+)", nxt or "x") and len(nxt) >= max(3, len(s) // 2):
            flush()
            sections.append((1 if nxt[0] in "=*" else 2, _clean_md_inline(s), []))
            i += 2
            continue
        if re.fullmatch(r"(=+|-+|~+|\^+|\*+)", s or "x") and len(s) >= 3 and not para:
            i += 1  # rst over-line
            continue
        if not s:
            flush()
        else:
            para.append(raw)
        i += 1
    flush()
    return sections


def _descriptive(paragraphs: List[str]) -> List[str]:
    out: List[str] = []
    for p in paragraphs:
        s = p.strip()
        if not s or s.startswith(("!", "<", "|", "[![", "[!", "---", "***", ".. ", ">", ":")):
            continue
        if _LIST_LINE_RE.match(s):
            continue
        cleaned = _clean_md_inline(s)
        if len(re.findall(r"[^\W\d_]{2,}", cleaned)) < 4:
            continue
        if _is_instruction_paragraph(s, cleaned) or _looks_secret(cleaned):
            continue
        out.append(cleaned)
    return out


def readme_intro(text: str) -> Tuple[str, str, str]:
    """Returns (intro, title, boilerplate tool) for a README text.

    `boilerplate tool` is set (and the intro empty) when the README is an unmodified
    scaffold template (create-next-app, Vite, ...). Otherwise the intro is the first one
    or two descriptive paragraphs: the text under the H1 (or before any heading) first,
    then the first section titled like About / Overview / Description / What is /
    Hakkında / Nedir / Açıklama / Özet, then the first descriptive paragraph of another
    non-instruction section. Headings, badges, images, HTML, tables, lists, code blocks
    and instruction paragraphs (install / usage / run sections, command lines,
    imperative openers such as "Run", "Double-click", "Önce", "Çift tıklayın") are
    skipped."""
    if not text.strip():
        return "", "", ""
    sections = _readme_sections(text)
    body = " ".join(p for _l, _h, ps in sections for p in ps)
    for tool, rx in _README_BOILERPLATE:
        m = rx.search(text)
        if m and (len(body) < 2500 or m.start() < 900):
            return "", "", tool
    title = next((h for lvl, h, _ in sections if lvl == 1 and h), "")
    first_h1 = next((i for i, (lvl, _h, _p) in enumerate(sections) if lvl == 1), None)
    top: List[str] = list(sections[0][2])
    if first_h1 is not None:
        top += sections[first_h1][2]
    chosen = _descriptive(top)
    if not chosen:
        for lvl, h, ps in sections[1:]:
            if h and _DESC_HEADING_RE.match(h):
                chosen = _descriptive(ps)
                if chosen:
                    break
    if not chosen:
        for idx, (lvl, h, ps) in enumerate(sections[1:], start=1):
            if idx == first_h1 or not h or _INSTR_HEADING_RE.search(h):
                continue
            chosen = _descriptive(ps)[:1]
            if chosen:
                break
    intro = _clip(" ".join(chosen[:2]), README_INTRO_CAP) if chosen else ""
    return intro, title, ""


def _first_sentences(text: str, limit: int = PURPOSE_MAX_CHARS, max_sentences: int = 3) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[^\s])", text)
    out = ""
    for n, sentence in enumerate(parts):
        if n >= max_sentences:
            break
        cand = (out + " " + sentence).strip()
        if len(cand) > limit:
            break
        out = cand
    if not out:
        out = _clip(text, limit)
    return out


def _valid_description(desc: str, project_name: str) -> bool:
    d = re.sub(r"\s+", " ", desc or "").strip()
    if len(d) < 8 or d.lower().startswith("todo") or _looks_secret(d):
        return False
    return d.strip(" .").lower() not in {project_name.strip().lower(), "description", "a project", "my project"}


# ---------------------------------------------------------------------------
# Directory walk
# ---------------------------------------------------------------------------
_BACKUP_DIR_RE = re.compile(r"^(?:backups?|.+[-_]backup|.+\.bak)$", re.IGNORECASE)


def _is_backup_dir(name: str) -> bool:
    """`backup`, `backups`, `*-backup`, `*_backup`, `*.bak` (case-insensitive): copies of
    the project that would only duplicate its files on the map."""
    return bool(_BACKUP_DIR_RE.match(name))


def _should_skip_dir(name: str, sibling_files: Iterable[str]) -> bool:
    low = name.lower()
    if name.startswith(".") or low in _IGNORED_DIRS_LOWER or low.endswith(IGNORED_DIR_SUFFIXES):
        return True
    if low in {"bin", "obj"}:
        return any(f.lower().endswith(_DOTNET_PROJECT_SUFFIXES) for f in sibling_files)
    return False


def _is_data_file(fn: str, ext: str) -> bool:
    low = fn.lower()
    if low in _LOCK_FILE_NAMES:
        return False
    return ext in DATA_EXTENSIONS or low in DATA_FILE_NAMES or low.endswith(".env.example")


@dataclass
class _WalkExtras:
    """Side results of `_walk_project`."""

    configs: List[str] = field(default_factory=list)  # tsconfig/jsconfig/package/composer.json (rel)
    backup_dirs: List[str] = field(default_factory=list)  # backup-named directories skipped (rel)
    readme_alt: Tuple[str, str] = ("", "")  # (rel, text) of a root README.rst / README.txt / README
    calls_disabled: bool = False  # project has more than CALL_ANALYSIS_MAX_CODE_FILES code files
    calls_skipped_large: int = 0  # files skipped by CALL_ANALYSIS_MAX_FILE_BYTES
    detail: bool = True  # file-level map expected: build signatures / class details


def _walk_project(
    folder: Path,
    data_files: Optional[List[Tuple[str, int]]] = None,
    extras: Optional[_WalkExtras] = None,
) -> Tuple[List[_FileInfo], int]:
    """Walks `folder` once, pruning ignored/dot/symlinked directories in place, then
    reads + analyses each analysable file exactly once. Returns (files, skipped_large).

    If `data_files` is a list, small config/data files (see `DATA_EXTENSIONS`) are
    appended to it as (relative path, size in bytes); they are never opened here."""
    extras = extras if extras is not None else _WalkExtras()
    entries: List[Tuple[str, str, str, int]] = []  # (full path, rel, ext, size)
    root = str(folder)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        kept = []
        for d in dirnames:
            if _should_skip_dir(d, filenames):
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                continue  # never follow directory symlinks (loops / outside trees)
            if _is_backup_dir(d):
                extras.backup_dirs.append(os.path.relpath(os.path.join(dirpath, d), root).replace(os.sep, "/"))
                continue
            kept.append(d)
        dirnames[:] = sorted(kept)
        at_root = os.path.normpath(dirpath) == os.path.normpath(root)

        for fn in sorted(filenames):
            if fn.startswith("."):
                continue  # also skips `.env` files: they are never read
            ext = os.path.splitext(fn)[1].lower()
            low = fn.lower()
            if low in _CONFIG_NAMES:
                extras.configs.append(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/"))
            if at_root and low in _README_ALT_NAMES and not extras.readme_alt[0]:
                extras.readme_alt = (fn, _read_small_text(os.path.join(dirpath, fn)))
            if ext not in CODE_EXTENSIONS:
                if data_files is not None and len(data_files) < DATA_FILE_LIST_CAP and _is_data_file(fn, ext):
                    full = os.path.join(dirpath, fn)
                    try:
                        if not os.path.islink(full):
                            size = os.stat(full).st_size
                            if size <= DATA_FILE_MAX_BYTES:
                                data_files.append((os.path.relpath(full, root).replace(os.sep, "/"), size))
                    except OSError:
                        pass
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue
            try:
                size = os.stat(full).st_size
            except OSError:
                continue
            entries.append((full, os.path.relpath(full, root).replace(os.sep, "/"), ext, size))

    code_count = sum(1 for e in entries if e[2] != ".md")
    extras.calls_disabled = code_count > CALL_ANALYSIS_MAX_CODE_FILES
    files: List[_FileInfo] = [
        _FileInfo(rel=rel, path=Path(full), ext=ext, lang=CODE_EXTENSIONS[ext], size=size) for full, rel, ext, size in entries
    ]
    # Same rule as build_code_map(): only file-level maps show signatures / class details.
    extras.detail = code_count <= FILE_LEVEL_MAX_FILES or _layout_is_flat(files)
    skipped = 0
    parsed = 0
    candidates = 0
    for info, (full, rel, ext, size) in zip(files, entries):
        info.layer = _classify_file_layer(rel)
        if candidates < PURPOSE_CANDIDATE_CAP and _is_purpose_candidate(rel, ext, info.layer):
            info.purpose_candidate = True
            candidates += 1
        if size > MAX_PARSE_BYTES:
            info.note = f"Not parsed: file is larger than {MAX_PARSE_BYTES // 1_000_000} MB."
            skipped += 1
            continue
        if parsed >= MAX_PARSED_FILES:
            info.note = f"Not parsed: project exceeds {MAX_PARSED_FILES} analysable files."
            skipped += 1
            continue
        try:
            with open(full, "rb") as fh:
                text = fh.read().decode("utf-8", errors="replace")
        except OSError as exc:
            info.note = f"Not parsed: could not read file ({exc.__class__.__name__})."
            continue
        parsed += 1
        info.lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        analyze_calls = not extras.calls_disabled
        if analyze_calls and size > CALL_ANALYSIS_MAX_FILE_BYTES and ext != ".md":
            analyze_calls = False
            extras.calls_skipped_large += 1
            info.calls_note = f"Call analysis skipped: file is larger than {CALL_ANALYSIS_MAX_FILE_BYTES // 1000} KB."
        try:
            if ext == ".py":
                _analyze_python_source(text, info, analyze_calls, extras.detail)
            elif ext == ".md":
                info.method = "markdown"
                info.doc, info.headings, info.wikilinks = _analyze_markdown_text(text)
                if "/" not in rel and posixpath.splitext(rel)[0].lower() == "readme":
                    info.readme_text = text[:README_TEXT_CAP]
            else:
                _analyze_generic_source(text, info, analyze_calls)
                if info.purpose_candidate:
                    _text_purpose_bits(text, info)
                elif ext == ".php" and ("PDO" in text or "mysqli" in text.lower()):
                    _php_db_usage(text, info)
        except RecursionError:
            info.note = "Analysis stopped: the file is too deeply nested to analyse."
    return files, skipped


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------
def _py_lookup(base: Sequence[str], dotted: str, rels: Set[str]) -> Optional[str]:
    parts = list(base) + ([p for p in dotted.split(".") if p] if dotted else [])
    if not parts:
        return "__init__.py" if "__init__.py" in rels else None
    stem = "/".join(parts)
    for cand in (stem + ".py", stem + "/__init__.py"):
        if cand in rels:
            return cand
    return None


def _py_package_dirs(rels: Iterable[str]) -> Set[Tuple[str, ...]]:
    return {tuple(r.split("/")[:-1]) for r in rels if r.endswith("/__init__.py") or r == "__init__.py"}


def _py_roots(dparts: Tuple[str, ...], package_dirs: Set[Tuple[str, ...]]) -> List[Tuple[str, ...]]:
    """Candidate sys.path roots of a file: its own directory when it is not a package,
    then every non-package ancestor (deepest first), ending with the project root."""
    roots: List[Tuple[str, ...]] = []
    for i in range(len(dparts), -1, -1):
        cand = dparts[:i]
        if cand and cand in package_dirs:
            continue
        if cand not in roots:
            roots.append(cand)
    return roots


def _resolve_python_imports(files: List[_FileInfo], rels: Set[str]) -> None:
    package_dirs = _py_package_dirs(rels)
    for info in files:
        if info.ext != ".py" or not info.py_imports:
            continue
        dparts = info.dir_parts
        roots = _py_roots(dparts, package_dirs)
        for level, module, names in info.py_imports:
            # (target rel, module text as written, imported names that are attributes)
            targets: List[Tuple[str, str, List[str]]] = []
            dots = "." * level
            if level > 0:
                up = level - 1
                if up > len(dparts):
                    continue
                base = dparts[: len(dparts) - up]
                for n in names:
                    if n == "*":
                        continue
                    sub = f"{module}.{n}" if module else n
                    hit = _py_lookup(base, sub, rels)
                    if hit:
                        targets.append((hit, dots + sub, []))
                if not targets:
                    hit = _py_lookup(base, module, rels)
                    if hit:
                        targets.append((hit, dots + module, list(names)))
            else:
                if not module:
                    continue
                for root in roots:
                    found: List[Tuple[str, str, List[str]]] = []
                    for n in names:
                        if n == "*":
                            continue
                        hit = _py_lookup(root, f"{module}.{n}", rels)
                        if hit:
                            found.append((hit, f"{module}.{n}", []))
                    if not found:
                        mparts = module.split(".")
                        for k in range(len(mparts), 0, -1):
                            sub = ".".join(mparts[:k])
                            hit = _py_lookup(root, sub, rels)
                            if hit:
                                found.append((hit, sub, list(names) if k == len(mparts) else []))
                                break
                    if found:
                        targets = found
                        break
                if not targets:
                    info.external_imports.add(module.split(".")[0])
            for t, mod_text, imported in targets:
                info.add_dep(t, mod_text, imported)


class _PathIndex:
    """Exact and case-insensitive (unique lower-case path) lookup of project files."""

    def __init__(self, rels: Set[str]) -> None:
        self.rels = rels
        counts: Counter = Counter(r.lower() for r in rels)
        self.lower = {r.lower(): r for r in rels if counts[r.lower()] == 1}
        self.case_hits = 0

    def get(self, cand: str) -> Optional[str]:
        if cand in self.rels:
            return cand
        hit = self.lower.get(cand.lower())
        if hit:
            self.case_hits += 1
        return hit


def _norm_rel(path: str) -> Optional[str]:
    p = posixpath.normpath(path.replace("\\", "/"))
    if p in (".", "") or p.startswith("../") or p == ".." or p.startswith("/"):
        return None
    return p


def _js_candidates(target: str) -> List[str]:
    if target == ".":
        target = ""
    prefix = f"{target}/" if target else ""
    cands = [target] if target else []
    cands += [target + e for e in _JS_EXTS] if target else []
    cands += [f"{prefix}index{e}" for e in _JS_EXTS]
    stem, ext = posixpath.splitext(target)
    if ext in _JS_TS_SUBST:
        cands += [stem + e for e in _JS_TS_SUBST[ext]]
    return cands


def _resolve_js_spec(from_rel: str, spec: str, rels, index: Optional[_PathIndex] = None) -> Optional[str]:
    """Resolves a relative ("./x", "../x") or root-absolute ("/x") specifier."""
    spec = spec.split("?")[0].split("#")[0]
    if not spec.startswith((".", "/")) or spec.startswith("//"):
        return None
    if spec.startswith("/"):
        target = posixpath.normpath(spec.lstrip("/")) if spec.strip("/") else "."
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), spec))
    if target.startswith(".."):
        return None
    return _lookup_js_target(target, from_rel, rels, index)


def _lookup_js_target(target: str, from_rel: str, rels, index: Optional[_PathIndex]) -> Optional[str]:
    cands = _js_candidates(target)
    for c in cands:
        if c in rels and c != from_rel:
            return c
    if index is not None:
        for c in cands:
            hit = index.get(c)
            if hit and hit != from_rel:
                return hit
    return None


_JSONC_COMMENT_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"|//[^\n]*|/\*.*?\*/", re.DOTALL)


def _load_jsonc(text: str) -> Optional[dict]:
    """json.loads for tsconfig-style JSON with comments and trailing commas."""
    try:
        return json.loads(text)
    except Exception:
        pass
    stripped = _JSONC_COMMENT_RE.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)
    stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
    try:
        data = json.loads(stripped)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class _AliasConfig:
    config: str  # rel path of the tsconfig/jsconfig file
    scope: str  # directory (rel, "" = root) whose files it applies to
    base: str  # baseUrl directory (rel)
    has_base_url: bool
    paths: List[Tuple[str, List[str]]]  # (pattern, targets)


def _read_alias_configs(folder: Path, configs: List[str]) -> List[_AliasConfig]:
    out: List[_AliasConfig] = []
    for rel in configs:
        if posixpath.basename(rel).lower() not in ("tsconfig.json", "jsconfig.json"):
            continue
        try:
            p = folder / rel
            if p.stat().st_size > MAX_PARSE_BYTES:
                continue
            data = _load_jsonc(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not data:
            continue
        opts = data.get("compilerOptions") or {}
        if not isinstance(opts, dict):
            continue
        scope = posixpath.dirname(rel)
        base_url = opts.get("baseUrl")
        base = _norm_rel(posixpath.join(scope, str(base_url))) if isinstance(base_url, str) else None
        if base is None:
            base = scope if not isinstance(base_url, str) or posixpath.normpath(posixpath.join(scope, str(base_url))) == "." else None
        if base is None:
            continue
        paths = []
        raw_paths = opts.get("paths") or {}
        if isinstance(raw_paths, dict):
            for pat, targets in raw_paths.items():
                if isinstance(targets, list):
                    paths.append((str(pat), [str(t) for t in targets if isinstance(t, str)]))
        if paths or isinstance(base_url, str):
            out.append(_AliasConfig(rel, scope, base, isinstance(base_url, str), paths))
    out.sort(key=lambda c: -len(c.scope))  # nearest config first
    return out


def _in_scope(rel: str, scope: str) -> bool:
    return not scope or rel.startswith(scope + "/")


def _resolve_alias(from_rel: str, spec: str, rels, index: _PathIndex, aliases: List[_AliasConfig], src_dirs: Set[str]) -> Tuple[Optional[str], str]:
    """Resolves a non-relative specifier through tsconfig/jsconfig `paths`/`baseUrl`
    or the Vite `@/` alias. Returns (rel or None, how)."""
    spec = spec.split("?")[0].split("#")[0]
    matched = False
    for cfg in aliases:
        if not _in_scope(from_rel, cfg.scope):
            continue
        for pat, targets in cfg.paths:
            if "*" in pat:
                pre, _, post = pat.partition("*")
                if not (spec.startswith(pre) and spec.endswith(post) and len(spec) >= len(pre) + len(post)):
                    continue
                star = spec[len(pre) : len(spec) - len(post)] if post else spec[len(pre) :]
            elif spec == pat:
                star = ""
            else:
                continue
            matched = True
            for t in targets:
                cand = _norm_rel(posixpath.join(cfg.base, t.replace("*", star)))
                if cand is None:
                    continue
                hit = _lookup_js_target(cand, from_rel, rels, index)
                if hit:
                    return hit, f"`{cfg.config}` paths"
        if cfg.has_base_url and not spec.startswith("@/"):
            cand = _norm_rel(posixpath.join(cfg.base, spec))
            if cand:
                hit = _lookup_js_target(cand, from_rel, rels, index)
                if hit:
                    return hit, f"`{cfg.config}` baseUrl"
        break  # only the nearest config applies (as in TypeScript)
    if spec.startswith("@/"):
        parts = from_rel.split("/")[:-1]
        for i in range(len(parts), -1, -1):
            src = "/".join(parts[:i] + ["src"])
            if src in src_dirs:
                cand = _norm_rel(posixpath.join(src, spec[2:]))
                if cand:
                    hit = _lookup_js_target(cand, from_rel, rels, index)
                    if hit:
                        return hit, "Vite `@/` alias"
                break
    return None, ("alias without an existing target" if matched else "")


def _resolve_js_imports(files: List[_FileInfo], rels: Set[str], index: Optional[_PathIndex] = None,
                        aliases: Sequence[_AliasConfig] = (), src_dirs: Optional[Set[str]] = None) -> int:
    """Resolves JS/TS specifiers; unresolved bare specifiers become external packages
    (Node built-ins are kept apart). Returns the number of alias-resolved imports."""
    index = index or _PathIndex(rels)
    src_dirs = src_dirs if src_dirs is not None else set()
    alias_hits = 0
    for info in files:
        for spec in info.js_specs:
            hit = _resolve_js_spec(info.rel, spec, rels, index)
            how = ""
            if hit is None and not spec.startswith((".", "/")):
                hit, how = _resolve_alias(info.rel, spec, rels, index, list(aliases), src_dirs)
                if hit:
                    alias_hits += 1
            if hit:
                info.spec_hits[spec] = hit
                info.add_dep(hit, spec + (f" (via {how})" if how else ""))
                continue
            if how or spec.startswith((".", "/", "@/", "~", "#", "$")):
                continue
            if spec.startswith("node:") or ":" in spec:
                if spec.startswith("node:"):
                    info.builtin_imports.add(spec[5:].split("/")[0])
                continue
            name = _js_package_name(spec)
            if name.split("/")[0] in _NODE_BUILTINS:
                info.builtin_imports.add(name)
            elif _NPM_NAME_RE.match(name):
                info.external_imports.add(name)
    return alias_hits


def _resolve_php_includes(files: List[_FileInfo], index: _PathIndex) -> None:
    for info in files:
        if info.ext != ".php":
            continue
        for base, path, written in info.php_includes:
            p = path.replace("\\", "/")
            here = posixpath.dirname(info.rel)
            if base == "dir":
                cands = [posixpath.join(here, p.lstrip("/"))]
            elif base == "root":
                cands = [p.lstrip("/")]
            else:
                if p.startswith("/") or re.match(r"^[A-Za-z]:/", p):
                    info.unresolved_includes += 1
                    continue
                cands = [posixpath.join(here, p), p]
            hit = None
            for c in cands:
                rel = _norm_rel(c)
                if rel is not None:
                    hit = index.get(rel)
                    if hit:
                        break
            if hit and hit != info.rel:
                info.add_dep(hit, hit, verb="includes")
            elif not hit:
                info.unresolved_includes += 1


def _resolve_php_uses(files: List[_FileInfo]) -> Dict[str, Set[str]]:
    """Resolves `use` statements to the single project file declaring the name.
    Returns unresolved namespaced uses: name -> files using it."""
    fqn: Dict[str, Set[str]] = defaultdict(set)
    short: Dict[str, Set[str]] = defaultdict(set)
    for f in files:
        if f.ext != ".php":
            continue
        for d in f.defs:
            if d.kind == "type":
                full = f"{f.php_namespace}\\{d.name}" if f.php_namespace else d.name
                fqn[full.lower()].add(f.rel)
                short[d.name.lower()].add(f.rel)
    unresolved: Dict[str, Set[str]] = defaultdict(set)
    for f in files:
        if f.ext != ".php":
            continue
        for use in f.php_uses:
            hits = fqn.get(use.lower()) or set()
            if not hits:
                hits = short.get(use.split("\\")[-1].lower()) or set()
            if len(hits) == 1:
                (t,) = hits
                if t != f.rel:
                    f.add_dep(t, use, verb="uses")
            elif not hits and "\\" in use:
                unresolved[use].add(f.rel)
    return unresolved


def _resolve_html_refs(files: List[_FileInfo], index: _PathIndex) -> None:
    for info in files:
        for ref in info.html_refs:
            r = ref.split("?")[0].split("#")[0]
            if not r:
                continue
            here = posixpath.dirname(info.rel)
            if r.startswith("/"):  # project root, else the HTML file's folder (e.g. a Vite root)
                cands = [_norm_rel(r.lstrip("/")), _norm_rel(posixpath.join(here, r.lstrip("/")))]
            else:
                cands = [_norm_rel(posixpath.join(here, r))]
            hit = next((h for h in (index.get(c) for c in cands if c) if h), None)
            if hit and hit != info.rel:
                info.add_dep(hit, ref, verb="references")


# ---------------------------------------------------------------------------
# Call resolution
# ---------------------------------------------------------------------------
#: (caller file, caller scope key, callee file, callee key). Keys: "f:name", "c:Class",
#: "m:Class.method", "mod" (module code, caller only).
Relation = Tuple[str, str, str, str]


class _PyCallResolver:
    """Resolves the raw call chains recorded by `_analyze_python_source` (see the
    module docstring for exactly what is resolved)."""

    def __init__(self, files: List[_FileInfo], rels: Set[str]) -> None:
        self.py = {f.rel: f for f in files if f.ext == ".py" and f.method == "ast"}
        self.rels = rels
        self.package_dirs = _py_package_dirs(rels)
        self._roots: Dict[Tuple[str, ...], List[Tuple[str, ...]]] = {}
        self._mod_cache: Dict[Tuple, Optional[str]] = {}
        self._static_cache: Dict[Tuple[str, Chain], Optional[Tuple[str, str]]] = {}

    def module_file(self, info: _FileInfo, level: int, dotted: str) -> Optional[str]:
        dparts = info.dir_parts
        key = (dparts, level, dotted)
        if key in self._mod_cache:
            return self._mod_cache[key]
        hit: Optional[str] = None
        if level > 0:
            up = level - 1
            if up <= len(dparts):
                hit = _py_lookup(dparts[: len(dparts) - up], dotted, self.rels)
        elif dotted:
            roots = self._roots.get(dparts)
            if roots is None:
                roots = self._roots[dparts] = _py_roots(dparts, self.package_dirs)
            for root in roots:
                hit = _py_lookup(root, dotted, self.rels)
                if hit:
                    break
        self._mod_cache[key] = hit
        return hit

    # --- lookups inside resolved modules ---------------------------------
    def def_chain(self, t: str, name: str, rest: Chain) -> Optional[Tuple[str, str]]:
        kind = self.py[t].py_top.get(name)
        if kind == "function":
            return (t, "f:" + name) if not rest else None
        if kind == "class":
            if not rest:
                return (t, "c:" + name)
            if len(rest) == 1:
                return self.find_method(t, name, rest[0])
        return None

    def in_module(self, t: str, rest: Chain, depth: int = 0) -> Optional[Tuple[str, str]]:
        if not rest or t not in self.py or depth > 3:
            return None
        info = self.py[t]
        name = rest[0]
        if name in info.py_top:
            return self.def_chain(t, name, rest[1:])
        if name in info.py_binds:
            return self.via_binding(info, name, rest[1:], depth + 1)
        if t.endswith("__init__.py"):
            pkg = posixpath.dirname(t)
            prefix = f"{pkg}/" if pkg else ""
            for cand in (f"{prefix}{name}.py", f"{prefix}{name}/__init__.py"):
                if cand in self.py:
                    return self.in_module(cand, rest[1:], depth + 1)
        return None

    def via_binding(self, info: _FileInfo, name: str, rest: Chain, depth: int = 0) -> Optional[Tuple[str, str]]:
        b = info.py_binds.get(name)
        if b is None or depth > 3:
            return None
        if b[0] == "mod":
            _, dotted, is_alias = b
            full = tuple(dotted.split(".")) + rest if is_alias else (name,) + rest
            for k in range(len(full) - 1, 0, -1):
                t = self.module_file(info, 0, ".".join(full[:k]))
                if t:
                    return self.in_module(t, full[k:], depth)
            return None
        _, level, module, imported = b
        sub = f"{module}.{imported}" if module else imported
        t = self.module_file(info, level, sub)
        if t:
            return self.in_module(t, rest, depth) if rest else None
        if not module and not level:
            return None
        t = self.module_file(info, level, module)
        if t is None or t not in self.py:
            return None
        return self.in_module(t, (imported,) + rest, depth)

    def static(self, info: _FileInfo, chain: Chain) -> Optional[Tuple[str, str]]:
        key = (info.rel, chain)
        if key in self._static_cache:
            return self._static_cache[key]
        head = chain[0]
        if head in info.py_top:
            res = self.def_chain(info.rel, head, chain[1:])
        elif head in info.py_binds:
            res = self.via_binding(info, head, chain[1:])
        else:
            res = None
        self._static_cache[key] = res
        return res

    def find_method(self, t: str, cls: str, meth: str, depth: int = 0) -> Optional[Tuple[str, str]]:
        info = self.py.get(t)
        if info is None or depth > 6:
            return None
        if meth in info.py_class_methods.get(cls, ()):
            return (t, f"m:{cls}.{meth}")
        for base in info.py_class_bases.get(cls, ()):
            r = self.static(info, base)
            if r and r[1].startswith("c:"):
                hit = self.find_method(r[0], r[1][2:], meth, depth + 1)
                if hit:
                    return hit
        return None

    def resolve(self, info: _FileInfo, scope: str, chain: Chain) -> Optional[Tuple[str, str]]:
        head = chain[0]
        me = info.py_self.get(scope)
        if me and head == me[0]:
            return self.find_method(info.rel, me[1], chain[1]) if len(chain) == 2 else None
        locals_ = info.py_locals.get(scope)
        if locals_ and head in locals_:
            ctor = locals_[head]
            if ctor is None or len(chain) != 2:
                return None
            r = self.static(info, ctor)
            if r and r[1].startswith("c:"):
                return self.find_method(r[0], r[1][2:], chain[1])
            return None
        return self.static(info, chain)

    def base_class(self, info: _FileInfo, base: Chain) -> Optional[Tuple[str, str]]:
        r = self.static(info, base)
        return r if r and r[1].startswith("c:") else None


def _resolve_calls(files: List[_FileInfo], rels: Set[str]) -> Tuple[Set[Relation], Dict[Tuple[str, str], List[Tuple[str, str]]]]:
    """Returns (relations, resolved Python base classes {(file, class): [(file, "c:Base")]})."""
    relations: Set[Relation] = set()
    py = _PyCallResolver(files, rels)
    bases: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for info in py.py.values():
        for scope, chains in info.py_scopes.items():
            for chain in chains:
                r = py.resolve(info, scope, chain)
                if r is not None and (r[0], r[1]) != (info.rel, scope):
                    relations.add((info.rel, scope, r[0], r[1]))
        for cls, base_chains in info.py_class_bases.items():
            hits = [h for h in (py.base_class(info, b) for b in base_chains) if h]
            if hits:
                bases[(info.rel, cls)] = hits

    by_rel = {f.rel: f for f in files}
    # JS/TS: names imported from resolved project files
    for info in files:
        if not info.js_binds or not (info.name_calls or info.member_calls):
            continue
        local_target: Dict[str, Tuple[str, str]] = {}
        for loc, imp, spec in info.js_binds:
            t = info.spec_hits.get(spec)
            if t and t in by_rel and by_rel[t].ext in _JS_EXT_SET:
                local_target.setdefault(loc, (t, imp))
        if not local_target:
            continue
        for scope, names in info.name_calls.items():
            for loc in names:
                hit = local_target.get(loc)
                if not hit or hit[1] == "*":
                    continue
                t, imp = hit
                tdefs = by_rel[t].defs
                if imp == "default":
                    d = next((d for d in tdefs if d.is_default and (d.kind == "function" or d.keyword == "class")), None)
                else:
                    d = next((d for d in tdefs if d.name == imp and (d.kind == "function" or d.keyword == "class")), None)
                if d is not None and (t, d.key) != (info.rel, scope):
                    relations.add((info.rel, scope, t, d.key))
        for scope, members in info.member_calls.items():
            for ns, member in members:
                hit = local_target.get(ns)
                if not hit or hit[1] != "*":
                    continue
                t = hit[0]
                d = next((d for d in by_rel[t].defs if d.name == member and (d.kind == "function" or d.keyword == "class")), None)
                if d is not None:
                    relations.add((info.rel, scope, t, d.key))

    # PHP: functions declared in exactly one project file that the caller includes directly
    php_funcs: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for f in files:
        if f.ext == ".php":
            for d in f.defs:
                if d.kind == "function":
                    php_funcs[d.name.lower()].append((f.rel, d.name))
    for info in files:
        if info.ext != ".php" or not info.name_calls:
            continue
        included = {t for t, verb in info.dep_verbs.items() if verb == "includes"}
        if not included:
            continue
        for scope, names in info.name_calls.items():
            for low in names:
                entries = php_funcs.get(low)
                if entries and len(entries) == 1 and entries[0][0] in included:
                    relations.add((info.rel, scope, entries[0][0], "f:" + entries[0][1]))
    return relations, bases


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
_PREFERRED_PY_ENTRIES = ("main.py", "__main__.py", "app.py", "manage.py")
MAX_ENTRY_POINTS = 3


def _detect_entries(files: List[_FileInfo], by_rel: Dict[str, _FileInfo], packages: List[Tuple[str, dict]], index: _PathIndex) -> List[Tuple[str, str]]:
    """[(rel, evidence)] for where the program starts (see the module docstring)."""
    out: List[Tuple[str, str]] = []

    def add(rel: str, evidence: str) -> None:
        for i, (r, ev) in enumerate(out):
            if r == rel:
                out[i] = (r, f"{ev}; {evidence}")
                return
        if len(out) < MAX_ENTRY_POINTS:
            out.append((rel, evidence))

    cands = [f for f in files if f.ext == ".py" and f.layer != "testing" and (f.has_main_block or f.name == "__main__.py")]
    preferred = [f for f in cands if f.name.lower() in _PREFERRED_PY_ENTRIES]
    unimported = [f for f in cands if not f.importers and f not in preferred]
    chosen = preferred + unimported
    if not chosen:
        chosen = cands
    chosen.sort(key=lambda f: (f.name.lower() not in _PREFERRED_PY_ENTRIES, bool(f.importers), len(f.dir_parts), f.rel))
    for f in chosen:
        bits = []
        if f.has_main_block:
            bits.append('has an `if __name__ == "__main__"` block')
        if f.name == "__main__.py":
            bits.append("is a package `__main__.py` (run with `python -m`)")
        if f.importers:
            bits.append(f"is also imported by {len(f.importers)} project file(s)")
        else:
            bits.append("is not imported by other project files")
        add(f.rel, " and ".join(bits))

    for pkg_dir, data in packages:
        here = f"{pkg_dir}/x" if pkg_dir else "x"
        targets: List[Tuple[str, str]] = []
        main = data.get("main")
        if isinstance(main, str):
            targets.append((main, "is the `main` of package.json"))
        bin_ = data.get("bin")
        if isinstance(bin_, str):
            targets.append((bin_, "is the `bin` of package.json"))
        elif isinstance(bin_, dict):
            targets.extend((v, f"is the `bin` `{k}` of package.json") for k, v in bin_.items() if isinstance(v, str))
        scripts = data.get("scripts") or {}
        start = scripts.get("start") if isinstance(scripts, dict) else None
        if isinstance(start, str):
            for tok in start.split():
                if not tok.startswith("-") and re.search(r"\.(?:m?[jt]s|c[jt]s|jsx|tsx)$|^[./]", tok):
                    targets.append((tok, f"is run by `scripts.start` (`{_clip(start, 40)}`)"))
        for path, ev in targets:
            spec = path if path.startswith((".", "/")) else "./" + path
            hit = _resolve_js_spec(here, spec, index.rels, index)
            if hit:
                where = f" in `{pkg_dir}/`" if pkg_dir else ""
                add(hit, ev + where)

    for rel in ("index.php", "public/index.php"):
        hit = index.get(rel)
        if hit:
            add(hit, "is `index.php` at the " + ("project root" if rel == "index.php" else "`public/` web root") + " (the page the web server opens first)")
            break
    pkg_dirs = {d for d, _ in packages}
    for f in files:
        if f.name.lower() in ("index.html", "index.htm") and (not f.dir_parts or "/".join(f.dir_parts) in pkg_dirs):
            add(f.rel, "is `index.html` " + ("at the project root" if not f.dir_parts else "next to a package.json") + " (static frontend entry)")
    return out


# ---------------------------------------------------------------------------
# External libraries
# ---------------------------------------------------------------------------
@dataclass
class _External:
    name: str
    ecosystem: str  # "Python", "npm", "Composer"
    importers: Set[str] = field(default_factory=set)
    declared: str = ""  # manifest rel path
    spec: str = ""  # declared version spec
    note: str = ""


#: Python import name -> distribution names (only to decide whether it is declared).
_PY_IMPORT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "yaml": ("pyyaml",), "cv2": ("opencv-python", "opencv-python-headless", "opencv-contrib-python"),
    "pil": ("pillow",), "sklearn": ("scikit-learn",), "bs4": ("beautifulsoup4",), "dotenv": ("python-dotenv",),
    "whisper": ("openai-whisper",), "skimage": ("scikit-image",), "dateutil": ("python-dateutil",),
    "jwt": ("pyjwt",), "serial": ("pyserial",), "usb": ("pyusb",), "magic": ("python-magic",),
    "telegram": ("python-telegram-bot",), "docx": ("python-docx",), "pptx": ("python-pptx",),
    "speech_recognition": ("speechrecognition",), "win32api": ("pywin32",), "win32con": ("pywin32",),
    "pythoncom": ("pywin32",), "fitz": ("pymupdf",), "google": ("google-api-python-client", "protobuf"),
    "attr": ("attrs",), "crypto": ("pycryptodome",), "git": ("gitpython",), "zmq": ("pyzmq",),
}


def _collect_externals(files: List[_FileInfo], manifests: "_Manifests", php_unresolved: Dict[str, Set[str]]) -> List[_External]:
    found: Dict[Tuple[str, str], _External] = {}
    project_names: Set[str] = set()
    for f in files:
        if f.ext == ".py":
            parts = f.rel.split("/")
            project_names.update(parts[:-1])
            project_names.add(posixpath.splitext(parts[-1])[0])
    for f in files:
        if f.ext == ".py":
            for n in f.external_imports:
                if n in _STDLIB_MODULES or n in project_names or not n.isidentifier():
                    continue
                ext = found.setdefault(("py", n), _External(n, "Python"))
                ext.importers.add(f.rel)
        elif f.ext in _JS_EXT_SET:
            for n in f.external_imports:
                ext = found.setdefault(("npm", n), _External(n, "npm"))
                ext.importers.add(f.rel)
    for ext in found.values():
        if ext.ecosystem == "Python":
            keys = [_norm_dep(ext.name)] + [_norm_dep(a) for a in _PY_IMPORT_ALIASES.get(ext.name.lower(), ())]
            for k in keys:
                if k in manifests.py_specs:
                    ext.spec, ext.declared = manifests.py_specs[k]
                    break
        else:
            if ext.name in manifests.npm_specs:
                ext.spec, ext.declared = manifests.npm_specs[ext.name]
    # PHP / Composer
    for name, (spec, where) in manifests.composer.items():
        found[("composer", name)] = _External(name, "Composer", declared=where, spec=spec)
    for use, users in php_unresolved.items():
        pkg = ""
        for prefix, package in manifests.psr4:
            if use.lower().startswith(prefix.lower()):
                pkg = package
                break
        if pkg:
            ext = found.setdefault(("composer", pkg), _External(pkg, "Composer"))
            ext.importers.update(users)
        elif manifests.composer:
            root_ns = use.split("\\")[0] + "\\"
            ext = found.setdefault(("composer-ns", root_ns), _External(root_ns, "Composer"))
            ext.importers.update(users)
            ext.note = "namespace used in `use` statements; package not identified (no vendor/composer/autoload_psr4.php)"
    return sorted(found.values(), key=lambda e: (-len(e.importers), e.name.lower()))


# ---------------------------------------------------------------------------
# Library table: capabilities (purpose evidence) + tech detection (dependency names)
# ---------------------------------------------------------------------------
class _Tech(NamedTuple):
    """One library family. `capability` is the plain phrase used in the purpose evidence
    ("speech-to-text"); `arch_type` / `description` (may be empty) feed the core plan's
    "Type" / "Technologies" lines. `exact` are package or import names (normalised with
    `_norm_dep` when matched), `prefixes` match scoped / family names."""

    capability: str
    arch_type: str
    description: str
    exact: Tuple[str, ...]
    prefixes: Tuple[str, ...] = ()


#: The single library table. Order = order of the capability line.
TECH_FINGERPRINTS: Dict[str, _Tech] = {
    "speech_to_text": _Tech("speech-to-text", "", "", ("faster-whisper", "openai-whisper", "whisper", "whisperx", "vosk", "speechrecognition", "speech-recognition", "pywhispercpp", "deepgram-sdk", "assemblyai")),
    "audio_input": _Tech("microphone/audio input", "", "", ("sounddevice", "pyaudio", "soundcard", "pvrecorder", "pvporcupine", "node-record-lpcm16", "mic")),
    "text_to_speech": _Tech("text-to-speech", "", "", ("pyttsx3", "edge-tts", "gtts", "elevenlabs", "say")),
    "ollama": _Tech("LLM / AI model", "Local LLM Integration", "Ollama local AI inference", ("ollama",)),
    "langchain": _Tech("LLM / AI model", "LLM & Agent Workflow", "LangChain language model chains and autonomous agents", ("langchain",), ("langchain-", "@langchain/")),
    "llm": _Tech("LLM / AI model", "", "", ("openai", "anthropic", "transformers", "llama-cpp", "llama-cpp-python", "google-generativeai", "google-genai", "groq", "mistralai", "cohere", "@anthropic-ai/sdk", "@google/generative-ai", "@google/genai", "litellm", "gpt4all")),
    "keyboard": _Tech("keyboard/mouse automation", "", "", ("keyboard", "pynput", "pyautogui", "mouse", "robotjs", "@nut-tree/nut-js", "pydirectinput", "autoit")),
    "system": _Tech("system/process monitoring", "", "", ("psutil", "systeminformation", "gputil", "wmi", "pywin32", "win32api", "win32con")),
    "discord": _Tech("Discord bot", "Discord Bot Service", "Discord API bot and community automation", ("discord", "discord-py", "discord.js", "py-cord", "nextcord", "discord.js-selfbot-v13", "disnake", "eris")),
    "telethon": _Tech("Telegram bot", "Telegram Bot & Automation", "Telethon MTProto API Telegram automation", ("telethon",)),
    "aiogram": _Tech("Telegram bot", "Telegram Bot Service", "Aiogram asynchronous bot framework", ("aiogram",)),
    "telegram": _Tech("Telegram bot", "", "", ("python-telegram-bot", "telegram", "telegraf", "node-telegram-bot-api", "grammy", "pytelegrambotapi", "telebot")),
    "whatsapp": _Tech("WhatsApp bot", "", "", ("whatsapp-web.js", "@whiskeysockets/baileys", "baileys", "venom-bot", "@wppconnect-team/wppconnect")),
    "fastapi": _Tech("web server / API", "High-Performance REST API", "FastAPI asynchronous microservice / API backend", ("fastapi",)),
    "flask": _Tech("web server / API", "Web Service / REST API", "Flask micro web framework service", ("flask",)),
    "django": _Tech("web server / API", "Full-Featured Web Platform", "Django database-driven web framework", ("django",)),
    "express": _Tech("web server / API", "Node.js Backend & API Service", "Express.js RESTful API infrastructure", ("express",)),
    "nestjs": _Tech("web server / API", "Enterprise TypeScript Backend", "NestJS modular backend service", (), ("@nestjs/",)),
    "spring": _Tech("web server / API", "Enterprise Java Service", "Spring Boot backend service", (), ("org.springframework",)),
    "web_server": _Tech("web server / API", "", "", ("koa", "fastify", "@hapi/hapi", "hapi", "sanic", "tornado", "bottle", "starlette", "laravel/framework", "slim/slim", "symfony/framework-bundle")),
    "realtime": _Tech("real-time communication", "", "", ("socket.io", "socket.io-client", "websockets", "ws", "python-socketio", "pusher", "pusher-js")),
    "sqlalchemy": _Tech("database", "Database ORM & Model Layer", "SQLAlchemy relational database abstraction", ("sqlalchemy",)),
    "prisma": _Tech("database", "Database ORM Layer", "Prisma type-safe database client", ("prisma",), ("@prisma/",)),
    "mongoose": _Tech("database", "NoSQL / MongoDB Data Layer", "Mongoose document object modeling", ("mongoose",)),
    "sqlite3": _Tech("database", "Embedded Database Store", "SQLite local file-based storage", ("sqlite3", "better-sqlite3")),
    "database": _Tech("database", "", "", ("mysql2", "mysql", "pg", "psycopg2", "psycopg2-binary", "psycopg", "pymysql", "mysql-connector-python", "mongodb", "pymongo", "redis", "sequelize", "typeorm", "knex", "peewee", "aiosqlite", "firebase-admin", "@supabase/supabase-js", "supabase", "PDO", "mysqli")),
    "react": _Tech("web frontend", "Web Frontend & Component UI", "React-based web interface", ("react", "react-dom")),
    "next": _Tech("web frontend", "Full-Stack Web Portal", "Next.js web application", ("next",)),
    "vue": _Tech("web frontend", "Web Frontend UI", "Vue.js single-page application", ("vue", "nuxt")),
    "svelte": _Tech("web frontend", "Reactive Web UI", "Svelte compiler-based web interface", ("svelte",), ("@sveltejs/",)),
    "frontend": _Tech("web frontend", "", "", ("preact", "solid-js", "@angular/core", "jquery", "alpinejs")),
    "tailwindcss": _Tech("", "Styling & Design System", "TailwindCSS utility-first styling", ("tailwindcss",)),
    "pyside6": _Tech("desktop GUI", "Desktop Graphics & UI Application", "PySide6 (Qt6) desktop graphical user interface", ("pyside6", "pyside6-essentials", "pyside6-addons")),
    "pyqt6": _Tech("desktop GUI", "Desktop Graphics & UI Application", "PyQt6 desktop windowing system", ("pyqt6",)),
    "pyqt5": _Tech("desktop GUI", "Desktop GUI Application", "PyQt5 desktop windowing system", ("pyqt5",)),
    "tkinter": _Tech("desktop GUI", "Desktop GUI Application", "Tkinter interface library", ("tkinter",)),
    "electron": _Tech("desktop GUI", "Cross-Platform Desktop Application", "Electron-based desktop system", ("electron",)),
    "tauri": _Tech("desktop GUI", "Lightweight Desktop Application", "Rust & Webview Tauri application", ("tauri",), ("@tauri-apps/",)),
    "desktop": _Tech("desktop GUI", "", "", ("customtkinter", "kivy", "wx", "wxpython", "pyqt4", "pyside2", "flet", "dearpygui", "eel", "pywebview")),
    "game": _Tech("game / multimedia", "", "", ("pygame", "pyglet", "arcade", "phaser", "pixi.js", "ursina", "panda3d")),
    "graphics3d": _Tech("3D graphics", "", "", ("three", "babylonjs", "@babylonjs/core")),
    "http_client": _Tech("HTTP client", "", "", ("requests", "axios", "httpx", "node-fetch", "aiohttp", "got", "urllib3", "undici", "guzzlehttp/guzzle")),
    "selenium": _Tech("browser automation / scraping", "Web Automation & Testing", "Selenium browser automation and scraping", ("selenium",)),
    "playwright": _Tech("browser automation / scraping", "End-to-End Web Automation", "Playwright browser automation", ("playwright",), ("@playwright/",)),
    "scrapy": _Tech("browser automation / scraping", "Web Crawling Engine", "Scrapy data extraction framework", ("scrapy",)),
    "scraping": _Tech("browser automation / scraping", "", "", ("puppeteer", "puppeteer-core", "beautifulsoup4", "bs4", "cheerio", "undetected-chromedriver", "pyppeteer", "mechanize")),
    "pandas": _Tech("data processing / spreadsheets", "Data Analysis & Processing Engine", "Pandas tabular data analytics", ("pandas",)),
    "scipy": _Tech("data processing / spreadsheets", "Scientific Computing Engine", "SciPy numerical analysis", ("scipy",)),
    "data": _Tech("data processing / spreadsheets", "", "", ("numpy", "openpyxl", "xlsxwriter", "polars", "xlrd", "exceljs", "xlsx", "gspread")),
    "charts": _Tech("charts / plotting", "", "", ("matplotlib", "plotly", "seaborn", "chart.js", "recharts", "d3")),
    "torch": _Tech("machine learning", "Deep Learning & AI", "PyTorch neural network training and inference", ("torch",)),
    "tensorflow": _Tech("machine learning", "AI & Machine Learning Model", "TensorFlow machine learning system", ("tensorflow", "tensorflow-gpu")),
    "ml": _Tech("machine learning", "", "", ("onnxruntime", "scikit-learn", "sklearn", "keras", "xgboost", "lightgbm", "@tensorflow/tfjs")),
    "vision": _Tech("computer vision / camera", "", "", ("opencv-python", "opencv-python-headless", "cv2", "mediapipe", "ultralytics", "face-recognition", "face_recognition")),
    "images": _Tech("image processing", "", "", ("pillow", "pil", "sharp", "jimp", "imageio", "wand")),
    "audio": _Tech("audio processing / playback", "", "", ("pydub", "playsound", "simpleaudio", "librosa", "soundfile", "pygame.mixer", "howler")),
    "pdf": _Tech("PDF handling", "", "", ("pypdf", "pypdf2", "reportlab", "pdfkit", "fpdf", "fpdf2", "pdf-lib", "pymupdf", "fitz", "pdfplumber", "pdfkit", "tcpdf", "dompdf/dompdf")),
    "documents": _Tech("Word/PowerPoint documents", "", "", ("python-docx", "docx", "python-pptx", "pptx", "docxtpl")),
    "email": _Tech("sending email", "", "", ("smtplib", "nodemailer", "yagmail", "phpmailer/phpmailer", "sendgrid", "@sendgrid/mail")),
    "payments": _Tech("payments", "", "", ("stripe", "iyzipay", "paypalrestsdk", "@paypal/checkout-server-sdk", "iyzico/iyzipay-php")),
    "scheduling": _Tech("scheduled tasks", "", "", ("schedule", "apscheduler", "node-cron", "cron", "celery")),
    "cli": _Tech("command-line interface", "", "", ("argparse", "click", "typer", "commander", "yargs", "inquirer", "rich", "fire")),
    "crypto_trading": _Tech("crypto / exchange trading", "", "", ("ccxt", "python-binance", "binance", "web3", "ethers")),
    "maps": _Tech("maps / geolocation", "", "", ("leaflet", "geopy", "folium", "mapbox-gl", "@react-google-maps/api")),
    "notifications": _Tech("desktop notifications", "", "", ("plyer", "win10toast", "node-notifier")),
    "translation": _Tech("translation", "", "", ("googletrans", "deep-translator", "deepl", "argostranslate")),
}
#: Standard-library entries: only seen as imports (never declared as dependencies).
_IMPORT_ONLY_TECH = {"tkinter", "sqlite3"}
_STDLIB_CAPABILITY_NAMES = {"tkinter", "sqlite3", "argparse", "smtplib"}


def _norm_dep(name: str) -> str:
    n = name.strip().lower()
    if n.startswith("@") or n.startswith("org."):
        return n
    return re.sub(r"[_.]+", "-", n) if n not in {"discord.js"} else n


_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _parse_requirement_names(lines: Iterable[str]) -> List[str]:
    names = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http:", "https:", "file:")):
            continue
        m = _REQ_NAME_RE.match(line)
        if m:
            names.append(m.group(1))
    return names


def _toml_load(text: str) -> Optional[dict]:
    if _tomllib is None:
        return None
    try:
        return _tomllib.loads(text)
    except Exception:
        return None


def _toml_section_keys(text: str, section_re: str) -> List[str]:
    """Regex fallback: keys of TOML tables whose header matches `section_re`."""
    keys: List[str] = []
    current = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            current = s.strip("[] ")
            continue
        if current and re.fullmatch(section_re, current):
            m = re.match(r"^\"?([A-Za-z0-9][A-Za-z0-9._-]*)\"?\s*=", s)
            if m:
                keys.append(m.group(1))
    return keys


def _toml_array_strings(text: str, key: str) -> List[str]:
    """Regex fallback: string items of `key = [ ... ]` arrays (multi-line)."""
    out: List[str] = []
    for m in re.finditer(rf"(?m)^\s*{re.escape(key)}\s*=\s*\[(.*?)\]", text, flags=re.DOTALL):
        out.extend(re.findall(r"[\"']([^\"']+)[\"']", m.group(1)))
    return out


def _pyproject_dependency_names(text: str) -> List[str]:
    names: List[str] = []
    data = _toml_load(text)
    if data is not None:
        project = data.get("project") or {}
        reqs = list(project.get("dependencies") or [])
        for group in (project.get("optional-dependencies") or {}).values():
            reqs.extend(group or [])
        for group in (data.get("dependency-groups") or {}).values():
            reqs.extend(x for x in (group or []) if isinstance(x, str))
        names.extend(_parse_requirement_names(str(r) for r in reqs))
        poetry = (data.get("tool") or {}).get("poetry") or {}
        tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
        for grp in (poetry.get("group") or {}).values():
            tables.append((grp or {}).get("dependencies") or {})
        for tbl in tables:
            names.extend(k for k in tbl.keys() if k.lower() != "python")
        return names
    # Regex fallback (Python 3.10 without tomllib)
    names.extend(_parse_requirement_names(_toml_array_strings(text, "dependencies")))
    for m in re.finditer(r"(?ms)^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)", text):
        names.extend(_parse_requirement_names(re.findall(r"[\"']([^\"']+)[\"']", m.group(1))))
    for k in _toml_section_keys(text, r"tool\.poetry\.(?:dev-)?dependencies|tool\.poetry\.group\.[\w-]+\.dependencies"):
        if k.lower() != "python":
            names.append(k)
    return names


def _pipfile_dependency_names(text: str) -> List[str]:
    data = _toml_load(text)
    if data is not None:
        return list((data.get("packages") or {}).keys()) + list((data.get("dev-packages") or {}).keys())
    return _toml_section_keys(text, r"packages|dev-packages")

_REQ_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;#]*)")


def _parse_requirement_specs(lines: Iterable[str]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http:", "https:", "file:")):
            continue
        m = _REQ_SPEC_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


@dataclass
class _Manifests:
    """Declared dependencies of a project (manifests in the root and its direct
    sub-directories)."""

    names: Set[str] = field(default_factory=set)  # normalised names of every ecosystem
    description: str = ""
    py_specs: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # norm name -> (spec, manifest rel)
    npm_specs: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # name -> (spec, manifest rel)
    composer: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # package -> (spec, manifest rel)
    psr4: List[Tuple[str, str]] = field(default_factory=list)  # (namespace prefix, package) from vendor autoload
    packages: List[Tuple[str, dict]] = field(default_factory=list)  # (dir rel, parsed package.json)
    #: (manifest rel, description) of the project root, in priority order: package.json,
    #: pyproject.toml, composer.json, Cargo.toml, setup.py, setup.cfg
    own_descriptions: List[Tuple[str, str]] = field(default_factory=list)


def _collect_manifests(folder: Path, extra_package_dirs: Iterable[str] = ()) -> _Manifests:
    """Reads package.json, requirements*.txt, pyproject.toml, Pipfile, setup.py,
    composer.json (+ vendor/composer/autoload_psr4.php), pom.xml and build.gradle in
    `folder` and its direct (non-ignored) sub-directories, plus package.json files in
    `extra_package_dirs` (relative paths found by the walk)."""
    man = _Manifests()
    dirs: List[Tuple[str, Path]] = [("", folder)]
    try:
        for child in sorted(folder.iterdir()):
            if child.is_dir() and not child.is_symlink() and not _should_skip_dir(child.name, []) and not _is_backup_dir(child.name):
                dirs.append((child.name, child))
    except OSError:
        pass
    known = {d for d, _ in dirs}
    for rel_dir in sorted(set(extra_package_dirs) - known):
        dirs.append((rel_dir, folder / rel_dir))

    def read(p: Path) -> str:
        try:
            if p.is_file() and p.stat().st_size <= MAX_PARSE_BYTES:
                return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        return ""

    def rel_of(d: str, name: str) -> str:
        return f"{d}/{name}" if d else name

    def add_py(name: str, spec: str, where: str) -> None:
        man.names.add(_norm_dep(name))
        man.py_specs.setdefault(_norm_dep(name), (spec, where))

    root_desc: Dict[str, str] = {}
    for d, path in dirs:
        top_level = "/" not in d
        pkg_text = read(path / "package.json")
        if pkg_text:
            try:
                pkg = json.loads(pkg_text)
            except Exception:
                pkg = None
            if isinstance(pkg, dict):
                man.packages.append((d, pkg))
                if d == "" and str(pkg.get("description", "")).strip():
                    man.description = str(pkg["description"]).strip()
                    root_desc["package.json"] = man.description
                for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    deps = pkg.get(key) or {}
                    if isinstance(deps, dict):
                        for n, v in deps.items():
                            man.names.add(_norm_dep(n))
                            man.npm_specs.setdefault(n, (str(v), rel_of(d, "package.json")))
        if not top_level:
            continue
        try:
            req_files = sorted(path.glob("requirements*.txt"))
        except OSError:
            req_files = []
        for rf in req_files:
            for n, spec in _parse_requirement_specs(read(rf).splitlines()):
                add_py(n, spec, rel_of(d, rf.name))
        py_text = read(path / "pyproject.toml")
        if py_text:
            data = _toml_load(py_text)
            if d == "":
                desc = ""
                if data is not None:
                    desc = str((data.get("project") or {}).get("description") or ((data.get("tool") or {}).get("poetry") or {}).get("description") or "")
                else:
                    m = re.search(r"^description\s*=\s*([\"'])(.+?)\1\s*$", py_text, re.MULTILINE)
                    desc = m.group(2) if m else ""
                if desc.strip():
                    root_desc["pyproject.toml"] = desc.strip()
            if data is not None:
                project = data.get("project") or {}
                reqs = list(project.get("dependencies") or [])
                for group in (project.get("optional-dependencies") or {}).values():
                    reqs.extend(group or [])
                for n, spec in _parse_requirement_specs(str(r) for r in reqs):
                    add_py(n, spec, rel_of(d, "pyproject.toml"))
                poetry = (data.get("tool") or {}).get("poetry") or {}
                tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
                for grp in (poetry.get("group") or {}).values():
                    tables.append((grp or {}).get("dependencies") or {})
                for tbl in tables:
                    for k, v in tbl.items():
                        if k.lower() != "python":
                            add_py(k, v if isinstance(v, str) else str((v or {}).get("version", "")) if isinstance(v, dict) else "", rel_of(d, "pyproject.toml"))
            for n in _pyproject_dependency_names(py_text):
                if _norm_dep(n) not in man.py_specs:
                    add_py(n, "", rel_of(d, "pyproject.toml"))
        pip_text = read(path / "Pipfile")
        if pip_text:
            for n in _pipfile_dependency_names(pip_text):
                add_py(n, "", rel_of(d, "Pipfile"))
        setup_text = read(path / "setup.py")
        if setup_text and d == "":
            m = re.search(r"(?<![\w])description\s*=\s*([\"'])(.+?)\1", setup_text)
            if m and m.group(2).strip():
                root_desc["setup.py"] = m.group(2).strip()
        if d == "":
            cfg_text = read(path / "setup.cfg")
            m = re.search(r"^description\s*=\s*(.+?)\s*$", cfg_text, re.MULTILINE) if cfg_text else None
            if m and m.group(1).strip():
                root_desc["setup.cfg"] = m.group(1).strip()
            cargo_text = read(path / "Cargo.toml")
            m = re.search(r"^description\s*=\s*([\"'])(.+?)\1\s*$", cargo_text, re.MULTILINE) if cargo_text else None
            if m and m.group(2).strip():
                root_desc["Cargo.toml"] = m.group(2).strip()
        if setup_text:
            for n, spec in _parse_requirement_specs(_toml_array_strings(setup_text, "install_requires")):
                add_py(n, spec, rel_of(d, "setup.py"))
        comp_text = read(path / "composer.json")
        if comp_text:
            try:
                comp = json.loads(comp_text)
            except Exception:
                comp = None
            if isinstance(comp, dict):
                if d == "" and str(comp.get("description") or "").strip():
                    root_desc["composer.json"] = str(comp["description"]).strip()
                for key in ("require", "require-dev"):
                    req = comp.get(key) or {}
                    if isinstance(req, dict):
                        for n, v in req.items():
                            if n.lower() == "php" or n.lower().startswith(("ext-", "lib-")):
                                continue
                            man.names.add(_norm_dep(n))
                            man.composer.setdefault(n, (str(v), rel_of(d, "composer.json")))
            psr4 = read(path / "vendor" / "composer" / "autoload_psr4.php")
            for m in re.finditer(r"'((?:[^'\\]|\\\\)+)'\s*=>\s*array\(\s*\$vendorDir\s*\.\s*'/([^/']+/[^/']+)", psr4):
                man.psr4.append((m.group(1).replace("\\\\", "\\"), m.group(2)))
        pom = read(path / "pom.xml")
        if pom:
            man.names.update(_norm_dep(n) for n in re.findall(r"<groupId>\s*([\w.\-]+)\s*</groupId>", pom))
        for gradle in ("build.gradle", "build.gradle.kts"):
            g_text = read(path / gradle)
            if g_text:
                man.names.update(
                    _norm_dep(m.split(":")[0]) for m in re.findall(r"[\"']([\w.\-]+:[\w.\-]+(?::[^\"']*)?)[\"']", g_text)
                )
    man.psr4.sort(key=lambda x: -len(x[0]))
    order = ("package.json", "pyproject.toml", "composer.json", "Cargo.toml", "setup.py", "setup.cfg")
    man.own_descriptions = [(k, root_desc[k]) for k in order if k in root_desc]
    return man


def _collect_dependency_names(folder: Path) -> Tuple[Set[str], str]:
    """Declared dependency names (normalised) and the package description."""
    man = _collect_manifests(folder)
    return {n for n in man.names if n}, man.description


def _match_fingerprints(dep_names: Set[str], imported: Set[str]) -> List[Tuple[str, str, str]]:
    """Returns (key, matched name, how) for table entries with a technology description,
    matched exactly or by prefix against declared dependency names (stdlib-only entries
    such as tkinter / sqlite3 also by import)."""
    hits: List[Tuple[str, str, str]] = []
    for key, tech in TECH_FINGERPRINTS.items():
        if not tech.description:
            continue
        exact_n = {_norm_dep(e) for e in tech.exact}
        matched = sorted(dep_names & exact_n)
        if not matched and tech.prefixes:
            matched = sorted(n for n in dep_names if any(n.startswith(p) for p in tech.prefixes))
        if matched:
            hits.append((key, matched[0], "declared dependency"))
            continue
        if key in _IMPORT_ONLY_TECH and key in imported:
            hits.append((key, key, "imported in source"))
    return hits


def _detect_project_technologies(folder: Path, imported: Optional[Set[str]] = None, manifests: Optional[_Manifests] = None) -> Tuple[List[str], List[str], str]:
    """Detects technologies from declared dependency NAMES (package.json, requirements*.txt,
    pyproject.toml, Pipfile, setup.py, composer.json, pom.xml, build.gradle) plus
    stdlib-only imports. Returns (architectural types, tech stack lines, package description)."""
    man = manifests if manifests is not None else _collect_manifests(folder)
    dep_names = {n for n in man.names if n}
    detected_types: List[str] = []
    tech_stack: List[str] = []
    for key, matched, how in _match_fingerprints(dep_names, imported or set()):
        tech = TECH_FINGERPRINTS[key]
        t_type, t_desc = tech.arch_type, tech.description
        if t_type not in detected_types:
            detected_types.append(t_type)
        tech_stack.append(f"{t_desc} (`{matched}`, {how})")
    if (folder / "Cargo.toml").exists():
        detected_types.append("Rust Systems & Performance Application")
        tech_stack.append("Cargo package manager and Rust compiler (`Cargo.toml`)")
    if (folder / "go.mod").exists():
        detected_types.append("Go Microservice & Concurrent System")
        tech_stack.append("Go runtime and module system (`go.mod`)")
    return detected_types, tech_stack, man.description


# ---------------------------------------------------------------------------
# Module (planet) grouping
# ---------------------------------------------------------------------------
def _compute_expanded_dirs(files: List[_FileInfo]) -> Set[Tuple[str, ...]]:
    """Directories whose children become modules. The root is always expanded; a child
    holding >= DOMINANT_DIR_SHARE of all files and having sub-directories with files is
    expanded too (repeatedly, up to MAX_DESCEND_DEPTH)."""
    expanded: Set[Tuple[str, ...]] = {()}
    total = len(files)
    current: Tuple[str, ...] = ()
    for _ in range(MAX_DESCEND_DEPTH):
        child_counts: Counter = Counter()
        for f in files:
            dp = f.dir_parts
            if len(dp) > len(current) and dp[: len(current)] == current:
                child_counts[dp[len(current)]] += 1
        if not child_counts:
            break
        child, count = child_counts.most_common(1)[0]
        cand = current + (child,)
        if count < DOMINANT_DIR_SHARE * total:
            break
        has_subdirs = any(len(f.dir_parts) > len(cand) and f.dir_parts[: len(cand)] == cand for f in files)
        if not has_subdirs:
            break
        expanded.add(cand)
        current = cand
    return expanded


def _module_key(info: _FileInfo, expanded: Set[Tuple[str, ...]]) -> Tuple[str, ...]:
    dp = info.dir_parts
    for k in range(len(dp)):
        if dp[: k + 1] not in expanded:
            return dp[: k + 1]
    return dp  # directly inside an expanded directory (or the root)


def _majority_layer(files: Iterable[_FileInfo]) -> str:
    counts = Counter(f.layer for f in files)
    if not counts:
        return "core"
    best = max(counts.values())
    for layer in _LAYER_PRIORITY:
        if counts.get(layer) == best:
            return layer
    return counts.most_common(1)[0][0]


@dataclass
class _Module:
    key: str
    title: str
    files: List[_FileInfo]
    kind: str  # "dir", "root", "other"
    folded: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def layer(self) -> str:
        return _majority_layer(self.files)


def _layout_is_flat(files: List[_FileInfo]) -> bool:
    """Flat layout: fewer than two directories with files, or more than
    `FLAT_ROOT_SHARE` of the files directly in the root (or the dominant directory)."""
    if not files:
        return True
    expanded = _compute_expanded_dirs(files)
    by_key: Counter = Counter(_module_key(f, expanded) for f in files)
    dir_keys = [k for k in by_key if k and k not in expanded]
    root_count = sum(c for k, c in by_key.items() if k in expanded)
    return len(dir_keys) < 2 or root_count > FLAT_ROOT_SHARE * len(files)


def _group_modules(files: List[_FileInfo], max_planets: int = MAX_PLANETS) -> Tuple[List[_Module], str]:
    """Directory modules for a non-flat project. Returns (modules, grouping description)."""
    expanded = _compute_expanded_dirs(files)
    by_key: Dict[Tuple[str, ...], List[_FileInfo]] = defaultdict(list)
    for f in files:
        by_key[_module_key(f, expanded)].append(f)

    modules: List[_Module] = []
    for k, f_list in by_key.items():
        if not k:
            modules.append(_Module(key="", title=ROOT_PLANET_TITLE, files=f_list, kind="root"))
        else:
            modules.append(_Module(key="/".join(k), title="/".join(k), files=f_list, kind="dir"))
    modules.sort(key=lambda m: (m.kind != "root", -len(m.files), m.title.lower()))

    if len(modules) > max_planets:
        keep = sorted(modules, key=lambda m: -len(m.files))[: max_planets - 1]
        keep_keys = {m.key for m in keep}
        rest = [m for m in modules if m.key not in keep_keys]
        other = _Module(
            key="__other__",
            title=OTHER_PLANET_TITLE,
            files=[f for m in rest for f in m.files],
            kind="other",
            folded=[(m.title, len(m.files)) for m in rest],
        )
        modules = [m for m in modules if m.key in keep_keys] + [other]

    deepest = max(expanded, key=len)
    if deepest:
        desc = (
            f"Modules are the project's real directories. `{'/'.join(deepest)}/` holds most of the code, "
            f"so its sub-directories are shown as separate modules."
        )
    else:
        desc = "Modules are the project's real top-level directories; root-level files form 'Project Root'."
    return modules, desc


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def _significance(f: _FileInfo) -> float:
    return len(f.importers) * 5 + f.def_count * 2 + min(f.lines, 3000) / 60.0 + (2 if f.doc else 0)


def _plural(n: int, word: str, plural: str = "") -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _capped(items: Sequence[str], cap: int, sep: str = ", ") -> str:
    items = list(items)
    text = sep.join(items[:cap])
    if len(items) > cap:
        text += f"{sep if sep.strip() == '' else ' '}+{len(items) - cap} more"
    return text


def _bullets(title: str, items: Sequence[str], cap: int = 12) -> str:
    items = list(items)
    lines = [f"{title}:"] + [f"• {x}" for x in items[:cap]]
    if len(items) > cap:
        lines.append(f"• +{len(items) - cap} more")
    return "\n".join(lines)


def _source_label(f: _FileInfo) -> str:
    return {
        "ast": "Python ast",
        "regex": "regex pattern matching, may miss or misidentify definitions",
        "markdown": "Markdown headings",
    }.get(f.method, "not parsed")


def _how_defs_found(f: _FileInfo) -> str:
    if f.method == "ast":
        return "found with the Python `ast` module"
    if f.method == "regex":
        return "found by regular-expression pattern matching (may miss or misidentify definitions)"
    return "not parsed"


def _file_head(f: _FileInfo) -> str:
    size = _plural(f.lines, "line") if f.lines else f"{f.size:,} bytes"
    return f"{f.name} — {f.lang} file, {size}"


def _about_line(f: _FileInfo) -> str:
    if f.ext == ".py" and f.doc:
        return "About: " + _clip(f.doc, 300)
    if f.lead_comment:
        return "About (first comment): " + _clip(f.lead_comment, 300)
    if f.doc:
        return "About (leading comment): " + _clip(f.doc, 300)
    return ""


def _defines_line(f: _FileInfo) -> str:
    if f.defs:
        funcs = sum(1 for d in f.defs if d.kind == "function")
        types = len(f.defs) - funcs
        word = "class" if f.method == "ast" else "type"
        return f"Defines: {_plural(funcs, 'function')}, {_plural(types, word, word + ('es' if word == 'class' else 's'))} — {_source_label(f)}"
    if f.top_names:
        return f"Defines: no functions or classes; {_plural(len(f.top_names), 'top-level name')} — Python ast"
    if f.method in ("ast", "regex"):
        return f"Defines: nothing found — {_source_label(f)}"
    return ""


def _external_split(f: _FileInfo) -> Tuple[List[str], List[str]]:
    """(standard-library / built-in modules, third-party package names) imported by `f`."""
    if f.ext == ".py":
        std = sorted(n for n in f.external_imports if n in _STDLIB_MODULES)
        third = sorted(n for n in f.external_imports if n not in _STDLIB_MODULES)
        return std, third
    return sorted(f.builtin_imports), sorted(f.external_imports)


def _key_display(key: str) -> str:
    if key == "mod":
        return "(module code)"
    kind, _, name = key.partition(":")
    return f"{name}()"


def _dep_label(f: _FileInfo, target: str, called: Sequence[str] = ()) -> str:
    """`imports islemler — calls gorev_yurut(), odak_modu()` style label."""
    mod, names = f.dep_labels.get(target, (target, []))
    verb = f.dep_verbs.get(target, "imports")
    names = [n for n in names if n and n != "*"]
    if called:
        label = f"{verb} {mod} — calls " + ", ".join(called[:3]) + (f" +{len(called) - 3}" if len(called) > 3 else "")
    else:
        label = f"{verb} {mod}"
        if names:
            label += f" ({', '.join(names[:4])}{', …' if len(names) > 4 else ''})"
    return label


def _import_label(module_text: str, names: Sequence[str]) -> str:
    label = f"imports {module_text}"
    names = [n for n in names if n and n != "*"]
    if names:
        label += f" ({', '.join(names[:4])}{', …' if len(names) > 4 else ''})"
    return label


def _short_file_label(rel: str) -> str:
    base = posixpath.basename(rel)
    parent = posixpath.basename(posixpath.dirname(rel))
    if parent and (base.startswith("__init__") or base.startswith("index.")):
        return f"{parent}/{base}"
    return base


def _edge_label(pairs: List[Tuple[str, str]]) -> str:
    uniq = list(dict.fromkeys(pairs))
    n = len(uniq)
    shown = list(dict.fromkeys(f"{_short_file_label(a)} → {_short_file_label(b)}" for a, b in uniq))
    examples = ", ".join(shown[:2])
    more = ", …" if n > 2 else ""
    return f"{n} import{'s' if n != 1 else ''} ({examples}{more})"


def _sat_title(f: _FileInfo, mod: "_Module", dup_names: Set[str]) -> str:
    if f.name not in dup_names:
        return f.name
    if mod.kind == "dir" and f.rel.startswith(mod.key + "/"):
        return f.rel[len(mod.key) + 1 :]
    return f.rel


def _normalized_path_key(path: str) -> str:
    """Identity key for a folder: normalised absolute path (case-folded on Windows)."""
    if not path:
        return ""
    try:
        p = os.path.abspath(os.path.expanduser(path))
    except Exception:
        p = path
    return os.path.normcase(os.path.normpath(p))


def _keep_only_real_links(system: SolarSystem) -> None:
    """Runs the model's layout/tour rebuild but discards any links it invents.

    `SolarSystem.rebuild_links_and_tour()` (core/models.py) adds default layer links
    whenever a system has no links; imported maps must only show real imports."""
    real = list(system.links)
    system.rebuild_links_and_tour()
    system.links = real


def _layer_color(layer: str) -> str:
    return LAYER_DEFINITIONS.get(layer, ("", "#4EA8DE"))[1]


def _layer_title(layer: str) -> str:
    return LAYER_DEFINITIONS.get(layer, (layer, ""))[0]


def _list_block(title: str, items: Sequence[str], cap: int = 12) -> str:
    return "\n" + _bullets(title, items, cap)


# ---------------------------------------------------------------------------
# Analysis result shared by both map modes
# ---------------------------------------------------------------------------
@dataclass
class _Analysis:
    folder: Path
    project_name: str
    files: List[_FileInfo]
    by_rel: Dict[str, _FileInfo]
    relations: Set[Relation]
    py_bases: Dict[Tuple[str, str], List[Tuple[str, str]]]
    entries: List[Tuple[str, str]]
    externals: List[_External]
    extras: _WalkExtras
    alias_hits: int = 0
    case_hits: int = 0
    _pair_calls: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)
    _mod_callers: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        pairs: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        for s, sk, d, k in self.relations:
            pairs[(s, d)].add(_key_display(k))
            if sk == "mod":
                self._mod_callers.add(s)
        self._pair_calls = dict(pairs)
        self._out: Dict[str, List[str]] = defaultdict(list)
        self._in: Dict[str, List[str]] = defaultdict(list)
        for s, d in self._pair_calls:
            if s != d:
                self._out[s].append(d)
                self._in[d].append(s)

    def entry_evidence(self, rel: str) -> str:
        return next((ev for r, ev in self.entries if r == rel), "")

    def calls_between(self, src: str, dst: str) -> List[str]:
        return sorted(self._pair_calls.get((src, dst), ()))

    def has_module_calls(self, rel: str) -> bool:
        return rel in self._mod_callers

    def callees_of(self, rel: str) -> List[str]:
        return sorted(self._out.get(rel, ()))

    def callers_of(self, rel: str) -> List[str]:
        return sorted(self._in.get(rel, ()))


class _SatRefs:
    """Maps (file, definition key) to "<module title>::<sub-component title>" references
    and collects the resolved call lists for each sub-component."""

    def __init__(self) -> None:
        self.by_key: Dict[Tuple[str, str], Tuple[PlanetNode, str]] = {}
        self.file_level: Dict[str, Tuple[PlanetNode, str]] = {}  # file -> file sub-component
        self.calls: Dict[str, Set[str]] = defaultdict(set)
        self.called_by: Dict[str, Set[str]] = defaultdict(set)

    @staticmethod
    def ref(planet: PlanetNode, title: str) -> str:
        return f"{planet.title}::{title}"

    def lookup(self, file: str, key: str) -> Optional[str]:
        hit = self.by_key.get((file, key))
        if hit is None and key.startswith("m:"):
            hit = self.by_key.get((file, "c:" + key[2:].split(".")[0]))
        if hit is None:
            hit = self.file_level.get(file)
        return self.ref(*hit) if hit else None

    def fill(self, relations: Set[Relation]) -> None:
        for sf, sk, tf, tk in relations:
            a, b = self.lookup(sf, sk), self.lookup(tf, tk)
            if a and b and a != b:
                self.calls[a].add(b)
                self.called_by[b].add(a)


def _ref_lines(refs: _SatRefs, ref: str, text_cap: int = 8) -> Tuple[List[str], List[str], List[str]]:
    calls = sorted(refs.calls.get(ref, ()))
    called_by = sorted(refs.called_by.get(ref, ()))
    lines = []
    if calls:
        lines.append("Calls: " + _capped(calls, text_cap))
    if called_by:
        lines.append("Called by: " + _capped(called_by, text_cap))
    return calls[:CALL_REF_CAP], called_by[:CALL_REF_CAP], lines


# ---------------------------------------------------------------------------
# Sub-component content
# ---------------------------------------------------------------------------
def _method_entry(m: _Method) -> str:
    marks = [f"@{x}" for x in m.flags if x in ("staticmethod", "classmethod", "property", "cached_property", "abstractmethod")]
    marks += [x for x in m.flags if x in ("static", "abstract", "get", "set")]
    sig = m.signature or f"{m.name}()"
    if m.vis in ("private", "protected", "dunder") and not sig.startswith(m.vis):
        sig = f"{sig} [{m.vis}]"
    return " ".join(marks + [sig])


def _def_content(d: _Def, f: _FileInfo, an: _Analysis, call_lines: List[str], method: Optional[_Method] = None) -> str:
    lines: List[str] = []
    if method is not None:
        lines.append(f"Signature: {_method_entry(method)}")
        if method.doc:
            lines.append(f"About: {method.doc}")
        lines.append(f"Method of: class {d.name} ({'public' if method.vis == 'public' else method.vis})")
        lines.extend(call_lines)
        lines.append(f"Location: {f.rel}, line {method.lineno}")
        lines.append(f"Source: {_source_label(f)}")
        return "\n".join(lines)
    if d.signature:
        lines.append(f"Signature: {d.signature}")
    if d.class_notes and d.kind != "function":
        lines.append("Kind: " + ", ".join(d.class_notes))
    if d.doc:
        lines.append(("About: " if d.doc_source == "docstring" else "Comment above: ") + d.doc)
    if d.decorators:
        lines.append("Decorators: " + _capped(d.decorators, 4))
    if d.exported is not None:
        lines.append("Exported: " + ("yes (default export)" if d.is_default else "yes" if d.exported else "no"))
    if d.kind != "function":
        if d.bases:
            resolved = {}
            for tf, tk in an.py_bases.get((f.rel, d.name), []):
                resolved[tk[2:]] = (tf, tk)
            parts = []
            for b in d.bases:
                short = b.split("[")[0].split(".")[-1]
                hit = resolved.get(short)
                if hit:
                    parts.append(f"{posixpath.basename(hit[0])}::class {short}")
                else:
                    parts.append(f"{b} (not in this project)" if f.method == "ast" else b)
            lines.append(("Inherits from: " if f.method == "ast" else "Extends: ") + ", ".join(parts))
        if d.implements:
            lines.append("Implements: " + ", ".join(d.implements))
        if d.fields:
            label = "Members" if "Enum" in d.class_notes else "Fields"
            lines.append(f"{label} ({len(d.fields)}): " + _capped(d.fields, 12))
        if d.method_infos:
            order = {"public": 0, "protected": 1, "private": 2, "dunder": 3}
            ms = sorted(d.method_infos, key=lambda m: (order.get(m.vis, 4), m.lineno))
            pub = sum(1 for m in ms if m.vis == "public")
            lines.append(f"Methods ({len(ms)}, {pub} public):")
            lines.extend(f"• {_method_entry(m)}" for m in ms[:12])
            if len(ms) > 12:
                lines.append(f"• +{len(ms) - 12} more")
    lines.extend(call_lines)
    lines.append(f"Location: {f.rel}, line {d.lineno}")
    lines.append(f"Source: {_source_label(f)}")
    return "\n".join(lines)


def _file_content(f: _FileInfo, an: _Analysis, call_lines: List[str], title_of: Dict[str, str]) -> str:
    lines = [_file_head(f)]
    about = _about_line(f)
    if f.ext == ".md":
        about = "About: " + _clip(f.doc, 300) if f.doc else ""
    if about:
        lines.append(about)
    if f.defs:
        lines.append("Defines: " + _capped([d.title for d in f.defs], 8))
    elif f.headings:
        lines.append("Sections: " + _capped(f.headings, 8))
    if f.deps:
        lines.append("Uses: " + _capped([title_of.get(d, d) for d in sorted(f.deps)], 8))
    if f.importers:
        lines.append("Used by: " + _capped([title_of.get(i, i) for i in sorted(f.importers)], 8))
    out_calls = an.callees_of(f.rel)
    in_calls = an.callers_of(f.rel)
    if out_calls:
        lines.append("Calls into: " + _capped([f"{title_of.get(t, t)} ({_capped(an.calls_between(f.rel, t), 3)})" for t in out_calls], 6, "; "))
    if in_calls:
        lines.append("Called from: " + _capped([f"{title_of.get(s, s)} ({_capped(an.calls_between(s, f.rel), 3)})" for s in in_calls], 6, "; "))
    # `call_lines` would repeat "Calls into" / "Called from"; the references stay in calls / called_by.
    std, third = _external_split(f)
    if third:
        lines.append("External: " + _capped(third, 10))
    ev = an.entry_evidence(f.rel)
    if ev:
        lines.append(f"Entry point: {ev}")
    if f.note:
        lines.append(f"Note: {f.note}")
    lines.append(f"Path: {f.rel}")
    lines.append(f"Source: {_source_label(f)}")
    return "\n".join(lines)


def _names_satellite_content(f: _FileInfo, cap: int = 60) -> str:
    lines = [f"Top-level names of {f.name} (constants / settings); values are not evaluated."]
    lines += [f"• {n} (line {ln})" for n, ln in f.top_names[:cap]]
    if len(f.top_names) > cap:
        lines.append(f"• +{len(f.top_names) - cap} more")
    lines.append("Source: Python ast")
    return "\n".join(lines)


def _uses_lines(f: _FileInfo, an: _Analysis, title_of: Dict[str, str]) -> List[str]:
    out = []
    if f.deps:
        out.append(_bullets("Uses", [f"{title_of.get(d, d)}: {_dep_label(f, d, an.calls_between(f.rel, d))}" for d in sorted(f.deps)]))
    if f.importers:
        items = []
        for i in sorted(f.importers):
            called = an.calls_between(i, f.rel)
            items.append(title_of.get(i, i) + (f": calls {_capped(called, 3)}" if called else ""))
        out.append(_bullets("Used by", items))
    return out


# ---------------------------------------------------------------------------
# Core plan
# ---------------------------------------------------------------------------
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


def _docstring_description(doc: str) -> str:
    """The first paragraph of a module docstring that is a description once noise lines
    (license, encoding, metadata, "generated" notes, ...) are removed ("" if none)."""
    for para in re.split(r"\n\s*\n", (doc or "").strip()):
        cleaned = clean_header_text(para)
        if cleaned:
            return cleaned
    return ""


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
    elif intro:
        info.purpose = _first_sentences(intro)
        info.purpose_from = f"the README intro in {rel}"
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
        # The project's own words win; a pending AI suggestion is no longer needed.
        existing.purpose_suggestion = existing.purpose_suggestion_key = ""
    elif existing.purpose_source == "ai" and existing.purpose.strip():
        return
    else:
        existing.purpose, existing.purpose_source = "", ""


def _core_plan_text(an: _Analysis, manifests: _Manifests, skipped_large: int, grouping_desc: str, link_desc: str,
                    purpose: Optional[_PurposeInfo] = None) -> str:
    """The generated core plan. It opens with the `Purpose:` line (the project's own
    words when found, otherwise "Not written yet" plus the capabilities implied by
    libraries), then entry points, libraries and the map facts. README text other than
    the intro is never copied here."""
    folder, files = an.folder, an.files
    purpose = purpose if purpose is not None else _collect_purpose(an, manifests)

    all_external: Set[str] = set()
    for f in files:
        all_external |= f.external_imports
    detected_types, tech_stack, _pkg_summary = _detect_project_technologies(folder, all_external, manifests)

    lines: List[str] = [
        purpose_line(purpose.purpose, "readme" if purpose.purpose else "", purpose.evidence, purpose.purpose_from)
    ]
    lines.append("")
    if an.entries:
        for rel, ev in an.entries:
            lines.append(f"Entry point: {rel} — {ev}")
    else:
        lines.append("Entry point: none found (no `__main__` block, package.json main/bin/start, index.php or index.html)")
    if an.externals:
        lines.append(f"External libraries: {_capped([e.name for e in an.externals], 8)} — see '{EXTERNAL_PLANET_TITLE}'")
    if detected_types:
        lines.append("Type (from declared dependencies): " + ", ".join(detected_types))
    if tech_stack:
        lines.append(_bullets("Technologies", tech_stack, 8))
    lines.append("")
    lines.append(f"Map: {grouping_desc}")
    lines.append(f"Links: {link_desc}")
    n_rel = len(an.relations)
    call_line = f"Calls: {_plural(n_rel, 'resolved call relation')} between definitions (Python via ast; JS/TS and PHP name-based, limited to imported / included names)"
    lines.append(call_line)
    if an.extras.calls_disabled:
        lines.append(f"Call analysis skipped: the project has more than {CALL_ANALYSIS_MAX_CODE_FILES:,} code files")
    elif an.extras.calls_skipped_large:
        lines.append(f"Call analysis skipped for {_plural(an.extras.calls_skipped_large, 'file')} larger than {CALL_ANALYSIS_MAX_FILE_BYTES // 1000} KB")
    if an.alias_hits:
        lines.append(f"Path aliases: {_plural(an.alias_hits, 'import')} resolved through tsconfig/jsconfig paths or the Vite `@/` alias")
    if an.case_hits:
        lines.append(f"Case-insensitive matches: {_plural(an.case_hits, 'path')} matched only when ignoring letter case (Windows-style)")

    lang_counts = Counter(f.lang for f in files)
    total_lines = sum(f.lines for f in files)
    code_n = sum(1 for f in files if f.is_code)
    lines.append(f"Size: {_plural(code_n, 'code file')}, {_plural(len(files) - code_n, 'Markdown file')}, ~{total_lines:,} lines")
    lines.append("Languages: " + ", ".join(f"{k} ({v})" for k, v in lang_counts.most_common()))
    methods = Counter(f.method or "unparsed" for f in files)
    analysis = (
        f"Analysis: {methods.get('ast', 0)} Python files parsed with ast, "
        f"{methods.get('regex', 0)} files scanned with pattern matching, {methods.get('markdown', 0)} Markdown files"
    )
    if skipped_large:
        analysis += f", {skipped_large} counted but not parsed (size/count limits)"
    lines.append(analysis)
    lines.extend(purpose.facts)
    lines.append(f"Folder: {folder}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# External Libraries module (both modes)
# ---------------------------------------------------------------------------
def _add_external_planet(system: SolarSystem, an: _Analysis, title_of: Dict[str, str]) -> Optional[PlanetNode]:
    exts = an.externals
    if not exts:
        return None
    declared = sum(1 for e in exts if e.declared)
    eco = Counter(e.ecosystem for e in exts)
    summary = [
        f"{EXTERNAL_PLANET_TITLE} — {_plural(len(exts), 'third-party package')} used by the code",
        "Ecosystems: " + ", ".join(f"{k} ({v})" for k, v in eco.most_common()),
        f"Declared in a manifest: {declared} · imported but undeclared: {len(exts) - declared}",
        "Manifests read: requirements*.txt, pyproject.toml, Pipfile, setup.py, package.json, composer.json (root and direct sub-folders).",
        "Sorted by number of importing files.",
        "Found from import / use statements (Python ast; JS/TS and PHP pattern matching); standard library and Node built-ins excluded.",
        "No links are drawn to this module; each file lists its own external imports.",
    ]
    shown = exts[:SATELLITE_CAP]
    if len(exts) > len(shown):
        summary.append(f"+{len(exts) - len(shown)} more packages not shown as sub-components: " + _capped([e.name for e in exts[len(shown):]], 20))
    planet = system.add_planet(title=EXTERNAL_PLANET_TITLE, summary="\n".join(summary), color=_layer_color("integration"), layer="integration")
    planet.kind = "external"
    for e in shown:
        lines = [f"{e.name} — {e.ecosystem} package"]
        if e.importers:
            lines.append(f"Imported by ({len(e.importers)}): " + _capped([title_of.get(r, r) for r in sorted(e.importers)], 8))
        elif e.ecosystem == "Composer":
            lines.append("Used by: no `use` statement mapped to it (Composer packages are autoloaded)")
        if e.declared:
            lines.append(f"Declared in: {e.declared}" + (f" ({e.spec})" if e.spec else " (no version spec)"))
        else:
            lines.append("Declared: no (imported but not in any manifest)")
        if e.note:
            lines.append(f"Note: {e.note}")
        sat = planet.add_satellite(title=e.name, content="\n".join(lines), visual_tag="code")
        sat.kind = "package"
        sat.signature = e.spec
    return planet


# ---------------------------------------------------------------------------
# File-level map (small or flat projects)
# ---------------------------------------------------------------------------
def _file_planet_summary(f: _FileInfo, an: _Analysis, title_of: Dict[str, str], hidden: List[str], expanded: List[str], class_count: int) -> str:
    lines = [_file_head(f)]
    about = _about_line(f)
    if about:
        lines.append(about)
    defines = _defines_line(f)
    if defines:
        lines.append(defines)
    if expanded:
        lines.append("Methods shown as sub-components: " + ", ".join(expanded))
    elif class_count and f.method == "ast":
        lines.append(
            f"Methods are listed inside the class sub-components (shown separately only for classes with at most "
            f"{METHOD_SATELLITE_MAX_PUBLIC} public methods while the module has at most {METHOD_SATELLITES_MAX_TOTAL} sub-components)"
        )
    if hidden:
        lines.append(f"Not shown as sub-components: +{len(hidden)} more definitions ({_capped(hidden, 20)})")
    lines.extend(_uses_lines(f, an, title_of))
    std, third = _external_split(f)
    if third:
        lines.append("External: " + _capped(third, 20))
    if std:
        lines.append(("Standard library: " if f.ext == ".py" else "Built-in modules: ") + _capped(std, 20))
    if f.unresolved_includes:
        lines.append(f"Unresolved includes: {f.unresolved_includes} (dynamic paths or files not found)")
    ev = an.entry_evidence(f.rel)
    if ev:
        lines.append(f"Entry point: {ev}")
    if f.note:
        lines.append(f"Note: {f.note}")
    if f.calls_note:
        lines.append(f"Note: {f.calls_note}")
    lines.append(f"Layer: {_layer_title(f.layer)} (from the file path)")
    return "\n".join(lines)


def _file_significance(f: _FileInfo) -> float:
    return _significance(f) + len(f.deps) * 3


def _plan_file_satellites(f: _FileInfo, an: _Analysis) -> Tuple[List[Tuple[str, str, str, Optional[_Def], Optional[_Method]]], List[str], List[str]]:
    """[(key, title, kind, def, method)], hidden definition titles, expanded classes."""
    has_mod_calls = an.has_module_calls(f.rel)
    defs = f.defs[: SATELLITE_CAP - (1 if has_mod_calls else 0)]
    hidden = [d.title for d in f.defs[len(defs) :]]
    base_count = len(defs) + (1 if has_mod_calls else 0) + (1 if (not f.defs and f.top_names) else 0)
    expanded: Set[str] = set()
    if f.method == "ast":
        count = base_count
        for d in defs:
            if d.kind != "class":
                continue
            pub = [m for m in d.method_infos if m.vis == "public"]
            if 0 < len(pub) <= METHOD_SATELLITE_MAX_PUBLIC and count + len(pub) <= METHOD_SATELLITES_MAX_TOTAL:
                expanded.add(d.name)
                count += len(pub)
    plan: List[Tuple[str, str, str, Optional[_Def], Optional[_Method]]] = []
    for d in defs:
        kind = "function" if d.kind == "function" else "class"
        plan.append((d.key, d.title, kind, d, None))
        if d.name in expanded:
            for m in d.method_infos:
                if m.vis == "public":
                    plan.append((f"m:{d.name}.{m.name}", f"{d.name}.{m.name}()", "method", d, m))
    if not f.defs and f.top_names:
        plan.append(("names", f"Top-level names ({len(f.top_names)})", "constants", None, None))
    if has_mod_calls:
        plan.append(("mod", MODULE_CODE_TITLE, "function", None, None))
    exp_titles = [f"{d.name} ({sum(1 for m in d.method_infos if m.vis == 'public')})" for d in defs if d.name in expanded]
    return plan, hidden, exp_titles


def _build_file_level(system: SolarSystem, an: _Analysis, data_files: List[Tuple[str, int]]) -> Tuple[str, str]:
    """Fills `system` with one module per code file. Returns (grouping, link) descriptions."""
    folder, project_name = an.folder, an.project_name
    code = [f for f in an.files if f.is_code]
    docs = [f for f in an.files if not f.is_code]
    has_docs_planet = bool(docs or data_files)
    slots = MAX_PLANETS - (1 if has_docs_planet else 0) - (1 if an.externals else 0)
    entry_rels = {r for r, _ in an.entries}
    if len(code) > slots:
        ranked = sorted(code, key=lambda f: (f.rel not in entry_rels, -_file_significance(f), f.rel))
        as_planets = sorted(ranked[: slots - 1], key=lambda f: f.rel)
        overflow = sorted(ranked[slots - 1 :], key=lambda f: f.rel)
    else:
        as_planets, overflow = sorted(code, key=lambda f: f.rel), []

    name_counts = Counter(f.name for f in code)
    title_of: Dict[str, str] = {f.rel: (f.name if name_counts[f.name] == 1 else f.rel) for f in code}
    planet_of: Dict[str, PlanetNode] = {}
    refs = _SatRefs()
    pending: List[Tuple] = []  # (satellite, kind, payload...) filled after call refs are known

    for f in as_planets:
        plan, hidden, expanded = _plan_file_satellites(f, an)
        class_count = sum(1 for d in f.defs if d.kind != "function")
        planet = system.add_planet(
            title=title_of[f.rel],
            summary=_file_planet_summary(f, an, title_of, hidden, expanded, class_count),
            color=_layer_color(f.layer),
            layer=f.layer,
        )
        planet.kind = "file"
        planet.file_path = str(folder / f.rel)
        planet.is_entry = f.rel in entry_rels
        planet_of[f.rel] = planet
        for key, title, kind, d, m in plan:
            sat = planet.add_satellite(title=title, content="", visual_tag="code", file_path=str(folder / f.rel))
            sat.kind = kind
            if m is not None:
                sat.signature, sat.line = _method_entry(m), m.lineno
            elif d is not None:
                sat.signature, sat.line = d.signature, d.lineno
            if key != "names":
                refs.by_key[(f.rel, key)] = (planet, title)
            pending.append((sat, planet, f, key, d, m))

    if overflow:
        layer = _majority_layer(overflow)
        shown = sorted(overflow, key=lambda f: (-_file_significance(f), f.rel))[:SATELLITE_CAP]
        summary = [
            f"{OTHER_FILES_PLANET_TITLE} — {_plural(len(overflow), 'code file')} that did not fit as separate modules",
            f"At most {MAX_PLANETS} modules are shown; entry points and the files with the most imports, definitions and lines got their own module.",
            _bullets("Files", [f"{f.rel} ({f.lang}, {_plural(f.lines, 'line')})" for f in overflow], SUMMARY_FILE_LIST_CAP),
        ]
        if len(overflow) > len(shown):
            summary.append(f"+{len(overflow) - len(shown)} more files not shown as sub-components.")
        summary.append("Links to and from these files come from resolved imports and point to this module.")
        other = system.add_planet(title=OTHER_FILES_PLANET_TITLE, summary="\n".join(summary), color=_layer_color(layer), layer=layer)
        other.kind = "layer"
        for f in overflow:
            planet_of[f.rel] = other
        for f in shown:
            sat = other.add_satellite(title=title_of[f.rel], content="", visual_tag="code" if f.defs else "diagram", file_path=str(folder / f.rel))
            sat.kind = "file"
            refs.file_level[f.rel] = (other, title_of[f.rel])
            pending.append((sat, other, f, "file", None, None))

    if has_docs_planet:
        entries: List[Tuple[str, str, str, str]] = []  # (title, content, abs path, kind)
        for f in sorted(docs, key=lambda f: f.rel):
            body = [f"{f.name} — Markdown document, {f.size:,} bytes"]
            if f.doc:
                body.append("About: " + _clip(f.doc, 400))
            if f.headings:
                body.append(_bullets("Headings", f.headings))
            body.append(f"Path: {f.rel}")
            entries.append((f.rel, "\n".join(body), str(folder / f.rel), "document"))
        for rel, size in sorted(data_files):
            entries.append((rel, f"{posixpath.basename(rel)} — config / data file, {size:,} bytes\nListed only; its contents are not parsed.\nPath: {rel}", str(folder / rel), "document"))
        listing = [f"{f.rel} ({f.size:,} bytes)" for f in sorted(docs, key=lambda f: f.rel)] + [f"{r} ({s:,} bytes)" for r, s in sorted(data_files)]
        summary = (
            f"{DOCS_PLANET_TITLE} — {_plural(len(docs), 'Markdown file')}, {_plural(len(data_files), 'config / data file')}\n"
            "Markdown headings are extracted; config/data files are only listed with their size.\n"
            + _bullets("Files", listing, SUMMARY_FILE_LIST_CAP)
        )
        if len(entries) > SATELLITE_CAP:
            summary += f"\n+{len(entries) - SATELLITE_CAP} more files not shown as sub-components."
        dp = system.add_planet(title=DOCS_PLANET_TITLE, summary=summary, color=_layer_color("docs"), layer="docs")
        dp.kind = "layer"
        for title, content, path, kind in entries[:SATELLITE_CAP]:
            sat = dp.add_satellite(title=title, content=content, visual_tag="diagram", file_path=path)
            sat.kind = kind

    _add_external_planet(system, an, title_of)

    refs.fill(an.relations)
    for sat, planet, f, key, d, m in pending:
        calls, called_by, call_lines = _ref_lines(refs, _SatRefs.ref(planet, sat.title))
        sat.calls, sat.called_by = calls, called_by
        if key == "file":
            sat.content = _file_content(f, an, call_lines, title_of)
        elif key == "names":
            sat.content = _names_satellite_content(f)
        elif key == "mod":
            sat.content = "\n".join(
                [f"Module-level code of {f.name} (statements outside functions and classes, incl. any `if __name__ == \"__main__\"` block)"]
                + call_lines + [f"Location: {f.rel}", "Source: " + _source_label(f)]
            )
        else:
            sat.content = _def_content(d, f, an, call_lines, method=m)

    pairs_by_link: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
    resolved = 0
    for f in code:
        for dep in sorted(f.deps):
            resolved += 1
            sp, dp_ = planet_of.get(f.rel), planet_of.get(dep)
            if sp is None or dp_ is None or sp is dp_:
                continue
            pairs_by_link[(sp.id, dp_.id)].append((f.rel, dep))
    by_rel = an.by_rel
    for (sid, tid), pairs in pairs_by_link.items():
        if len(pairs) == 1:
            a, b = pairs[0]
            label = _dep_label(by_rel[a], b, an.calls_between(a, b))
            if planet_of[a].kind != "file" or planet_of[b].kind != "file":
                label = f"{_short_file_label(a)} → {_short_file_label(b)}: {label}"
        else:
            label = _edge_label(pairs)
        system.add_link(sid, tid, label=label, relation_type="depends_on")

    grouping = f"File-level map: every code file is one module ({_plural(len(code), 'code file')}; small or flat project)."
    if overflow:
        grouping += f" {len(overflow)} less significant files are listed in '{OTHER_FILES_PLANET_TITLE}'."
    if has_docs_planet:
        grouping += f" Markdown and config/data files are listed in '{DOCS_PLANET_TITLE}'."
    link_desc = f"{_plural(len(system.links), 'file link')} from {resolved} resolved project-local imports / includes / references"
    return grouping, link_desc


# ---------------------------------------------------------------------------
# Directory map (larger projects with real directories)
# ---------------------------------------------------------------------------
def _module_summary(mod: "_Module", an: _Analysis, shown: List[_FileInfo], deps_out: Dict[str, int], deps_in: Dict[str, int]) -> str:
    n = len(mod.files)
    total_lines = sum(f.lines for f in mod.files)
    if mod.kind == "dir":
        head = f"{mod.key}/ — directory, {_plural(n, 'file')}, ~{total_lines:,} lines"
    elif mod.kind == "root":
        head = f"{ROOT_PLANET_TITLE} — files directly in the project folder, {_plural(n, 'file')}, ~{total_lines:,} lines"
    else:
        head = f"{OTHER_PLANET_TITLE} — smaller directories folded together, {_plural(n, 'file')}, ~{total_lines:,} lines"
    lines = [head]
    langs = Counter(f.lang for f in mod.files)
    lines.append("Languages: " + ", ".join(f"{k} ({v})" for k, v in langs.most_common()))
    for f in mod.files:
        ev = an.entry_evidence(f.rel)
        if ev:
            lines.append(f"Entry point: {f.rel} — {ev}")
    if mod.folded:
        lines.append("Folded: " + _capped([f"{t} ({_plural(c, 'file')})" for t, c in mod.folded], 30))
    if deps_out:
        lines.append(_bullets("Uses", [f"{t} ({_plural(c, 'import')})" for t, c in sorted(deps_out.items(), key=lambda x: -x[1])]))
    if deps_in:
        lines.append(_bullets("Used by", [f"{t} ({_plural(c, 'import')})" for t, c in sorted(deps_in.items(), key=lambda x: -x[1])]))
    third = sorted({x for f in mod.files for x in _external_split(f)[1]})
    if third:
        lines.append("External: " + _capped(third, 20))
    names = sorted(f.rel for f in mod.files)
    lines.append(_bullets(f"Files ({n})", names, SUMMARY_FILE_LIST_CAP))
    hidden = n - len(shown)
    if hidden > 0:
        lines.append(
            f"+{hidden} more files not shown as sub-components (only the {len(shown)} most significant "
            "files by import fan-in, definitions and size are shown)."
        )
    layer = mod.layer
    votes = Counter(f.layer for f in mod.files)
    lines.append(f"Layer: {_layer_title(layer)} ({votes.get(layer, 0)} of {n} files, from path names)")
    methods = Counter(f.method or "unparsed" for f in mod.files)
    how = []
    if methods.get("ast"):
        how.append(f"{_plural(methods['ast'], 'Python file')} parsed with ast")
    if methods.get("regex"):
        how.append(f"{_plural(methods['regex'], 'file')} scanned with regex pattern matching")
    if methods.get("markdown"):
        how.append(_plural(methods["markdown"], "Markdown file"))
    if methods.get("unparsed"):
        how.append(f"{_plural(methods['unparsed'], 'file')} not parsed (too large or unreadable)")
    lines.append("Source: " + "; ".join(how))
    return "\n".join(lines)


def _build_dir_level(system: SolarSystem, an: _Analysis) -> Tuple[str, str]:
    folder, files = an.folder, an.files
    modules, grouping_desc = _group_modules(files, MAX_PLANETS - (1 if an.externals else 0))
    module_of: Dict[str, _Module] = {}
    for m in modules:
        for f in m.files:
            module_of[f.rel] = m

    edge_pairs: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
    resolved_imports = 0
    for f in files:
        for d in sorted(f.deps):
            resolved_imports += 1
            src_m, dst_m = module_of[f.rel], module_of[d]
            if src_m.key != dst_m.key:
                edge_pairs[(src_m.key, dst_m.key)].append((f.rel, d))

    deps_out: Dict[str, Dict[str, int]] = defaultdict(dict)
    deps_in: Dict[str, Dict[str, int]] = defaultdict(dict)
    title_of_mod = {m.key: m.title for m in modules}
    for (s, d), pairs in edge_pairs.items():
        cnt = len(set(pairs))
        deps_out[s][title_of_mod[d]] = cnt
        deps_in[d][title_of_mod[s]] = cnt

    entry_rels = {r for r, _ in an.entries}
    planet_of: Dict[str, PlanetNode] = {}
    refs = _SatRefs()
    pending: List[Tuple] = []
    file_title: Dict[str, str] = {f.rel: f.rel for f in files}
    for mod in modules:
        ranked = sorted(mod.files, key=lambda f: (f.rel not in entry_rels, -_significance(f), f.rel))
        shown = ranked[:SATELLITE_CAP]
        layer = mod.layer
        planet = system.add_planet(
            title=mod.title,
            summary=_module_summary(mod, an, shown, deps_out.get(mod.key, {}), deps_in.get(mod.key, {})),
            color=_layer_color(layer),
            layer=layer,
        )
        if mod.kind == "dir":
            planet.kind, planet.file_path = "dir", str(folder / mod.key)
        elif mod.kind == "root":
            planet.kind, planet.file_path = "dir", str(folder)
        else:
            planet.kind = "layer"
        planet.is_entry = any(f.rel in entry_rels for f in mod.files)
        planet_of[mod.key] = planet
        name_counts = Counter(f.name for f in shown)
        dup_names = {n for n, c in name_counts.items() if c > 1}
        for f in shown:
            title = _sat_title(f, mod, dup_names)
            sat = planet.add_satellite(title=title, content="", visual_tag="code" if (f.classes or f.functions) else "diagram", file_path=str(folder / f.rel))
            sat.kind = "document" if f.ext == ".md" else "file"
            refs.file_level[f.rel] = (planet, title)
            pending.append((sat, planet, f))

    _add_external_planet(system, an, file_title)
    refs.fill(an.relations)
    for sat, planet, f in pending:
        calls, called_by, call_lines = _ref_lines(refs, _SatRefs.ref(planet, sat.title))
        sat.calls, sat.called_by = calls, called_by
        sat.content = _file_content(f, an, call_lines, file_title)

    for (s, d), pairs in sorted(edge_pairs.items(), key=lambda kv: (title_of_mod[kv[0][0]], title_of_mod[kv[0][1]])):
        system.add_link(planet_of[s].id, planet_of[d].id, label=_edge_label(pairs), relation_type="depends_on")
    link_desc = f"{_plural(len(edge_pairs), 'module link')} from {resolved_imports} resolved project-local imports / includes / references"
    return grouping_desc, link_desc


@dataclass
class CodeMapStats:
    """What `build_code_map()` found. `mode` is "file", "dir" or "" (nothing analysable)."""

    mode: str = ""
    files: int = 0  # analysable files (code + Markdown)
    code_files: int = 0
    data_files: int = 0
    lines: int = 0
    planets: int = 0
    links: int = 0
    core_plan: str = ""
    calls: int = 0  # resolved call relations between definitions
    entries: List[str] = field(default_factory=list)  # entry point files (relative paths)
    externals: int = 0  # third-party packages listed in the External Libraries module
    purpose: str = ""  # the project's own words (description / README intro), or ""
    purpose_evidence: str = ""  # assembled code evidence (also written to the system)
    purpose_items: List[str] = field(default_factory=list)  # its labelled lines (for compose_purpose_evidence)
    backup_dirs: List[str] = field(default_factory=list)  # backup-named folders that were skipped
    identity: str = ""  # the identity line (also written to `system.identity`), see identity_line()


def count_code_files(folder: Path, limit: int = 1000) -> int:
    """Counts analysable code files (Markdown excluded) under `folder` with the same
    pruning as the full walk, without reading them; stops at `limit`."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(str(folder), topdown=True, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if not _should_skip_dir(d, filenames) and not _is_backup_dir(d) and not os.path.islink(os.path.join(dirpath, d))
        ]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if not fn.startswith(".") and ext in CODE_EXTENSIONS and ext != ".md":
                count += 1
                if count >= limit:
                    return count
    return count


def build_code_map(folder: Path, system: SolarSystem) -> CodeMapStats:
    """Analyses `folder` and appends the resulting modules (planets), sub-components and
    import links to `system` (expected to have no planets yet). Does not save, does not
    look up or choose the system's identity, and does not set `system.core_plan`; the
    generated core plan text is returned in `CodeMapStats.core_plan`."""
    folder = Path(folder)
    project_name = system.project_name or folder.name or str(folder)
    data_files: List[Tuple[str, int]] = []
    extras = _WalkExtras()
    files, skipped_large = _walk_project(folder, data_files, extras)
    stats = CodeMapStats(files=len(files), code_files=sum(1 for f in files if f.is_code), data_files=len(data_files))
    if not files:
        return stats
    stats.lines = sum(f.lines for f in files)

    rels = {f.rel for f in files}
    by_rel = {f.rel: f for f in files}
    index = _PathIndex(rels)
    package_dirs = [posixpath.dirname(c) for c in extras.configs if posixpath.basename(c).lower() == "package.json"]
    manifests = _collect_manifests(folder, package_dirs)
    aliases = _read_alias_configs(folder, extras.configs)
    src_dirs = {"/".join(r.split("/")[: i + 1]) for r in rels for i, p in enumerate(r.split("/")[:-1]) if p == "src"}

    _resolve_python_imports(files, rels)
    alias_hits = _resolve_js_imports(files, rels, index, aliases, src_dirs)
    _resolve_php_includes(files, index)
    php_unresolved = _resolve_php_uses(files)
    _resolve_html_refs(files, index)
    for f in files:
        for d in f.deps:
            by_rel[d].importers.add(f.rel)

    relations, py_bases = (set(), {}) if extras.calls_disabled else _resolve_calls(files, rels)
    an = _Analysis(
        folder=folder,
        project_name=project_name,
        files=files,
        by_rel=by_rel,
        relations=relations,
        py_bases=py_bases,
        entries=_detect_entries(files, by_rel, manifests.packages, index),
        externals=_collect_externals(files, manifests, php_unresolved),
        extras=extras,
        alias_hits=alias_hits,
        case_hits=index.case_hits,
    )

    if extras.detail:  # == code files <= FILE_LEVEL_MAX_FILES or a flat layout (decided by the walk)
        stats.mode = "file"
        grouping, link_desc = _build_file_level(system, an, data_files)
    else:
        stats.mode = "dir"
        grouping, link_desc = _build_dir_level(system, an)
    _keep_only_real_links(system)
    stats.planets = len(system.planets)
    stats.links = len(system.links)
    stats.calls = len(relations)
    stats.entries = [r for r, _ in an.entries]
    stats.externals = len(an.externals)
    purpose = _collect_purpose(an, manifests)
    stats.purpose, stats.purpose_evidence, stats.purpose_items = purpose.purpose, purpose.evidence, list(purpose.items)
    stats.backup_dirs = list(extras.backup_dirs)
    stats.core_plan = _core_plan_text(an, manifests, skipped_large, grouping, link_desc, purpose)
    stats.identity = system.identity = _analysis_identity(an, manifests)
    # The fresh system gets the deterministic results; refreshes go through merge_purpose().
    system.purpose_evidence = purpose.evidence
    if system.purpose_source != "user":
        if purpose.purpose:
            system.purpose, system.purpose_source = purpose.purpose, "readme"
        elif system.purpose_source != "ai" or not system.purpose.strip():
            system.purpose, system.purpose_source = "", ""
    return stats


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


# ---------------------------------------------------------------------------
# Project identity, refresh and duplicate cleanup (shared with agent_importer)
# ---------------------------------------------------------------------------
_WIN_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)")


def workspace_key(path: str) -> str:
    """Identity key of a folder path. Windows-style paths (`C:\\...`) are compared
    case-insensitively on any OS; POSIX paths are normalised (case-folded on Windows)."""
    p = str(path or "").strip().strip("\"'").strip()
    if not p:
        return ""
    if _WIN_PATH_RE.match(p):
        s = str(PureWindowsPath(p)).replace("İ", "i").replace("I", "i").lower()
        return s.rstrip("\\") if len(s) > 3 else s
    norm = posixpath.normpath(p)
    return os.path.normcase(norm) if os.name == "nt" else norm


def _identity_keys(path: str) -> Set[str]:
    keys = {workspace_key(path)} - {""}
    if path and not (_WIN_PATH_RE.match(path) and os.name != "nt"):
        try:
            p = Path(path)
            if p.exists():
                keys.add(workspace_key(str(p.resolve())))
        except (OSError, ValueError, RuntimeError):
            pass
    return keys


def find_systems_for_folder(store: GalaxyStore, folder: str) -> List[SolarSystem]:
    """All non-manual systems (any source) whose `workspace_path` is the same folder."""
    target = _identity_keys(folder)
    if not target:
        return []
    return [s for s in store.systems if s.source != "manual" and s.workspace_path and _identity_keys(s.workspace_path) & target]


def has_user_content(system: SolarSystem) -> bool:
    """True if anything in `system` was made or edited by the user."""
    if system.is_user_modified or system.notes:
        return True
    if any(l.is_user_modified for l in system.links):
        return True
    return any(p.is_user_modified or any(s.is_user_modified for s in p.satellites) for p in system.planets)


#: Planet kinds that only the importers create. Their `is_user_modified` flag is not
#: proof of a user edit (the canvas auto-layout used to set it when it moved them), so
#: `has_user_made_content` ignores it; a sub-component the user added to them (no
#: `kind`) still counts.
GENERATED_ONLY_PLANET_KINDS = frozenset({"external", "history"})


def has_user_made_content(system: SolarSystem) -> bool:
    """Stricter "did the user make anything here?" test used before deleting a stale
    system: manual systems, user-edited systems, canvas notes, user-drawn links and a
    purpose typed by the user always count; a user-modified module / sub-component
    counts unless it belongs to an importer-only module kind
    (`GENERATED_ONLY_PLANET_KINDS`), where only sub-components without a `kind` (made
    by the user) count. Position changes cannot be told apart from edits, which is why
    those kinds are excluded."""
    if system.source == "manual" or system.is_user_modified or system.notes:
        return True
    if (getattr(system, "purpose_source", "") or "") == "user":
        return True
    if any(l.is_user_modified for l in system.links):
        return True
    for p in system.planets:
        if p.kind in GENERATED_ONLY_PLANET_KINDS:
            if any(s.is_user_modified and not s.kind for s in p.satellites):
                return True
            continue
        if p.is_user_modified or any(s.is_user_modified for s in p.satellites):
            return True
    return False


# ---------------------------------------------------------------------------
# Ignored folders (store setting written by the UI)
# ---------------------------------------------------------------------------
IGNORED_FOLDERS_SETTING = "ignored_folders"

#: Statistics of the most recent scan per source, shared with `connectors.agent_importer`
#: (same dict object). Codebase scans use the key "codebase":
#: {"skipped_ignored": 1, "skipped_ignored_paths": ["C:\\...\\old"]}.
LAST_SCAN_STATS: Dict[str, Dict[str, object]] = {}
_STATS_PATH_CAP = 20


def _key_inside(key: str, base: str) -> bool:
    if not key or not base:
        return False
    if key == base:
        return True
    sep = "\\" if _WIN_PATH_RE.match(base) else "/"
    return key.startswith(base.rstrip("\\/") + sep)


def ignored_folder_keys(store: Optional[GalaxyStore]) -> List[str]:
    """Normalised keys of the `ignored_folders` setting (bad values are ignored)."""
    try:
        raw = store.get_setting(IGNORED_FOLDERS_SETTING, []) if store is not None else []
    except Exception:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    keys: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            keys.extend(sorted(_identity_keys(item.strip())))
    return list(dict.fromkeys(k for k in keys if k))


def is_ignored_folder(store: Optional[GalaxyStore], path, keys: Optional[Sequence[str]] = None) -> bool:
    """True if `path` equals or is inside a folder of the store's `ignored_folders`
    setting (normalised like `workspace_key`; Windows paths case-insensitively)."""
    bases = list(keys) if keys is not None else ignored_folder_keys(store)
    if not bases or not path:
        return False
    targets = _identity_keys(str(path))
    return any(_key_inside(t, b) for t in targets for b in bases)


def count_skipped_ignored(source: str, path: str) -> None:
    """Counts a skipped ignored folder in `LAST_SCAN_STATS[source]`."""
    stats = LAST_SCAN_STATS.setdefault(source, {})
    stats["skipped_ignored"] = int(stats.get("skipped_ignored", 0) or 0) + 1  # type: ignore[arg-type]
    paths = stats.setdefault("skipped_ignored_paths", [])
    if isinstance(paths, list) and path and path not in paths and len(paths) < _STATS_PATH_CAP:
        paths.append(path)


def add_source(system: SolarSystem, source: str) -> None:
    sources = list(dict.fromkeys(([system.source] if system.source else []) + list(system.sources or [])))
    if source and source not in sources:
        sources.append(source)
    system.sources = sources


def _ollama_tail(core_plan: str) -> str:
    if OLLAMA_MARKER in core_plan:
        return "\n\n" + core_plan[core_plan.find(OLLAMA_MARKER) :]
    return ""


def history_note(system: SolarSystem) -> str:
    """Core-plan paragraph pointing at the Work History module (empty if there is none)."""
    if not any(p.kind == "history" for p in system.planets):
        return ""
    tools = [
        str(SOURCE_CATEGORIES.get(s, {}).get("title", s))
        for s in dict.fromkeys([system.source] + list(system.sources or []))
        if s in AGENT_SOURCES
    ]
    return (
        "\n\nSession history: AI coding sessions"
        + (f" from {', '.join(tools)}" if tools else "")
        + f" are listed in the '{HISTORY_PLANET_TITLE}' module. They are history only: the other modules "
        "and their links come from the source code, not from the sessions."
    )


def _attach_user_sats(target: PlanetNode, sats: Iterable) -> None:
    for us in sats:
        key = us.title.strip().lower()
        target.satellites = [s for s in target.satellites if s.title.strip().lower() != key]
        target.satellites.append(us)


def merge_generated_planets(
    old_planets: List[PlanetNode],
    fresh_planets: List[PlanetNode],
    keep_kinds: Iterable[str] = (),
) -> List[PlanetNode]:
    """Returns `fresh_planets` with user edits carried over from `old_planets`.

    Matching is by title (case-insensitive). For a match: `ai_summary` is kept when the
    kinds are equal (or the old module predates `kind`); a user-modified module keeps
    its summary, colour and position; user-modified sub-components replace same-title
    ones. Unmatched old modules: kinds in `keep_kinds` are kept unchanged (e.g. the
    Work History module during a code refresh); user-modified ones are kept; otherwise
    only their user-modified sub-components survive (in a module of the same title)."""
    keep = set(keep_kinds)
    by_title: Dict[str, PlanetNode] = {}
    for p in fresh_planets:
        by_title.setdefault(p.title.strip().lower(), p)
    carried: List[PlanetNode] = []
    for old in old_planets:
        target = by_title.get(old.title.strip().lower())
        if old.kind in keep and (target is None or target.kind != old.kind):
            carried.append(old)
            continue
        user_sats = [s for s in old.satellites if s.is_user_modified]
        if target is not None:
            if old.ai_summary and old.kind in ("", target.kind):
                target.ai_summary = old.ai_summary
            if old.is_user_modified:
                target.summary = old.summary
                target.color = old.color
                target.angle = old.angle
                target.orbit_radius = old.orbit_radius
                target.is_user_modified = True
            _attach_user_sats(target, user_sats)
            continue
        if old.is_user_modified:
            carried.append(old)
        elif user_sats:
            old.satellites = user_sats
            carried.append(old)
    return list(fresh_planets) + carried


def refresh_system_from_code(existing: SolarSystem, fresh: SolarSystem, core_plan: str) -> None:
    """Replaces the code-derived modules and links of `existing` with those of `fresh`
    (see `merge_generated_planets`), keeps the Work History module, user-drawn links and
    the Ollama block, and rewrites the core plan unless the user edited it."""
    captured = existing.capture_user_links()
    existing.planets = merge_generated_planets(existing.planets, fresh.planets, keep_kinds={"history"})
    existing.links = list(fresh.links)
    existing.restore_user_links(captured)
    if not existing.is_user_modified:
        existing.core_plan = core_plan + history_note(existing) + _ollama_tail(existing.core_plan)
    merge_purpose(existing, fresh)
    merge_identity(existing, fresh)
    update_core_plan_purpose(existing)
    _keep_only_real_links(existing)


def _transfer_user_content(src: SolarSystem, dst: SolarSystem) -> None:
    """Moves user-modified modules, sub-components, links and notes from `src` to `dst`."""
    from core.models import generate_id

    title_map = {p.title.strip().lower(): p for p in dst.planets}
    captured = src.capture_user_links()
    for p in src.planets:
        user_sats = [s for s in p.satellites if s.is_user_modified]
        if not p.is_user_modified and not user_sats:
            continue
        key = p.title.strip().lower()
        target = title_map.get(key)
        if target is None:
            new_p = copy.deepcopy(p)
            new_p.id = generate_id()
            if not p.is_user_modified:
                new_p.satellites = [copy.deepcopy(s) for s in user_sats]
            dst.planets.append(new_p)
            title_map[key] = new_p
        else:
            if p.is_user_modified and not target.is_user_modified:
                target.summary, target.color = p.summary, p.color
                target.angle, target.orbit_radius = p.angle, p.orbit_radius
                target.is_user_modified = True
            _attach_user_sats(target, [copy.deepcopy(s) for s in user_sats])
        p.is_user_modified = False
        p.satellites = [s for s in p.satellites if not s.is_user_modified]
    for link, s_title, d_title in captured:
        sp, tp = title_map.get(s_title), title_map.get(d_title)
        if sp is None or tp is None or sp is tp:
            continue
        if not any(l.source_planet_id == sp.id and l.target_planet_id == tp.id for l in dst.links):
            new_l = copy.deepcopy(link)
            new_l.id = generate_id()
            new_l.source_planet_id, new_l.target_planet_id = sp.id, tp.id
            dst.links.append(new_l)
        src.links.remove(link)
    if src.notes:
        dst.notes.extend(src.notes)
        src.notes = []


def consolidate_folder_systems(store: GalaxyStore, systems: List[SolarSystem]) -> Tuple[Optional[SolarSystem], int]:
    """Merges several systems for the same folder into the best one (codebase first,
    then the one with user edits, then the most modules). User content of the others is
    moved into it and their sources are added; the others are removed from the store
    only if nothing user-made is left in them. Returns (kept system, removed count)."""
    uniq = list({id(s): s for s in systems}.values())
    if not uniq:
        return None, 0
    if len(uniq) == 1:
        return uniq[0], 0
    ordered = sorted(uniq, key=lambda s: (s.source != "codebase", not has_user_content(s), -len(s.planets)))
    best = ordered[0]
    removed = 0
    for other in ordered[1:]:
        _transfer_user_content(other, best)
        for src in [other.source] + list(other.sources or []):
            if src and src != "manual":
                add_source(best, src)
        if not has_user_content(other) and other in store.systems:
            store.systems.remove(other)
            removed += 1
    return best, removed


# ---------------------------------------------------------------------------
# Public importer
# ---------------------------------------------------------------------------
class CodebaseImporter:
    """Converts local project directories or Understand-Anything (.ua) graphs into Helios systems."""

    @staticmethod
    def analyze_directory(folder_path: Path, store: GalaxyStore) -> Optional[SolarSystem]:
        """Analyzes the given directory and adds it to the store (`source='codebase'`), or
        refreshes the existing system for the same folder under any source."""
        folder = Path(folder_path).resolve()
        if not folder.exists() or not folder.is_dir():
            return None
        if is_ignored_folder(store, folder):
            count_skipped_ignored("codebase", str(folder))
            return None

        for ua_dir_name in (".ua", ".understand-anything"):
            ua_graph_path = folder / ua_dir_name / "knowledge-graph.json"
            if ua_graph_path.exists():
                ua_sys = CodebaseImporter._import_from_ua_graph(folder, ua_graph_path)
                if ua_sys:
                    return CodebaseImporter._save_or_update(store, ua_sys)

        system = CodebaseImporter._build_system(folder)
        if system is None:
            return None
        return CodebaseImporter._save_or_update(store, system)

    @staticmethod
    def _build_system(folder: Path) -> Optional[SolarSystem]:
        project_name = folder.name or str(folder)
        system = SolarSystem(
            name=project_name,
            project_name=project_name,
            sun_title=project_name,
            core_plan="",
            source="codebase",
            source_id=_normalized_path_key(str(folder)),
            workspace_path=str(folder),
        )
        stats = build_code_map(folder, system)
        if stats.files == 0:
            return None
        system.core_plan = stats.core_plan
        return system

    @staticmethod
    def _import_from_ua_graph(folder: Path, graph_path: Path) -> Optional[SolarSystem]:
        """Converts an Understand-Anything `.ua/knowledge-graph.json` file directly into a project system."""
        try:
            data = json.loads(graph_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None

        project_name = str(data.get("projectName") or data.get("name") or folder.name)
        summary = data.get("summary") or data.get("description") or ""
        nodes = [n for n in (data.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (data.get("edges") or []) if isinstance(e, dict)]

        core_plan = (
            f"{summary or f'{project_name} (no summary in the knowledge graph).'}\n\n"
            f"Map: imported from an Understand-Anything knowledge graph (`{graph_path.relative_to(folder)}`), "
            f"{len(nodes)} nodes, {len(edges)} edges. The source code was not re-analysed by Helios.\n"
            f"Folder: {folder}"
        )
        system = SolarSystem(
            name=project_name,
            project_name=project_name,
            sun_title=f"{project_name} (.ua Graph)",
            core_plan=core_plan,
            source="codebase",
            source_id=_normalized_path_key(str(folder)),
            workspace_path=str(folder),
        )

        by_layer: Dict[str, List[dict]] = defaultdict(list)
        for node in nodes:
            layer_name = str(node.get("layer") or node.get("type") or "core").strip().lower() or "core"
            by_layer[layer_name].append(node)

        groups = sorted(by_layer.items(), key=lambda kv: -len(kv[1]))
        folded: List[Tuple[str, int]] = []
        if len(groups) > MAX_PLANETS:
            folded = [(k, len(v)) for k, v in groups[MAX_PLANETS - 1 :]]
            other_nodes = [n for _, v in groups[MAX_PLANETS - 1 :] for n in v]
            groups = groups[: MAX_PLANETS - 1] + [("__other__", other_nodes)]

        planet_by_node_id: Dict[str, PlanetNode] = {}
        for layer_name, n_list in groups:
            if layer_name == "__other__":
                mapped_layer = "core"
                title = OTHER_PLANET_TITLE
            else:
                mapped_layer = _classify_file_layer(f"{layer_name}/x")
                if mapped_layer == "core" and "test" in _split_tokens(layer_name):
                    mapped_layer = "testing"
                title = f"Layer: {layer_name.title()}"
            shown = n_list[:SATELLITE_CAP]
            p_summary = f"{len(n_list)} components in the Understand-Anything '{layer_name}' group (imported as-is from the knowledge graph)."
            if folded and layer_name == "__other__":
                p_summary += "\nFolded groups: " + ", ".join(f"{t} ({c})" for t, c in folded)
            if len(n_list) > len(shown):
                hidden_names = [str(n.get("label") or n.get("name") or n.get("id") or "?") for n in n_list[len(shown) :]]
                p_summary += f"\n+{len(hidden_names)} more components not shown:\n• " + "\n• ".join(hidden_names[:SUMMARY_FILE_LIST_CAP])
            planet = system.add_planet(title=title, summary=p_summary, layer=mapped_layer)
            planet.kind = "layer"
            for n in n_list:
                if n.get("id") is not None:
                    planet_by_node_id[str(n["id"])] = planet
            for n in shown:
                n_title = str(n.get("label") or n.get("name") or n.get("id") or "Component")
                n_desc = str(n.get("summary") or n.get("description") or "")
                planet.add_satellite(
                    title=n_title[:60],
                    content=n_desc or json.dumps(n, ensure_ascii=False, indent=2)[:800],
                    visual_tag="code",
                    file_path=str(n.get("filePath") or n.get("path") or ""),
                )

        edge_groups: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        pid_to_planet = {p.id: p for p in system.planets}
        for edge in edges:
            s_id = str(edge.get("source", ""))
            t_id = str(edge.get("target", ""))
            sp, tp = planet_by_node_id.get(s_id), planet_by_node_id.get(t_id)
            if sp and tp and sp.id != tp.id:
                edge_groups[(sp.id, tp.id)].append(str(edge.get("type") or edge.get("label") or "connected"))
        for (sid, tid), kinds in edge_groups.items():
            kc = Counter(kinds)
            label = f"{len(kinds)} edge{'s' if len(kinds) != 1 else ''} (" + ", ".join(k for k, _ in kc.most_common(2)) + ")"
            if sid in pid_to_planet and tid in pid_to_planet:
                system.add_link(sid, tid, label=label, relation_type="depends_on")

        _keep_only_real_links(system)
        return system

    @staticmethod
    def _find_matches(store: GalaxyStore, parsed: SolarSystem) -> List[SolarSystem]:
        """Systems for the same folder under any non-manual source, plus legacy codebase
        entries (old source_id formats, or no workspace path but the same name)."""
        matches = find_systems_for_folder(store, parsed.workspace_path)
        key = parsed.source_id
        legacy_key = parsed.workspace_path.lower()
        for s in store.systems:
            if s.source != "codebase" or s in matches:
                continue
            if s.source_id and s.source_id in (key, legacy_key) and (not s.workspace_path or _identity_keys(s.workspace_path) & _identity_keys(parsed.workspace_path)):
                matches.append(s)
        if not matches:
            name = parsed.project_name.strip().lower()
            for s in store.systems:
                if s.source == "codebase" and not s.workspace_path and name in {s.project_name.strip().lower(), s.name.strip().lower()}:
                    matches.append(s)
                    break
        return matches

    @staticmethod
    def _find_existing(store: GalaxyStore, parsed: SolarSystem) -> Optional[SolarSystem]:
        """The system that a scan of `parsed.workspace_path` updates (no consolidation)."""
        matches = CodebaseImporter._find_matches(store, parsed)
        if not matches:
            return None
        return sorted(matches, key=lambda s: (s.source != "codebase", not has_user_content(s), -len(s.planets)))[0]

    @staticmethod
    def _disambiguate_name(store: GalaxyStore, parsed: SolarSystem, exclude: Iterable[SolarSystem] = ()) -> None:
        """Gives a different folder with an already-used project name (any non-manual
        source) a distinct name, so name-based grouping/merging never mixes two folders."""
        excluded = {id(s) for s in exclude}
        name = parsed.project_name.strip().lower()
        clash = any(
            id(s) not in excluded
            and s.source != "manual"
            and name in {s.project_name.strip().lower(), s.name.strip().lower()}
            for s in store.systems
        )
        if not clash:
            return
        parent = Path(parsed.workspace_path).parent.name or Path(parsed.workspace_path).anchor
        old_name = parsed.project_name
        new_name = f"{old_name} ({parent})"
        parsed.name = new_name
        parsed.project_name = new_name
        if parsed.sun_title.startswith(old_name):
            parsed.sun_title = new_name + parsed.sun_title[len(old_name) :]

    @staticmethod
    def _save_or_update(store: GalaxyStore, parsed: SolarSystem) -> SolarSystem:
        matches = CodebaseImporter._find_matches(store, parsed)
        existing, _removed = consolidate_folder_systems(store, matches)
        same_folder = matches + find_systems_for_folder(store, parsed.workspace_path)
        if existing is None:
            CodebaseImporter._disambiguate_name(store, parsed, same_folder)
            real_links = list(parsed.links)
            added = store.add_system(parsed)
            if added.links != real_links:
                added.links = real_links
                store.save()
            return added

        if existing.source == "codebase":
            CodebaseImporter._disambiguate_name(store, parsed, same_folder)
            existing.name = parsed.name
            existing.project_name = parsed.project_name
            existing.sun_title = parsed.sun_title
            existing.source_id = parsed.source_id
            existing.workspace_path = parsed.workspace_path
        elif not existing.workspace_path:
            existing.workspace_path = parsed.workspace_path
        refresh_system_from_code(existing, parsed, parsed.core_plan)
        add_source(existing, "codebase")
        store.save()
        return existing

    @staticmethod
    def scan_parent_projects_directory(
        parent_dir: Path,
        store: GalaxyStore,
    ) -> List[SolarSystem]:
        """Detects all sub-projects inside a parent directory and builds an architecture map for each.

        One failing project never aborts the scan; failures are logged and collected in
        the module-level `LAST_SCAN_FAILURES` list as (folder, error message)."""
        LAST_SCAN_FAILURES.clear()
        LAST_SCAN_STATS["codebase"] = {"skipped_ignored": 0, "skipped_ignored_paths": []}
        parent_dir = Path(parent_dir)
        if not parent_dir.exists() or not parent_dir.is_dir():
            return []
        ignored = ignored_folder_keys(store)
        if is_ignored_folder(store, parent_dir, ignored):
            count_skipped_ignored("codebase", str(parent_dir))
            return []

        results: List[SolarSystem] = []
        project_indicators = {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "cargo.toml",
            "go.mod",
            "pom.xml",
            ".git",
            "composer.json",
            "gemfile",
            "docker-compose.yml",
            "docker-compose.yaml",
        }

        try:
            entries = sorted(parent_dir.iterdir())
        except Exception as exc:
            LAST_SCAN_FAILURES.append((str(parent_dir), f"{type(exc).__name__}: {exc}"))
            logger.warning("Cannot list %s: %s", parent_dir, exc)
            return []

        for child in entries:
            try:
                if not child.is_dir() or child.is_symlink():
                    continue
                if _should_skip_dir(child.name, []) or child.name.startswith("$"):
                    continue
                if is_ignored_folder(store, child, ignored):
                    count_skipped_ignored("codebase", str(child))
                    continue
                try:
                    names = {p.name.lower() for p in child.iterdir()}
                except OSError:
                    names = set()
                is_project = bool(names & project_indicators)
                if not is_project:
                    is_project = any(os.path.splitext(n)[1] in CODE_EXTENSIONS for n in names)
                if not is_project:
                    continue
                sys_obj = CodebaseImporter.analyze_directory(child, store)
                if sys_obj:
                    sys_obj.merge_duplicate_nodes()
                    results.append(sys_obj)
            except Exception as exc:
                LAST_SCAN_FAILURES.append((str(child), f"{type(exc).__name__}: {exc}"))
                logger.exception("Failed to analyse project folder %s", child)

        if results:
            store.save()
        return results

    @staticmethod
    def get_helios_projects_dir() -> Path:
        """Returns Helios's internal `projects/` directory, creating it if needed."""
        helios_root = Path(__file__).resolve().parent.parent
        proj_dir = helios_root / "projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        return proj_dir

    @staticmethod
    def scan_helios_projects(store: GalaxyStore) -> List[SolarSystem]:
        """Scans only Helios's internal `projects/` folder and builds architecture maps for projects placed there."""
        proj_dir = CodebaseImporter.get_helios_projects_dir()
        return CodebaseImporter.scan_parent_projects_directory(proj_dir, store)
