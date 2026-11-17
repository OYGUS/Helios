"""`NavigationMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

Canvas signals and navigation: selection / view-mode changes, the breadcrumb, canvas
context actions, search results, the map filter button, export, the guided tour and
the top-bar view toggles (motion, show all details).

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _selected_node, _selected_type, _store_busy, btn_bc_galaxy, btn_bc_project,
    btn_filter, btn_show_details, btn_toggle_orbit, canvas, central_stack,
    filter_popover, lbl_bc_sep1, lbl_bc_sep2, lbl_breadcrumb, status_label, store,
    top_actions, tour_card, tour_status_label
Of those, assigned by this mixin:
    _selected_node, _selected_type
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog

from core.models import CanvasNote, PlanetLink, PlanetNode, SatelliteNode, SolarSystem
from ui import export as map_export, theme


class NavigationMixin:
    """Canvas signals, breadcrumb, search, filter, export, tour and view toggles."""

    def _toggle_show_all_details(self) -> None:
        self.canvas.set_show_all_details(self.btn_show_details.isChecked())
        self.status_label.setText(
            "Showing every module's components." if self.canvas.show_all_details
            else "Components are shown for the selected or hovered module."
        )

    def _on_show_all_details_changed(self, enabled: bool) -> None:
        if self.btn_show_details.isChecked() != enabled:
            self.btn_show_details.setChecked(enabled)

    def _on_project_crumb_clicked(self) -> None:
        if self.canvas.active_system is None:
            return
        if self.canvas.view_mode != "system":
            self.canvas.enter_solar_system(self.canvas.active_system)
        else:
            self.canvas.focus_on_sun()

    def _update_breadcrumb(self) -> None:
        sys_obj = self.canvas.active_system
        in_galaxy = self.canvas.view_mode == "galaxy"
        self.btn_bc_galaxy.setProperty("current", "true" if in_galaxy or not sys_obj else "false")
        theme.repolish(self.btn_bc_galaxy)
        if in_galaxy or not sys_obj:
            self.lbl_bc_sep1.setVisible(False)
            self.btn_bc_project.setVisible(False)
            self.lbl_bc_sep2.setVisible(False)
            n = len(self.store.systems)
            self.lbl_breadcrumb.setText(f"{n} project{'s' if n != 1 else ''}" if in_galaxy else "")
            theme.set_role(self.lbl_breadcrumb, "muted")
            return

        proj_name = sys_obj.project_name or sys_obj.name
        fm = self.btn_bc_project.fontMetrics()
        self.btn_bc_project.setText(fm.elidedText(proj_name, Qt.TextElideMode.ElideRight, 190))
        self.btn_bc_project.setToolTip(f"{proj_name} — show the whole project")
        self.lbl_bc_sep1.setVisible(True)
        self.btn_bc_project.setVisible(True)

        parts: List[str] = []
        sel = self.canvas.selected_node
        if isinstance(sel, PlanetNode):
            parts.append(sel.title)
        elif isinstance(sel, SatelliteNode):
            parent_p = sys_obj.find_parent_planet(sel)
            if parent_p:
                parts.append(parent_p.title)
            parts.append(sel.title)
        elif isinstance(sel, PlanetLink):
            p_map = {p.id: p.title for p in sys_obj.planets}
            parts.append(f"{p_map.get(sel.source_planet_id, '?')} → {p_map.get(sel.target_planet_id, '?')}")
        elif isinstance(sel, CanvasNote):
            parts.append(sel.title)
        self.btn_bc_project.setProperty("current", "false" if parts else "true")
        theme.repolish(self.btn_bc_project)
        self.lbl_bc_sep2.setVisible(bool(parts))
        theme.set_role(self.lbl_breadcrumb, "crumb")
        self.lbl_breadcrumb.setText("  ›  ".join(parts))

    def _on_canvas_node_selected(self, node_obj: object, node_type: str) -> None:
        if node_type == "sun" and isinstance(node_obj, SolarSystem):
            self._refresh_sun_anchor_card()
            # A full rebuild only when the data changed; a pure selection resets the rows.
            self._refresh_tree_for_selection()

        self._highlight_node_in_tree(node_obj)
        self._load_node_into_inspector(node_obj, node_type)
        self._update_breadcrumb()

    def _on_view_mode_changed(self, mode: str) -> None:
        self._update_breadcrumb()
        self._on_map_filter_changed("")
        if mode == "galaxy":
            self.status_label.setText("All projects · click a card to open it · scroll to zoom")
        else:
            self.status_label.setText(
                "Click a module to see its details · drag to move · right-click for more · Esc for all projects"
            )
            self._refresh_sun_anchor_card()

    def _on_drawing_tool_changed(self, tool_key: str, hint_text: str) -> None:
        if hint_text:
            self.status_label.setText(hint_text)

    def _on_canvas_context_action(self, action_name: str, target_obj: object) -> None:
        if action_name == "ollama_enhance" and isinstance(target_obj, SolarSystem):
            self._run_ollama_for_systems([target_obj])
        elif action_name == "add_planet" and isinstance(target_obj, SolarSystem):
            self.canvas.active_system = target_obj
            self._add_planet_to_active()
        elif action_name == "add_satellite" and isinstance(target_obj, PlanetNode):
            self._selected_node = target_obj
            self._selected_type = "planet"
            self._add_satellite_to_selected()
        elif action_name == "add_link" and isinstance(target_obj, PlanetNode):
            self._selected_node = target_obj
            self._selected_type = "planet"
            self._prompt_add_link_from_selected()
        elif action_name == "edit_link_label" and isinstance(target_obj, PlanetLink):
            new_lbl, ok = QInputDialog.getText(
                self,
                "Rename link",
                "What does this link mean? (e.g. imports, calls, sends data to)",
                text=target_obj.label,
            )
            if ok and new_lbl.strip():
                self.store.push_undo()
                target_obj.label = new_lbl.strip()
                target_obj.is_user_modified = True  # edited by the user
                self.store.save()
                self.canvas.rebuild_scene()
                self.canvas.select_object(target_obj, "link")
                self.status_label.setText(f"Link renamed to '{target_obj.label}'.")
        elif action_name == "clear_links" and isinstance(target_obj, PlanetNode):
            if self.canvas.active_system:
                self.store.push_undo()
                self.canvas.active_system.links = [
                    l
                    for l in self.canvas.active_system.links
                    if l.source_planet_id != target_obj.id and l.target_planet_id != target_obj.id
                ]
                self.store.save()
                self.canvas.rebuild_scene()
                self._load_node_into_inspector(target_obj, "planet")
                self.status_label.setText(f"Cleared links for module '{target_obj.title}'.")
        elif action_name == "open_path":
            self._open_node_path(target_obj)
        elif action_name == "copy_prompt" and isinstance(target_obj, SolarSystem):
            self._copy_project_as_prompt(target_obj)
        elif action_name == "rebalance":
            self._rebalance_active_system_layout()
        elif action_name == "delete_node":
            self._selected_node = target_obj
            if isinstance(target_obj, PlanetNode):
                self._selected_type = "planet"
            elif isinstance(target_obj, SatelliteNode):
                self._selected_type = "satellite"
            elif isinstance(target_obj, PlanetLink):
                self._selected_type = "link"
            elif isinstance(target_obj, CanvasNote):
                self._selected_type = "note"
            elif isinstance(target_obj, SolarSystem):
                self._selected_type = "sun"
            self._delete_current_node()
        elif action_name == "remove_project" and isinstance(target_obj, SolarSystem):
            self._remove_project(target_obj)
        elif action_name == "edit_purpose" and isinstance(target_obj, SolarSystem):
            if self.canvas.active_system is not target_obj:
                self.canvas.enter_solar_system(target_obj)
            self._begin_purpose_edit()
        elif action_name == "create_new_project":
            self._create_new_system()
        elif action_name == "analyze_folder":
            self._analyze_codebase_folder()
        elif action_name == "scan_helios_projects":
            self._scan_helios_projects_directly()
        elif action_name == "open_projects_folder":
            self._open_helios_projects_folder()
        elif action_name == "open_integrations":
            self._switch_to_page("connections")
        elif action_name in ("export_png", "export_md") and isinstance(target_obj, SolarSystem):
            self._export_map(target_obj, action_name.split("_", 1)[1])
        elif action_name == "merge_duplicates":
            self._open_settings_dialog()
        elif action_name == "open_settings":
            self._open_settings_dialog()
        elif action_name == "refresh_tree":
            self._populate_tree()
            self._refresh_sun_anchor_card()

    def _on_search_text_changed(self, text: str) -> None:
        """Runs after typing pauses (SearchController): dims the canvas, filters the tree."""
        self.canvas.set_search_query(text)
        self._filter_tree(text)

    def _open_search_result(self, entry) -> None:
        """Opens a search result: its project, then selects and centres the node."""
        from core.search_index import find_targets

        system, planet, sat = find_targets(self.store.systems, entry)
        if system is None:
            self.status_label.setText("That item is no longer on the map.")
            return
        if self.central_stack.currentIndex() != 0:
            self._switch_to_page("studio")
        if self.canvas.view_mode != "system" or self.canvas.active_system is not system:
            self.canvas.enter_solar_system(system)
        node, kind = (sat, "satellite") if sat is not None else (planet, "planet") if planet is not None else (system, "sun")
        if node is system:
            self.canvas.select_object(system, "sun", self.canvas.sun_item)
            self.canvas.fit_to_view()
        else:
            self.canvas.select_object(node, kind)
            self.canvas.center_on_node(node)
        self.canvas.setFocus()
        where = f" in {entry.where}" if entry.where and planet is not None else ""
        self.status_label.setText(f"{entry.kind_label}: {entry.name}{where}")

    def _open_filter_popover(self) -> None:
        if self.canvas.view_mode != "system" or self.canvas.active_system is None:
            return
        self.filter_popover.open_below(self.top_actions.anchor_for(self.btn_filter))

    def _on_map_filter_changed(self, status: str) -> None:
        flt = self.canvas.map_filter
        n = flt.active_count()
        # Constant text keeps the top bar's width; an active filter turns the button amber
        # and the canvas shows a "Filter on" pill with "Show everything".
        self.btn_filter.setProperty("active", "true" if n else "false")
        theme.repolish(self.btn_filter)
        in_project = self.canvas.view_mode == "system" and self.canvas.active_system is not None
        self.btn_filter.setEnabled(in_project)
        tip = "Choose which links and modules the map shows (only the view changes)"
        if n:
            tip = f"Filter on: {n} choice{'s' if n != 1 else ''} hidden. " + tip
        self.btn_filter.setToolTip(tip if in_project else "Open a project to filter its map")
        if status:
            self.status_label.setText(status)

    def _export_map(self, system: Optional[SolarSystem], fmt: Optional[str] = None) -> None:
        if system is None or self._store_busy:
            return
        map_export.export_map(self, system, fmt)

    def _on_tour_clicked(self) -> None:
        self.canvas.start_or_advance_tour()

    def _on_tour_step_changed(self, step_idx: int, total: int, title: str) -> None:
        self.tour_card.setVisible(True)
        self.tour_status_label.setText(f"Tour {step_idx}/{total} · {title}")

    def _stop_tour(self) -> None:
        self.tour_card.setVisible(False)
        self.canvas.stop_tour()

    def _toggle_galaxy_view(self) -> None:
        if self.canvas.view_mode == "system":
            self.canvas.switch_to_galaxy_view()
        else:
            if self.canvas.active_system:
                self.canvas.enter_solar_system(self.canvas.active_system)

    def _toggle_orbit_motion(self) -> None:
        self.canvas.set_orbit_motion(not self.canvas.orbit_motion_enabled)
        self.btn_toggle_orbit.setChecked(self.canvas.orbit_motion_enabled)
        self.status_label.setText("Motion on: modules orbit slowly." if self.canvas.orbit_motion_enabled
                                  else "Motion off.")
