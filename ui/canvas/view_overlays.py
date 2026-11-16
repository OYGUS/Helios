"""Overlays and background of `SpaceCanvasView` (`OverlayMixin`).

Positions the child widgets drawn over the viewport (tool strip, option bar, welcome
panel, filter banner, empty-state hint) on resize, chooses which empty-state panel shows,
and paints the dotted background with the orbit rings (`drawBackground`).
"""

from __future__ import annotations

import math
from typing import Set

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPen

from ui import theme


class OverlayMixin:
    """Overlay widget placement, resize handling, empty-state panels and the background.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

    def _reposition_overlays(self) -> None:
        if hasattr(self, "drawing_toolbar"):
            self.drawing_toolbar.adjustSize()
            self.drawing_toolbar.move(10, 10)
        if hasattr(self, "drawing_subbar"):
            self.drawing_subbar.adjustSize()
            self.drawing_subbar.move(58, 10)
        if hasattr(self, "welcome_panel") and self.welcome_panel.isHidden() is False:
            vp = self.viewport().rect()
            self.welcome_panel.place(vp.width(), vp.height())
        if hasattr(self, "filter_banner") and self.filter_banner.isHidden() is False:
            fb = self.filter_banner
            fb.adjustSize()
            vp = self.viewport().rect()
            fb.move(max(8, (vp.width() - fb.width()) // 2), vp.height() - fb.height() - 12)
        if hasattr(self, "empty_overlay") and self.empty_overlay.isHidden() is False:
            ov = self.empty_overlay
            ov.adjustSize()
            vp = self.viewport().rect()
            # Below the centre so the sun (project) stays visible above the hint.
            top = int(vp.height() * 0.5 + 90) if ov.mode == "no_modules" else (vp.height() - ov.height()) // 2
            ov.move(max(60, (vp.width() - ov.width()) // 2), max(20, min(top, vp.height() - ov.height() - 16)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_overlays()
        if getattr(self, "_auto_fit", False):
            self.fit_to_view()

    # ------------------------------------------------------------------
    # Background
    # ------------------------------------------------------------------
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, theme.qcolor(theme.CANVAS_BG))
        grid_step = 44 if self._current_scale >= 0.55 else 88
        left = int(math.floor(rect.left() / grid_step) * grid_step)
        top = int(math.floor(rect.top() / grid_step) * grid_step)
        right = int(math.ceil(rect.right()))
        bottom = int(math.ceil(rect.bottom()))
        cols = max(1, (right - left) // grid_step)
        rows = max(1, (bottom - top) // grid_step)
        if cols * rows <= 3500:
            painter.setPen(QPen(theme.qcolor(theme.TEXT_2, 16), 1.4))
            for x in range(left, right, grid_step):
                for y in range(top, bottom, grid_step):
                    painter.drawPoint(x, y)

        if self.view_mode == "system" and self.active_system and self.planet_items:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            focus_id = self.selected_planet_id()
            hover = self.hovered_planet_item
            strong: Set[int] = set()
            radii: Set[int] = set()
            for p_item in self.planet_items:
                r = int(round(p_item.disp_radius))
                radii.add(r)
                if p_item.planet.id == focus_id or p_item is hover:
                    strong.add(r)
            for r in sorted(radii):
                alpha = 40 if r in strong else 11
                painter.setPen(QPen(theme.qcolor(theme.TEXT_2, alpha), 1.0))
                painter.drawEllipse(QPointF(0, 0), float(r), float(r))

    def _update_empty_overlay(self) -> None:
        if not hasattr(self, "empty_overlay"):
            return
        if not self.store.systems:
            # No projects at all: the welcome panel (ui/welcome.py) replaces the short hint.
            self.empty_overlay.hide()
            self.welcome_panel.refresh_paths()
            self.welcome_panel.show()
            self.welcome_panel.raise_()
            self._reposition_overlays()
            return
        self.welcome_panel.hide()
        mode = ""
        if self.view_mode == "system" and self.active_system is not None and not self.active_system.planets \
                and self.drawing_tool == "select":
            mode = "no_modules"
        if mode:
            self.empty_overlay.show_mode(mode)
            self._reposition_overlays()
        else:
            self.empty_overlay.hide()
