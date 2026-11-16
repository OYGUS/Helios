"""Python analysis with `ast`: definitions (signatures, decorators, docstrings, class
details), imports and the raw call scopes later resolved by `connectors.codebase.calls`.
Files that fail to parse fall back to the pattern matcher of `lang_common`.
"""

from __future__ import annotations

import ast
import re
import warnings
from typing import Dict, Iterable, List, Optional, Set, Tuple

from connectors.purpose_evidence import clean_header_text, is_noise_comment_line
from connectors.codebase.fileinfo import Chain, _Def, _FileInfo, _Method
from connectors.codebase.lang_common import _LANG_PATTERNS, _analyze_with_patterns, _clean_comment_line, _clip
from connectors.codebase.purpose_text import _docstring_description, _python_purpose_bits


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


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - malformed trees
        return "…"


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
        d.method_names = [n.name for n in node.body if isinstance(n, _FUNC_TYPES)]
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
