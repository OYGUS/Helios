"""Mouse, keyboard and context-menu handling of `SpaceCanvasView` (`InputMixin`).

Qt event handler overrides on the view (`mousePressEvent`, `mouseMoveEvent`,
`mouseReleaseEvent`, `contextMenuEvent`, `keyPressEvent`); items handle their own events.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog

from ui.canvas.helpers import _canvas_menu
from ui.canvas.overview_items import GalaxySectorBannerItem


class InputMixin:
    """Mouse press / move / release, empty-canvas context menu and keyboard shortcuts.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

    def mousePressEvent(self, event) -> None:
        if self.view_mode == "system" and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            self._draft_cursor_pos = scene_pos

            if self.drawing_tool == "add_planet":
                existing_p = self._find_planet_item_at(scene_pos)
                if existing_p is not None:
                    self.select_object(existing_p.planet, "planet", existing_p)
                    event.accept()
                    return
                self.place_planet_at(scene_pos)
                event.accept()
                return
            if self.drawing_tool == "add_satellite":
                self.place_satellite_on_planet(None, scene_pos)
                event.accept()
                return
            if self.drawing_tool == "add_note":
                for n_item in self.note_items:
                    if n_item.contains(n_item.mapFromScene(scene_pos)):
                        self.select_object(n_item.note, "note", n_item)
                        event.accept()
                        return
                self.place_note_at(scene_pos)
                event.accept()
                return
            if self.drawing_tool == "draw_link":
                p_item = self._find_planet_item_at(scene_pos)
                if p_item is not None:
                    self.handle_planet_click_for_link(p_item, scene_pos)
                else:
                    self.is_drawing_link = False
                    self._link_source_item = None
                    self.scene_obj.update()
                event.accept()
                return
            if self.drawing_tool == "select" and self.itemAt(event.position().toPoint()) is None:
                self._press_on_empty = event.position().toPoint()

        if event.button() == Qt.MouseButton.RightButton and self.is_drawing_link:
            self.is_drawing_link = False
            self._link_source_item = None
            self.scene_obj.update()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.view_mode == "system":
            scene_pos = self.mapToScene(event.position().toPoint())
            self._draft_cursor_pos = scene_pos
            if self.drawing_tool != "select" or self.is_drawing_link:
                self.scene_obj.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.view_mode == "system" and event.button() == Qt.MouseButton.LeftButton and self.is_drawing_link:
            scene_pos = self.mapToScene(event.position().toPoint())
            self.finish_interactive_link_drag(scene_pos)
        press = getattr(self, "_press_on_empty", None)
        self._press_on_empty = None
        if (
            press is not None
            and event.button() == Qt.MouseButton.LeftButton
            and (event.position().toPoint() - press).manhattanLength() < 5
            and self.active_system is not None
            and self.has_focus_selection()
        ):
            # A click on empty space clears the focus (back to the project overview).
            self.select_object(self.active_system, "sun", self.sun_item)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        """Quick-add and view actions when right-clicking empty canvas space."""
        item = self.itemAt(event.pos())
        if item is not None and not isinstance(item, (GalaxySectorBannerItem,)):
            super().contextMenuEvent(event)
            return

        menu = _canvas_menu(self)
        scene_pt = self.mapToScene(event.pos())

        if self.view_mode == "system" and self.active_system:
            act_add_p = menu.addAction("Add module here…")
            act_add_n = menu.addAction("Add note here…")
            menu.addSeparator()
            act_fit = menu.addAction("Fit to view")
            act_details = menu.addAction("Show all details")
            act_details.setCheckable(True)
            act_details.setChecked(self.show_all_details)
            act_rebal = menu.addAction("Auto-align layout")
            act_tour = menu.addAction("Guided tour")
            act_ollama = menu.addAction("Enhance with Ollama") if self.ai_available else None
            menu.addSeparator()
            act_back = menu.addAction("All projects")

            chosen = menu.exec(event.globalPos())
            if chosen == act_add_p:
                title, ok = QInputDialog.getText(self, "Add module", "Module name:")
                if ok and title.strip():
                    self.store.push_undo()
                    p = self.active_system.add_planet_at_pos(
                        scene_pt.x(), scene_pt.y(), title=title.strip(), summary="New module."
                    )
                    self.store.save()
                    self.rebuild_scene()
                    self.select_object(p, "planet")
                    self.contextActionRequested.emit("refresh_tree", None)
            elif chosen == act_add_n:
                title, ok = QInputDialog.getText(self, "Add note", "Note title:", text="Note")
                if ok and title.strip():
                    self.store.push_undo()
                    n = self.active_system.add_note(
                        scene_pt.x(), scene_pt.y(), title=title.strip(), content="Write rules or notes here."
                    )
                    self.store.save()
                    self.rebuild_scene()
                    self.select_object(n, "note")
                    self.contextActionRequested.emit("refresh_tree", None)
            elif chosen == act_fit:
                self.fit_to_view()
            elif chosen == act_details:
                self.set_show_all_details(not self.show_all_details)
            elif chosen == act_rebal:
                self.contextActionRequested.emit("rebalance", None)
            elif chosen == act_tour:
                self.start_or_advance_tour()
            elif act_ollama is not None and chosen == act_ollama:
                self.contextActionRequested.emit("ollama_enhance", self.active_system)
            elif chosen == act_back:
                self.switch_to_galaxy_view()
        else:
            act_analyze = menu.addAction("Analyze a folder…")
            act_new = menu.addAction("Design a new project…")
            act_scan = menu.addAction("Scan the 'projects/' folder")
            act_merge = menu.addAction("Merge duplicate projects…")
            menu.addSeparator()
            act_sett = menu.addAction("Settings…")

            chosen = menu.exec(event.globalPos())
            if chosen == act_analyze:
                self.contextActionRequested.emit("analyze_folder", None)
            elif chosen == act_new:
                self.contextActionRequested.emit("create_new_project", None)
            elif chosen == act_scan:
                self.contextActionRequested.emit("scan_helios_projects", None)
            elif chosen == act_merge:
                self.contextActionRequested.emit("merge_duplicates", None)
            elif chosen == act_sett:
                self.contextActionRequested.emit("open_settings", None)
        event.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self.is_drawing_link:
                self.is_drawing_link = False
                self._link_source_item = None
                self.scene_obj.update()
            elif self.drawing_tool != "select":
                self.set_drawing_tool("select")
            elif self.tour_index >= 0:
                self.stop_tour()
            elif self.view_mode == "system":
                self.switch_to_galaxy_view()
            event.accept()
            return
        if self.view_mode == "system" and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            key_tool_map = {
                Qt.Key.Key_1: "select",
                Qt.Key.Key_2: "add_planet",
                Qt.Key.Key_3: "add_satellite",
                Qt.Key.Key_4: "draw_link",
                Qt.Key.Key_5: "add_note",
                Qt.Key.Key_6: "eraser",
            }
            if key in key_tool_map:
                self.set_drawing_tool(key_tool_map[key])
                event.accept()
                return
        if key in (Qt.Key.Key_Home, Qt.Key.Key_R, Qt.Key.Key_F):
            self.fit_to_view()
            event.accept()
            return
        super().keyPressEvent(event)
