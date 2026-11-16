"""`NodeEditsMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

Editing the map from the UI: saving / deleting the selected node, quick-add of
modules, components, notes and links, auto-align, and designing a new project.

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _selected_node, _selected_type, _updating_ui, canvas, combo_meta, edit_content,
    edit_title, status_label, store
Of those, assigned by this mixin:
    _updating_ui
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from core.models import CanvasNote, LAYER_DEFINITIONS, PlanetLink, PlanetNode, SatelliteNode, SolarSystem
from ui.window.new_project_dialog import NewProjectStudioDialog


class NodeEditsMixin:
    """Saving, deleting and adding nodes; designing a new project."""

    def _quick_add_module_to_system(self, sys_obj: SolarSystem) -> None:
        self.canvas.enter_solar_system(sys_obj)
        title, ok = QInputDialog.getText(
            self, "Add module", "Module name (e.g. Auth API, Database service):"
        )
        if not ok or not title.strip():
            return
        self.store.push_undo()
        layer_keys = list(LAYER_DEFINITIONS.keys())
        layer_titles = [LAYER_DEFINITIONS[k][0] for k in layer_keys]
        chosen_l_title, ok2 = QInputDialog.getItem(
            self, "Module layer", "Layer:", layer_titles, 0, False
        )
        chosen_layer = layer_keys[layer_titles.index(chosen_l_title)] if ok2 else "core"
        planet = sys_obj.add_planet(
            title=title.strip(),
            summary=f"The {title.strip()} module handles {chosen_l_title} responsibilities in {sys_obj.project_name}.",
            layer=chosen_layer,
        )
        planet.is_user_modified = True
        self.store.save()
        self.canvas.rebuild_scene()
        self.canvas.select_object(planet, "planet")
        self._populate_tree()

    def _quick_add_sat_to_planet(self, planet: PlanetNode) -> None:
        title, ok = QInputDialog.getText(
            self, "Add component", f"Component name in '{planet.title}' (e.g. TokenValidator):"
        )
        if not ok or not title.strip():
            return
        self.store.push_undo()
        sat = planet.add_satellite(
            title=title.strip(),
            content="Describe what this component does and how it works.",
            visual_tag="code",
        )
        sat.is_user_modified = True
        self.store.save()
        self.canvas.rebuild_scene()
        self.canvas.select_object(sat, "satellite")
        self._populate_tree()

    def _quick_add_note_to_system(self, sys_obj: SolarSystem) -> None:
        self.canvas.enter_solar_system(sys_obj)
        title, ok = QInputDialog.getText(
            self, "Add note", "Note title (e.g. Rule, To-do):"
        )
        if not ok or not title.strip():
            return
        self.store.push_undo()
        note = sys_obj.add_note(
            0.0,
            0.0,
            title=title.strip(),
            content="Write data flow rules, security notes, or architectural guidelines here.",
            color="#3B82F6",
        )
        note.is_user_modified = True
        self.store.save()
        self.canvas.rebuild_scene()
        self.canvas.select_object(note, "note")
        self._populate_tree()

    def _rebalance_active_system_layout(self) -> None:
        if not self.canvas.active_system:
            return
        self.store.push_undo()
        self.canvas.active_system.rebalance_layout(force=True)
        self.store.save()
        self.canvas.rebuild_scene()
        self.status_label.setText("Modules and components are back on evenly spaced orbits.")

    def _prompt_add_link_from_selected(self) -> None:
        if not isinstance(self._selected_node, PlanetNode) or not self.canvas.active_system:
            return
        src_planet = self._selected_node
        sys_obj = self.canvas.active_system
        candidates = [p for p in sys_obj.planets if p.id != src_planet.id]
        if not candidates:
            QMessageBox.information(
                self,
                "No other modules",
                "A link needs at least two modules in the project.",
            )
            return

        titles = [p.title for p in candidates]
        chosen_title, ok = QInputDialog.getItem(
            self,
            "Add link",
            f"Select the target module to connect from '{src_planet.title}':",
            titles,
            0,
            False,
        )
        if not ok or not chosen_title:
            return

        target_planet = next((p for p in candidates if p.title == chosen_title), None)
        if not target_planet:
            return

        rel_label, ok2 = QInputDialog.getText(
            self,
            "Link label",
            "What does this link mean? (e.g. imports, calls, sends data to)",
            text="uses",
        )
        if not ok2:
            return

        self.store.push_undo()
        sys_obj.add_link(
            src_planet.id,
            target_planet.id,
            label=rel_label.strip() or "connected",
            relation_type="flows_to",
        )
        self.store.save()
        self.canvas.rebuild_scene()
        self._load_node_into_inspector(src_planet, "planet")
        self.status_label.setText(
            f"Link added: {src_planet.title} → {target_planet.title}"
        )

    def _save_current_node(self) -> None:
        if not self._selected_node:
            return

        self.store.push_undo()

        new_title = self.edit_title.text().strip()
        new_content = self.edit_content.toPlainText().strip()

        if self._selected_type == "sun" and isinstance(self._selected_node, SolarSystem):
            if new_title:
                self._selected_node.project_name = new_title
                self._selected_node.name = new_title
                self._selected_node.sun_title = new_title
            self._selected_node.core_plan = new_content
            self._selected_node.is_user_modified = True

        elif self._selected_type == "planet" and isinstance(self._selected_node, PlanetNode):
            if new_title:
                self._selected_node.title = new_title
            self._selected_node.summary = new_content
            chosen_color = self.combo_meta.currentData()
            if chosen_color:
                self._selected_node.color = str(chosen_color)
            self._selected_node.is_user_modified = True

        elif self._selected_type == "satellite" and isinstance(self._selected_node, SatelliteNode):
            if new_title:
                self._selected_node.title = new_title
            self._selected_node.content = new_content
            chosen_tag = self.combo_meta.currentData()
            if chosen_tag:
                self._selected_node.visual_tag = str(chosen_tag)
            self._selected_node.is_user_modified = True

        elif self._selected_type == "link" and isinstance(self._selected_node, PlanetLink):
            if new_title:
                self._selected_node.label = new_title
            chosen_rel = self.combo_meta.currentData()
            if chosen_rel:
                self._selected_node.relation_type = str(chosen_rel)
            self._selected_node.description = new_content
            self._selected_node.is_user_modified = True

        elif self._selected_type == "note" and isinstance(self._selected_node, CanvasNote):
            if new_title:
                self._selected_node.title = new_title
            self._selected_node.content = new_content
            chosen_color = self.combo_meta.currentData()
            if chosen_color:
                self._selected_node.color = str(chosen_color)
            self._selected_node.is_user_modified = True

        self._updating_ui = True
        self.store.save()
        self._updating_ui = False
        self._refresh_sun_anchor_card()
        self._populate_tree()
        self.canvas.rebuild_scene()
        self._update_breadcrumb()
        if self._selected_node is not None:
            self._load_node_into_inspector(self._selected_node, self._selected_type)
        self.status_label.setText("Changes saved. They are kept when the project is scanned again.")

    def _delete_current_node(self) -> None:
        if not self._selected_node:
            return
        if self._selected_type == "sun" and isinstance(self._selected_node, SolarSystem):
            # Projects are removed through the remove dialog (optionally ignoring the folder).
            self._remove_project(self._selected_node)
            return

        active_sys = self.canvas.active_system

        # Ask user for deletion confirmation
        item_label = "this item"
        if self._selected_type == "sun" and isinstance(self._selected_node, SolarSystem):
            item_label = f"project '{self._selected_node.project_name}'"
        elif self._selected_type == "planet" and isinstance(self._selected_node, PlanetNode):
            item_label = f"module '{self._selected_node.title}'"
        elif self._selected_type == "satellite" and isinstance(self._selected_node, SatelliteNode):
            item_label = f"component '{self._selected_node.title}'"
        elif self._selected_type == "link" and isinstance(self._selected_node, PlanetLink):
            item_label = f"link '{self._selected_node.label}'"
        elif self._selected_type == "note" and isinstance(self._selected_node, CanvasNote):
            item_label = f"note '{self._selected_node.title}'"

        reply = QMessageBox.question(
            self,
            "Delete",
            f"Delete {item_label}?\nYou can undo this with Ctrl+Z.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.store.push_undo()

        if self._selected_type == "planet" and isinstance(self._selected_node, PlanetNode) and active_sys:
            active_sys.remove_planet(self._selected_node)
            self._updating_ui = True
            self.store.save()
            self._updating_ui = False
            self.canvas.rebuild_scene()
            self.canvas.select_object(active_sys, "sun")

        elif self._selected_type == "satellite" and isinstance(self._selected_node, SatelliteNode) and active_sys:
            parent_planet = active_sys.find_parent_planet(self._selected_node)
            if parent_planet:
                parent_planet.remove_satellite(self._selected_node)
                self._updating_ui = True
                self.store.save()
                self._updating_ui = False
                self.canvas.rebuild_scene()
                self.canvas.select_object(parent_planet, "planet")

        elif self._selected_type == "link" and isinstance(self._selected_node, PlanetLink) and active_sys:
            active_sys.remove_link(self._selected_node)
            self._updating_ui = True
            self.store.save()
            self._updating_ui = False
            self.canvas.rebuild_scene()
            self.canvas.select_object(active_sys, "sun")

        elif self._selected_type == "note" and isinstance(self._selected_node, CanvasNote) and active_sys:
            active_sys.remove_note(self._selected_node)
            self._updating_ui = True
            self.store.save()
            self._updating_ui = False
            self.canvas.rebuild_scene()
            self.canvas.select_object(active_sys, "sun")

        self._populate_tree()
        self._refresh_sun_anchor_card()
        self._update_breadcrumb()
        self.status_label.setText(f"Deleted {item_label} · Ctrl+Z to undo.")

    def _create_new_system(self) -> None:
        dlg = NewProjectStudioDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        clean_name = dlg.edit_name.text().strip() or "New Architecture Project"
        template_key = str(dlg.combo_template.currentData() or "blank")
        purpose_text = dlg.edit_purpose.toPlainText().strip()
        stack_text = dlg.edit_stack.text().strip()

        plan_parts = [
            "PROJECT TOPIC & PURPOSE (WHAT IT DOES):\n"
            + (
                purpose_text
                or f"Architectural structure, modules, and data flow of the {clean_name} project."
            )
        ]
        if stack_text:
            plan_parts.append(f"\nTECH STACK:\n{stack_text}")
        plan_parts.append("\nAPPLICATION SOURCE: Personal Projects (Architecture Studio)")

        new_sys = SolarSystem(
            name=clean_name,
            project_name=clean_name,
            sun_title=clean_name,
            core_plan="\n".join(plan_parts),
            source="manual",
            is_user_modified=True,
        )
        new_sys.apply_template(template_key)

        self.store.push_undo()
        self._updating_ui = True
        self.store.add_system(new_sys)
        self._updating_ui = False
        self.canvas.enter_solar_system(new_sys)
        self._populate_tree()
        self._refresh_sun_anchor_card()

        if template_key == "blank":
            # Activate Module Drawing tool directly if blank canvas is selected
            self.canvas.set_drawing_tool("add_planet")
            self.status_label.setText(
                f"'{clean_name}' created! Click anywhere on the canvas to place your modules."
            )
        else:
            self.canvas.set_drawing_tool("select")
            self.status_label.setText(
                f"'{clean_name}' is ready. Use the tools on the left of the map to add modules, links or notes."
            )

    def _add_planet_to_active(self) -> None:
        if not self.canvas.active_system:
            return
        title, ok = QInputDialog.getText(
            self,
            "Add module",
            "Module name:",
        )
        if not ok or not title.strip():
            return

        self.store.push_undo()
        planet = self.canvas.active_system.add_planet(
            title=title.strip(),
            summary="What this module is responsible for.",
            layer="core",
        )
        planet.is_user_modified = True
        self.canvas.active_system.rebuild_links_and_tour()
        self._updating_ui = True
        self.store.save()
        self._updating_ui = False
        if self.canvas.view_mode != "system":
            self.canvas.enter_solar_system(self.canvas.active_system)
        else:
            self.canvas.rebuild_scene()
        self._populate_tree()
        self.canvas.select_object(planet, "planet")

    def _add_satellite_to_selected(self) -> None:
        if not self.canvas.active_system:
            return

        target_planet: Optional[PlanetNode] = None
        if isinstance(self._selected_node, PlanetNode):
            target_planet = self._selected_node
        elif isinstance(self._selected_node, SatelliteNode):
            target_planet = self.canvas.active_system.find_parent_planet(self._selected_node)
        elif self.canvas.active_system.planets:
            target_planet = self.canvas.active_system.planets[0]

        if not target_planet:
            QMessageBox.information(
                self,
                "Add a module first",
                "Add a module to the project before adding components.",
            )
            return

        title, ok = QInputDialog.getText(
            self,
            f"Add component to '{target_planet.title}'",
            "Component name:",
        )
        if not ok or not title.strip():
            return

        self.store.push_undo()
        sat = target_planet.add_satellite(
            title=title.strip(),
            content="Detailed description, code block, or architectural decision note...",
            visual_tag="code",
        )
        sat.is_user_modified = True
        self._updating_ui = True
        self.store.save()
        self._updating_ui = False
        if self.canvas.view_mode != "system":
            self.canvas.enter_solar_system(self.canvas.active_system)
        else:
            self.canvas.rebuild_scene()
        self._populate_tree()
        self.canvas.select_object(sat, "satellite")
