"""Helpers shared by the language analysers: text clipping, comment / string masking
(positions and line numbers are preserved), brace matching, and the pattern-matched
definitions of languages without a dedicated analyser (Go, Rust, C#, Java, Kotlin, Ruby,
C/C++, SQL; they never produce links). What each analyser resolves is described in
`connectors.codebase.resolve`.
"""

from __future__ import annotations

import bisect
import re
from typing import Dict, List, Optional, Set, Tuple

from connectors.codebase.fileinfo import _Def, _FileInfo


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clean_comment_line(line: str) -> str:
    s = line.strip()
    s = s.lstrip("#/*!-<").rstrip("*/->").strip()
    s = re.sub(r"^[=\-*~#_]{3,}$", "", s)
    return s.strip(" =-*~")


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
