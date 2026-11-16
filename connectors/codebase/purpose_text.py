"""Purpose evidence, per file, collected during the single walk (header comments,
docstrings, user-facing strings, page titles; credential-like values are never kept),
and the README intro parser (`readme_intro`). How the evidence is assembled and used is
described in `connectors.codebase.purpose`.
"""

from __future__ import annotations

import ast
import os
import posixpath
import re
from typing import Dict, Iterable, List, Optional, Tuple

from connectors.purpose_evidence import clean_header_text, is_noise_comment_line
from connectors.codebase.constants import _JS_EXTS, _JS_EXT_SET
from connectors.codebase.fileinfo import _FileInfo
from connectors.codebase.lang_common import _clean_comment_line, _clip, _php_code_regions


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


def _docstring_description(doc: str) -> str:
    """The first paragraph of a module docstring that is a description once noise lines
    (license, encoding, metadata, "generated" notes, ...) are removed ("" if none)."""
    for para in re.split(r"\n\s*\n", (doc or "").strip()):
        cleaned = clean_header_text(para)
        if cleaned:
            return cleaned
    return ""
