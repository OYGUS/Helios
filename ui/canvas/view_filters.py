"""Search dimming and the map filter of `SpaceCanvasView` (`SearchFilterMixin`).

Search dims non-matching modules / components / project cards; the map filter (view state
only, see `ui.filters`) hides link kinds, layers and history / external modules and shows
the filter banner.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QGraphicsItem

from ui import filters as map_filters


class SearchFilterMixin:
    """Search dimming, the map filter and its banner.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def set_search_query(self, query: str) -> None:
        self.search_query = query.strip().lower()
        self._apply_search_filter()

    def _apply_search_filter(self, center: bool = True) -> None:
        q = self.search_query
        if self.view_mode == "system":
            self._search_revealed = set()
            if not q:
                for p_item in self.planet_items:
                    p_item.is_dimmed = False
                    p_item.refresh_opacity()
                    for s_item in p_item.satellite_items:
                        s_item.is_dimmed = False
                        s_item.setOpacity(1.0)
                    p_item.update()
                self._refresh_moon_reveal()
                return

            first_match_item: Optional[QGraphicsItem] = None
            for p_item in self.planet_items:
                p_match = q in p_item.planet.title.lower() or q in (p_item.planet.summary or "").lower()
                any_sat_match = False
                for s_item in p_item.satellite_items:
                    s_match = q in s_item.satellite.title.lower() or q in (s_item.satellite.content or "").lower()
                    s_item.is_dimmed = not (s_match or p_match)
                    s_item.setOpacity(0.3 if s_item.is_dimmed else 1.0)
                    if s_match:
                        any_sat_match = True
                        if first_match_item is None:
                            first_match_item = p_item
                if any_sat_match:
                    self._search_revealed.add(p_item.planet.id)
                p_item.is_dimmed = not (p_match or any_sat_match)
                p_item.refresh_opacity()
                p_item.update()
                if p_match and first_match_item is None:
                    first_match_item = p_item
            self._refresh_moon_reveal()
            if center and first_match_item is not None:
                self.centerOn(first_match_item.scenePos())
        else:
            for g_item in self.galaxy_items:
                if not q:
                    g_item.is_dimmed = False
                else:
                    sys_ = g_item.system
                    match = (
                        q in sys_.name.lower()
                        or q in sys_.project_name.lower()
                        or q in str(getattr(sys_, "purpose", "") or "").lower()
                        or q in str(getattr(g_item, "_identity_snippet", "") or "").lower()
                        or q in sys_.core_plan.lower()
                        or any(q in p.title.lower() for p in sys_.planets)
                    )
                    g_item.is_dimmed = not match
                g_item.update()

    # ------------------------------------------------------------------
    # Map filter (view state only; see ui/filters.py)
    # ------------------------------------------------------------------
    def apply_map_filter(self, status: str = "") -> None:
        """Re-applies `map_filter` to the drawn items (no scene rebuild, no data change)."""
        if self.view_mode == "system" and self.active_system is not None:
            map_filters.apply_to_canvas(self)
            if self.map_filter.hides_node(self.active_system, self.selected_node):
                self.selected_node = self.active_system
                self.nodeSelected.emit(self.active_system, "sun")
            self._update_focus_state()
            self.scene_obj.update()
        self._update_filter_banner()
        self.mapFilterChanged.emit(status)

    def reset_map_filter(self, status: str = "") -> None:
        self.map_filter.reset()
        self.apply_map_filter(status or "Showing everything again.")

    def _update_filter_banner(self) -> None:
        if not hasattr(self, "filter_banner"):
            return
        show = self.view_mode == "system" and self.active_system is not None and self.map_filter.is_active()
        if show:
            hidden_mods = sum(1 for it in self.planet_items if not it.isVisible())
            hidden_links = sum(1 for it in self.link_items if not it.isVisible())
            self.filter_banner.set_counts(hidden_mods, hidden_links)
            self.filter_banner.show()
            self.filter_banner.raise_()
            self._reposition_overlays()
        else:
            self.filter_banner.hide()
