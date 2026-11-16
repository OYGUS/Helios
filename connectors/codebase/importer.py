"""The public importer, `CodebaseImporter`: analyses a directory (or converts a
`.ua/knowledge-graph.json` graph) and saves or refreshes the system in the store.

1. Imports `.ua/knowledge-graph.json` or `.understand-anything/knowledge-graph.json`
   directly if present (the graph is converted, not re-analysed).

Project identity and refresh rules: see `connectors.codebase.project`.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from core import paths as app_paths
from core.models import GalaxyStore, PlanetNode, SolarSystem
from connectors.codebase.constants import (
    CODE_EXTENSIONS,
    LAST_SCAN_FAILURES,
    MAX_PLANETS,
    OTHER_PLANET_TITLE,
    SATELLITE_CAP,
    SUMMARY_FILE_LIST_CAP,
)
from connectors.codebase.fileinfo import _classify_file_layer, _split_tokens
from connectors.codebase.walk import _should_skip_dir
from connectors.codebase.content import _keep_only_real_links, _normalized_path_key
from connectors.codebase.mapbuild import build_code_map
from connectors.codebase.project import (
    LAST_SCAN_STATS,
    _identity_keys,
    add_source,
    consolidate_folder_systems,
    count_skipped_ignored,
    find_systems_for_folder,
    has_user_content,
    ignored_folder_keys,
    is_ignored_folder,
    refresh_system_from_code,
)

#: Same logger name as before the split into `connectors.codebase`.
logger = logging.getLogger("connectors.codebase_importer")


# ---------------------------------------------------------------------------
# Public importer
# ---------------------------------------------------------------------------
class CodebaseImporter:
    """Converts local project directories or Understand-Anything (.ua) graphs into Helios systems."""

    @staticmethod
    def analyze_directory(folder_path: Path, store: GalaxyStore) -> Optional[SolarSystem]:
        """Analyzes the given directory and adds it to the store (`source='codebase'`), or
        refreshes the existing system for the same folder under any source."""
        folder = Path(folder_path).resolve()
        if not folder.exists() or not folder.is_dir():
            return None
        if is_ignored_folder(store, folder):
            count_skipped_ignored("codebase", str(folder))
            return None

        for ua_dir_name in (".ua", ".understand-anything"):
            ua_graph_path = folder / ua_dir_name / "knowledge-graph.json"
            if ua_graph_path.exists():
                ua_sys = CodebaseImporter._import_from_ua_graph(folder, ua_graph_path)
                if ua_sys:
                    return CodebaseImporter._save_or_update(store, ua_sys)

        system = CodebaseImporter._build_system(folder)
        if system is None:
            return None
        return CodebaseImporter._save_or_update(store, system)

    @staticmethod
    def _build_system(folder: Path) -> Optional[SolarSystem]:
        project_name = folder.name or str(folder)
        system = SolarSystem(
            name=project_name,
            project_name=project_name,
            sun_title=project_name,
            core_plan="",
            source="codebase",
            source_id=_normalized_path_key(str(folder)),
            workspace_path=str(folder),
        )
        stats = build_code_map(folder, system)
        if stats.files == 0:
            return None
        system.core_plan = stats.core_plan
        return system

    @staticmethod
    def _import_from_ua_graph(folder: Path, graph_path: Path) -> Optional[SolarSystem]:
        """Converts an Understand-Anything `.ua/knowledge-graph.json` file directly into a project system."""
        try:
            data = json.loads(graph_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None

        project_name = str(data.get("projectName") or data.get("name") or folder.name)
        summary = data.get("summary") or data.get("description") or ""
        nodes = [n for n in (data.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (data.get("edges") or []) if isinstance(e, dict)]

        core_plan = (
            f"{summary or f'{project_name} (no summary in the knowledge graph).'}\n\n"
            f"Map: imported from an Understand-Anything knowledge graph (`{graph_path.relative_to(folder)}`), "
            f"{len(nodes)} nodes, {len(edges)} edges. The source code was not re-analysed by Helios.\n"
            f"Folder: {folder}"
        )
        system = SolarSystem(
            name=project_name,
            project_name=project_name,
            sun_title=f"{project_name} (.ua Graph)",
            core_plan=core_plan,
            source="codebase",
            source_id=_normalized_path_key(str(folder)),
            workspace_path=str(folder),
        )

        by_layer: Dict[str, List[dict]] = defaultdict(list)
        for node in nodes:
            layer_name = str(node.get("layer") or node.get("type") or "core").strip().lower() or "core"
            by_layer[layer_name].append(node)

        groups = sorted(by_layer.items(), key=lambda kv: -len(kv[1]))
        folded: List[Tuple[str, int]] = []
        if len(groups) > MAX_PLANETS:
            folded = [(k, len(v)) for k, v in groups[MAX_PLANETS - 1 :]]
            other_nodes = [n for _, v in groups[MAX_PLANETS - 1 :] for n in v]
            groups = groups[: MAX_PLANETS - 1] + [("__other__", other_nodes)]

        planet_by_node_id: Dict[str, PlanetNode] = {}
        for layer_name, n_list in groups:
            if layer_name == "__other__":
                mapped_layer = "core"
                title = OTHER_PLANET_TITLE
            else:
                mapped_layer = _classify_file_layer(f"{layer_name}/x")
                if mapped_layer == "core" and "test" in _split_tokens(layer_name):
                    mapped_layer = "testing"
                title = f"Layer: {layer_name.title()}"
            shown = n_list[:SATELLITE_CAP]
            p_summary = f"{len(n_list)} components in the Understand-Anything '{layer_name}' group (imported as-is from the knowledge graph)."
            if folded and layer_name == "__other__":
                p_summary += "\nFolded groups: " + ", ".join(f"{t} ({c})" for t, c in folded)
            if len(n_list) > len(shown):
                hidden_names = [str(n.get("label") or n.get("name") or n.get("id") or "?") for n in n_list[len(shown) :]]
                p_summary += f"\n+{len(hidden_names)} more components not shown:\n• " + "\n• ".join(hidden_names[:SUMMARY_FILE_LIST_CAP])
            planet = system.add_planet(title=title, summary=p_summary, layer=mapped_layer)
            planet.kind = "layer"
            for n in n_list:
                if n.get("id") is not None:
                    planet_by_node_id[str(n["id"])] = planet
            for n in shown:
                n_title = str(n.get("label") or n.get("name") or n.get("id") or "Component")
                n_desc = str(n.get("summary") or n.get("description") or "")
                planet.add_satellite(
                    title=n_title[:60],
                    content=n_desc or json.dumps(n, ensure_ascii=False, indent=2)[:800],
                    visual_tag="code",
                    file_path=str(n.get("filePath") or n.get("path") or ""),
                )

        edge_groups: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        pid_to_planet = {p.id: p for p in system.planets}
        for edge in edges:
            s_id = str(edge.get("source", ""))
            t_id = str(edge.get("target", ""))
            sp, tp = planet_by_node_id.get(s_id), planet_by_node_id.get(t_id)
            if sp and tp and sp.id != tp.id:
                edge_groups[(sp.id, tp.id)].append(str(edge.get("type") or edge.get("label") or "connected"))
        for (sid, tid), kinds in edge_groups.items():
            kc = Counter(kinds)
            label = f"{len(kinds)} edge{'s' if len(kinds) != 1 else ''} (" + ", ".join(k for k, _ in kc.most_common(2)) + ")"
            if sid in pid_to_planet and tid in pid_to_planet:
                system.add_link(sid, tid, label=label, relation_type="depends_on")

        _keep_only_real_links(system)
        return system

    @staticmethod
    def _find_matches(store: GalaxyStore, parsed: SolarSystem) -> List[SolarSystem]:
        """Systems for the same folder under any non-manual source, plus legacy codebase
        entries (old source_id formats, or no workspace path but the same name)."""
        matches = find_systems_for_folder(store, parsed.workspace_path)
        key = parsed.source_id
        legacy_key = parsed.workspace_path.lower()
        for s in store.systems:
            if s.source != "codebase" or s in matches:
                continue
            if s.source_id and s.source_id in (key, legacy_key) and (not s.workspace_path or _identity_keys(s.workspace_path) & _identity_keys(parsed.workspace_path)):
                matches.append(s)
        if not matches:
            name = parsed.project_name.strip().lower()
            for s in store.systems:
                if s.source == "codebase" and not s.workspace_path and name in {s.project_name.strip().lower(), s.name.strip().lower()}:
                    matches.append(s)
                    break
        return matches

    @staticmethod
    def _find_existing(store: GalaxyStore, parsed: SolarSystem) -> Optional[SolarSystem]:
        """The system that a scan of `parsed.workspace_path` updates (no consolidation)."""
        matches = CodebaseImporter._find_matches(store, parsed)
        if not matches:
            return None
        return sorted(matches, key=lambda s: (s.source != "codebase", not has_user_content(s), -len(s.planets)))[0]

    @staticmethod
    def _disambiguate_name(store: GalaxyStore, parsed: SolarSystem, exclude: Iterable[SolarSystem] = ()) -> None:
        """Gives a different folder with an already-used project name (any non-manual
        source) a distinct name, so name-based grouping/merging never mixes two folders."""
        excluded = {id(s) for s in exclude}
        name = parsed.project_name.strip().lower()
        clash = any(
            id(s) not in excluded
            and s.source != "manual"
            and name in {s.project_name.strip().lower(), s.name.strip().lower()}
            for s in store.systems
        )
        if not clash:
            return
        parent = Path(parsed.workspace_path).parent.name or Path(parsed.workspace_path).anchor
        old_name = parsed.project_name
        new_name = f"{old_name} ({parent})"
        parsed.name = new_name
        parsed.project_name = new_name
        if parsed.sun_title.startswith(old_name):
            parsed.sun_title = new_name + parsed.sun_title[len(old_name) :]

    @staticmethod
    def _save_or_update(store: GalaxyStore, parsed: SolarSystem) -> SolarSystem:
        matches = CodebaseImporter._find_matches(store, parsed)
        existing, _removed = consolidate_folder_systems(store, matches)
        same_folder = matches + find_systems_for_folder(store, parsed.workspace_path)
        if existing is None:
            CodebaseImporter._disambiguate_name(store, parsed, same_folder)
            real_links = list(parsed.links)
            added = store.add_system(parsed)
            if added.links != real_links:
                added.links = real_links
                store.save()
            return added

        if existing.source == "codebase":
            CodebaseImporter._disambiguate_name(store, parsed, same_folder)
            existing.name = parsed.name
            existing.project_name = parsed.project_name
            existing.sun_title = parsed.sun_title
            existing.source_id = parsed.source_id
            existing.workspace_path = parsed.workspace_path
        elif not existing.workspace_path:
            existing.workspace_path = parsed.workspace_path
        refresh_system_from_code(existing, parsed, parsed.core_plan)
        add_source(existing, "codebase")
        store.save()
        return existing

    @staticmethod
    def scan_parent_projects_directory(
        parent_dir: Path,
        store: GalaxyStore,
    ) -> List[SolarSystem]:
        """Detects all sub-projects inside a parent directory and builds an architecture map for each.

        One failing project never aborts the scan; failures are logged and collected in
        the module-level `LAST_SCAN_FAILURES` list as (folder, error message)."""
        LAST_SCAN_FAILURES.clear()
        LAST_SCAN_STATS["codebase"] = {"skipped_ignored": 0, "skipped_ignored_paths": []}
        parent_dir = Path(parent_dir)
        if not parent_dir.exists() or not parent_dir.is_dir():
            return []
        ignored = ignored_folder_keys(store)
        if is_ignored_folder(store, parent_dir, ignored):
            count_skipped_ignored("codebase", str(parent_dir))
            return []

        results: List[SolarSystem] = []
        project_indicators = {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "cargo.toml",
            "go.mod",
            "pom.xml",
            ".git",
            "composer.json",
            "gemfile",
            "docker-compose.yml",
            "docker-compose.yaml",
        }

        try:
            entries = sorted(parent_dir.iterdir())
        except Exception as exc:
            LAST_SCAN_FAILURES.append((str(parent_dir), f"{type(exc).__name__}: {exc}"))
            logger.warning("Cannot list %s: %s", parent_dir, exc)
            return []

        for child in entries:
            try:
                if not child.is_dir() or child.is_symlink():
                    continue
                if _should_skip_dir(child.name, []) or child.name.startswith("$"):
                    continue
                if is_ignored_folder(store, child, ignored):
                    count_skipped_ignored("codebase", str(child))
                    continue
                try:
                    names = {p.name.lower() for p in child.iterdir()}
                except OSError:
                    names = set()
                is_project = bool(names & project_indicators)
                if not is_project:
                    is_project = any(os.path.splitext(n)[1] in CODE_EXTENSIONS for n in names)
                if not is_project:
                    continue
                sys_obj = CodebaseImporter.analyze_directory(child, store)
                if sys_obj:
                    sys_obj.merge_duplicate_nodes()
                    results.append(sys_obj)
            except Exception as exc:
                LAST_SCAN_FAILURES.append((str(child), f"{type(exc).__name__}: {exc}"))
                logger.exception("Failed to analyse project folder %s", child)

        if results:
            store.save()
        return results

    @staticmethod
    def get_helios_projects_dir() -> Path:
        """Returns Helios's `projects/` directory (`core.paths.projects_dir`), creating it if needed."""
        proj_dir = app_paths.projects_dir()
        proj_dir.mkdir(parents=True, exist_ok=True)
        return proj_dir

    @staticmethod
    def scan_helios_projects(store: GalaxyStore) -> List[SolarSystem]:
        """Scans only Helios's internal `projects/` folder and builds architecture maps for projects placed there."""
        proj_dir = CodebaseImporter.get_helios_projects_dir()
        return CodebaseImporter.scan_parent_projects_directory(proj_dir, store)
