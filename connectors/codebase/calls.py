"""Call relations (`SatelliteNode.calls` / `called_by`, references
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
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from connectors.codebase.constants import _JS_EXT_SET
from connectors.codebase.fileinfo import Chain, _FileInfo
from connectors.codebase.resolve import _py_lookup, _py_package_dirs, _py_roots


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
