"""`ConstellationLinkItem`: a link drawn as a directed curve between two modules."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.models import PlanetLink, RELATION_TYPES
from ui import theme
from ui.canvas.helpers import _arrow_head, _canvas_menu, _draw_chip, chip_rect_for
from ui.canvas.module_item import PlanetGraphicsItem

if TYPE_CHECKING:  # annotations only: a runtime import would be an import cycle
    from ui.canvas.view import SpaceCanvasView


# ----------------------------------------------------------------------------
# Links
# ----------------------------------------------------------------------------
class ConstellationLinkItem(QGraphicsObject):
    """Clickable directed curve between two modules (a link)."""

    def __init__(
        self,
        link: PlanetLink,
        source_item: "PlanetGraphicsItem",
        target_item: "PlanetGraphicsItem",
        canvas_view: "SpaceCanvasView",
    ) -> None:
        super().__init__()
        self.link = link
        self.source_item = source_item
        self.target_item = target_item
        self.canvas_view = canvas_view
        self.is_highlighted = False  # touches the focused module (or is selected)
        self.is_dimmed = False  # a selection exists and this link is unrelated
        self.label_t = 0.5  # where along the curve the label chip sits when shown
        self._hovered = False
        self._has_reverse = False
        self._curve_key: Optional[tuple] = None
        self._curve: Optional[Tuple[QPointF, QPointF, QPointF, QPainterPath]] = None
        self.setZValue(4)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_reverse_flag()
        self._refresh_tooltip()

    def refresh_reverse_flag(self) -> None:
        self._has_reverse = False
        pairs = getattr(self.canvas_view, "_link_pairs", None) if self.canvas_view else None
        if pairs is not None:
            # Set by `rebuild_scene` while it creates the link items (one pass over the
            # links instead of one per link).
            self._has_reverse = (self.link.target_planet_id, self.link.source_planet_id) in pairs
            return
        system = self.canvas_view.active_system if self.canvas_view else None
        if system:
            for other in system.links:
                if (
                    other.source_planet_id == self.link.target_planet_id
                    and other.target_planet_id == self.link.source_planet_id
                ):
                    self._has_reverse = True
                    break

    def _refresh_tooltip(self) -> None:
        rel_title, _ = RELATION_TYPES.get(self.link.relation_type, ("Data Flow", ""))
        rel_title = rel_title.split("(")[0].strip()
        src_t = self.source_item.planet.title
        dst_t = self.target_item.planet.title
        label = f"\n{self.link.label}" if self.link.label else ""
        self.setToolTip(f"{src_t} → {dst_t}{label}\nType: {rel_title}")

    def _compute_curve(self) -> Optional[Tuple[QPointF, QPointF, QPointF, QPainterPath]]:
        """(start, control point, end, path) of the curve; recomputed only when an input
        changed (the end points, the module radii or the reverse flag). Callers only read
        the returned objects."""
        p1 = self.source_item.pos()
        p2 = self.target_item.pos()
        key = (p1.x(), p1.y(), p2.x(), p2.y(), self.source_item.radius, self.target_item.radius, self._has_reverse)
        if key != self._curve_key:
            self._curve = self._curve_from(p1, p2)
            self._curve_key = key
        return self._curve

    def _curve_from(self, p1: QPointF, p2: QPointF) -> Optional[Tuple[QPointF, QPointF, QPointF, QPainterPath]]:
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 40:
            return None
        ux = dx / dist
        uy = dy / dist
        start = QPointF(p1.x() + ux * (self.source_item.radius + 5), p1.y() + uy * (self.source_item.radius + 5))
        end = QPointF(p2.x() - ux * (self.target_item.radius + 6), p2.y() - uy * (self.target_item.radius + 6))
        mid = QPointF((start.x() + end.x()) * 0.5, (start.y() + end.y()) * 0.5)
        nx, ny = -uy, ux
        if self._has_reverse:
            curve_offset = max(30.0, min(60.0, dist * 0.18))
        else:
            curve_offset = min(22.0, max(10.0, dist * 0.06))
        ctrl = QPointF(mid.x() + nx * curve_offset, mid.y() + ny * curve_offset)
        path = QPainterPath(start)
        path.quadTo(ctrl, end)
        return start, ctrl, end, path

    def label_visible(self) -> bool:
        if not self.link.label:
            return False
        return self._hovered or self.canvas_view.selected_node is self.link or self.is_highlighted_focus()

    def is_highlighted_focus(self) -> bool:
        return self.is_highlighted and self.canvas_view.has_focus_selection()

    def label_center(self) -> Optional[QPointF]:
        curve = self._compute_curve()
        if not curve:
            return None
        return curve[3].pointAtPercent(max(0.1, min(0.9, self.label_t)))

    def boundingRect(self) -> QRectF:
        p1 = self.source_item.pos()
        p2 = self.target_item.pos()
        rect = QRectF(p1, p2).normalized()
        return rect.adjusted(-110, -80, 110, 80)

    def shape(self) -> QPainterPath:
        curve = self._compute_curve()
        if not curve:
            return QPainterPath()
        stroker = QPainterPathStroker()
        stroker.setWidth(12.0)
        hit_path = stroker.createStroke(curve[3])
        if self.label_visible():
            c = self.label_center()
            if c is not None:
                hit_path.addRect(chip_rect_for(c, self.link.label))
        return hit_path

    def update_geometry(self) -> None:
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        curve = self._compute_curve()
        if not curve:
            return
        start, ctrl, end, path = curve
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        is_selected = self.canvas_view.selected_node is self.link
        focus = self.is_highlighted_focus()

        if is_selected:
            color = theme.qcolor(theme.ACCENT)
            width = 2.0
        elif self._hovered:
            color = theme.qcolor(theme.TEXT, 200)
            width = 1.6
        elif focus:
            color = theme.qcolor(theme.ACCENT_TEXT, 210)
            width = 1.6
        elif self.is_dimmed:
            color = theme.qcolor(theme.TEXT_2, 30)
            width = 1.0
        else:
            color = theme.qcolor(theme.TEXT_2, 82)
            width = 1.1

        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        head = 7.0 if (is_selected or focus or self._hovered) else 6.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(_arrow_head(end, ctrl, head))
        if self.link.relation_type == "api_call":
            painter.drawPolygon(_arrow_head(start, ctrl, head * 0.85))

        if self.label_visible():
            center = path.pointAtPercent(max(0.1, min(0.9, self.label_t)))
            _draw_chip(painter, center, self.link.label, accent=is_selected or focus)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self._refresh_tooltip()
        self.setZValue(6)
        self.update_geometry()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.setZValue(4)
        self.update_geometry()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.canvas_view.drawing_tool == "eraser":
                self.canvas_view.contextActionRequested.emit("delete_node", self.link)
                event.accept()
                return
            self.canvas_view.select_object(self.link, "link", self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.canvas_view.contextActionRequested.emit("edit_link_label", self.link)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.canvas_view.select_object(self.link, "link", self)
        menu = _canvas_menu(self.canvas_view)
        act_edit = menu.addAction("Rename link…")
        act_reverse = menu.addAction("Reverse direction")
        type_menu = menu.addMenu("Link type")
        type_actions = {}
        for r_key, (r_label, _) in RELATION_TYPES.items():
            act = type_menu.addAction(r_label)
            act.setCheckable(True)
            act.setChecked(r_key == self.link.relation_type)
            type_actions[act] = r_key
        menu.addSeparator()
        act_del = menu.addAction("Delete link")
        chosen = menu.exec(event.screenPos())
        if chosen == act_edit:
            self.canvas_view.contextActionRequested.emit("edit_link_label", self.link)
        elif chosen == act_reverse:
            self.canvas_view.store.push_undo()
            self.link.source_planet_id, self.link.target_planet_id = (
                self.link.target_planet_id,
                self.link.source_planet_id,
            )
            self.canvas_view.store.save()
            self.canvas_view.rebuild_scene()
            self.canvas_view.select_object(self.link, "link")
        elif chosen in type_actions:
            self.canvas_view.store.push_undo()
            self.link.relation_type = type_actions[chosen]
            self.canvas_view.store.save()
            self.update()
            self.canvas_view.select_object(self.link, "link", self)
        elif chosen == act_del:
            self.canvas_view.contextActionRequested.emit("delete_node", self.link)
        event.accept()
