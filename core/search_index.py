"""A small, pure search index over everything already stored on the map (no Qt).

The search box builds one :class:`SearchIndex` from the store (only when needed, see
`ui.search`) and asks it for ranked matches. Indexed, all from stored data only:

* projects (name; their purpose and identity line match too),
* modules (title; an entry module also matches "entry point"),
* components (title; their signature matches too) — a component of kind function /
  class / method / file / package is labelled as such,
* code facts the analysis stored on a file component or file module: its complete
  `defined_names` list (functions, classes, methods; data saved before that field
  existed falls back to the clipped ``Defines:`` line) and the files listed under
  ``Files (N):`` that are not shown as components — a hit leads to that component or
  module (the place that contains it),
* external libraries (the package components of the "External Libraries" module).

Nothing is scanned again; in old data a name cut off from the ``Defines:`` line
("+3 more") is not in the index either.
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from core.map_facts import node_defined_names

# Shown as the small type label of a result.
KIND_LABELS: Dict[str, str] = {
    "project": "Project",
    "module": "Module",
    "entry": "Entry point",
    "component": "Component",
    "function": "Function",
    "class": "Class",
    "method": "Method",
    "file": "File",
    "library": "Library",
    "session": "Session",
    "name": "Name",
}
# Tie-breaker between equally good matches (lower first).
KIND_RANK: Dict[str, int] = {
    "project": 0, "entry": 1, "module": 2, "component": 3, "class": 4, "function": 5,
    "method": 6, "file": 7, "library": 8, "name": 9, "session": 10,
}
_SAT_KINDS = {
    "function": "function",
    "class": "class",
    "method": "method",
    "file": "file",
    "document": "file",
    "package": "library",
    "session": "session",
}
SEPARATOR = " › "
_WORD_START = re.compile(r"[^a-z0-9]")


@dataclass
class SearchEntry:
    name: str
    kind: str  # a key of KIND_LABELS
    system_id: str
    planet_id: str = ""  # module to open ("" = the project itself)
    satellite_id: str = ""  # component to select ("" = the module)
    where: str = ""  # "Project › Module", shown next to the name
    extra: str = ""  # more text that may match (lower-case), never shown
    key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self.key = self.name.lower()

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, "Component")


def _files_listed(summary: str) -> List[str]:
    """The bullet lines under ``Files (N):`` in a folder module's generated summary."""
    out: List[str] = []
    inside = False
    for line in str(summary or "").splitlines():
        s = line.strip()
        if s.startswith("Files (") and s.endswith(":"):
            inside = True
            continue
        if inside:
            if s.startswith("• "):
                item = s[2:].strip()
                if item and not item.startswith("+"):
                    out.append(item)
            else:
                break
    return out


def _system_entries(system: object, identity_of: Optional[Callable[[object], str]]) -> Iterator[SearchEntry]:
    """Every search entry of one project, before de-duplication, in index order."""
    sid = str(getattr(system, "id", ""))
    pname = str(getattr(system, "project_name", "") or getattr(system, "name", "") or "Project")
    planets = list(getattr(system, "planets", []) or [])
    try:
        identity = identity_of(system) if identity_of else str(getattr(system, "identity", "") or "")
    except Exception:
        identity = str(getattr(system, "identity", "") or "")
    extra = " ".join([str(getattr(system, "purpose", "") or ""), str(identity or "")])
    yield SearchEntry(pname, "project", sid, where=f"{len(planets)} module{'s' if len(planets) != 1 else ''}",
                      extra=extra.lower())
    for planet in planets:
        pid = str(getattr(planet, "id", ""))
        ptitle = str(getattr(planet, "title", "") or "")
        pkind = str(getattr(planet, "kind", "") or "")
        entry = bool(getattr(planet, "is_entry", False))
        where_p = pname
        fp = str(getattr(planet, "file_path", "") or "").replace("\\", "/").rstrip("/")
        base = fp.rsplit("/", 1)[-1] if fp else ""
        yield SearchEntry(ptitle, "entry" if entry else "module", sid, pid, where=where_p,
                          extra=(("entry point start main " if entry else "") + (base if base != ptitle else "")).lower())
        where_s = f"{pname}{SEPARATOR}{ptitle}"
        sat_names: Set[str] = set()
        for sat in list(getattr(planet, "satellites", []) or []):
            stitle = str(getattr(sat, "title", "") or "")
            sat_names.add(stitle.lower())
            skind = _SAT_KINDS.get(str(getattr(sat, "kind", "") or ""), "component")
            if pkind == "history" and skind == "component":
                skind = "session"
            sat_id = str(getattr(sat, "id", ""))
            yield SearchEntry(stitle, skind, sid, pid, sat_id, where=where_s,
                              extra=str(getattr(sat, "signature", "") or "").lower())
            where_n = f"{where_s}{SEPARATOR}{stitle}"
            for name, nkind in node_defined_names(sat, str(getattr(sat, "content", "") or "")):
                yield SearchEntry(name, nkind, sid, pid, sat_id, where=where_n)
        summary = str(getattr(planet, "summary", "") or "")
        for name, nkind in node_defined_names(planet, summary):
            yield SearchEntry(name, nkind, sid, pid, where=where_s)
        for rel in _files_listed(summary):
            fname = rel.rsplit("/", 1)[-1]
            if fname.lower() in sat_names or rel.lower() in sat_names:
                continue
            yield SearchEntry(fname, "file", sid, pid, where=where_s, extra=rel.lower())


def _dedup(entries: Iterable[SearchEntry], seen: Set[Tuple[str, str, str, str, str]], out: List[SearchEntry]) -> List[SearchEntry]:
    for entry in entries:
        k = (entry.system_id, entry.planet_id, entry.satellite_id, entry.kind, entry.key)
        if k in seen or not entry.name.strip():
            continue
        seen.add(k)
        out.append(entry)
    return out


def _system_fingerprint(system: object) -> Optional[str]:
    """The project's complete stored data as compact JSON (None for objects that are not
    the store's dataclasses): equal fingerprints mean equal search entries."""
    try:
        from core.models import dumps_compact

        return dumps_compact(system)
    except Exception:
        return None


def build_entries(
    systems: Iterable[object],
    identity_of: Optional[Callable[[object], str]] = None,
    cache: Optional[Dict[str, List[SearchEntry]]] = None,
) -> List[SearchEntry]:
    """All search entries of `systems`, de-duplicated, in store order. `identity_of` gives
    a project's identity line (default: the stored `identity` field).

    `cache` (fingerprint -> entries, filled and pruned here) lets a rebuild reuse the
    entries of projects whose data did not change (`_system_fingerprint`: their whole
    stored data as JSON, so any change of any field makes a new fingerprint)."""
    systems = list(systems)
    ids = [str(getattr(s, "id", "")) for s in systems]
    if cache is None or len(set(ids)) != len(ids):
        # Without a cache, or when two projects share an id (entries of both are then
        # de-duplicated together), build everything in one pass as always.
        seen: Set[Tuple[str, str, str, str, str]] = set()
        out: List[SearchEntry] = []
        for system in systems:
            _dedup(_system_entries(system, identity_of), seen, out)
        if cache is not None:
            cache.clear()
        return out
    # Entries never repeat across projects with different ids, so per-project lists
    # de-duplicated on their own concatenate to exactly the one-pass result.
    out = []
    fresh: Dict[str, List[SearchEntry]] = {}
    for system in systems:
        fp = _system_fingerprint(system)
        part = None if fp is None else (fresh.get(fp) or cache.get(fp))
        if part is None:
            part = _dedup(_system_entries(system, identity_of), set(), [])
        if fp is not None:
            fresh[fp] = part
        out.extend(part)
    cache.clear()
    cache.update(fresh)
    return out


def _score(entry: SearchEntry, q: str) -> Optional[int]:
    """0 exact · 1 prefix · 2 word start · 3 inside the name · 4 other text; None = no match."""
    key = entry.key
    bare = key[:-2] if key.endswith("()") else key
    for word in ("class ", "interface ", "trait ", "enum "):
        if bare.startswith(word):
            bare = bare[len(word):]
            if bare == q:
                return 0
            if bare.startswith(q):
                return 1
    if q in key:
        if key == q or bare == q:
            return 0
        if key.startswith(q):
            return 1
        idx = key.find(q)
        while idx > 0:
            if _WORD_START.match(key[idx - 1]):
                return 2
            idx = key.find(q, idx + 1)
        return 3
    if entry.extra and q in entry.extra:
        return 4
    return None


class SearchIndex:
    """Entries plus ranked lookup. Cheap to build; rebuilt when the map changes."""

    def __init__(
        self,
        systems: Iterable[object],
        identity_of: Optional[Callable[[object], str]] = None,
        cache: Optional[Dict[str, List[SearchEntry]]] = None,
    ) -> None:
        self.entries: List[SearchEntry] = build_entries(systems, identity_of, cache)
        self._keys: List[str] = [e.key for e in self.entries]
        self._extras: List[str] = [e.extra for e in self.entries]
        self._static_rank: Optional[List[tuple]] = None
        # (query, indices of the entries it matched): a longer query that contains it can
        # only match a subset of them (substring matching), so typing on narrows that list.
        self._last: Tuple[str, Optional[List[int]]] = ("", None)

    def __len__(self) -> int:
        return len(self.entries)

    def _matching(self, q: str) -> List[int]:
        """Indices (in index order) of the entries `_score` matches: the query is inside
        the name (the class/interface and "()" forms are parts of the name) or the extra text."""
        prev_q, prev = self._last
        candidates: Iterable[int] = prev if (prev is not None and prev_q and prev_q in q) else range(len(self.entries))
        keys, extras = self._keys, self._extras
        matched = [i for i in candidates if q in keys[i] or (extras[i] and q in extras[i])]
        self._last = (q, matched)
        return matched

    def search(self, query: str, limit: int = 8, active_system_id: str = "") -> Tuple[List[SearchEntry], int]:
        """Best `limit` matches and the total number of matches."""
        q = " ".join(str(query or "").lower().split())
        if not q:
            return [], 0
        matched = self._matching(q)
        if self._static_rank is None:
            self._static_rank = [(KIND_RANK.get(e.kind, 99), len(e.name), e.key, e.where.lower()) for e in self.entries]
        static = self._static_rank
        entries = self.entries
        ranked = []
        for i in matched:
            e = entries[i]
            s = _score(e, q)
            if s is None:  # pragma: no cover - `_matching` and `_score` agree
                continue
            ranked.append(((s, 0 if (active_system_id and e.system_id == active_system_id) else 1) + static[i] + (i,), i))
        # Same order as a stable sort of every match by rank (the index breaks ties).
        best = heapq.nsmallest(max(0, limit), ranked) if limit > 0 else []
        return [entries[i] for _, i in best], len(ranked)


def find_targets(systems: Sequence[object], entry: SearchEntry) -> Tuple[Optional[object], Optional[object], Optional[object]]:
    """(project, module, component) objects for an entry; None for missing parts."""
    system = next((s for s in systems if str(getattr(s, "id", "")) == entry.system_id), None)
    if system is None:
        return None, None, None
    planet = None
    if entry.planet_id:
        planet = next((p for p in system.planets if p.id == entry.planet_id), None)
    sat = None
    if planet is not None and entry.satellite_id:
        sat = next((s for s in planet.satellites if s.id == entry.satellite_id), None)
    return system, planet, sat
