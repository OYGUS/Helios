"""Scene construction and display layout of `SpaceCanvasView` (`SceneBuildMixin`).

Builds the project map (sun, modules, links, notes) and the all-projects cards
(`rebuild_scene`, `_build_galaxy_cards`), spreads auto-placed modules on their orbits
(`_apply_auto_layout`), keeps module titles and link labels from overlapping and re-reads
project texts into existing items.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QRectF

from core.models import SolarSystem
from ui import filters as map_filters
from ui.canvas.helpers import QUIET_KINDS, chip_rect_for
from ui.canvas.link_item import ConstellationLinkItem
from ui.canvas.module_item import PlanetGraphicsItem
from ui.canvas.note_item import CanvasNoteGraphicsItem
from ui.canvas.overview_items import GalaxyClusterItem, GalaxySectorBannerItem
from ui.canvas.sun_item import SunGraphicsItem


class SceneBuildMixin:
    """Scene building (project map and all-projects cards) and display layout.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

    # ------------------------------------------------------------------
    # Scene construction
    # ------------------------------------------------------------------
    def rebuild_scene(self) -> None:
        self._hover_hide_timer.stop()
        self.hovered_planet_item = None
        self.scene_obj.clear()
        self.sun_item = None
        self.planet_items.clear()
        self.planet_item_map.clear()
        self.link_items.clear()
        self.note_items.clear()
        self.galaxy_items.clear()
        self.sector_banners.clear()

        if hasattr(self, "drawing_toolbar"):
            self.drawing_toolbar.setVisible(self.view_mode == "system" and self.active_system is not None)
        if hasattr(self, "drawing_subbar"):
            self.drawing_subbar.setVisible(self.view_mode == "system" and self.drawing_tool != "select")

        if self.view_mode == "system" and self.active_system:
            self.sun_item = SunGraphicsItem(self.active_system, self)
            self.scene_obj.addItem(self.sun_item)

            for planet in self.active_system.planets:
                p_item = PlanetGraphicsItem(planet, self)
                self.scene_obj.addItem(p_item)
                self.planet_items.append(p_item)
                self.planet_item_map[planet.id] = p_item
            self._apply_auto_layout()

            # (source, target) of every link, for the items' reverse-direction flag.
            self._link_pairs = {(l.source_planet_id, l.target_planet_id) for l in self.active_system.links}
            try:
                for link in self.active_system.links:
                    src_item = self.planet_item_map.get(link.source_id)
                    dst_item = self.planet_item_map.get(link.target_id)
                    if src_item and dst_item and src_item is not dst_item:
                        l_item = ConstellationLinkItem(link, src_item, dst_item, self)
                        self.scene_obj.addItem(l_item)
                        self.link_items.append(l_item)
            finally:
                self._link_pairs = None

            for note in self.active_system.notes:
                n_item = CanvasNoteGraphicsItem(note, self)
                self.scene_obj.addItem(n_item)
                self.note_items.append(n_item)

            map_filters.apply_to_canvas(self)
            self.resolve_label_collisions()
            self._update_focus_state()
            if self.search_query:
                self._apply_search_filter(center=False)
        else:
            self._build_galaxy_cards()
            if self.search_query:
                self._apply_search_filter(center=False)
        self._update_empty_overlay()
        self._update_filter_banner()

    def _build_galaxy_cards(self, columns: Optional[int] = None) -> None:
        self.store.recalculate_galaxy_sectors()  # keeps the established project order
        grouped = self.store.grouped_by_source()
        cols = columns or self._best_galaxy_columns(grouped)
        cw, ch = GalaxyClusterItem.CARD_W, GalaxyClusterItem.CARD_H
        gap_x, gap_y, header_h, group_gap = 20.0, 18.0, 34.0, 30.0
        total_w = cols * cw + (cols - 1) * gap_x
        left = -total_w / 2.0
        y = 0.0
        for source_key, sys_list in grouped.items():
            if not sys_list:
                continue
            banner = GalaxySectorBannerItem(source_key, QRectF(left, y, total_w, header_h - 8), len(sys_list))
            self.scene_obj.addItem(banner)
            self.sector_banners.append(banner)
            y += header_h
            for idx, system in enumerate(sys_list):
                r, c = divmod(idx, cols)
                c_item = GalaxyClusterItem(system, self)
                c_item.setPos(left + c * (cw + gap_x) + cw / 2.0, y + r * (ch + gap_y) + ch / 2.0)
                self.scene_obj.addItem(c_item)
                self.galaxy_items.append(c_item)
            rows = (len(sys_list) + cols - 1) // cols
            y += rows * (ch + gap_y) - gap_y + group_gap

    def _best_galaxy_columns(self, grouped: Dict[str, List[SolarSystem]]) -> int:
        vw = max(400, self.viewport().width())
        vh = max(300, self.viewport().height())
        counts = [len(v) for v in grouped.values() if v]
        if not counts:
            return 3
        best, best_scale = 3, -1.0
        for cols in range(1, 6):
            if cols > max(counts) and cols > 1:
                break
            w = cols * GalaxyClusterItem.CARD_W + (cols - 1) * 20.0 + 120
            h = sum(34 + ((n + cols - 1) // cols) * (GalaxyClusterItem.CARD_H + 18) + 12 for n in counts) + 80
            scale = min(vw / w, vh / h, 1.0)
            if scale > best_scale + 0.02:
                best, best_scale = cols, scale
        return best

    # ------------------------------------------------------------------
    # Display layout
    # ------------------------------------------------------------------
    def _apply_auto_layout(self) -> None:
        """Spreads auto-placed modules evenly; history / external modules go outermost."""
        items = self.planet_items
        auto_main = [it for it in items if not it.planet.is_user_modified and it.planet.kind not in QUIET_KINDS]
        auto_quiet = [it for it in items if not it.planet.is_user_modified and it.planet.kind in QUIET_KINDS]
        for it in items:
            if it.planet.is_user_modified:
                it.set_display_polar(it.planet.angle, it.planet.orbit_radius)

        auto_main.sort(key=lambda it: (not bool(getattr(it.planet, "is_entry", False)),))
        n = len(auto_main)
        inner = 0.0
        if n:
            inner = max(210.0, n * 185.0 / (2 * math.pi))
            stagger = 46.0 if n > 6 else 0.0
            for idx, it in enumerate(auto_main):
                ang = -90.0 + idx * 360.0 / n
                it.set_display_polar(ang, inner + (idx % 2) * stagger)
            inner += stagger
        m = len(auto_quiet)
        if m:
            outer = (inner or 180.0) + 84.0  # quiet modules sit just outside the main ring
            outer = max(outer, m * 160.0 / (2 * math.pi))
            if n:
                # In the gaps between main modules, preferring the lower left (as before).
                candidates = [(-90.0 + (i + 0.5) * 360.0 / n) % 360.0 for i in range(n)]
            else:
                candidates = [(135.0 + i * 360.0 / m) % 360.0 for i in range(m)]
            preferred = [135.0, 45.0, 225.0, 315.0, 180.0, 90.0, 0.0, 270.0]
            used: List[float] = []
            for idx, it in enumerate(auto_quiet):
                if len(used) < len(candidates):
                    want = preferred[idx % len(preferred)]
                    free = [a for a in candidates if a not in used]
                    ang = min(free, key=lambda a: abs((a - want + 180.0) % 360.0 - 180.0))
                    used.append(ang)
                else:
                    ang = (135.0 + idx * 360.0 / m) % 360.0
                it.set_display_polar(ang, outer)

    def resolve_label_collisions(self) -> None:
        """Nudges module titles so no two titles overlap each other or the sun."""
        if self.view_mode != "system":
            return
        blocked: List[QRectF] = []
        if self.sun_item is not None:
            r = self.sun_item.radius + 6
            blocked.append(QRectF(-r, -r, 2 * r, 2 * r))
            blocked.append(self.sun_item.name_rect().translated(self.sun_item.pos()))
        circles = [QRectF(it.pos().x() - it.radius - 4, it.pos().y() - it.radius - 4,
                          2 * it.radius + 8, 2 * it.radius + 8) for it in self.planet_items]
        blocked.extend(circles)
        ordered = sorted(self.planet_items, key=lambda it: (it.pos().y(), it.pos().x()))
        for it in ordered:
            placed = False
            own = QRectF(it.pos().x() - it.radius - 4, it.pos().y() - it.radius - 4, 2 * it.radius + 8, 2 * it.radius + 8)
            for above, dy in ((False, 0.0), (True, 0.0), (False, 18.0), (True, 18.0), (False, 36.0), (True, 36.0)):
                it.set_label_placement(above, dy)
                rect = it.scene_label_rect()
                if not any(rect.intersects(b) for b in blocked if b != own):
                    placed = True
                    break
            if not placed:
                it.set_label_placement(False, 0.0)
            blocked.append(it.scene_label_rect())

    def _assign_link_label_positions(self) -> None:
        """Spreads visible link labels along their curves so the chips don't overlap."""
        placed: List[QRectF] = [it.scene_label_rect() for it in self.planet_items]
        for it in self.planet_items:
            r = it.radius + 4
            placed.append(QRectF(it.pos().x() - r, it.pos().y() - r, 2 * r, 2 * r))
        if self.sun_item is not None:
            r = self.sun_item.radius + 10
            placed.append(QRectF(-r, -r, 2 * r, 2 * r))
            placed.append(self.sun_item.name_rect())
        for l_item in self.link_items:
            l_item.label_t = 0.5
        for l_item in self.link_items:
            if not l_item.label_visible():
                continue
            curve = l_item._compute_curve()
            if not curve:
                continue
            path = curve[3]
            best_t, best_rect = 0.5, None
            for t in (0.5, 0.38, 0.62, 0.28, 0.72, 0.2, 0.8):
                rect = chip_rect_for(path.pointAtPercent(t), l_item.link.label)
                if not any(rect.intersects(p) for p in placed):
                    best_t, best_rect = t, rect
                    break
            if best_rect is None:
                best_rect = chip_rect_for(path.pointAtPercent(0.5), l_item.link.label)
            l_item.label_t = best_t
            placed.append(best_rect)
            l_item.update_geometry()

    def moon_blocked_rects(self) -> List[QRectF]:
        """Scene areas component labels must never cover: the sun and the project title."""
        if self.sun_item is None:
            return []
        r = self.sun_item.radius + 8.0
        c = self.sun_item.pos()
        return [
            QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r),
            self.sun_item.name_rect().translated(c).adjusted(-6, -4, 6, 4),
        ]

    def refresh_project_texts(self, system_ids: Optional[Set[str]] = None) -> None:
        """Re-reads project texts (purpose) into the sun tooltip and the overview cards."""
        if self.sun_item is not None and (system_ids is None or self.sun_item.system.id in system_ids):
            self.sun_item._refresh_tooltip()
        for g_item in self.galaxy_items:
            if system_ids is None or g_item.system.id in system_ids:
                g_item.refresh_texts()
