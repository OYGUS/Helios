"""Module (planet) items: `PlanetGraphicsItem` (moon fan layout, drag, paint) and the
`EntryBadgeItem` shown on the module where the program starts."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.models import PlanetNode
from ui import theme
from ui.canvas.component_items import MoreMoonsChipItem, SatelliteGraphicsItem
from ui.canvas.helpers import (
    DIM_OPACITY,
    DRAG_COMMIT_PX,
    DRAG_START_PX,
    MAX_VISIBLE_MOONS,
    MOON_LABEL_W,
    MOON_ROW,
    PLANET_LABEL_W,
    QUIET_KINDS,
    SEARCH_DIM_OPACITY,
    _canvas_menu,
    _view_distance_px,
    elide,
    planet_caption,
)

if TYPE_CHECKING:  # annotations only: a runtime import would be an import cycle
    from ui.canvas.view import SpaceCanvasView


class EntryBadgeItem(QGraphicsItem):
    """Small "start" badge on the module where the program starts."""

    def __init__(self, parent_planet_item: "PlanetGraphicsItem") -> None:
        super().__init__(parent_planet_item)
        self.parent_planet_item = parent_planet_item
        self.setToolTip("Program starts here")
        self.setZValue(1)
        self.setAcceptHoverEvents(True)

    def boundingRect(self) -> QRectF:
        return QRectF(-9, -9, 18, 18)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme.qcolor(theme.CANVAS_BG), 2.0))
        painter.setBrush(QBrush(theme.qcolor(theme.ACCENT)))
        painter.drawEllipse(QPointF(0, 0), 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.qcolor(theme.ON_ACCENT)))
        painter.drawPolygon(QPolygonF([QPointF(-2.5, -4), QPointF(-2.5, 4), QPointF(4.2, 0)]))


_CHIP_WIDTHS: dict = {}


def _widest_chip_width() -> float:
    """Width reserved for the "+N more" chip in the moon fan (measured once per font)."""
    f = theme.font(theme.FS_CAPTION, QFont.Weight.Medium)
    key = f.key()
    w = _CHIP_WIDTHS.get(key)
    if w is None:
        w = _CHIP_WIDTHS[key] = QFontMetricsF(f).horizontalAdvance("+999 more") + 18
    return w


# ----------------------------------------------------------------------------
# Planets (modules)
# ----------------------------------------------------------------------------
class PlanetGraphicsItem(QGraphicsObject):
    """A module drawn as a soft planet with its title below."""

    def __init__(self, planet: PlanetNode, canvas_view: "SpaceCanvasView") -> None:
        super().__init__()
        self.planet = planet
        self.canvas_view = canvas_view
        kind = str(getattr(planet, "kind", "") or "")
        n = len(planet.satellites)
        if kind in QUIET_KINDS:
            self.radius = 15.0
        else:
            self.radius = round(17.0 + min(12.0, 2.6 * math.sqrt(n)), 1)
        self._hovered = False
        self.is_dimmed = False  # search
        self.focus_dimmed = False  # selection focus
        self.is_related = False  # owns a component the selected component calls / is called by
        self.is_tour_active = False
        self.moons_revealed = False
        self.label_above = False
        self.label_dy = 0.0
        self._press_screen_pos = QPoint()
        self._press_active = False
        self.satellite_items: List[SatelliteGraphicsItem] = []
        # Display position (polar, around the sun). Hand-moved planets use the stored values.
        self.disp_angle = float(planet.angle)
        self.disp_radius = float(planet.orbit_radius)

        self._title_font = theme.font(theme.FS_BODY, QFont.Weight.DemiBold)
        self._caption_font = theme.font(theme.FS_CAPTION)
        self._caption = planet_caption(planet)
        # Measured once per text (title_rect / caption_rect run on every bounding rect,
        # shape and paint): title -> (elided title, width), caption -> width.
        self._title_key: Optional[str] = None
        self._title_metrics: Tuple[str, float] = ("", 0.0)
        self._caption_key: Optional[str] = None
        self._caption_w = 0.0

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(10)
        self._dragging = False
        self.update_orbit_position()
        self._refresh_tooltip()

        for sat in self.planet.satellites:
            sat_item = SatelliteGraphicsItem(sat, self, self.canvas_view)
            sat_item.setVisible(False)
            self.satellite_items.append(sat_item)
        self.more_chip = MoreMoonsChipItem(self, canvas_view)
        self.more_chip.setVisible(False)
        self.entry_badge: Optional[EntryBadgeItem] = None
        if bool(getattr(planet, "is_entry", False)):
            self.entry_badge = EntryBadgeItem(self)
            off = self.radius * 0.72
            self.entry_badge.setPos(-off, -off)

    # --- text ------------------------------------------------------------
    def _refresh_tooltip(self) -> None:
        kind_label = theme.PLANET_KIND_LABELS.get(str(getattr(self.planet, "kind", "") or ""), "Module")
        lines = [self.planet.title, kind_label + (f" · {self._caption}" if self._caption else "")]
        if bool(getattr(self.planet, "is_entry", False)):
            lines.append("Program starts here")
        self.setToolTip("\n".join(lines))

    def _measured_title(self) -> Tuple[str, float]:
        title = self.planet.title
        if title != self._title_key:
            text = elide(title, self._title_font, PLANET_LABEL_W)
            self._title_metrics = (text, QFontMetricsF(self._title_font).horizontalAdvance(text) + 4)
            self._title_key = title
        return self._title_metrics

    def display_title(self) -> str:
        return self._measured_title()[0]

    def title_rect(self) -> QRectF:
        w = self._measured_title()[1]
        h_total = 18.0 + (15.0 if self._caption else 0.0)
        if self.label_above:
            top = -self.radius - 8 - h_total - self.label_dy
        else:
            top = self.radius + 7 + self.label_dy
        return QRectF(-w / 2.0, top, w, 18.0)

    def caption_rect(self) -> QRectF:
        tr = self.title_rect()
        if self._caption != self._caption_key:
            self._caption_w = QFontMetricsF(self._caption_font).horizontalAdvance(self._caption) + 4
            self._caption_key = self._caption
        w = self._caption_w
        return QRectF(-w / 2.0, tr.bottom() - 1, w, 15.0)

    def label_block_rect(self) -> QRectF:
        r = self.title_rect()
        if self._caption:
            r = r.united(self.caption_rect())
        return r.adjusted(-4, -1, 4, 1)

    def scene_label_rect(self) -> QRectF:
        return self.label_block_rect().translated(self.pos())

    def set_label_placement(self, above: bool, dy: float) -> None:
        if above != self.label_above or abs(dy - self.label_dy) > 0.1:
            self.prepareGeometryChange()
            self.label_above = above
            self.label_dy = dy
            self.update()

    def boundingRect(self) -> QRectF:
        r = self.radius + 10
        return QRectF(-r, -r, 2 * r, 2 * r).united(self.label_block_rect().adjusted(-4, -4, 4, 4))

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.radius + 4, self.radius + 4)
        path.addRect(self.label_block_rect())
        return path

    # --- position ----------------------------------------------------------
    def update_orbit_position(self) -> None:
        rad = math.radians(self.disp_angle)
        r = self.disp_radius
        self.setPos(math.cos(rad) * r, math.sin(rad) * r)

    def set_display_polar(self, angle: float, radius: float) -> None:
        self.disp_angle = angle % 360.0
        self.disp_radius = radius
        self.update_orbit_position()

    def advance_orbit(self, speed_multiplier: float = 1.0) -> None:
        if self._dragging:
            return
        self.disp_angle = (self.disp_angle + self.planet.orbit_speed * speed_multiplier) % 360.0
        self.update_orbit_position()
        if self.moons_revealed:
            self.layout_moons()

    # --- moons -------------------------------------------------------------
    def outward_vector(self) -> Tuple[float, float]:
        x, y = self.pos().x(), self.pos().y()
        d = math.hypot(x, y)
        if d < 1:
            return 1.0, 0.0
        return x / d, y / d

    def layout_moons(self, fade: bool = False) -> None:
        """Places visible moons on a fan facing away from the sun; labels never overlap.

        Moons (and their labels) never cover the sun or the project title: fan slots that
        would are left empty, fewer moons are shown and the "+N more" chip grows instead.
        """
        items = self.satellite_items
        total = len(items)
        compact = self.canvas_view.show_all_details and not (
            self.canvas_view.selected_planet_id() == self.planet.id
        )
        label_w = 118.0 if compact else MOON_LABEL_W
        if total > MAX_VISIBLE_MOONS:
            candidates = items[: MAX_VISIBLE_MOONS - 1]
        else:
            candidates = list(items)
        blocked = self.canvas_view.moon_blocked_rects()
        origin = self.pos()

        def hits(local_rect: QRectF) -> bool:
            r = local_rect.translated(origin)
            return any(r.intersects(b) for b in blocked)

        shown: List[SatelliteGraphicsItem] = []
        auto: List[SatelliteGraphicsItem] = []
        for it in candidates:
            if it.satellite.is_user_modified:
                it.label_width = label_w
                if not it._dragging:
                    it.update_orbit_position()
                    it.set_label_side(1 if it.pos().x() >= 0 else -1)
                if it._dragging or not hits(it.mapRectToParent(it.boundingRect())):
                    shown.append(it)
            else:
                auto.append(it)

        need_chip = total > len(candidates) or len(shown) + len(auto) < len(candidates)
        slots = self._fan_slots(len(auto) + (1 if need_chip else 0), label_w)
        chip_w = _widest_chip_width()
        usable: List[Tuple[int, QPointF]] = []
        for side, pos in slots:
            dot = QRectF(pos.x() - 10, pos.y() - 10, 20, 20)
            if side >= 0:
                lab = QRectF(pos.x() + 5, pos.y() - 11, max(label_w, chip_w) + 10, 22)
            else:
                lab = QRectF(pos.x() - 5 - max(label_w, chip_w) - 10, pos.y() - 11, max(label_w, chip_w) + 10, 22)
            if not hits(dot.united(lab)):
                usable.append((side, pos))
        if len(usable) < len(auto) + (1 if need_chip else 0):
            need_chip = True
            n_auto = max(0, len(usable) - 1)
        else:
            n_auto = len(auto)
        placed_auto = auto[:n_auto]
        for it, (side, pos) in zip(placed_auto, usable):
            it.label_width = label_w
            it.set_label_side(side)
            if not it._dragging:
                it.setPos(pos)
            shown.append(it)
        hidden_count = total - len(shown)
        chip_shown = False
        if hidden_count > 0 and len(usable) > n_auto:
            side, pos = usable[n_auto]
            self.more_chip.count = hidden_count
            self.more_chip.prepareGeometryChange()
            self.more_chip.label_side = side
            self.more_chip.setPos(pos)
            chip_shown = True
        self.more_chip.count = max(hidden_count, 0)
        self._shown_moons = shown

        if self.moons_revealed:
            shown_ids = {id(it) for it in shown}
            for it in items:
                vis = id(it) in shown_ids
                if vis and not it.isVisible():
                    it.setVisible(True)
                    it.fade_in(fade)
                elif not vis and it.isVisible():
                    it.setVisible(False)
            self.more_chip.setVisible(chip_shown)
            self.more_chip.update()

    def _fan_slots(self, n: int, label_w: float) -> List[Tuple[int, QPointF]]:
        """Up to ``n`` (label side, position) slots on a fan that faces away from the sun."""
        if n <= 0:
            return []
        ux, uy = self.outward_vector()
        if abs(ux) >= 0.35:
            # Left or right of the sun: one column on the outward side, never towards the sun.
            sides = [(1 if ux >= 0 else -1, n)]
        else:
            # Above or below the sun: both sides, biased away from it.
            out_side = 1 if ux >= 0 else -1
            sides = [(out_side, (n + 1) // 2), (-out_side, n // 2)]
        label = self.label_block_rect()
        slots: List[Tuple[int, QPointF]] = []
        for side, m in sides:
            if m == 0:
                continue
            radius = max(self.radius + 30.0, (m * MOON_ROW) / 1.75)
            bias = max(-radius * 0.45, min(radius * 0.45, uy * radius * 0.4))
            for i in range(m):
                y = (i - (m - 1) / 2.0) * MOON_ROW + bias
                x = math.sqrt(max(0.0, radius * radius - y * y))
                x = max(x, self.radius + 16.0)
                # Keep clear of the module title block.
                if label.top() - 12 <= y <= label.bottom() + 12:
                    x = max(x, label.width() / 2.0 + 14.0)
                slots.append((side, QPointF(side * x, y)))
        return slots

    def set_moons_revealed(self, revealed: bool, animate: bool = False) -> None:
        if revealed == self.moons_revealed:
            if revealed:
                self.layout_moons()
            return
        self.moons_revealed = revealed
        if revealed:
            self.layout_moons(fade=animate)
        else:
            for s_item in self.satellite_items:
                s_item.setVisible(False)
            self.more_chip.setVisible(False)
        self.update()

    def refresh_opacity(self) -> None:
        if self.is_dimmed:
            self.setOpacity(SEARCH_DIM_OPACITY)
        elif self.focus_dimmed:
            self.setOpacity(DIM_OPACITY)
        else:
            self.setOpacity(1.0)

    # --- painting ------------------------------------------------------------
    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        kind = str(getattr(self.planet, "kind", "") or "")
        quiet = kind in QUIET_KINDS
        base = theme.soft_color(self.planet.color)
        is_selected = self.canvas_view.selected_node is self.planet
        r = self.radius

        if self.moons_revealed:
            # faint spokes to manually placed / fanned moons
            painter.setPen(QPen(theme.qcolor(theme.TEXT_2, 26), 1.0))
            for s_item in self.satellite_items:
                if s_item.isVisible():
                    painter.drawLine(QPointF(0, 0), s_item.pos())

        if is_selected or self.is_tour_active:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT, 210), 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), r + 5, r + 5)
        elif self.is_related:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT, 140), 1.2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), r + 5, r + 5)
        elif self._hovered:
            painter.setPen(QPen(theme.qcolor(theme.TEXT, 70), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), r + 5, r + 5)

        if bool(getattr(self.planet, "is_entry", False)):
            painter.setPen(QPen(theme.qcolor(theme.ACCENT, 150), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), r + 2.5, r + 2.5)

        if quiet:
            painter.setBrush(QBrush(theme.qcolor(theme.PANEL)))
            pen = QPen(theme.qcolor(theme.TEXT_2, 150), 1.3, Qt.PenStyle.DashLine if kind == "history" else Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawEllipse(QPointF(0, 0), r, r)
        else:
            grad = QRadialGradient(QPointF(-r * 0.35, -r * 0.4), r * 1.5)
            grad.setColorAt(0.0, base.lighter(118))
            grad.setColorAt(1.0, base.darker(150))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(0, 0), r, r)

        # Component count badge while the moons are hidden.
        n = len(self.planet.satellites)
        if n and not self.moons_revealed:
            f = theme.font(10, QFont.Weight.DemiBold)
            txt = str(n) if n < 1000 else "999+"
            w = max(16.0, QFontMetricsF(f).horizontalAdvance(txt) + 8.0)
            off = r * 0.72
            badge = QRectF(off - w / 2.0 + 2, -off - 8, w, 16)
            painter.setPen(QPen(theme.qcolor(theme.CANVAS_BG), 2.0))
            painter.setBrush(QBrush(theme.qcolor(theme.RAISED)))
            painter.drawRoundedRect(badge, 8, 8)
            painter.setFont(f)
            painter.setPen(theme.qcolor(theme.TEXT_2))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, txt)

        # Title + caption with a soft backdrop so link lines never cross the text.
        block = self.label_block_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.qcolor(theme.CANVAS_BG, 190)))
        painter.drawRoundedRect(block, 5, 5)
        painter.setFont(self._title_font)
        painter.setPen(theme.qcolor(theme.TEXT if not quiet else theme.TEXT_2))
        painter.drawText(self.title_rect(), Qt.AlignmentFlag.AlignCenter, self.display_title())
        if self._caption:
            painter.setFont(self._caption_font)
            painter.setPen(theme.qcolor(theme.MUTED))
            painter.drawText(self.caption_rect(), Qt.AlignmentFlag.AlignCenter, self._caption)

    # --- events ----------------------------------------------------------------
    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.canvas_view.notify_planet_hovered(self, True)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.canvas_view.notify_planet_hovered(self, False)
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.canvas_view.drawing_tool == "eraser":
                self.canvas_view.contextActionRequested.emit("delete_node", self.planet)
                event.accept()
                return
            if self.canvas_view.drawing_tool == "draw_link" or (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self.canvas_view.handle_planet_click_for_link(self, event.scenePos())
                event.accept()
                return
            if self.canvas_view.drawing_tool == "add_satellite":
                self.canvas_view.place_satellite_on_planet(self, None)
                event.accept()
                return
            self._press_screen_pos = event.screenPos()
            self._press_active = True
            self._dragging = False
            self._drag_start = (self.disp_angle, self.disp_radius)
            self._drag_start_pos = QPointF(self.pos())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        self._press_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self.canvas_view.is_drawing_link:
                self.canvas_view.update_interactive_link(event.scenePos())
                event.accept()
                return
            if not getattr(self, "_press_active", False):
                # The press was handled by a tool (eraser, add component, link): never a drag.
                event.accept()
                return
            if (event.screenPos() - self._press_screen_pos).manhattanLength() >= DRAG_START_PX:
                self._dragging = True
                self.canvas_view._auto_fit = False
            if self._dragging:
                # Display only while dragging; the model is written on release (real move only).
                scene_pt = event.scenePos()
                dist = max(115.0, min(950.0, math.hypot(scene_pt.x(), scene_pt.y())))
                ang = math.degrees(math.atan2(scene_pt.y(), scene_pt.x())) % 360.0
                self.set_display_polar(round(ang, 1), round(dist, 1))
                if self.moons_revealed:
                    self.layout_moons()
                for l_item in self.canvas_view.link_items:
                    if l_item.source_item is self or l_item.target_item is self:
                        l_item.update_geometry()
                self.canvas_view.scene_obj.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.canvas_view.is_drawing_link:
                self.canvas_view.finish_interactive_link_drag(event.scenePos())
                event.accept()
                return
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            was_press = getattr(self, "_press_active", False)
            self._press_active = False
            if self._dragging:
                self._dragging = False
                moved_px = _view_distance_px(self.canvas_view, self._drag_start_pos, self.pos())
                if moved_px >= DRAG_COMMIT_PX:
                    store = self.canvas_view.store
                    store.push_undo()
                    self.planet.angle = round(self.disp_angle, 1)
                    self.planet.orbit_radius = round(self.disp_radius, 1)
                    self.planet.is_user_modified = True  # moved by the user
                    store.save()
                else:
                    self.set_display_polar(*self._drag_start)
                    if self.moons_revealed:
                        self.layout_moons()
                    for l_item in self.canvas_view.link_items:
                        if l_item.source_item is self or l_item.target_item is self:
                            l_item.update_geometry()
                self.canvas_view.resolve_label_collisions()
                self.canvas_view.select_object(self.planet, "planet", self)
            elif was_press and (event.screenPos() - self._press_screen_pos).manhattanLength() < 28:
                self.canvas_view.select_object(self.planet, "planet", self)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.canvas_view.select_object(self.planet, "planet", self)
        menu = _canvas_menu(self.canvas_view)
        act_add_sat = menu.addAction("Add component…")
        act_add_link = menu.addAction("Link to another module…")
        act_clear_links = menu.addAction("Remove this module's links")
        menu.addSeparator()
        act_del = menu.addAction("Delete module")
        chosen = menu.exec(event.screenPos())
        if chosen == act_add_sat:
            self.canvas_view.contextActionRequested.emit("add_satellite", self.planet)
        elif chosen == act_add_link:
            self.canvas_view.contextActionRequested.emit("add_link", self.planet)
        elif chosen == act_clear_links:
            self.canvas_view.contextActionRequested.emit("clear_links", self.planet)
        elif chosen == act_del:
            self.canvas_view.contextActionRequested.emit("delete_node", self.planet)
        event.accept()
