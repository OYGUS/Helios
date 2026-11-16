"""Pattern-based analysers: JS/TS (and the `<script>` blocks of Vue/Svelte), PHP, HTML
references, the per-extension dispatcher `_analyze_generic_source` and Markdown
(summary, headings, wikilinks). What they resolve is described in
`connectors.codebase.resolve`; their call scopes in `connectors.codebase.calls`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from connectors.purpose_evidence import is_noise_comment_line
from connectors.codebase.fileinfo import _Def, _FileInfo, _Method
from connectors.codebase.lang_common import (
    _EXT_TO_PATTERN_KEY,
    _JS_IMPORT_PATTERNS,
    _JS_TOKEN_RE,
    _LANG_PATTERNS,
    _Lines,
    _PHP_TOKEN_RE,
    _analyze_with_patterns,
    _body_after_params,
    _body_from,
    _brace_pairs,
    _clip,
    _direct_member_checker,
    _mask_both,
    _match_paren,
    _nest_defs,
    _php_code_regions,
    _scope_finder,
    _script_blocks,
    _split_names,
)


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
