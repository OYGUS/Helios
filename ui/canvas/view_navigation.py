"""Zoom, pan, fit and view-mode transitions of `SpaceCanvasView` (`NavigationMixin`).

`fit_to_view`, `center_on_node`, `_ensure_selection_visible`, `enter_solar_system` /
`switch_to_galaxy_view` / `focus_on_sun`, and the wheel / zoom handlers.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QGraphicsItem, QGraphicsView

from core.models import PlanetNode, SatelliteNode, SolarSystem
from ui import theme
from ui.canvas.overview_items import GalaxyClusterItem


class NavigationMixin:
    """Fit, zoom, pan, centring and project / all-projects view transitions.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

    def fit_to_view(self) -> None:
        """Zooms and centres so the whole map (or all project cards) is visible."""
        rect = QRectF()
        if self.view_mode == "system":
            items: Sequence[QGraphicsItem] = [it for it in (*self.planet_items, *self.note_items) if it.isVisible()]
            if self.sun_item is not None:
                rect = self.sun_item.sceneBoundingRect()
            for it in items:
                rect = rect.united(it.sceneBoundingRect())
            for p_item in self.planet_items:
                if p_item.moons_revealed and p_item.isVisible():
                    for s_item in p_item.satellite_items:
                        if s_item.isVisible():
                            rect = rect.united(s_item.sceneBoundingRect())
            margin, max_scale = theme.MAP_FIT_MARGIN, 1.15
        else:
            rect = self.scene_obj.itemsBoundingRect()
            margin, max_scale = 50.0, 1.0
        if rect.isEmpty():
            self.resetTransform()
            self._current_scale = 1.0
            self.centerOn(0, 0)
            return
        vp = self.viewport().rect()
        if vp.width() < 120 or vp.height() < 120:
            self._auto_fit = True
            self.resetTransform()
            self._current_scale = 1.0
            self.centerOn(rect.center())
            return
        self._auto_fit = True
        rect = rect.adjusted(-margin, -margin, margin, margin)
        # Keep the left tool strip clear.
        avail_w = max(100.0, vp.width() - 56.0)
        scale = min(avail_w / rect.width(), vp.height() / rect.height(), max_scale)
        floor = self.min_fit_scale() if self.view_mode == "system" else 0.3
        clamped = scale < floor
        scale = max(floor, scale)
        self.resetTransform()
        self.scale(scale, scale)
        self._current_scale = scale
        center = rect.center()
        if self.view_mode == "system":
            if clamped and self.planet_items:
                # The map is larger than the view at a readable size: show the sun and the
                # top of the ring (where the entry module sits) if both fit, else the top of
                # the ring; the user pans to the rest.
                half_h = (vp.height() / scale) / 2.0
                top = min([it for it in self.planet_items if it.isVisible()] or self.planet_items,
                          key=lambda it: it.pos().y())
                top_y = top.sceneBoundingRect().top() - margin
                sun_bottom = self.sun_item.sceneBoundingRect().bottom() if self.sun_item is not None else 0.0
                if sun_bottom - top_y <= 2 * half_h:
                    cy = (top_y + sun_bottom) / 2.0
                else:
                    cy = top_y + half_h
                cx = top.pos().x() * 0.5 if abs(top.pos().x()) < (avail_w / scale) / 2.0 else 0.0
                center = QPointF(cx, cy)
            center = QPointF(center.x() - 28.0 / scale, center.y())
        self.centerOn(center)

    @staticmethod
    def min_fit_scale() -> float:
        """Smallest fit zoom at which module titles stay readable (theme.MAP_MIN_TITLE_PX)."""
        return min(1.0, theme.MAP_MIN_TITLE_PX / float(theme.FS_BODY))

    def center_on_node(self, node: object) -> None:
        if isinstance(node, PlanetNode):
            item = self.planet_item_map.get(node.id)
            if item is not None:
                self.centerOn(item.scenePos())
        elif isinstance(node, SatelliteNode) and self.active_system:
            parent = self.active_system.find_parent_planet(node)
            item = self.planet_item_map.get(parent.id) if parent else None
            if item is not None:
                s_item = next((s for s in item.satellite_items if s.satellite is node), None)
                self.centerOn(s_item.scenePos() if s_item is not None and s_item.isVisible() else item.scenePos())

    # ------------------------------------------------------------------
    # Selection and view mode transitions
    # ------------------------------------------------------------------

    def _ensure_selection_visible(self) -> None:
        """Pans (never zooms) so a selected module and its revealed components are on screen."""
        if self.view_mode != "system" or self.tour_index >= 0:
            return
        pid = self.selected_planet_id()
        item = self.planet_item_map.get(pid) if pid else None
        if item is None or self.viewport().width() < 200:
            return
        rect = item.sceneBoundingRect()
        for s_item in item.satellite_items:
            if s_item.isVisible():
                rect = rect.united(s_item.sceneBoundingRect())
        if item.more_chip.isVisible():
            rect = rect.united(item.more_chip.sceneBoundingRect())
        view_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        if view_rect.contains(rect):
            return
        if rect.width() < view_rect.width() - 40 and rect.height() < view_rect.height() - 40:
            self.ensureVisible(rect, 24, 24)
        else:
            self.centerOn(rect.center())

    def enter_solar_system(self, system: SolarSystem) -> None:
        """Opens one project's map and fits it into the view."""
        self.active_system = system
        self.selected_node = system
        self.view_mode = "system"
        self.tour_index = -1
        self.resetTransform()
        self._current_scale = 1.0
        self.rebuild_scene()
        self.fit_to_view()
        self.viewModeChanged.emit("system")
        self.nodeSelected.emit(system, "sun")

    def switch_to_galaxy_view(self) -> None:
        """Switches to the all-projects overview."""
        self.view_mode = "galaxy"
        self.tour_index = -1
        if self.drawing_tool != "select":
            self.drawing_tool = "select"
            for k, btn in self._tool_buttons.items():
                btn.setChecked(k == "select")
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.resetTransform()
        self.rebuild_scene()
        self.fit_to_view()
        self.viewModeChanged.emit("galaxy")

    def focus_on_sun(self) -> None:
        if self.view_mode != "system" and self.active_system:
            self.enter_solar_system(self.active_system)
        else:
            self.fit_to_view()
            if self.active_system:
                self.select_object(self.active_system, "sun", self.sun_item)

    def _find_galaxy_cluster_near(self, scene_pos: QPointF) -> Optional[SolarSystem]:
        for item in self.scene_obj.items(scene_pos):
            if isinstance(item, GalaxyClusterItem):
                return item.system
        best_sys: Optional[SolarSystem] = None
        best_dist = 240.0
        for g_item in self.galaxy_items:
            d = math.hypot(g_item.pos().x() - scene_pos.x(), g_item.pos().y() - scene_pos.y())
            if d < best_dist:
                best_dist = d
                best_sys = g_item.system
        return best_sys

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._auto_fit = False
        delta = event.angleDelta().y()
        if delta == 0:
            return
        zoom_factor = 1.14 if delta > 0 else 1 / 1.14
        new_scale = self._current_scale * zoom_factor
        if 0.20 <= new_scale <= 3.5:
            self._current_scale = new_scale
            self.scale(zoom_factor, zoom_factor)

    def zoom_in(self) -> None:
        self._auto_fit = False
        factor = 1.15
        if self._current_scale * factor <= 3.5:
            self._current_scale *= factor
            self.scale(factor, factor)

    def zoom_out(self) -> None:
        self._auto_fit = False
        factor = 1.0 / 1.15
        if self._current_scale * factor >= 0.20:
            self._current_scale *= factor
            self.scale(factor, factor)

    def reset_zoom(self) -> None:
        """Fits the current map (or all project cards) into the view."""
        self.fit_to_view()
