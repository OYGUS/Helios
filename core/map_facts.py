"""Pure helpers that read facts already stored on a project map (no Qt, no scanning).

Used by the search index (`core.search_index`), the map filter (`ui.filters`) and the
Markdown export (`core.markdown_export`). Nothing here creates data: every helper only
reads what the importers or the user stored on `SolarSystem` / `PlanetNode` /
`SatelliteNode` / `PlanetLink`.

Link kinds
----------
`PlanetLink` has no kind field. The codebase importer writes every link it found in
source with ``relation_type="depends_on"`` and a label that starts with the verb of the
statement it came from (``imports …``, ``includes …``, ``references …``, ``uses …``),
or ``a.py → b.py: imports …`` / ``N imports (…)`` when a link groups several files, or
``N edges (…)`` when it was converted from a knowledge-graph file. :func:`link_kind` reads
that back. Links drawn on the map, added in the inspector or by the live bridge have
another relation type (or a label that is not an importer label) and count as "manual".
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

# key -> (short label, plain explanation). Order = display order.
LINK_KINDS: Dict[str, Tuple[str, str]] = {
    "imports": ("Imports", "A file imports another file (found in the code)"),
    "includes": ("Includes", "A PHP file includes or requires another file (found in the code)"),
    "uses": ("Uses", "A PHP file uses a class from another file (found in the code)"),
    "references": ("References", "An HTML file loads another file, e.g. a script or a stylesheet (found in the code)"),
    "graph": ("From a knowledge graph", "Taken from the project's own knowledge-graph file"),
    "found": ("Other links from code", "Found by the code analysis"),
    "manual": ("Added by hand", "Drawn on the map or added in Helios, not found in the code"),
}

_VERBS = ("imports", "includes", "uses", "references")
_GROUPED_RE = re.compile(r"^\d+ (import|imports)\b")
_GRAPH_RE = re.compile(r"^\d+ edges? \(")
# "a.py → b.py: imports x" (a single file pair inside a folder-level link)
_PAIR_RE = re.compile(r"^.+? → .+?: (\w+)\b")


def link_kind(link: object) -> str:
    """The kind key of a link (see `LINK_KINDS`), read from its stored label and type."""
    relation = str(getattr(link, "relation_type", "") or "")
    label = str(getattr(link, "label", "") or "").strip()
    if relation != "depends_on":
        return "manual"
    first = label.split(" ", 1)[0].lower() if label else ""
    if first in _VERBS:
        return first
    m = _PAIR_RE.match(label)
    if m and m.group(1).lower() in _VERBS:
        return m.group(1).lower()
    if _GROUPED_RE.match(label):
        return "imports"
    if _GRAPH_RE.match(label):
        return "graph"
    if bool(getattr(link, "is_user_modified", False)):
        return "manual"
    return "found"


def link_kind_label(kind: str) -> str:
    return LINK_KINDS.get(kind, (kind.title(), ""))[0]


def link_kind_counts(links: Sequence[object]) -> Dict[str, int]:
    """Kind -> number of links, only for kinds that occur, in `LINK_KINDS` order."""
    counts: Dict[str, int] = {}
    for link in links:
        k = link_kind(link)
        counts[k] = counts.get(k, 0) + 1
    order = list(LINK_KINDS)
    return {k: counts[k] for k in sorted(counts, key=lambda k: order.index(k) if k in order else len(order))}


# ----------------------------------------------------------------------------
# "Key: value" lines of generated texts
# ----------------------------------------------------------------------------
_MORE_RE = re.compile(r"\s*\+\d+ more$")


def content_field(text: str, key: str) -> str:
    """Value of the first ``Key: value`` line of `text` ("" when there is none)."""
    prefix = key + ":"
    for line in str(text or "").splitlines():
        s = line.strip()
        if s.startswith(prefix):
            return s[len(prefix):].strip()
    return ""


def split_list(value: str) -> List[str]:
    """Items of a stored comma list, without a trailing ``+N more``."""
    value = _MORE_RE.sub("", str(value or "").strip())
    return [p.strip() for p in value.split(", ") if p.strip()]


def classify_name(item: str) -> Optional[Tuple[str, str]]:
    """(name, kind) for one stored definition title ("run()", "class Motor",
    "Motor.spin()", "interface Port"); None for counts and notes."""
    if isinstance(item, str):
        return _classify_name_cached(item)
    return _classify_name(item)


@lru_cache(maxsize=1 << 16)
def _classify_name_cached(item: str) -> Optional[Tuple[str, str]]:
    # A pure function of the string and its result is immutable, so memoising it is exact
    # (the search index classifies every stored definition name on each build).
    return _classify_name(item)


def _classify_name(item: object) -> Optional[Tuple[str, str]]:
    item = str(item or "").strip()
    if not item or item[0].isdigit() or item.startswith("nothing") or " — " in item:
        return None
    if item.startswith("class "):
        return item[len("class "):].strip(), "class"
    if item.startswith(("interface ", "trait ", "enum ", "type ", "struct ")):
        return item.split(" ", 1)[1].strip(), "class"
    if item.endswith("()") and "." in item[:-2]:
        return item, "method"
    if item.endswith("()"):
        return item, "function"
    return item, "name"


def node_defined_names(node: object, text: str) -> List[Tuple[str, str]]:
    """(name, kind) of every definition of a module / component: the complete stored
    `defined_names` list when the analysis wrote one, else the clipped ``Defines:`` line
    of `text` (data saved before the field existed)."""
    stored = list(getattr(node, "defined_names", []) or [])
    if stored:
        return [c for c in (classify_name(n) for n in stored) if c is not None]
    return defined_names(text)


def defined_names(text: str) -> List[Tuple[str, str]]:
    """(name, kind) pairs from the ``Defines:`` line the importer writes for a file,
    e.g. ``Defines: run(), class Motor, Motor.spin() +3 more``. Counts such as
    ``1 function, 0 classes — Python ast`` and "nothing found" are not names."""
    out: List[Tuple[str, str]] = []
    for item in split_list(content_field(text, "Defines")):
        c = classify_name(item)
        if c is not None:
            out.append(c)
    return out


# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/")


def relative_path(path: str, root: str) -> str:
    """`path` relative to the project folder `root` with "/" separators; just the file
    name when it is outside (never an absolute path)."""
    p, r = _norm(path), _norm(root)
    if not p:
        return ""
    if r:
        if os.name == "nt" or (len(r) > 1 and r[1] == ":"):
            pl, rl = p.lower(), r.lower()
        else:
            pl, rl = p, r
        if pl == rl:
            return "."
        if pl.startswith(rl + "/"):
            return p[len(r) + 1:]
    return p.rsplit("/", 1)[-1]


def scrub_root(text: str, root: str) -> str:
    """Replaces the absolute project folder inside a stored text with a relative form."""
    text = str(text or "")
    r = str(root or "").rstrip("/\\")
    if not r or len(r) < 2:
        return text
    variants = {r, r.replace("\\", "/"), r.replace("/", "\\")}
    for v in sorted(variants, key=len, reverse=True):
        for sep in ("/", "\\"):
            text = text.replace(v + sep, "")
        text = text.replace(v, ".")
    return text


# Lines of the generated project overview that say how the map was derived.
ANALYSIS_KEYS = ("Map", "Links", "Calls", "Analysis", "Size", "Languages")


def analysis_lines(core_plan: str) -> List[str]:
    """The overview lines that state how the analysis built the map ("" keys skipped)."""
    out = []
    for key in ANALYSIS_KEYS:
        v = content_field(core_plan, key)
        if v:
            out.append(f"{key}: {v}")
    return out


def entry_note(planet: object) -> Optional[str]:
    """The stored "Entry point:" evidence of a module or file component, if any."""
    text = str(getattr(planet, "summary", "") or getattr(planet, "content", "") or "")
    v = content_field(text, "Entry point")
    return v or None
