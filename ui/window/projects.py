"""`ProjectActionsMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

Project-level actions: remove (optionally ignoring the folder), merge sources,
analyze a folder, scan the 'projects/' folder, sync AI tools, open a file / folder,
copy a project as a prompt, and the “folders skipped” status-bar link.

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _ignore_undo, _purpose_edit_target, _selected_node, _skipped_details,
    _skipped_dialog, _store_busy, _updating_ui, canvas, lbl_skipped_link, status_label,
    store
Of those, assigned by this mixin:
    _selected_node, _skipped_details, _skipped_dialog, _updating_ui
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from connectors import agent_importer
from connectors.codebase_importer import CodebaseImporter
from core.models import (
    PlanetNode,
    RELATION_TYPES,
    SOURCE_CATEGORIES,
    SatelliteNode,
    SolarSystem,
    get_source_meta,
)
from ui import theme
from ui.inspector import SkippedFoldersDialog, skipped_folder_summary
from ui.window.ignored_folders import (
    _covering_entries,
    add_ignored_folder,
    folder_is_ignored,
    normalize_folder,
    remove_ignored_folder,
)
from ui.window.remove_project_dialog import RemoveProjectDialog
from ui.workers import AgentSyncWorker, CodebaseScanWorker


class ProjectActionsMixin:
    """Project-level actions: remove, merge, analyze, scan, sync, open, copy as prompt."""

    def _open_helios_projects_folder(self) -> None:
        p_dir = CodebaseImporter.get_helios_projects_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(p_dir))
            else:
                subprocess.Popen(["xdg-open", str(p_dir)])
            self.status_label.setText(f"Opened 'projects/' directory: {p_dir}")
        except Exception as exc:
            QMessageBox.warning(self, "Could not open folder", f"Error: {exc}")

    def _scan_helios_projects_directly(self) -> None:
        worker = CodebaseScanWorker(self.store)
        if not self._run_store_worker(
            worker, self._on_projects_scan_finished, "Scanning the Helios 'projects/' directory..."
        ):
            QMessageBox.information(
                self,
                "Scan already running",
                "Another scan is running. Please wait for it to finish.",
            )

    def _on_projects_scan_finished(self, ok: bool, msg: str, payload: object) -> None:
        if not ok:
            self.status_label.setText(msg)
            QMessageBox.warning(self, "Scan failed", msg)
            return
        data = payload if isinstance(payload, dict) else {}
        results = data.get("systems") or []
        p_dir = data.get("projects_dir") or CodebaseImporter.get_helios_projects_dir()
        if results:
            # Tree, canvas, anchor card and breadcrumb were rebuilt by _refresh_after_store_worker.
            self.status_label.setText(
                f"Analyzed {len(results)} project(s) in the Helios 'projects/' directory!"
            )
            QMessageBox.information(
                self,
                "Projects scanned",
                f"Analyzed {len(results)} project(s) in the 'projects/' directory and generated their architecture maps!",
            )
        else:
            QMessageBox.information(
                self,
                "No projects found",
                f"No project directories were found inside '{p_dir}'.\n\n"
                f"Copy any project folders you want to inspect into this 'projects/' directory.",
            )

    def _remove_project(self, system: Optional[SolarSystem]) -> bool:
        if system is None or self._store_busy or not any(s is system for s in self.store.systems):
            return False
        choice = RemoveProjectDialog.ask(system, self)
        if choice not in (RemoveProjectDialog.REMOVE, RemoveProjectDialog.IGNORE):
            return False
        ignore = choice == RemoveProjectDialog.IGNORE and bool((system.workspace_path or "").strip())
        self._do_remove_project(system, ignore)
        return True

    def _do_remove_project(self, system: SolarSystem, ignore: bool) -> None:
        name = system.project_name or system.name
        self.store.push_undo()
        depth = len(getattr(self.store, "_undo_stack", []))
        self.store.systems = [s for s in self.store.systems if s is not system]
        ignored = ""
        if ignore and add_ignored_folder(self.store, system.workspace_path):
            ignored = normalize_folder(system.workspace_path)
            self._ignore_undo.append((depth, ignored))
        self._save_store_quietly()
        if self._purpose_edit_target is system:
            self._cancel_purpose_edit(refresh=False)
        remaining = list(self.store.systems)
        self.canvas.active_system = remaining[0] if remaining else None
        self.canvas.selected_node = None
        self._selected_node = None
        self._updating_ui = True
        try:
            self.canvas.switch_to_galaxy_view()
            self._populate_tree()
            self._refresh_sun_anchor_card()
            self._update_breadcrumb()
        finally:
            self._updating_ui = False
        if self.canvas.active_system is not None:
            self._load_node_into_inspector(self.canvas.active_system, "sun")
        else:
            self._clear_inspector()
        msg = f"Removed '{name}'."
        if ignored:
            msg += " Its folder will not be imported again (Settings › Ignored folders)."
        self.status_label.setText(msg + " Ctrl+Z to undo.")

    def _confirm_analyze_ignored(self, folder: Path) -> bool:
        """Themed question: the chosen folder is on the ignore list. True = stop ignoring and analyze."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("This folder is ignored")
        box.setText(f"'{folder.name}' is on your list of folders Helios never imports.")
        box.setInformativeText(f"{folder}\n\nStop ignoring it and analyze it now?")
        btn_go = box.addButton("Stop ignoring and analyze", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_go)
        box.exec()
        return box.clickedButton() is btn_go

    def _merge_specific_systems(self, sys_list: List[SolarSystem]) -> None:
        """Merges different sources of the same project selected by the user into a single project."""
        if len(sys_list) <= 1:
            return

        proj_name = sys_list[0].project_name or sys_list[0].name
        sources_str = " & ".join(s.source.capitalize() for s in sys_list)
        reply = QMessageBox.question(
            self,
            "Merge projects",
            f"The {len(sys_list)} sources of '{proj_name}' ({sources_str}) will be merged into one project.\n\n"
            "All modules, overviews, links and notes are kept.\n\n"
            "Merge them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.store.push_undo()
        primary = sys_list[0]
        others = sys_list[1:]
        p_cnt, m_cnt = self.store.merge_systems(primary, others)
        self.store.save()

        self._updating_ui = True
        self._populate_tree()
        self.canvas.rebuild_scene()
        self.canvas.enter_solar_system(primary)
        self._refresh_sun_anchor_card()
        self._update_breadcrumb()
        self._updating_ui = False

        self.status_label.setText(f"Merged the sources of '{proj_name}'.")
        QMessageBox.information(
            self,
            "Merge complete",
            f"Merged {p_cnt} sources and {m_cnt} modules or components of '{proj_name}'.",
        )

    def _open_node_path(self, node_obj: Optional[object]) -> None:
        target_path_str = ""
        if isinstance(node_obj, (SatelliteNode, PlanetNode)) and getattr(node_obj, "file_path", ""):
            target_path_str = node_obj.file_path
        elif isinstance(node_obj, SolarSystem) and node_obj.workspace_path:
            target_path_str = node_obj.workspace_path
        elif self.canvas.active_system and self.canvas.active_system.workspace_path:
            target_path_str = self.canvas.active_system.workspace_path

        if not target_path_str:
            QMessageBox.information(
                self,
                "No file or folder",
                "Helios has no saved file or folder location for this item.",
            )
            return

        target_path = Path(target_path_str)
        if not target_path.exists():
            # Open parent folder if it exists
            if target_path.parent.exists():
                target_path = target_path.parent
            else:
                QMessageBox.warning(
                    self,
                    "Location not found",
                    f"The specified path was not found on disk:\n{target_path_str}",
                )
                return

        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target_path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target_path)])
            else:
                subprocess.Popen(["xdg-open", str(target_path)])
            self.status_label.setText(f"Opened: {target_path}")
        except Exception as exc:
            QMessageBox.warning(self, "Could not open", f"Could not open the location:\n{exc}")

    def _copy_project_as_prompt(self, sys_obj: Optional[SolarSystem]) -> None:
        if not sys_obj:
            return
        cat_meta = get_source_meta(sys_obj.source)
        lines: List[str] = [
            f"# PROJECT ARCHITECTURAL CONTEXT: {sys_obj.project_name or sys_obj.name}",
        ]
        purpose = " ".join(str(getattr(sys_obj, "purpose", "") or "").split())
        if purpose:
            lines.append(f"Purpose: {purpose}")
        lines.append(f"Source: {cat_meta['title']}")
        if sys_obj.workspace_path:
            lines.append(f"Workspace Directory: {sys_obj.workspace_path}")
        lines.append("\n## 1. Project Topic, Purpose, and Overall Architecture")
        lines.append(sys_obj.core_plan.strip())

        if sys_obj.planets:
            lines.append("\n## 2. Architectural Modules and Sub-Components")
            for idx, p in enumerate(sys_obj.planets, start=1):
                lines.append(f"\n### {idx}. {p.title}")
                if p.summary:
                    lines.append(p.summary.strip())
                ai_text = " ".join(str(getattr(p, "ai_summary", "") or "").split())
                if ai_text:
                    lines.append(f"AI description (Ollama, may be inaccurate): {ai_text}")
                for s in p.satellites:
                    s_desc = s.content.strip().replace("\n", " ")
                    if len(s_desc) > 220:
                        s_desc = s_desc[:220].rstrip() + "..."
                    lines.append(f"- **{s.title}**: {s_desc}")

        if sys_obj.links:
            lines.append("\n## 3. Data Flow and Inter-Module Relationships")
            p_map = {p.id: p.title for p in sys_obj.planets}
            for l in sys_obj.links:
                src_t = p_map.get(l.source_planet_id, "?")
                dst_t = p_map.get(l.target_planet_id, "?")
                rel_meta = RELATION_TYPES.get(l.relation_type, ("Data Flow", "#3B82F6"))[0]
                lines.append(f"- **{src_t}** ➔ **{dst_t}** [{l.label} / {rel_meta}]")
                if l.description:
                    lines.append(f"  *Technical Contract & Flow:* {l.description.strip()}")

        if sys_obj.notes:
            lines.append("\n## 4. Architectural Rules and Design Notes")
            for n in sys_obj.notes:
                lines.append(f"### Note: {n.title}")
                lines.append(n.content.strip())

        prompt_text = "\n".join(lines)
        QApplication.clipboard().setText(prompt_text)
        self.status_label.setText(
            f"Architectural summary for '{sys_obj.project_name}' copied to clipboard as AI Prompt ({len(prompt_text)} chars)."
        )

    def _update_skipped_folders_link(self) -> None:
        stats = getattr(agent_importer, "LAST_SCAN_STATS", {})
        try:
            total, details = skipped_folder_summary(stats)
        except Exception:
            total, details = 0, []
        self._skipped_details = details
        if total > 0:
            self.lbl_skipped_link.setText(
                f"· <a href='skipped' style='color:{theme.ACCENT_TEXT}; text-decoration:none;'>"
                f"{total} folder{'s' if total != 1 else ''} skipped</a>"
            )
            self.lbl_skipped_link.setToolTip("Show which folders were skipped and why")
            self.lbl_skipped_link.setVisible(True)
        else:
            self.lbl_skipped_link.setVisible(False)

    def _show_skipped_folders_dialog(self) -> None:
        if not self._skipped_details:
            return
        titles = {k: str(v.get("title", k)) for k, v in SOURCE_CATEGORIES.items()}
        if self._skipped_dialog is not None:
            self._skipped_dialog.close()
            self._skipped_dialog.deleteLater()
        dlg = SkippedFoldersDialog(self._skipped_details, titles, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self._skipped_dialog = dlg
        dlg.show()

    def _analyze_codebase_folder(self) -> None:
        folder_str = QFileDialog.getExistingDirectory(
            self,
            "Choose a project folder to analyze",
            str(Path.home() / "Desktop"),
        )
        if not folder_str:
            return

        folder_path = Path(folder_str)
        self._analyze_folder_path(folder_path)

    def _analyze_folder_path(self, folder_path: Path) -> bool:
        if folder_is_ignored(self.store, folder_path):
            if not self._confirm_analyze_ignored(folder_path):
                self.status_label.setText(f"'{folder_path.name}' is ignored; nothing was analyzed.")
                return False
            for entry in _covering_entries(self.store, folder_path) or [str(folder_path)]:
                remove_ignored_folder(self.store, entry)
            self.status_label.setText(f"'{folder_path.name}' is no longer ignored.")
        worker = CodebaseScanWorker(self.store, folder=folder_path)
        if not self._run_store_worker(
            worker, self._on_codebase_folder_analyzed, f"Analyzing codebase '{folder_path.name}'..."
        ):
            QMessageBox.information(
                self,
                "Scan already running",
                "Another scan is running. Please wait for it to finish.",
            )
            return False
        return True

    def _on_codebase_folder_analyzed(self, ok: bool, msg: str, payload: object) -> None:
        if not ok:
            self.status_label.setText(msg)
            QMessageBox.warning(self, "Analysis failed", msg)
            return
        data = payload if isinstance(payload, dict) else {}
        system = data.get("system")
        folder_path = Path(data.get("folder") or ".")
        if not system:
            QMessageBox.warning(
                self,
                "Nothing to analyze",
                f"No supported source code or Markdown files were found in '{folder_path.name}'.",
            )
            return

        self.canvas.enter_solar_system(system)
        self._populate_tree()
        self._refresh_sun_anchor_card()
        self.status_label.setText(
            f"'{system.project_name}' analyzed: {len(system.planets)} modules, {len(system.links)} links."
        )
        # Purpose first (if missing), then the module descriptions.
        self._maybe_auto_suggest_purposes(then_modules_id=system.id)

    def _sync_agent_histories(self) -> None:
        connected = self.store.get_setting("connected_sources", ["antigravity", "codebase"])
        sources = list(connected) if isinstance(connected, list) and connected else []
        worker = AgentSyncWorker(self.store, sources, max_sessions=30)
        if not self._run_store_worker(
            worker, self._on_agent_histories_synced, "Scanning connected AI tool histories..."
        ):
            QMessageBox.information(
                self,
                "Scan already running",
                "Another scan is running. Please wait for it to finish.",
            )

    def _on_agent_histories_synced(self, ok: bool, msg: str, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        connected = data.get("sources")
        if connected is None:
            connected = self.store.get_setting("connected_sources", ["antigravity", "codebase"])
        if not ok:
            self.status_label.setText(msg)
            QMessageBox.warning(self, "Sync failed", msg)
            return
        updated = data.get("updated", 0)
        conn_str = ", ".join(str(c).title() for c in (connected if isinstance(connected, list) else []))
        QMessageBox.information(
            self,
            "AI tools synced",
            f"Scanned connected AI tools ({conn_str or 'None'}).\n"
            f"Projects updated while preserving user edits: {updated}",
        )
