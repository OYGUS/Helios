"""Project identity, refresh and duplicate cleanup (shared with `connectors.agent_importer`),
and the ignored-folders setting.

Ignored folders: every scan entry point skips a folder that equals or is inside a path
of the store setting `ignored_folders` (`is_ignored_folder`), counted in
`LAST_SCAN_STATS["codebase"]["skipped_ignored"]` (+ `_paths`).

Project identity: one project per folder, whatever tool saw it. `analyze_directory()`
looks for an existing non-manual system whose normalised `workspace_path` is the same
folder (case-insensitive for Windows paths) under ANY source, consolidates stale
duplicates left by older versions (`consolidate_folder_systems()`), and refreshes that
system in place: it keeps its `source`, adds `codebase` to `sources`, keeps user-modified
modules / sub-components / links, positions and colours of user-modified nodes, the
`ai_summary` of same-title modules, the Ollama block of the core plan and the
"Work History" module written by `connectors.agent_importer`.
"""

from __future__ import annotations

import copy
import os
import posixpath
import re
from pathlib import Path, PureWindowsPath
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.models import SOURCE_CATEGORIES, GalaxyStore, PlanetNode, SolarSystem
from connectors.codebase.constants import AGENT_SOURCES, HISTORY_PLANET_TITLE, OLLAMA_MARKER
from connectors.codebase.content import _keep_only_real_links
from connectors.codebase.purpose import merge_purpose, update_core_plan_purpose
from connectors.codebase.identity import merge_identity


# ---------------------------------------------------------------------------
# Project identity, refresh and duplicate cleanup (shared with agent_importer)
# ---------------------------------------------------------------------------
_WIN_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)")


def workspace_key(path: str) -> str:
    """Identity key of a folder path. Windows-style paths (`C:\\...`) are compared
    case-insensitively on any OS; POSIX paths are normalised (case-folded on Windows)."""
    p = str(path or "").strip().strip("\"'").strip()
    if not p:
        return ""
    if _WIN_PATH_RE.match(p):
        s = str(PureWindowsPath(p)).replace("İ", "i").replace("I", "i").lower()
        return s.rstrip("\\") if len(s) > 3 else s
    norm = posixpath.normpath(p)
    return os.path.normcase(norm) if os.name == "nt" else norm


def _identity_keys(path: str) -> Set[str]:
    keys = {workspace_key(path)} - {""}
    if path and not (_WIN_PATH_RE.match(path) and os.name != "nt"):
        try:
            p = Path(path)
            if p.exists():
                keys.add(workspace_key(str(p.resolve())))
        except (OSError, ValueError, RuntimeError):
            pass
    return keys


def find_systems_for_folder(store: GalaxyStore, folder: str) -> List[SolarSystem]:
    """All non-manual systems (any source) whose `workspace_path` is the same folder."""
    target = _identity_keys(folder)
    if not target:
        return []
    return [s for s in store.systems if s.source != "manual" and s.workspace_path and _identity_keys(s.workspace_path) & target]


def has_user_content(system: SolarSystem) -> bool:
    """True if anything in `system` was made or edited by the user."""
    if system.is_user_modified or system.notes:
        return True
    if any(l.is_user_modified for l in system.links):
        return True
    return any(p.is_user_modified or any(s.is_user_modified for s in p.satellites) for p in system.planets)


#: Planet kinds that only the importers create. Their `is_user_modified` flag is not
#: proof of a user edit (the canvas auto-layout used to set it when it moved them), so
#: `has_user_made_content` ignores it; a sub-component the user added to them (no
#: `kind`) still counts.
GENERATED_ONLY_PLANET_KINDS = frozenset({"external", "history"})


def has_user_made_content(system: SolarSystem) -> bool:
    """Stricter "did the user make anything here?" test used before deleting a stale
    system: manual systems, user-edited systems, canvas notes, user-drawn links and a
    purpose typed by the user always count; a user-modified module / sub-component
    counts unless it belongs to an importer-only module kind
    (`GENERATED_ONLY_PLANET_KINDS`), where only sub-components without a `kind` (made
    by the user) count. Position changes cannot be told apart from edits, which is why
    those kinds are excluded."""
    if system.source == "manual" or system.is_user_modified or system.notes:
        return True
    if (getattr(system, "purpose_source", "") or "") == "user":
        return True
    if any(l.is_user_modified for l in system.links):
        return True
    for p in system.planets:
        if p.kind in GENERATED_ONLY_PLANET_KINDS:
            if any(s.is_user_modified and not s.kind for s in p.satellites):
                return True
            continue
        if p.is_user_modified or any(s.is_user_modified for s in p.satellites):
            return True
    return False


# ---------------------------------------------------------------------------
# Ignored folders (store setting written by the UI)
# ---------------------------------------------------------------------------
IGNORED_FOLDERS_SETTING = "ignored_folders"

#: Statistics of the most recent scan per source, shared with `connectors.agent_importer`
#: (same dict object). Codebase scans use the key "codebase":
#: {"skipped_ignored": 1, "skipped_ignored_paths": ["C:\\...\\old"]}.
LAST_SCAN_STATS: Dict[str, Dict[str, object]] = {}
_STATS_PATH_CAP = 20


def _key_inside(key: str, base: str) -> bool:
    if not key or not base:
        return False
    if key == base:
        return True
    sep = "\\" if _WIN_PATH_RE.match(base) else "/"
    return key.startswith(base.rstrip("\\/") + sep)


def ignored_folder_keys(store: Optional[GalaxyStore]) -> List[str]:
    """Normalised keys of the `ignored_folders` setting (bad values are ignored)."""
    try:
        raw = store.get_setting(IGNORED_FOLDERS_SETTING, []) if store is not None else []
    except Exception:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    keys: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            keys.extend(sorted(_identity_keys(item.strip())))
    return list(dict.fromkeys(k for k in keys if k))


def is_ignored_folder(store: Optional[GalaxyStore], path, keys: Optional[Sequence[str]] = None) -> bool:
    """True if `path` equals or is inside a folder of the store's `ignored_folders`
    setting (normalised like `workspace_key`; Windows paths case-insensitively)."""
    bases = list(keys) if keys is not None else ignored_folder_keys(store)
    if not bases or not path:
        return False
    targets = _identity_keys(str(path))
    return any(_key_inside(t, b) for t in targets for b in bases)


def count_skipped_ignored(source: str, path: str) -> None:
    """Counts a skipped ignored folder in `LAST_SCAN_STATS[source]`."""
    stats = LAST_SCAN_STATS.setdefault(source, {})
    stats["skipped_ignored"] = int(stats.get("skipped_ignored", 0) or 0) + 1  # type: ignore[arg-type]
    paths = stats.setdefault("skipped_ignored_paths", [])
    if isinstance(paths, list) and path and path not in paths and len(paths) < _STATS_PATH_CAP:
        paths.append(path)


def add_source(system: SolarSystem, source: str) -> None:
    sources = list(dict.fromkeys(([system.source] if system.source else []) + list(system.sources or [])))
    if source and source not in sources:
        sources.append(source)
    system.sources = sources


def _ollama_tail(core_plan: str) -> str:
    if OLLAMA_MARKER in core_plan:
        return "\n\n" + core_plan[core_plan.find(OLLAMA_MARKER) :]
    return ""


def history_note(system: SolarSystem) -> str:
    """Core-plan paragraph pointing at the Work History module (empty if there is none)."""
    if not any(p.kind == "history" for p in system.planets):
        return ""
    tools = [
        str(SOURCE_CATEGORIES.get(s, {}).get("title", s))
        for s in dict.fromkeys([system.source] + list(system.sources or []))
        if s in AGENT_SOURCES
    ]
    return (
        "\n\nSession history: AI coding sessions"
        + (f" from {', '.join(tools)}" if tools else "")
        + f" are listed in the '{HISTORY_PLANET_TITLE}' module. They are history only: the other modules "
        "and their links come from the source code, not from the sessions."
    )


def _attach_user_sats(target: PlanetNode, sats: Iterable) -> None:
    for us in sats:
        key = us.title.strip().lower()
        target.satellites = [s for s in target.satellites if s.title.strip().lower() != key]
        target.satellites.append(us)


def merge_generated_planets(
    old_planets: List[PlanetNode],
    fresh_planets: List[PlanetNode],
    keep_kinds: Iterable[str] = (),
) -> List[PlanetNode]:
    """Returns `fresh_planets` with user edits carried over from `old_planets`.

    Matching is by title (case-insensitive). For a match: `ai_summary` is kept when the
    kinds are equal (or the old module predates `kind`); a user-modified module keeps
    its summary, colour and position; user-modified sub-components replace same-title
    ones. Unmatched old modules: kinds in `keep_kinds` are kept unchanged (e.g. the
    Work History module during a code refresh); user-modified ones are kept; otherwise
    only their user-modified sub-components survive (in a module of the same title)."""
    keep = set(keep_kinds)
    by_title: Dict[str, PlanetNode] = {}
    for p in fresh_planets:
        by_title.setdefault(p.title.strip().lower(), p)
    carried: List[PlanetNode] = []
    for old in old_planets:
        target = by_title.get(old.title.strip().lower())
        if old.kind in keep and (target is None or target.kind != old.kind):
            carried.append(old)
            continue
        user_sats = [s for s in old.satellites if s.is_user_modified]
        if target is not None:
            if old.ai_summary and old.kind in ("", target.kind):
                target.ai_summary = old.ai_summary
            if old.is_user_modified:
                target.summary = old.summary
                target.color = old.color
                target.angle = old.angle
                target.orbit_radius = old.orbit_radius
                target.is_user_modified = True
            _attach_user_sats(target, user_sats)
            continue
        if old.is_user_modified:
            carried.append(old)
        elif user_sats:
            old.satellites = user_sats
            carried.append(old)
    return list(fresh_planets) + carried


def refresh_system_from_code(existing: SolarSystem, fresh: SolarSystem, core_plan: str) -> None:
    """Replaces the code-derived modules and links of `existing` with those of `fresh`
    (see `merge_generated_planets`), keeps the Work History module, user-drawn links and
    the Ollama block, and rewrites the core plan unless the user edited it."""
    captured = existing.capture_user_links()
    existing.planets = merge_generated_planets(existing.planets, fresh.planets, keep_kinds={"history"})
    existing.links = list(fresh.links)
    existing.restore_user_links(captured)
    if not existing.is_user_modified:
        existing.core_plan = core_plan + history_note(existing) + _ollama_tail(existing.core_plan)
    merge_purpose(existing, fresh)
    merge_identity(existing, fresh)
    update_core_plan_purpose(existing)
    _keep_only_real_links(existing)


def _transfer_user_content(src: SolarSystem, dst: SolarSystem) -> None:
    """Moves user-modified modules, sub-components, links and notes from `src` to `dst`."""
    from core.models import generate_id

    title_map = {p.title.strip().lower(): p for p in dst.planets}
    captured = src.capture_user_links()
    for p in src.planets:
        user_sats = [s for s in p.satellites if s.is_user_modified]
        if not p.is_user_modified and not user_sats:
            continue
        key = p.title.strip().lower()
        target = title_map.get(key)
        if target is None:
            new_p = copy.deepcopy(p)
            new_p.id = generate_id()
            if not p.is_user_modified:
                new_p.satellites = [copy.deepcopy(s) for s in user_sats]
            dst.planets.append(new_p)
            title_map[key] = new_p
        else:
            if p.is_user_modified and not target.is_user_modified:
                target.summary, target.color = p.summary, p.color
                target.angle, target.orbit_radius = p.angle, p.orbit_radius
                target.is_user_modified = True
            _attach_user_sats(target, [copy.deepcopy(s) for s in user_sats])
        p.is_user_modified = False
        p.satellites = [s for s in p.satellites if not s.is_user_modified]
    for link, s_title, d_title in captured:
        sp, tp = title_map.get(s_title), title_map.get(d_title)
        if sp is None or tp is None or sp is tp:
            continue
        if not any(l.source_planet_id == sp.id and l.target_planet_id == tp.id for l in dst.links):
            new_l = copy.deepcopy(link)
            new_l.id = generate_id()
            new_l.source_planet_id, new_l.target_planet_id = sp.id, tp.id
            dst.links.append(new_l)
        src.links.remove(link)
    if src.notes:
        dst.notes.extend(src.notes)
        src.notes = []


def consolidate_folder_systems(store: GalaxyStore, systems: List[SolarSystem]) -> Tuple[Optional[SolarSystem], int]:
    """Merges several systems for the same folder into the best one (codebase first,
    then the one with user edits, then the most modules). User content of the others is
    moved into it and their sources are added; the others are removed from the store
    only if nothing user-made is left in them. Returns (kept system, removed count)."""
    uniq = list({id(s): s for s in systems}.values())
    if not uniq:
        return None, 0
    if len(uniq) == 1:
        return uniq[0], 0
    ordered = sorted(uniq, key=lambda s: (s.source != "codebase", not has_user_content(s), -len(s.planets)))
    best = ordered[0]
    removed = 0
    for other in ordered[1:]:
        _transfer_user_content(other, best)
        for src in [other.source] + list(other.sources or []):
            if src and src != "manual":
                add_source(best, src)
        if not has_user_content(other) and other in store.systems:
            store.systems.remove(other)
            removed += 1
    return best, removed
