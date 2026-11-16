"""Import / include / reference resolution (links come only from here) and entry points.

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

   **Entry points** (`PlanetNode.is_entry`, at most 3; on the directory module that
   contains the file in directory maps), by evidence: a Python file with an
   `if __name__ == "__main__":` block or a package `__main__.py` (test files excluded;
   `main.py`/`__main__.py`/`app.py`/`manage.py` preferred; files imported by other
   project files are used only when no un-imported candidate exists); package.json
   `main` / `bin` / `scripts.start` targets that exist; `index.php` at the root or in
   `public/`; `index.html` at the root or next to a package.json. The evidence is
   written into the module summary and the core plan.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from connectors.codebase.constants import MAX_PARSE_BYTES, _JS_EXTS, _JS_TS_SUBST, _NODE_BUILTINS, _NPM_NAME_RE
from connectors.codebase.fileinfo import _FileInfo
from connectors.codebase.lang_common import _clip
from connectors.codebase.lang_patterns import _js_package_name


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
