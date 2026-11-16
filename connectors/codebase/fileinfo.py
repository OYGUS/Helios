"""Layer classification of files and the per-file analysis records (`_FileInfo`, `_Def`, `_Method`).

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
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Set, Tuple

from connectors.codebase.constants import _LAYER_PRIORITY


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
    # Every method name (any visibility) when `method_infos` is not filled (cheap directory-
    # map parse); only feeds the search list `defined_names`, never display text.
    method_names: List[str] = field(default_factory=list)
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
