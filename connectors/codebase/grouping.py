"""Module (planet) grouping of files for directory maps, and the flat-layout test.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set, Tuple

from connectors.codebase.constants import (
    DOMINANT_DIR_SHARE,
    FLAT_ROOT_SHARE,
    MAX_DESCEND_DEPTH,
    MAX_PLANETS,
    OTHER_PLANET_TITLE,
    ROOT_PLANET_TITLE,
    _LAYER_PRIORITY,
)
from connectors.codebase.fileinfo import _FileInfo


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
