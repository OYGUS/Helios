"""Component (moon) items: `SatelliteGraphicsItem` and the "+N more" `MoreMoonsChipItem`."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional, Tuple

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QBrush, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.models import SatelliteNode
from ui import theme
from ui.canvas.helpers import (
    DRAG_COMMIT_PX,
    DRAG_START_PX,
    MOON_LABEL_W,
    QUIET_KINDS,
    _canvas_menu,
    _draw_chip,
    _view_distance_px,
    elide,
)

if TYPE_CHECKING:  # annotations only: a runtime import would be an import cycle
    from ui.canvas.module_item import PlanetGraphicsItem
    from ui.canvas.view import SpaceCanvasView


# ----------------------------------------------------------------------------
# Moons (components)
# ----------------------------------------------------------------------------
class SatelliteGraphicsItem(QGraphicsObject):
    """A component shown as a small moon of its module, with a label beside it."""

    def __init__(
        self,
        satellite: SatelliteNode,
        parent_planet_item: "PlanetGraphicsItem",
        canvas_view: "SpaceCanvasView",
    ) -> None:
        super().__init__(parent_planet_item)
        self.satellite = satellite
        self.parent_planet_item = parent_planet_item
        self.canvas_view = canvas_view
        self.radius = 5.0
        self._hovered = False
        self.is_dimmed = False
        self.is_related = False
        self.label_side = 1  # +1: label to the right of the dot, -1: to the left
        self.label_width = MOON_LABEL_W
        self._press_screen_pos = QPoint()
        self._press_active = False
        self._fade: Optional[QPropertyAnimation] = None

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(15)
        self._dragging = False
        self._font = theme.font(theme.FS_SMALL)
        # (title, label width) -> (elided title, label width in px): measured once per value
        # instead of on every boundingRect / shape / paint (moving a module re-reads the
        # bounding rects of all its components, also the hidden ones).
        self._text_key: Optional[tuple] = None
        self._text_metrics: Tuple[str, float] = ("", 0.0)
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        kind = theme.SATELLITE_KIND_LABELS.get(str(getattr(self.satellite, "kind", "") or ""), "Component")
        tip = f"{self.satellite.title}\n{kind}"
        sig = str(getattr(self.satellite, "signature", "") or "").strip()
        if sig:
            tip += f"\n{sig}"
        self.setToolTip(tip)

    def _measured_title(self) -> Tuple[str, float]:
        key = (self.satellite.title, self.label_width)
        if key != self._text_key:
            text = elide(self.satellite.title, self._font, self.label_width)
            width = min(self.label_width, QFontMetricsF(self._font).horizontalAdvance(text) + 2)
            self._text_metrics = (text, width)
            self._text_key = key
        return self._text_metrics

    def display_title(self) -> str:
        return self._measured_title()[0]

    def _label_rect(self) -> QRectF:
        w = self._measured_title()[1]
        if self.label_side >= 0:
            return QRectF(self.radius + 6, -9, w, 18)
        return QRectF(-self.radius - 6 - w, -9, w, 18)

    def boundingRect(self) -> QRectF:
        dot = QRectF(-self.radius - 4, -self.radius - 4, (self.radius + 4) * 2, (self.radius + 4) * 2)
        return dot.united(self._label_rect().adjusted(-4, -2, 4, 2))

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.radius + 5, self.radius + 5)
        path.addRect(self._label_rect())
        return path

    def update_orbit_position(self) -> None:
        """Positions a hand-moved moon from its stored angle / distance."""
        rad = math.radians(self.satellite.orbit_angle)
        r = self.satellite.orbit_radius
        self.setPos(math.cos(rad) * r, math.sin(rad) * r)

    def advance_orbit(self, speed_multiplier: float = 1.0) -> None:  # kept for API compatibility
        return

    def set_label_side(self, side: int) -> None:
        if side != self.label_side:
            self.prepareGeometryChange()
            self.label_side = side

    def fade_in(self, animate: bool) -> None:
        if self._fade is not None:
            self._fade.stop()
            self._fade = None
        target = 0.3 if self.is_dimmed else 1.0
        if not animate:
            self.setOpacity(target)
            return
        self.setOpacity(0.0)
        anim = QPropertyAnimation(self, b"opacity")
        anim.setDuration(140)
        anim.setStartValue(0.0)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade = anim
        anim.start()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        is_selected = self.canvas_view.selected_node is self.satellite
        base = theme.soft_color(self.parent_planet_item.planet.color)
        if is_selected:
            base = theme.qcolor(theme.ACCENT)
        elif self.is_related:
            base = theme.qcolor(theme.ACCENT_TEXT)
        kind = str(getattr(self.satellite, "kind", "") or "")
        r = self.radius + (1.0 if (is_selected or self._hovered) else 0.0)

        if is_selected or self.is_related:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT, 140), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), r + 4, r + 4)

        if kind in ("package", "session") or self.parent_planet_item.planet.kind in QUIET_KINDS:
            painter.setBrush(QBrush(theme.qcolor(theme.BG)))
            painter.setPen(QPen(base, 1.4))
        else:
            painter.setBrush(QBrush(base))
            painter.setPen(Qt.PenStyle.NoPen)
        if kind == "class":
            painter.drawRoundedRect(QRectF(-r, -r, 2 * r, 2 * r), 2, 2)
        elif kind == "session":
            painter.drawPolygon(QPolygonF([QPointF(0, -r - 1), QPointF(r + 1, 0), QPointF(0, r + 1), QPointF(-r - 1, 0)]))
        else:
            painter.drawEllipse(QPointF(0, 0), r, r)

        rect = self._label_rect()
        bg = QRectF(rect).adjusted(-3, 0, 3, 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.qcolor(theme.CANVAS_BG, 205)))
        painter.drawRoundedRect(bg, 4, 4)
        f = theme.font(theme.FS_SMALL, QFont.Weight.DemiBold if (is_selected or self._hovered) else QFont.Weight.Normal)
        painter.setFont(f)
        painter.setPen(theme.qcolor(theme.TEXT if (is_selected or self._hovered or self.is_related) else theme.TEXT_2))
        align = Qt.AlignmentFlag.AlignLeft if self.label_side >= 0 else Qt.AlignmentFlag.AlignRight
        painter.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, elide(self.satellite.title, f, rect.width() + 1))

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.canvas_view.notify_planet_hovered(self.parent_planet_item, True)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.canvas_view.notify_planet_hovered(self.parent_planet_item, False)
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.canvas_view.drawing_tool == "eraser":
                self.canvas_view.contextActionRequested.emit("delete_node", self.satellite)
                event.accept()
                return
            self._press_screen_pos = event.screenPos()
            self._press_active = True
            self._dragging = False
            self._drag_start_pos = QPointF(self.pos())
            self._drag_polar: Optional[Tuple[float, float]] = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            if not getattr(self, "_press_active", False):
                event.accept()
                return
            if (event.screenPos() - self._press_screen_pos).manhattanLength() >= DRAG_START_PX:
                self._dragging = True
            if self._dragging:
                # Display only while dragging; the model is written on release (real move only).
                parent_pos = self.parent_planet_item.mapFromScene(event.scenePos())
                dist = max(40.0, min(260.0, math.hypot(parent_pos.x(), parent_pos.y())))
                ang = math.degrees(math.atan2(parent_pos.y(), parent_pos.x())) % 360.0
                self._drag_polar = (round(ang, 1), round(dist, 1))
                rad = math.radians(ang)
                self.setPos(math.cos(rad) * dist, math.sin(rad) * dist)
                self.set_label_side(1 if parent_pos.x() >= 0 else -1)
                self.parent_planet_item.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            was_press = getattr(self, "_press_active", False)
            self._press_active = False
            if self._dragging:
                self._dragging = False
                moved_px = _view_distance_px(self.canvas_view, self._drag_start_pos, self.pos())
                if self._drag_polar is not None and moved_px >= DRAG_COMMIT_PX:
                    store = self.canvas_view.store
                    store.push_undo()
                    self.satellite.angle, self.satellite.distance = self._drag_polar
                    self.satellite.is_user_modified = True  # moved by the user
                    store.save()
                else:
                    self.setPos(self._drag_start_pos)
                    self.parent_planet_item.layout_moons()
                self.canvas_view.select_object(self.satellite, "satellite", self)
            elif was_press and (event.screenPos() - self._press_screen_pos).manhattanLength() < 28:
                self.canvas_view.select_object(self.satellite, "satellite", self)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.canvas_view.select_object(self.satellite, "satellite", self)
        menu = _canvas_menu(self.canvas_view)
        act_open = menu.addAction("Open file or folder")
        act_open.setEnabled(bool(self.satellite.file_path))
        menu.addSeparator()
        act_del = menu.addAction("Delete component")
        chosen = menu.exec(event.screenPos())
        if chosen == act_open:
            self.canvas_view.contextActionRequested.emit("open_path", self.satellite)
        elif chosen == act_del:
            self.canvas_view.contextActionRequested.emit("delete_node", self.satellite)
        event.accept()


class MoreMoonsChipItem(QGraphicsObject):
    """"+N more" chip shown when a module has more components than fit around it."""

    def __init__(self, parent_planet_item: "PlanetGraphicsItem", canvas_view: "SpaceCanvasView") -> None:
        super().__init__(parent_planet_item)
        self.parent_planet_item = parent_planet_item
        self.canvas_view = canvas_view
        self.count = 0
        self.label_side = 1
        self.setZValue(15)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("The full list is in the panel on the right")
        self._width_key: Optional[str] = None
        self._width = 0.0

    def text(self) -> str:
        return f"+{self.count} more"

    def _rect(self) -> QRectF:
        text = self.text()
        if text != self._width_key:
            self._width = QFontMetricsF(theme.font(theme.FS_CAPTION, QFont.Weight.Medium)).horizontalAdvance(text) + 14
            self._width_key = text
        w = self._width
        if self.label_side >= 0:
            return QRectF(-4, -9, w, 18)
        return QRectF(4 - w, -9, w, 18)

    def boundingRect(self) -> QRectF:
        return self._rect().adjusted(-2, -2, 2, 2)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._rect()
        _draw_chip(painter, r.center(), self.text())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            p = self.parent_planet_item
            self.canvas_view.select_object(p.planet, "planet", p)
            event.accept()
            return
        super().mousePressEvent(event)
