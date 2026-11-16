"""Tunables, titles and lookup tables shared by every module of the codebase importer.
"""

from __future__ import annotations

import re
import sys
from typing import Dict, List, Set, Tuple


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MAX_PLANETS = 12  # modules shown on the canvas (smallest folded into "Other")
SATELLITE_CAP = 30  # files / definitions shown as sub-components per module
# Definition names stored per file for search (`defined_names`), before display clipping.
# Generous: only pathological (e.g. generated) files hit it; keeps galaxy.json bounded.
DEFINED_NAMES_CAP = 400
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
