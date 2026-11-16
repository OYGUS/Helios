"""Selection, hover and focus highlighting of `SpaceCanvasView` (`FocusMixin`).

`select_object`, hover tracking of modules, which modules reveal their components (moons),
the focus state (highlighted links, dimmed unrelated modules) and the "Show all details"
toggle.
"""

from __future__ import annotations

from typing import Optional, Set

from PySide6.QtWidgets import QGraphicsItem

from core.models import PlanetLink, PlanetNode, SatelliteNode
from ui.canvas.helpers import SHOW_ALL_DETAILS_KEY, resolve_component_ref
from ui.canvas.module_item import PlanetGraphicsItem


class FocusMixin:
    """Selection, hover, moon reveal and focus highlighting / dimming.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

    # ------------------------------------------------------------------
    # Settings-backed toggles
    # ------------------------------------------------------------------
    def set_show_all_details(self, enabled: bool) -> None:
        """Shows every module's components all the time (persisted in settings)."""
        enabled = bool(enabled)
        changed = enabled != self.show_all_details
        self.show_all_details = enabled
        try:
            self.store.set_setting(SHOW_ALL_DETAILS_KEY, enabled)
        except Exception:
            pass
        self._refresh_moon_reveal(animate=False)
        self.scene_obj.update()
        if changed:
            self.showAllDetailsChanged.emit(enabled)

    # ------------------------------------------------------------------
    # Hover / focus / moon reveal
    # ------------------------------------------------------------------
    def selected_planet_id(self) -> Optional[str]:
        sel = self.selected_node
        if isinstance(sel, PlanetNode):
            return sel.id
        if isinstance(sel, SatelliteNode) and self.active_system:
            parent_p = self.active_system.find_parent_planet(sel)
            if parent_p:
                return parent_p.id
        return None

    def has_focus_selection(self) -> bool:
        return isinstance(self.selected_node, (PlanetNode, SatelliteNode, PlanetLink))

    def notify_planet_hovered(self, p_item: PlanetGraphicsItem, entered: bool) -> None:
        if entered:
            self._hover_hide_timer.stop()
            if self.hovered_planet_item is not p_item:
                self.hovered_planet_item = p_item
                self._refresh_moon_reveal(animate=self.orbit_motion_enabled)
                self.viewport().update()
        else:
            self._hover_hide_timer.start()

    def _on_hover_hide_timeout(self) -> None:
        if self.hovered_planet_item is not None:
            item = self.hovered_planet_item
            under_mouse = item.isUnderMouse() or any(s.isUnderMouse() for s in item.satellite_items if s.isVisible())
            if under_mouse:
                return
            self.hovered_planet_item = None
            self._refresh_moon_reveal(animate=False)
            self.viewport().update()

    def _refresh_moon_reveal(self, animate: bool = False) -> None:
        sel_pid = self.selected_planet_id()
        for p_item in self.planet_items:
            reveal = (
                self.show_all_details
                or p_item.planet.id == sel_pid
                or p_item is self.hovered_planet_item
                or p_item.planet.id in self._search_revealed
            )
            p_item.set_moons_revealed(reveal, animate=animate and reveal)

    def _update_focus_state(self) -> None:
        """Highlights the selection's links and dims unrelated modules and links."""
        sel = self.selected_node
        focus_pid = self.selected_planet_id()
        related_pids: Set[str] = set()
        related_sats: Set[int] = set()
        focus_link: Optional[PlanetLink] = sel if isinstance(sel, PlanetLink) else None

        if isinstance(sel, SatelliteNode) and self.active_system:
            for ref in list(getattr(sel, "calls", []) or []) + list(getattr(sel, "called_by", []) or []):
                planet, sat = resolve_component_ref(self.active_system, str(ref))
                if planet is not None:
                    related_pids.add(planet.id)
                if sat is not None:
                    related_sats.add(id(sat))

        keep: Set[str] = set()
        if focus_pid:
            keep.add(focus_pid)
            keep |= related_pids
            for l_item in self.link_items:
                if l_item.link.source_id == focus_pid:
                    keep.add(l_item.link.target_id)
                elif l_item.link.target_id == focus_pid:
                    keep.add(l_item.link.source_id)
        elif focus_link is not None:
            keep = {focus_link.source_id, focus_link.target_id}
        has_focus = bool(keep)

        for p_item in self.planet_items:
            pid = p_item.planet.id
            p_item.focus_dimmed = has_focus and pid not in keep
            p_item.is_related = pid in related_pids and pid != focus_pid
            p_item.refresh_opacity()
            for s_item in p_item.satellite_items:
                s_item.is_related = id(s_item.satellite) in related_sats
            p_item.update()

        for l_item in self.link_items:
            link = l_item.link
            if focus_pid:
                hl = link.source_id == focus_pid or link.target_id == focus_pid
            elif focus_link is not None:
                hl = link is focus_link
            else:
                hl = False
            l_item.is_highlighted = hl
            l_item.is_dimmed = has_focus and not hl
            l_item.prepareGeometryChange()
            l_item.update()

        self._refresh_moon_reveal(animate=self.orbit_motion_enabled)
        self._assign_link_label_positions()

    def _update_link_highlights(self) -> None:  # kept for compatibility
        self._update_focus_state()

    def select_object(self, node_obj: object, node_type: str, graphics_item: Optional[QGraphicsItem] = None) -> None:
        if self.view_mode == "system" and self.map_filter.hides_node(self.active_system, node_obj):
            # The user asked for something the filter hides: show everything again.
            self.reset_map_filter("The filter was turned off to show the selected item.")
        self.selected_node = node_obj
        self._update_focus_state()
        self.scene_obj.update()
        self._ensure_selection_visible()
        self.nodeSelected.emit(node_obj, node_type)
