"""Helios Interactive Architecture Canvas (QGraphicsView & QGraphicsScene).

Features:
- Clean, high-contrast dark theme with an adaptive dot grid
- Smooth switching between All Projects Overview (Source Sectors) and Project Architecture View
- Non-overlapping project cards with accurate hit-box calculation
- Directed dependency links between modules, live fuzzy search highlighting, and Guided Architecture Tour
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

from core.models import (
    LAYER_DEFINITIONS,
    RELATION_TYPES,
    SOURCE_CATEGORIES,
    CanvasNote,
    GalaxyStore,
    PlanetLink,
    PlanetNode,
    SatelliteNode,
    SolarSystem,
)

TAG_BADGE_MAP: Dict[str, Tuple[str, str]] = {
    "idea": ("IDEA", "#F59E0B"),
    "code": ("CODE", "#10B981"),
    "diagram": ("DIAGRAM", "#3B82F6"),
    "thought": ("NOTE", "#A855F7"),
}


def animate_item_scale(item: QGraphicsObject, target: float, duration: int = 160) -> None:
    """Smoothly animates a graphics item's scale toward the target value."""
    prev = getattr(item, "_scale_anim", None)
    if prev is not None:
        prev.stop()
    anim = QPropertyAnimation(item, b"scale")
    anim.setDuration(duration)
    anim.setStartValue(item.scale())
    anim.setEndValue(target)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    item._scale_anim = anim  # type: ignore[attr-defined]
    anim.start()


def animate_item_pop_in(item: QGraphicsObject, duration: int = 260) -> None:
    """Animates a newly added graphics item with a subtle pop-in and fade-in."""
    item.setScale(0.55)
    item.setOpacity(0.0)
    scale_anim = QPropertyAnimation(item, b"scale")
    scale_anim.setDuration(duration)
    scale_anim.setStartValue(0.55)
    scale_anim.setEndValue(1.0)
    scale_anim.setEasingCurve(QEasingCurve.Type.OutBack)
    fade_anim = QPropertyAnimation(item, b"opacity")
    fade_anim.setDuration(int(duration * 0.8))
    fade_anim.setStartValue(0.0)
    fade_anim.setEndValue(1.0)
    fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    item._pop_anims = (scale_anim, fade_anim)  # type: ignore[attr-defined]
    scale_anim.start()
    fade_anim.start()


class ConstellationLinkItem(QGraphicsObject):
    """Clickable, editable directed curve illustrating inter-module dependencies and data flow."""

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
        self.is_highlighted = False
        self._hovered = False
        self.setZValue(4)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        rel_title, _ = RELATION_TYPES.get(self.link.relation_type, ("Data Flow", "#3B82F6"))
        src_t = self.source_item.planet.title
        dst_t = self.target_item.planet.title
        self.setToolTip(
            f"DATA FLOW / LINK: {src_t} → {dst_t}\n"
            f"Label: {self.link.label}\n"
            f"Flow Type: {rel_title}\n\n"
            "[Left Click: Select & Edit  •  Double Click: Rename Label  •  Right Click: Actions]"
        )

    def _compute_curve(self) -> Optional[Tuple[QPointF, QPointF, QPointF, QPainterPath]]:
        p1 = self.source_item.pos()
        p2 = self.target_item.pos()
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        dist = math.hypot(dx, dy)
        if dist < 40:
            return None
        ux = dx / dist
        uy = dy / dist
        start = QPointF(
            p1.x() + ux * (self.source_item.radius + 6),
            p1.y() + uy * (self.source_item.radius + 6),
        )
        end = QPointF(
            p2.x() - ux * (self.target_item.radius + 8),
            p2.y() - uy * (self.target_item.radius + 8),
        )
        mid = QPointF((start.x() + end.x()) * 0.5, (start.y() + end.y()) * 0.5)
        nx = -uy
        ny = ux

        # Check if a reverse link (B -> A) exists so curves bow outward cleanly
        has_reverse = False
        if self.canvas_view and self.canvas_view.active_system:
            for other_l in self.canvas_view.active_system.links:
                if (
                    other_l.source_planet_id == self.link.target_planet_id
                    and other_l.target_planet_id == self.link.source_planet_id
                ):
                    has_reverse = True
                    break

        if has_reverse:
            curve_offset = max(42.0, min(80.0, dist * 0.24))
        else:
            curve_offset = min(26.0, max(14.0, dist * 0.08))

        ctrl = QPointF(mid.x() + nx * curve_offset, mid.y() + ny * curve_offset)
        path = QPainterPath(start)
        path.quadTo(ctrl, end)
        return start, ctrl, end, path

    def boundingRect(self) -> QRectF:
        p1 = self.source_item.pos()
        p2 = self.target_item.pos()
        rect = QRectF(p1, p2).normalized()
        return rect.adjusted(-110, -110, 110, 110)

    def shape(self) -> QPainterPath:
        curve = self._compute_curve()
        if not curve:
            return QPainterPath()
        _, _, _, path = curve
        stroker = QPainterPathStroker()
        stroker.setWidth(18.0)
        hit_path = stroker.createStroke(path)
        label_pos = path.pointAtPercent(0.5)
        hit_path.addRoundedRect(QRectF(label_pos.x() - 76, label_pos.y() - 12, 152, 24), 5, 5)
        return hit_path

    def update_geometry(self) -> None:
        self.prepareGeometryChange()
        self.update()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        curve = self._compute_curve()
        if not curve:
            return
        start, ctrl, end, path = curve
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        is_selected = self.canvas_view.selected_node is self.link
        _, type_hex = RELATION_TYPES.get(self.link.relation_type, ("Data Flow", "#3B82F6"))
        base_color = QColor(type_hex)

        if is_selected or self._hovered:
            line_color = base_color.lighter(135)
            pen = QPen(line_color, 2.4, Qt.PenStyle.SolidLine)
        elif self.is_highlighted:
            line_color = base_color
            pen_style = (
                Qt.PenStyle.DashLine
                if self.link.relation_type == "depends_on"
                else Qt.PenStyle.SolidLine
            )
            pen = QPen(line_color, 1.9, pen_style)
        else:
            line_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 110)
            pen = QPen(line_color, 1.3, Qt.PenStyle.DashLine)

        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # Target arrowhead
        tx = end.x() - ctrl.x()
        ty = end.y() - ctrl.y()
        t_len = math.hypot(tx, ty) or 1.0
        tx /= t_len
        ty /= t_len
        arrow_size = 8.5 if (is_selected or self._hovered) else 7.2
        left_pt = QPointF(
            end.x() - tx * arrow_size - ty * (arrow_size * 0.54),
            end.y() - ty * arrow_size + tx * (arrow_size * 0.54),
        )
        right_pt = QPointF(
            end.x() - tx * arrow_size + ty * (arrow_size * 0.54),
            end.y() - ty * arrow_size - tx * (arrow_size * 0.54),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(line_color))
        painter.drawPolygon(QPolygonF([end, left_pt, right_pt]))

        # Draw a small arrowhead at the origin as well for bidirectional API calls
        if self.link.relation_type == "api_call":
            sx = start.x() - ctrl.x()
            sy = start.y() - ctrl.y()
            s_len = math.hypot(sx, sy) or 1.0
            sx /= s_len
            sy /= s_len
            s_size = 6.5
            s_left = QPointF(
                start.x() - sx * s_size - sy * (s_size * 0.52),
                start.y() - sy * s_size + sx * (s_size * 0.52),
            )
            s_right = QPointF(
                start.x() - sx * s_size + sy * (s_size * 0.52),
                start.y() - sy * s_size - sx * (s_size * 0.52),
            )
            painter.drawPolygon(QPolygonF([start, s_left, s_right]))

        # Data flow pill label
        if self.link.label:
            label_pos = path.pointAtPercent(0.5)
            lbl_text = self.link.label[:28]
            pill_w = max(72.0, min(164.0, len(lbl_text) * 5.8 + 20.0))
            label_rect = QRectF(label_pos.x() - pill_w * 0.5, label_pos.y() - 10, pill_w, 20)
            bg_alpha = 245 if (is_selected or self._hovered) else 210
            painter.setBrush(QBrush(QColor(15, 23, 42, bg_alpha)))
            border_c = line_color if (is_selected or self._hovered) else QColor("#2D3B4E")
            painter.setPen(QPen(border_c, 1.2 if (is_selected or self._hovered) else 0.9))
            painter.drawRoundedRect(label_rect, 5, 5)
            painter.setFont(
                QFont(
                    "Segoe UI",
                    7,
                    QFont.Weight.Bold if (is_selected or self._hovered) else QFont.Weight.DemiBold,
                )
            )
            painter.setPen(QColor("#F8FAFC") if (is_selected or self._hovered) else QColor("#CBD5E1"))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, lbl_text)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self._refresh_tooltip()
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
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
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #151C28; color: #E2E8F0; border: 1px solid #2A364B; padding: 4px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2563EB; color: #FFFFFF; }"
        )
        act_edit = menu.addAction("Edit Data Flow Label...")
        act_reverse = menu.addAction("Reverse Flow Direction (A ↔ B)")
        type_menu = menu.addMenu("Change Flow Type")
        type_actions = {}
        for r_key, (r_label, _) in RELATION_TYPES.items():
            act = type_menu.addAction(r_label)
            type_actions[act] = r_key
        menu.addSeparator()
        act_del = menu.addAction("Delete Data Flow Link")
        chosen = menu.exec(event.screenPos())
        if chosen == act_edit:
            self.canvas_view.contextActionRequested.emit("edit_link_label", self.link)
        elif chosen == act_reverse:
            self.link.source_planet_id, self.link.target_planet_id = (
                self.link.target_planet_id,
                self.link.source_planet_id,
            )
            self.canvas_view.store.save()
            self.canvas_view.rebuild_scene()
            self.canvas_view.select_object(self.link, "link")
        elif chosen in type_actions:
            self.link.relation_type = type_actions[chosen]
            self.canvas_view.store.save()
            self.update()
            self.canvas_view.select_object(self.link, "link", self)
        elif chosen == act_del:
            self.canvas_view.contextActionRequested.emit("delete_node", self.link)
        event.accept()


class CanvasNoteGraphicsItem(QGraphicsObject):
    """Draggable freeform Architecture Note / Rule Card placed on the canvas."""

    def __init__(self, note: CanvasNote, canvas_view: "SpaceCanvasView") -> None:
        super().__init__()
        self.note = note
        self.canvas_view = canvas_view
        self._hovered = False
        self._dragging = False
        self._press_screen_pos = QPoint()

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(12)
        self.setPos(note.x, note.y)
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        self.setToolTip(
            f"ARCHITECTURE NOTE: {self.note.title}\n\n{self.note.content[:260]}\n\n"
            "[Left Click: Select & Edit  •  Drag: Move  •  Right Click: Delete]"
        )

    def cardRect(self) -> QRectF:
        w = self.note.width
        h = self.note.height
        return QRectF(-w * 0.5, -h * 0.5, w, h)

    def boundingRect(self) -> QRectF:
        return self.cardRect().adjusted(-6, -6, 6, 6)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.cardRect(), 8, 8)
        return path

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.cardRect()
        accent = QColor(self.note.color or "#F59E0B")
        is_selected = self.canvas_view.selected_node is self.note

        bg = QColor(24, 32, 48, 240) if (is_selected or self._hovered) else QColor(18, 24, 36, 230)
        border_c = QColor("#F8FAFC") if is_selected else (accent.lighter(125) if self._hovered else accent)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(border_c, 1.8 if (is_selected or self._hovered) else 1.2))
        painter.drawRoundedRect(rect, 8, 8)

        # Top accent stripe
        top_bar = QRectF(rect.left() + 8, rect.top() + 6, 36, 3.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawRoundedRect(top_bar, 1.5, 1.5)

        # Title
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(accent.lighter(135))
        title_rect = QRectF(rect.left() + 10, rect.top() + 12, rect.width() - 20, 18)
        short_title = self.note.title if len(self.note.title) <= 26 else self.note.title[:24] + "…"
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, short_title)

        # Body snippet
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#CBD5E1"))
        body_rect = QRectF(rect.left() + 10, rect.top() + 32, rect.width() - 20, rect.height() - 38)
        snippet = (self.note.content or "").strip()
        if len(snippet) > 110:
            snippet = snippet[:108].rstrip() + "…"
        painter.drawText(
            body_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            snippet,
        )

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self._refresh_tooltip()
        animate_item_scale(self, 1.03, duration=140)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        animate_item_scale(self, 1.0, duration=150)
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.canvas_view.drawing_tool == "eraser":
                self.canvas_view.contextActionRequested.emit("delete_node", self.note)
                event.accept()
                return
            self._press_screen_pos = event.screenPos()
            self._dragging = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            if (event.screenPos() - self._press_screen_pos).manhattanLength() >= 5:
                if not self._dragging:
                    self.canvas_view.store.push_undo()
                self._dragging = True
            if self._dragging:
                scene_pt = event.scenePos()
                self.note.x = round(scene_pt.x(), 1)
                self.note.y = round(scene_pt.y(), 1)
                self.setPos(self.note.x, self.note.y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if self._dragging:
                self.canvas_view.store.save()
            self.canvas_view.select_object(self.note, "note", self)
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.canvas_view.select_object(self.note, "note", self)
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #151C28; color: #E2E8F0; border: 1px solid #2A364B; padding: 4px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2563EB; color: #FFFFFF; }"
        )
        act_del = menu.addAction("Delete Architecture Note")
        chosen = menu.exec(event.screenPos())
        if chosen == act_del:
            self.canvas_view.contextActionRequested.emit("delete_node", self.note)
        event.accept()


class SatelliteGraphicsItem(QGraphicsObject):
    """Sub-component / feature node orbiting an architectural module."""

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
        self.radius = 9.0
        self._hovered = False
        self.is_dimmed = False
        self._press_screen_pos = QPoint()

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(15)
        self._dragging = False
        self.update_orbit_position()
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        preview = (self.satellite.content or "").strip()
        if len(preview) > 220:
            preview = preview[:220].rstrip() + "…"
        tip = f"{self.satellite.title}\n"
        if preview:
            tip += f"\n{preview}\n"
        tip += "\n[Left Click: Select  •  Drag: Move  •  Right Click: Actions]"
        self.setToolTip(tip)

    def boundingRect(self) -> QRectF:
        return QRectF(-76, -20, 152, 54)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.radius + 5, self.radius + 5)
        path.addRoundedRect(QRectF(-68, self.radius + 2, 136, 20), 4, 4)
        return path

    def update_orbit_position(self) -> None:
        rad = math.radians(self.satellite.orbit_angle)
        r = self.satellite.orbit_radius
        self.setPos(math.cos(rad) * r, math.sin(rad) * r)

    def advance_orbit(self, speed_multiplier: float = 1.0) -> None:
        self.satellite.orbit_angle = (
            self.satellite.orbit_angle + self.satellite.orbit_speed * speed_multiplier
        ) % 360.0
        self.update_orbit_position()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(0.20 if self.is_dimmed else 1.0)

        is_selected = self.canvas_view.selected_node is self.satellite
        badge_text, tag_hex = TAG_BADGE_MAP.get(
            self.satellite.visual_tag, ("DETAIL", "#64748B")
        )
        accent_color = QColor(tag_hex)

        # Node circle
        bg_color = QColor("#1E293B") if (is_selected or self._hovered) else QColor("#131A26")
        border_color = (
            QColor("#F8FAFC")
            if is_selected
            else (accent_color.lighter(120) if self._hovered else accent_color)
        )
        border_width = 2.0 if (is_selected or self._hovered) else 1.4

        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, border_width))
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

        # Center dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent_color))
        painter.drawEllipse(QPointF(0, 0), 3.0, 3.0)

        # Title label
        title = self.satellite.title
        short_title = title if len(title) <= 22 else title[:20] + "…"
        label_rect = QRectF(-72, self.radius + 4, 144, 18)

        if is_selected or self._hovered:
            painter.setBrush(QBrush(QColor(15, 23, 42, 235)))
            painter.setPen(QPen(border_color, 1.0))
            painter.drawRoundedRect(label_rect, 4, 4)

        font = QFont("Segoe UI", 7)
        font.setBold(is_selected or self._hovered)
        painter.setFont(font)
        painter.setPen(
            QColor("#F8FAFC") if (is_selected or self._hovered) else QColor("#94A3B8")
        )
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, short_title)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        animate_item_scale(self, 1.15, duration=140)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        animate_item_scale(self, 1.0, duration=150)
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.canvas_view.drawing_tool == "eraser":
                self.canvas_view.contextActionRequested.emit("delete_node", self.satellite)
                event.accept()
                return
            self._press_screen_pos = event.screenPos()
            self._dragging = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            if (event.screenPos() - self._press_screen_pos).manhattanLength() >= 6:
                if not self._dragging:
                    self.canvas_view.store.push_undo()
                self._dragging = True
            if self._dragging:
                parent_pos = self.parent_planet_item.mapFromScene(event.scenePos())
                dist = max(58.0, min(260.0, math.hypot(parent_pos.x(), parent_pos.y())))
                ang = math.degrees(math.atan2(parent_pos.y(), parent_pos.x())) % 360.0
                self.satellite.angle = round(ang, 1)
                self.satellite.distance = round(dist, 1)
                self.satellite.is_user_modified = True
                self.update_orbit_position()
                self.parent_planet_item.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if self._dragging:
                self.canvas_view.store.save()
                self.canvas_view.select_object(self.satellite, "satellite", self)
            elif (event.screenPos() - self._press_screen_pos).manhattanLength() < 28:
                self.canvas_view.select_object(self.satellite, "satellite", self)
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.canvas_view.select_object(self.satellite, "satellite", self)
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #151C28; color: #E2E8F0; border: 1px solid #2A364B; padding: 4px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2563EB; color: #FFFFFF; }"
        )
        act_open = menu.addAction("Open Source File / Folder")
        menu.addSeparator()
        act_del = menu.addAction("Delete Sub-Component")
        chosen = menu.exec(event.screenPos())
        if chosen == act_open:
            self.canvas_view.contextActionRequested.emit("open_path", self.satellite)
        elif chosen == act_del:
            self.canvas_view.contextActionRequested.emit("delete_node", self.satellite)
        event.accept()


class PlanetGraphicsItem(QGraphicsObject):
    """Architectural layer / module node."""

    def __init__(
        self,
        planet: PlanetNode,
        canvas_view: "SpaceCanvasView",
    ) -> None:
        super().__init__()
        self.planet = planet
        self.canvas_view = canvas_view
        self.radius = 26.0
        self._hovered = False
        self.is_dimmed = False
        self.is_tour_active = False
        self._press_screen_pos = QPoint()
        self.satellite_items: List[SatelliteGraphicsItem] = []

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(10)
        self._dragging = False
        self.update_orbit_position()
        self._refresh_tooltip()

        for sat in self.planet.satellites:
            sat_item = SatelliteGraphicsItem(sat, self, self.canvas_view)
            self.satellite_items.append(sat_item)

    def _refresh_tooltip(self) -> None:
        preview = (self.planet.summary or "").strip()
        if len(preview) > 220:
            preview = preview[:220].rstrip() + "…"
        tip = f"{self.planet.title} ({len(self.planet.satellites)} sub-components)\n"
        if preview:
            tip += f"\n{preview}\n"
        tip += "\n[Left Click: Select  •  Drag: Reposition  •  Right Click: Actions]"
        self.setToolTip(tip)

    def boundingRect(self) -> QRectF:
        return QRectF(-92, -42, 184, 96)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.radius + 4, self.radius + 4)
        path.addRoundedRect(QRectF(-78, self.radius + 5, 156, 22), 5, 5)
        return path

    def update_orbit_position(self) -> None:
        rad = math.radians(self.planet.orbit_angle)
        r = self.planet.orbit_radius
        self.setPos(math.cos(rad) * r, math.sin(rad) * r)

    def advance_orbit(self, speed_multiplier: float = 1.0) -> None:
        self.planet.orbit_angle = (
            self.planet.orbit_angle + self.planet.orbit_speed * speed_multiplier
        ) % 360.0
        self.update_orbit_position()
        for sat_item in self.satellite_items:
            sat_item.advance_orbit(speed_multiplier)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(0.20 if self.is_dimmed else 1.0)

        base_color = QColor(self.planet.color)
        is_selected = self.canvas_view.selected_node is self.planet

        # Thin radial guide lines from the module center to its sub-components
        for sat_item in self.satellite_items:
            sat_pos = sat_item.pos()
            line_pen = QPen(QColor(100, 116, 139, 55), 1.0, Qt.PenStyle.SolidLine)
            painter.setPen(line_pen)
            painter.drawLine(QPointF(0, 0), sat_pos)

        # Selection / Tour ring
        if is_selected or self.is_tour_active or self._hovered:
            ring_color = QColor("#F8FAFC") if is_selected else base_color.lighter(130)
            ring_color.setAlpha(180 if (is_selected or self.is_tour_active) else 100)
            painter.setPen(QPen(ring_color, 1.5, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), self.radius + 5, self.radius + 5)

        # Module main circle
        fill_color = QColor("#161F30") if not (is_selected or self._hovered) else QColor("#1E293B")
        painter.setBrush(QBrush(fill_color))
        painter.setPen(QPen(base_color, 2.2 if (is_selected or self._hovered) else 1.8))
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

        # Sub-component count inside the module circle
        sat_count = len(self.planet.satellites)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(base_color.lighter(135))
        painter.drawText(
            QRectF(-self.radius, -self.radius, self.radius * 2, self.radius * 2),
            Qt.AlignmentFlag.AlignCenter,
            str(sat_count),
        )

        # Module title pill
        title = self.planet.title
        short_title = title if len(title) <= 25 else title[:23] + "…"
        pill_rect = QRectF(-82, self.radius + 6, 164, 22)
        painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
        border_c = base_color if (is_selected or self._hovered) else QColor("#263042")
        painter.setPen(QPen(border_c, 1.0))
        painter.drawRoundedRect(pill_rect, 5, 5)

        font = QFont("Segoe UI", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#F1F5F9") if (is_selected or self._hovered) else QColor("#CBD5E1"))
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, short_title)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        animate_item_scale(self, 1.06, duration=140)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        animate_item_scale(self, 1.0, duration=150)
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
            self._dragging = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self.canvas_view.is_drawing_link:
                self.canvas_view.update_interactive_link(event.scenePos())
                event.accept()
                return
            if (event.screenPos() - self._press_screen_pos).manhattanLength() >= 6:
                if not self._dragging:
                    self.canvas_view.store.push_undo()
                self._dragging = True
            if self._dragging:
                scene_pt = event.scenePos()
                dist = max(115.0, min(950.0, math.hypot(scene_pt.x(), scene_pt.y())))
                ang = math.degrees(math.atan2(scene_pt.y(), scene_pt.x())) % 360.0
                self.planet.angle = round(ang, 1)
                self.planet.orbit_radius = round(dist, 1)
                self.planet.is_user_modified = True
                self.update_orbit_position()
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
            if self._dragging:
                self.canvas_view.store.save()
                self.canvas_view.select_object(self.planet, "planet", self)
            elif (event.screenPos() - self._press_screen_pos).manhattanLength() < 28:
                self.canvas_view.select_object(self.planet, "planet", self)
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.canvas_view.select_object(self.planet, "planet", self)
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #151C28; color: #E2E8F0; border: 1px solid #2A364B; padding: 4px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2563EB; color: #FFFFFF; }"
        )
        act_add_sat = menu.addAction("+ Add Sub-Component")
        act_add_link = menu.addAction("Connect to Another Module (Arrow)...")
        act_clear_links = menu.addAction("Clear Module Connections")
        menu.addSeparator()
        act_del = menu.addAction("Delete Module")
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


class SunGraphicsItem(QGraphicsObject):
    """Central Project Core & Summary node."""

    def __init__(self, system: SolarSystem, canvas_view: "SpaceCanvasView") -> None:
        super().__init__()
        self.system = system
        self.canvas_view = canvas_view
        self.radius = 46.0
        self._hovered = False
        self._press_screen_pos = QPoint()

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(8)
        self.setPos(0, 0)
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        plan_preview = (self.system.core_plan or "").strip()
        if len(plan_preview) > 360:
            plan_preview = plan_preview[:360].rstrip() + "…"
        proj_name = self.system.project_name or self.system.name
        self.setToolTip(
            f"PROJECT CORE: {proj_name}\n\n{plan_preview}\n\n"
            "[Left Click: Inspect Project Core  •  Right Click: Actions & Ollama Analysis]"
        )

    def boundingRect(self) -> QRectF:
        return QRectF(-95, -62, 190, 148)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.radius + 6, self.radius + 6)
        path.addRoundedRect(QRectF(-82, self.radius + 8, 164, 24), 6, 6)
        return path

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        is_selected = self.canvas_view.selected_node is self.system

        cat_info = SOURCE_CATEGORIES.get(self.system.source, SOURCE_CATEGORIES["manual"])
        accent = QColor(cat_info["color"])

        # Outer highlight ring
        ring_pen = QPen(
            accent if (is_selected or self._hovered) else QColor(accent.red(), accent.green(), accent.blue(), 90),
            2.0 if (is_selected or self._hovered) else 1.2,
        )
        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(0, 0), self.radius + 6, self.radius + 6)

        # Central core circle
        bg = QColor("#172033") if (is_selected or self._hovered) else QColor("#111726")
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(accent.lighter(115) if is_selected else accent, 2.4))
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

        # Center subtitle
        font_sub = QFont("Segoe UI", 6, QFont.Weight.Bold)
        painter.setFont(font_sub)
        painter.setPen(QColor("#94A3B8"))
        painter.drawText(
            QRectF(-self.radius, -16, self.radius * 2, 14),
            Qt.AlignmentFlag.AlignCenter,
            "CORE PLAN",
        )

        font_main = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font_main)
        painter.setPen(QColor("#F8FAFC"))
        proj_label = self.system.project_name or self.system.name
        short_proj = proj_label if len(proj_label) <= 13 else proj_label[:11] + "…"
        painter.drawText(
            QRectF(-self.radius + 4, -2, (self.radius - 4) * 2, 22),
            Qt.AlignmentFlag.AlignCenter,
            short_proj,
        )

        # Bottom label pill
        pill_rect = QRectF(-82, self.radius + 9, 164, 22)
        painter.setBrush(QBrush(QColor(15, 23, 42, 235)))
        painter.setPen(QPen(accent if is_selected else QColor("#263042"), 1.0))
        painter.drawRoundedRect(pill_rect, 5, 5)

        font_title = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font_title)
        painter.setPen(QColor("#F8FAFC"))
        full_label = proj_label if len(proj_label) <= 22 else proj_label[:20] + "…"
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, full_label)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_screen_pos = event.screenPos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if (event.screenPos() - self._press_screen_pos).manhattanLength() < 28:
                self.canvas_view.select_object(self.system, "sun", self)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.canvas_view.select_object(self.system, "sun", self)
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #151C28; color: #E2E8F0; border: 1px solid #2A364B; padding: 4px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2563EB; color: #FFFFFF; }"
        )
        act_ollama = menu.addAction("Enhance Project Summary with Ollama")
        act_add_p = menu.addAction("+ Add Architectural Module")
        act_open_dir = menu.addAction("Open Project Folder")
        act_copy_prompt = menu.addAction("Copy as AI Context (Prompt)")
        act_rebalance = menu.addAction("Auto-Align Module Layout")
        menu.addSeparator()
        act_galaxy = menu.addAction("Back to All Projects Overview (ESC)")
        chosen = menu.exec(event.screenPos())
        if chosen == act_ollama:
            self.canvas_view.contextActionRequested.emit("ollama_enhance", self.system)
        elif chosen == act_add_p:
            self.canvas_view.contextActionRequested.emit("add_planet", self.system)
        elif chosen == act_open_dir:
            self.canvas_view.contextActionRequested.emit("open_path", self.system)
        elif chosen == act_copy_prompt:
            self.canvas_view.contextActionRequested.emit("copy_prompt", self.system)
        elif chosen == act_rebalance:
            self.canvas_view.contextActionRequested.emit("rebalance", self.system)
        elif chosen == act_galaxy:
            self.canvas_view.switch_to_galaxy_view()
        event.accept()


class GalaxySectorBannerItem(QGraphicsItem):
    """Sector container banner for each application source (Antigravity, Codex, etc.) in the All Projects view."""

    def __init__(
        self,
        source_key: str,
        bounds_rect: QRectF,
        project_count: int,
    ) -> None:
        super().__init__()
        self.source_key = source_key
        self.cat_info = SOURCE_CATEGORIES.get(source_key, SOURCE_CATEGORIES["manual"])
        self.project_count = project_count
        self.box_rect = bounds_rect

        # Disable mouse interaction so sector banners never block clicks on project cards
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(1)

    def boundingRect(self) -> QRectF:
        return self.box_rect.adjusted(-12, -12, 12, 12)

    def shape(self) -> QPainterPath:
        return QPainterPath()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.cat_info["color"])

        # Sector background frame
        bg_color = QColor(15, 20, 30, 175)
        border_color = QColor(color.red(), color.green(), color.blue(), 80)
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1.4, Qt.PenStyle.SolidLine))
        painter.drawRoundedRect(self.box_rect, 14, 14)

        # Top header background bar
        header_bg_rect = QRectF(
            self.box_rect.left() + 1,
            self.box_rect.top() + 1,
            self.box_rect.width() - 2,
            46,
        )
        header_path = QPainterPath()
        header_path.addRoundedRect(header_bg_rect, 13, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 22)))
        painter.drawPath(header_path)

        # Header bottom divider line
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 55), 1.0))
        painter.drawLine(
            QPointF(self.box_rect.left() + 16, self.box_rect.top() + 47),
            QPointF(self.box_rect.right() - 16, self.box_rect.top() + 47),
        )

        header_rect = QRectF(
            self.box_rect.left() + 20,
            self.box_rect.top() + 10,
            self.box_rect.width() - 40,
            28,
        )

        # Left accent marker
        dot_rect = QRectF(header_rect.left(), header_rect.top() + 6, 10, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(dot_rect, 3, 3)

        # Source title and subtitle
        font_title = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font_title)
        painter.setPen(QColor("#F8FAFC"))
        title_rect = QRectF(
            header_rect.left() + 18,
            header_rect.top(),
            header_rect.width() - 110,
            28,
        )
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self.cat_info['title']}  —  {self.cat_info.get('subtitle', '')}",
        )

        # Project count badge
        badge_rect = QRectF(header_rect.right() - 84, header_rect.top() + 3, 84, 22)
        painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 120), 1.0))
        painter.drawRoundedRect(badge_rect, 5, 5)

        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(color.lighter(135))
        suffix = "Project" if self.project_count == 1 else "Projects"
        painter.drawText(
            badge_rect,
            Qt.AlignmentFlag.AlignCenter,
            f"{self.project_count} {suffix}",
        )


class GalaxyClusterItem(QGraphicsObject):
    """Rich Project Card in the All Projects view that opens on click."""

    CARD_W = 280.0
    CARD_H = 148.0

    def __init__(self, system: SolarSystem, canvas_view: "SpaceCanvasView") -> None:
        super().__init__()
        self.system = system
        self.canvas_view = canvas_view
        self._hovered = False
        self.is_dimmed = False
        self._press_screen_pos = QPoint()
        self._last_pan_pos = QPoint()
        self._was_dragged = False

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(10)
        self.setPos(system.galaxy_x, system.galaxy_y)

        self._purpose_snippet = self._extract_purpose_snippet()
        plan_preview = (self.system.core_plan or "").strip()
        if len(plan_preview) > 420:
            plan_preview = plan_preview[:420].rstrip() + "…"
        self.setToolTip(
            f"{self.system.project_name or self.system.name}\n\n{plan_preview}\n\n"
            "[Left Click: Enter Project  •  Right Click: Quick Actions]"
        )

    def _extract_purpose_snippet(self) -> str:
        """Extracts a concise summary of what the project does to display directly on the card."""
        raw = (self.system.core_plan or "").strip()
        if not raw:
            return "Project architecture and sub-component details."
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        cleaned_lines: List[str] = []
        for ln in lines:
            upper = ln.upper()
            if any(
                upper.startswith(h)
                for h in (
                    "PROJECT PURPOSE",
                    "CORE ARCHITECTURE",
                    "TECH STACK",
                    "KEY FILES",
                    "APPLICATION SOURCE",
                    "WORKSPACE PATH",
                    "STRUCTURE & SCOPE",
                    "PROJECT DIRECTORY",
                    "[OLLAMA",
                )
            ):
                continue
            cleaned_lines.append(ln.lstrip("•-* "))
            if len(" ".join(cleaned_lines)) > 120:
                break
        text = " ".join(cleaned_lines).strip()
        if not text:
            text = raw.replace("\n", " ").strip()
        return text[:118].rstrip() + ("…" if len(text) > 118 else "")

    def cardRect(self) -> QRectF:
        return QRectF(
            -self.CARD_W / 2.0,
            -self.CARD_H / 2.0,
            self.CARD_W,
            self.CARD_H,
        )

    def boundingRect(self) -> QRectF:
        return self.cardRect().adjusted(-8, -8, 8, 8)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.cardRect(), 10, 10)
        return path

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: Optional[QWidget] = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(0.22 if self.is_dimmed else 1.0)

        cat_info = SOURCE_CATEGORIES.get(self.system.source, SOURCE_CATEGORIES["manual"])
        accent = QColor(cat_info["color"])
        rect = self.cardRect()
        is_active = self.canvas_view.active_system is self.system

        # Card background and border
        bg_color = QColor("#1B2536") if self._hovered else QColor("#131A26")
        if self._hovered:
            border_color = accent.lighter(130)
            border_w = 2.0
        elif is_active:
            border_color = accent
            border_w = 1.6
        else:
            border_color = QColor("#263246")
            border_w = 1.1

        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, border_w))
        painter.drawRoundedRect(rect, 10, 10)

        # Left vertical accent stripe
        stripe_rect = QRectF(rect.left() + 1.5, rect.top() + 10, 4.0, rect.height() - 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawRoundedRect(stripe_rect, 2.0, 2.0)

        # Row 1: Project Name + AI Verified Badge (if enhanced)
        display_name = self.system.project_name or self.system.name
        short_name = display_name if len(display_name) <= 22 else display_name[:20] + "…"
        font_title = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font_title)
        painter.setPen(QColor("#F8FAFC"))
        title_rect = QRectF(rect.left() + 14, rect.top() + 10, rect.width() - 84, 22)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            short_name,
        )

        # Top-right verification badge
        if self.system.ollama_enhanced or "[OLLAMA" in (self.system.core_plan or ""):
            ai_badge = QRectF(rect.right() - 72, rect.top() + 12, 62, 18)
            painter.setBrush(QBrush(QColor(16, 185, 129, 35)))
            painter.setPen(QPen(QColor("#10B981"), 0.9))
            painter.drawRoundedRect(ai_badge, 4, 4)
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            painter.setPen(QColor("#34D399"))
            painter.drawText(ai_badge, Qt.AlignmentFlag.AlignCenter, "AI Verified")

        # Row 2: Project purpose snippet (2 lines)
        font_desc = QFont("Segoe UI", 8)
        painter.setFont(font_desc)
        painter.setPen(QColor("#CBD5E1") if self._hovered else QColor("#94A3B8"))
        desc_rect = QRectF(rect.left() + 14, rect.top() + 34, rect.width() - 24, 38)
        painter.drawText(
            desc_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            self._purpose_snippet,
        )

        # Row 3: Architectural Module preview pills (up to 3 modules)
        pill_x = rect.left() + 14
        pill_y = rect.top() + 80
        max_right = rect.right() - 12
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
        for p_node in self.system.planets[:3]:
            p_label = p_node.title
            if len(p_label) > 13:
                p_label = p_label[:12].rstrip() + "…"
            pill_w = min(84.0, max(54.0, len(p_label) * 5.6 + 16.0))
            if pill_x + pill_w > max_right:
                break
            p_rect = QRectF(pill_x, pill_y, pill_w, 20)
            p_color = QColor(p_node.color)
            painter.setBrush(QBrush(QColor(p_color.red(), p_color.green(), p_color.blue(), 28)))
            painter.setPen(QPen(QColor(p_color.red(), p_color.green(), p_color.blue(), 95), 0.9))
            painter.drawRoundedRect(p_rect, 4, 4)
            painter.setPen(p_color.lighter(135))
            painter.drawText(p_rect, Qt.AlignmentFlag.AlignCenter, p_label)
            pill_x += pill_w + 5.0

        # Footer divider line
        footer_y = rect.bottom() - 30
        painter.setPen(QPen(QColor("#1E293B"), 1.0))
        painter.drawLine(
            QPointF(rect.left() + 12, footer_y),
            QPointF(rect.right() - 12, footer_y),
        )

        # Row 4 (Footer): Module/Sub-Component count + "Open Project →"
        p_count = len(self.system.planets)
        s_count = sum(len(p.satellites) for p in self.system.planets)
        meta_text = f"{p_count} Modules • {s_count} Items"
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
        painter.setPen(QColor("#94A3B8"))
        meta_rect = QRectF(rect.left() + 14, footer_y + 4, 135, 22)
        painter.drawText(
            meta_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            meta_text,
        )

        action_rect = QRectF(rect.right() - 128, footer_y + 4, 116, 22)
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        painter.setPen(accent.lighter(135) if self._hovered else QColor("#64748B"))
        painter.drawText(
            action_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "Open Project →" if self._hovered else "Click to Inspect →",
        )

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.canvas_view.hovered_galaxy_system = self.system
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        if self.canvas_view.hovered_galaxy_system is self.system:
            self.canvas_view.hovered_galaxy_system = None
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_screen_pos = event.screenPos()
            self._last_pan_pos = event.screenPos()
            self._was_dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            if (event.screenPos() - self._press_screen_pos).manhattanLength() >= 8:
                self._was_dragged = True
                # Pan the canvas if dragging across a card instead of entering the project
                delta = event.screenPos() - self._last_pan_pos
                self._last_pan_pos = event.screenPos()
                h_bar = self.canvas_view.horizontalScrollBar()
                v_bar = self.canvas_view.verticalScrollBar()
                h_bar.setValue(h_bar.value() - delta.x())
                v_bar.setValue(v_bar.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            dist = (event.screenPos() - self._press_screen_pos).manhattanLength()
            if not self._was_dragged and dist < 8:
                target_system = self.system
                QTimer.singleShot(0, lambda s=target_system: self.canvas_view.enter_solar_system(s))
            self._was_dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            target_system = self.system
            QTimer.singleShot(0, lambda s=target_system: self.canvas_view.enter_solar_system(s))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background-color: #151C28; color: #E2E8F0; border: 1px solid #2A364B; padding: 4px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2563EB; color: #FFFFFF; }"
        )
        act_enter = menu.addAction("Open Project")
        act_ollama = menu.addAction("Enhance Project Summary with Ollama")
        act_open_dir = menu.addAction("Open Project Folder")
        act_copy = menu.addAction("Copy as AI Context (Prompt)")
        chosen = menu.exec(event.screenPos())
        if chosen == act_enter:
            target_system = self.system
            QTimer.singleShot(0, lambda s=target_system: self.canvas_view.enter_solar_system(s))
        elif chosen == act_ollama:
            self.canvas_view.contextActionRequested.emit("ollama_enhance", self.system)
        elif chosen == act_open_dir:
            self.canvas_view.contextActionRequested.emit("open_path", self.system)
        elif chosen == act_copy:
            self.canvas_view.contextActionRequested.emit("copy_prompt", self.system)
        event.accept()


class SpaceCanvasView(QGraphicsView):
    """Zoomable, pannable interactive architecture canvas."""

    nodeSelected = Signal(object, str)  # (node_obj, "sun" | "planet" | "satellite" | "link" | "note")
    viewModeChanged = Signal(str)  # "system" | "galaxy"
    tourStepChanged = Signal(int, int, str)  # (current_idx, total, step_title)
    contextActionRequested = Signal(str, object)  # (action_name, target_obj)
    drawingToolChanged = Signal(str, str)  # (tool_key, status_hint)

    def __init__(self, store: GalaxyStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.view_mode: str = "system"
        self.active_system: Optional[SolarSystem] = (
            self.store.systems[0] if self.store.systems else None
        )
        self.selected_node: Optional[object] = self.active_system

        self.scene_obj = QGraphicsScene(self)
        self.scene_obj.setSceneRect(-3200, -3200, 6400, 6400)
        self.setScene(self.scene_obj)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        # Rendering and interaction settings
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

        # Static architectural layout by default (orbit motion disabled)
        self.orbit_motion_enabled = False
        self._current_scale = 1.0
        self.search_query: str = ""
        self.tour_index: int = -1

        # Drawing & Design Studio state
        self.drawing_tool: str = "select"  # select, add_planet, add_satellite, draw_link, add_note, eraser
        self.drawing_layer: str = "core"
        self.drawing_sat_tag: str = "code"
        self.drawing_relation_type: str = "flows_to"
        self.drawing_note_color: str = "#F59E0B"

        self.is_drawing_link: bool = False
        self._link_source_item: Optional[PlanetGraphicsItem] = None
        self._draft_cursor_pos: Optional[QPointF] = None
        self._tool_buttons: Dict[str, QPushButton] = {}
        self._suboption_buttons: List[QPushButton] = []

        self.sun_item: Optional[SunGraphicsItem] = None
        self.planet_items: List[PlanetGraphicsItem] = []
        self.planet_item_map: Dict[str, PlanetGraphicsItem] = {}
        self.link_items: List[ConstellationLinkItem] = []
        self.note_items: List[CanvasNoteGraphicsItem] = []
        self.galaxy_items: List[GalaxyClusterItem] = []
        self.sector_banners: List[GalaxySectorBannerItem] = []
        self.hovered_galaxy_system: Optional[SolarSystem] = None

        self._setup_drawing_toolbar()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_animation_tick)
        self.timer.start(33)

        self.rebuild_scene()

    # ------------------------------------------------------------------
    # Floating Drawing & Architecture Design Toolbar
    # ------------------------------------------------------------------
    def _setup_drawing_toolbar(self) -> None:
        self.drawing_toolbar = QFrame(self)
        self.drawing_toolbar.setObjectName("DrawingStudioBar")
        self.drawing_toolbar.setStyleSheet(
            """
            QFrame#DrawingStudioBar {
                background-color: rgba(17, 23, 34, 242);
                border: 1px solid #283449;
                border-radius: 10px;
            }
            QLabel#StudioBarHeader {
                color: #64748B;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.8px;
                padding: 2px 4px;
            }
            QPushButton.ToolBtn {
                background-color: transparent;
                color: #CBD5E1;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 6px 10px;
                text-align: left;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.ToolBtn:hover {
                background-color: #1E293B;
                color: #F8FAFC;
            }
            QPushButton.ToolBtn[active="true"] {
                background-color: #1D4ED8;
                color: #FFFFFF;
                border: 1px solid #3B82F6;
            }
            """
        )
        tb_layout = QVBoxLayout(self.drawing_toolbar)
        tb_layout.setContentsMargins(6, 8, 6, 8)
        tb_layout.setSpacing(4)

        lbl_head = QLabel("DRAWING TOOLS")
        lbl_head.setObjectName("StudioBarHeader")
        tb_layout.addWidget(lbl_head)

        tool_specs = [
            ("select", "↖  Select & Move", "1", "Select nodes, drag to reposition, or pan the canvas (Shortcut: 1)"),
            ("add_planet", "⊕  Draw Module", "2", "Place a new Architectural Module where you click on the canvas (Shortcut: 2)"),
            ("add_satellite", "⊙  Sub-Component", "3", "Add a Sub-Component around the nearest module where you click (Shortcut: 3)"),
            ("draw_link", "↗  Draw Data Flow", "4", "Draw a directed Data Flow arrow by clicking or dragging between two modules (Shortcut: 4)"),
            ("add_note", "▤  Arch Note", "5", "Place a movable Architecture Note / Rule card on the canvas (Shortcut: 5)"),
            ("eraser", "✕  Eraser Tool", "6", "Delete any module, sub-component, data flow arrow, or note you click (Shortcut: 6)"),
        ]

        for t_key, t_label, shortcut_key, tip in tool_specs:
            btn = QPushButton(f"{t_label}")
            btn.setProperty("class", "ToolBtn")
            btn.setProperty("active", "true" if t_key == "select" else "false")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setFixedWidth(132)
            btn.clicked.connect(lambda checked=False, k=t_key: self.set_drawing_tool(k))
            tb_layout.addWidget(btn)
            self._tool_buttons[t_key] = btn

        # Contextual Sub-Option Bar (Layer / Flow Type / Tag Selector)
        self.drawing_subbar = QFrame(self)
        self.drawing_subbar.setObjectName("DrawingSubBar")
        self.drawing_subbar.setStyleSheet(
            """
            QFrame#DrawingSubBar {
                background-color: rgba(17, 23, 34, 242);
                border: 1px solid #283449;
                border-radius: 8px;
            }
            QLabel {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.SubOptBtn {
                background-color: #161F2E;
                color: #CBD5E1;
                border: 1px solid #263246;
                border-radius: 5px;
                padding: 4px 9px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton.SubOptBtn:hover {
                background-color: #1E293B;
                color: #FFFFFF;
            }
            QPushButton.SubOptBtn[active="true"] {
                background-color: #2563EB;
                color: #FFFFFF;
                border: 1px solid #60A5FA;
            }
            """
        )
        self.subbar_layout = QHBoxLayout(self.drawing_subbar)
        self.subbar_layout.setContentsMargins(10, 6, 10, 6)
        self.subbar_layout.setSpacing(6)
        self.drawing_subbar.setVisible(False)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        if hasattr(self, "drawing_toolbar"):
            self.drawing_toolbar.move(14, 14)
            self.drawing_toolbar.adjustSize()
        if hasattr(self, "drawing_subbar"):
            self.drawing_subbar.move(164, 14)
            self.drawing_subbar.adjustSize()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_overlays()

    def set_drawing_tool(self, tool_key: str) -> None:
        """Switches the active drawing tool and updates the contextual sub-option bar."""
        if self.view_mode != "system" and tool_key != "select":
            if self.active_system:
                self.enter_solar_system(self.active_system)

        self.drawing_tool = tool_key
        self.is_drawing_link = False
        self._link_source_item = None

        for k, btn in self._tool_buttons.items():
            btn.setProperty("active", "true" if k == tool_key else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if tool_key == "select":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

        self._rebuild_subbar_options()
        self.scene_obj.update()

        hints = {
            "select": "SELECT & MOVE MODE  •  Select nodes and drag to reposition. (Hold Shift+Drag to quickly draw a data flow link)",
            "add_planet": "MODULE DRAW MODE  •  Choose a layer type above and click anywhere on the canvas to place a new module.",
            "add_satellite": "SUB-COMPONENT MODE  •  Click around any module on the canvas to attach a new sub-component.",
            "draw_link": "DATA FLOW DRAW MODE  •  Click the source module first, then click the target module (or drag from one to another).",
            "add_note": "ARCHITECTURE NOTE MODE  •  Click anywhere on the canvas to place a freeform note or rule card.",
            "eraser": "ERASER MODE  •  Click any module, sub-component, data flow arrow, or note card to delete it.",
        }
        self.drawingToolChanged.emit(tool_key, hints.get(tool_key, ""))

    def _rebuild_subbar_options(self) -> None:
        while self.subbar_layout.count():
            child = self.subbar_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if self.view_mode != "system" or self.drawing_tool == "select":
            self.drawing_subbar.setVisible(False)
            return

        if self.drawing_tool == "add_planet":
            lbl = QLabel("Module Layer:")
            self.subbar_layout.addWidget(lbl)
            short_layers = [
                ("ui", "UI Layer"),
                ("core", "Core / Data"),
                ("integration", "API / Integration"),
                ("architecture", "Architecture & Plan"),
                ("testing", "Testing & Security"),
            ]
            for l_key, l_name in short_layers:
                btn = QPushButton(l_name)
                btn.setProperty("class", "SubOptBtn")
                btn.setProperty("active", "true" if self.drawing_layer == l_key else "false")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, k=l_key: self._set_drawing_layer(k))
                self.subbar_layout.addWidget(btn)

        elif self.drawing_tool == "add_satellite":
            lbl = QLabel("Sub-Component Type:")
            self.subbar_layout.addWidget(lbl)
            for t_key, (t_badge, _) in TAG_BADGE_MAP.items():
                btn = QPushButton(t_badge)
                btn.setProperty("class", "SubOptBtn")
                btn.setProperty("active", "true" if self.drawing_sat_tag == t_key else "false")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, k=t_key: self._set_drawing_sat_tag(k))
                self.subbar_layout.addWidget(btn)

        elif self.drawing_tool == "draw_link":
            lbl = QLabel("Flow Type:")
            self.subbar_layout.addWidget(lbl)
            for r_key, (r_label, _) in RELATION_TYPES.items():
                btn = QPushButton(r_label)
                btn.setProperty("class", "SubOptBtn")
                btn.setProperty("active", "true" if self.drawing_relation_type == r_key else "false")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, k=r_key: self._set_drawing_relation_type(k))
                self.subbar_layout.addWidget(btn)

        elif self.drawing_tool == "add_note":
            lbl = QLabel("Note Card Color:")
            self.subbar_layout.addWidget(lbl)
            note_colors = [
                ("#F59E0B", "Amber (Warning/Note)"),
                ("#3B82F6", "Blue (Info)"),
                ("#10B981", "Emerald (Task)"),
                ("#8B5CF6", "Purple (API/Rule)"),
                ("#F43F5E", "Rose (Critical)"),
            ]
            for c_hex, c_name in note_colors:
                btn = QPushButton(c_name)
                btn.setProperty("class", "SubOptBtn")
                btn.setProperty("active", "true" if self.drawing_note_color == c_hex else "false")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked=False, c=c_hex: self._set_drawing_note_color(c))
                self.subbar_layout.addWidget(btn)

        elif self.drawing_tool == "eraser":
            lbl = QLabel("Eraser Active: Click any node or data flow arrow to delete it (Press ESC to exit)")
            self.subbar_layout.addWidget(lbl)

        self.drawing_subbar.setVisible(True)
        self._reposition_overlays()

    def _set_drawing_layer(self, layer_key: str) -> None:
        self.drawing_layer = layer_key
        self._rebuild_subbar_options()
        self.scene_obj.update()

    def _set_drawing_sat_tag(self, tag_key: str) -> None:
        self.drawing_sat_tag = tag_key
        self._rebuild_subbar_options()
        self.scene_obj.update()

    def _set_drawing_relation_type(self, rel_key: str) -> None:
        self.drawing_relation_type = rel_key
        self._rebuild_subbar_options()
        self.scene_obj.update()

    def _set_drawing_note_color(self, hex_color: str) -> None:
        self.drawing_note_color = hex_color
        self._rebuild_subbar_options()
        self.scene_obj.update()

    # ------------------------------------------------------------------
    # Background Rendering (Dark Theme + Adaptive Dot Grid)
    # ------------------------------------------------------------------
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#0B0E14"))

        grid_step = 48 if self._current_scale >= 0.55 else 96
        left = int(math.floor(rect.left() / grid_step) * grid_step)
        top = int(math.floor(rect.top() / grid_step) * grid_step)
        right = int(math.ceil(rect.right()))
        bottom = int(math.ceil(rect.bottom()))

        max_points = 3500
        cols = max(1, (right - left) // grid_step)
        rows = max(1, (bottom - top) // grid_step)
        if cols * rows <= max_points:
            pen = QPen(QColor(148, 163, 184, 28), 1.5)
            painter.setPen(pen)
            for x in range(left, right, grid_step):
                for y in range(top, bottom, grid_step):
                    painter.drawPoint(x, y)

        if self.view_mode == "system" and self.active_system:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radii = sorted({round(p.orbit_radius) for p in self.active_system.planets})
            for r in radii:
                orbit_pen = QPen(QColor(148, 163, 184, 24), 1.0, Qt.PenStyle.DashLine)
                painter.setPen(orbit_pen)
                painter.drawEllipse(QPointF(0, 0), float(r), float(r))

    # ------------------------------------------------------------------
    # Live Drawing Preview (Ghost Node & Link Arrow)
    # ------------------------------------------------------------------
    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self.view_mode != "system" or not self.active_system or self._draft_cursor_pos is None:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pt = self._draft_cursor_pos

        if self.drawing_tool == "add_planet":
            dist = max(125.0, min(950.0, math.hypot(pt.x(), pt.y())))
            ang = math.atan2(pt.y(), pt.x())
            snap_pt = QPointF(math.cos(ang) * dist, math.sin(ang) * dist)
            layer_title, color_hex = LAYER_DEFINITIONS.get(
                self.drawing_layer, ("Architectural Module", "#3B82F6")
            )
            c = QColor(color_hex)

            # Orbit guide ring
            painter.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 70), 1.1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), dist, dist)

            # Ghost module circle
            painter.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 45)))
            painter.setPen(QPen(c.lighter(125), 2.0, Qt.PenStyle.DashLine))
            painter.drawEllipse(snap_pt, 26.0, 26.0)

            # Info label
            lbl_rect = QRectF(snap_pt.x() - 95, snap_pt.y() + 32, 190, 22)
            painter.setBrush(QBrush(QColor(15, 23, 42, 235)))
            painter.setPen(QPen(c, 1.0))
            painter.drawRoundedRect(lbl_rect, 5, 5)
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.setPen(QColor("#F8FAFC"))
            painter.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, f"+ {layer_title[:22]}")

        elif self.drawing_tool == "add_satellite":
            nearest_p = self._find_nearest_planet_item(pt)
            if nearest_p is not None:
                p_pos = nearest_p.pos()
                dx = pt.x() - p_pos.x()
                dy = pt.y() - p_pos.y()
                dist = max(62.0, min(260.0, math.hypot(dx, dy)))
                ang = math.atan2(dy, dx)
                sat_pt = QPointF(p_pos.x() + math.cos(ang) * dist, p_pos.y() + math.sin(ang) * dist)
                badge_lbl, tag_hex = TAG_BADGE_MAP.get(self.drawing_sat_tag, ("CODE", "#10B981"))
                c = QColor(tag_hex)

                painter.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 150), 1.4, Qt.PenStyle.DashLine))
                painter.drawLine(p_pos, sat_pt)

                painter.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 55)))
                painter.setPen(QPen(c.lighter(130), 1.8))
                painter.drawEllipse(sat_pt, 9.5, 9.5)

                lbl_rect = QRectF(sat_pt.x() - 80, sat_pt.y() + 14, 160, 20)
                painter.setBrush(QBrush(QColor(15, 23, 42, 235)))
                painter.setPen(QPen(c, 1.0))
                painter.drawRoundedRect(lbl_rect, 4, 4)
                painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                painter.setPen(QColor("#F8FAFC"))
                painter.drawText(
                    lbl_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    f"+ {badge_lbl} → {nearest_p.planet.title[:14]}",
                )

        elif self.is_drawing_link and self._link_source_item is not None:
            p1 = self._link_source_item.pos()
            hover_target = self._find_planet_item_at(pt)
            p2 = (
                hover_target.pos()
                if (hover_target and hover_target is not self._link_source_item)
                else pt
            )

            rel_title, rel_hex = RELATION_TYPES.get(
                self.drawing_relation_type, ("Data Flow (→)", "#3B82F6")
            )
            c = QColor(rel_hex)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(c.lighter(135), 2.2, Qt.PenStyle.SolidLine))
            painter.drawEllipse(p1, self._link_source_item.radius + 7, self._link_source_item.radius + 7)

            if hover_target and hover_target is not self._link_source_item:
                painter.setPen(QPen(QColor("#34D399"), 2.4, Qt.PenStyle.SolidLine))
                painter.drawEllipse(p2, hover_target.radius + 8, hover_target.radius + 8)

            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            dist = math.hypot(dx, dy)
            if dist > 20:
                ux = dx / dist
                uy = dy / dist
                start = QPointF(
                    p1.x() + ux * (self._link_source_item.radius + 6),
                    p1.y() + uy * (self._link_source_item.radius + 6),
                )
                end = (
                    QPointF(
                        p2.x() - ux * (hover_target.radius + 8),
                        p2.y() - uy * (hover_target.radius + 8),
                    )
                    if (hover_target and hover_target is not self._link_source_item)
                    else p2
                )
                mid = QPointF((start.x() + end.x()) * 0.5, (start.y() + end.y()) * 0.5)
                ctrl = QPointF(mid.x() - uy * min(32.0, dist * 0.1), mid.y() + ux * min(32.0, dist * 0.1))

                path = QPainterPath(start)
                path.quadTo(ctrl, end)
                painter.setPen(QPen(c.lighter(130), 2.2, Qt.PenStyle.DashLine))
                painter.drawPath(path)

                tx = end.x() - ctrl.x()
                ty = end.y() - ctrl.y()
                t_len = math.hypot(tx, ty) or 1.0
                tx /= t_len
                ty /= t_len
                a_sz = 9.0
                left_pt = QPointF(end.x() - tx * a_sz - ty * (a_sz * 0.55), end.y() - ty * a_sz + tx * (a_sz * 0.55))
                right_pt = QPointF(end.x() - tx * a_sz + ty * (a_sz * 0.55), end.y() - ty * a_sz - tx * (a_sz * 0.55))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(c.lighter(130)))
                painter.drawPolygon(QPolygonF([end, left_pt, right_pt]))

                lbl_pos = path.pointAtPercent(0.5)
                lbl_rect = QRectF(lbl_pos.x() - 78, lbl_pos.y() - 11, 156, 22)
                painter.setBrush(QBrush(QColor(15, 23, 42, 235)))
                painter.setPen(QPen(c, 1.1))
                painter.drawRoundedRect(lbl_rect, 5, 5)
                painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                painter.setPen(QColor("#F8FAFC"))
                painter.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, rel_title)

        elif self.drawing_tool == "add_note":
            c = QColor(self.drawing_note_color)
            n_rect = QRectF(pt.x() - 107.5, pt.y() - 54.0, 215.0, 108.0)
            painter.setBrush(QBrush(QColor(18, 24, 36, 170)))
            painter.setPen(QPen(c.lighter(125), 1.8, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(n_rect, 8, 8)
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.setPen(c.lighter(135))
            painter.drawText(n_rect, Qt.AlignmentFlag.AlignCenter, "+ Architecture Note Card (Click)")

    # ------------------------------------------------------------------
    # Scene Construction (Project View <-> All Projects Overview)
    # ------------------------------------------------------------------
    def rebuild_scene(self) -> None:
        self.scene_obj.clear()
        self.sun_item = None
        self.planet_items.clear()
        self.planet_item_map.clear()
        self.link_items.clear()
        self.note_items.clear()
        self.galaxy_items.clear()
        self.sector_banners.clear()

        if hasattr(self, "drawing_toolbar"):
            self.drawing_toolbar.setVisible(self.view_mode == "system")
        if hasattr(self, "drawing_subbar"):
            self.drawing_subbar.setVisible(
                self.view_mode == "system" and self.drawing_tool != "select"
            )

        if self.view_mode == "system" and self.active_system:
            self.sun_item = SunGraphicsItem(self.active_system, self)
            self.scene_obj.addItem(self.sun_item)

            for planet in self.active_system.planets:
                p_item = PlanetGraphicsItem(planet, self)
                self.scene_obj.addItem(p_item)
                self.planet_items.append(p_item)
                self.planet_item_map[planet.id] = p_item

            for link in self.active_system.links:
                src_item = self.planet_item_map.get(link.source_id)
                dst_item = self.planet_item_map.get(link.target_id)
                if src_item and dst_item and src_item is not dst_item:
                    l_item = ConstellationLinkItem(link, src_item, dst_item, self)
                    self.scene_obj.addItem(l_item)
                    self.link_items.append(l_item)

            for note in self.active_system.notes:
                n_item = CanvasNoteGraphicsItem(note, self)
                self.scene_obj.addItem(n_item)
                self.note_items.append(n_item)

            self._update_link_highlights()
            if self.search_query:
                self._apply_search_filter()
        else:
            self.store.recalculate_galaxy_sectors()
            grouped = self.store.grouped_by_source()

            for source_key, sys_list in grouped.items():
                if not sys_list:
                    continue

                xs = [s.galaxy_x for s in sys_list]
                ys = [s.galaxy_y for s in sys_list]
                min_x = min(xs) - GalaxyClusterItem.CARD_W / 2.0 - 28.0
                max_x = max(xs) + GalaxyClusterItem.CARD_W / 2.0 + 28.0
                min_y = min(ys) - GalaxyClusterItem.CARD_H / 2.0 - 68.0
                max_y = max(ys) + GalaxyClusterItem.CARD_H / 2.0 + 26.0

                if (max_x - min_x) < 420.0:
                    cx = (min_x + max_x) * 0.5
                    min_x = cx - 210.0
                    max_x = cx + 210.0

                bounds = QRectF(min_x, min_y, max_x - min_x, max_y - min_y)
                banner = GalaxySectorBannerItem(source_key, bounds, len(sys_list))
                self.scene_obj.addItem(banner)
                self.sector_banners.append(banner)

                for system in sys_list:
                    c_item = GalaxyClusterItem(system, self)
                    self.scene_obj.addItem(c_item)
                    self.galaxy_items.append(c_item)

            if self.search_query:
                self._apply_search_filter()

    # ------------------------------------------------------------------
    # Interactive Drawing & Placement Methods
    # ------------------------------------------------------------------
    def _find_planet_item_at(self, scene_pos: QPointF) -> Optional[PlanetGraphicsItem]:
        for item in self.scene_obj.items(scene_pos):
            if isinstance(item, PlanetGraphicsItem):
                return item
        for p_item in self.planet_items:
            if math.hypot(p_item.pos().x() - scene_pos.x(), p_item.pos().y() - scene_pos.y()) <= p_item.radius + 16:
                return p_item
        return None

    def _find_nearest_planet_item(self, scene_pos: QPointF) -> Optional[PlanetGraphicsItem]:
        best_item: Optional[PlanetGraphicsItem] = None
        best_d = 1e9
        for p_item in self.planet_items:
            d = math.hypot(p_item.pos().x() - scene_pos.x(), p_item.pos().y() - scene_pos.y())
            if d < best_d:
                best_d = d
                best_item = p_item
        return best_item

    def handle_planet_click_for_link(self, planet_item: PlanetGraphicsItem, scene_pos: QPointF) -> None:
        if not self.is_drawing_link or self._link_source_item is None:
            self.is_drawing_link = True
            self._link_source_item = planet_item
            self._draft_cursor_pos = scene_pos
            self.scene_obj.update()
        elif planet_item is not self._link_source_item:
            self._create_link_between(self._link_source_item.planet, planet_item.planet)
            self.is_drawing_link = False
            self._link_source_item = None
            self.scene_obj.update()

    def update_interactive_link(self, scene_pos: QPointF) -> None:
        self._draft_cursor_pos = scene_pos
        self.scene_obj.update()

    def finish_interactive_link_drag(self, scene_pos: QPointF) -> None:
        target_item = self._find_planet_item_at(scene_pos)
        if (
            self.is_drawing_link
            and self._link_source_item is not None
            and target_item is not None
            and target_item is not self._link_source_item
        ):
            self._create_link_between(self._link_source_item.planet, target_item.planet)
            self.is_drawing_link = False
            self._link_source_item = None
            self.scene_obj.update()

    def _create_link_between(self, src_planet: PlanetNode, dst_planet: PlanetNode) -> None:
        if not self.active_system or src_planet.id == dst_planet.id:
            return
        self.store.push_undo()
        rel_title, _ = RELATION_TYPES.get(self.drawing_relation_type, ("Data Flow", "#3B82F6"))
        default_label = rel_title.split("(")[0].strip()
        link = self.active_system.add_link(
            src_planet.id,
            dst_planet.id,
            label=default_label,
            relation_type=self.drawing_relation_type,
            user_drawn=True,
        )
        if link:
            link.relation_type = self.drawing_relation_type
            link.is_user_modified = True
            self.store.save()
            self.rebuild_scene()
            self.select_object(link, "link")

    def place_planet_at(self, scene_pos: QPointF) -> None:
        if not self.active_system:
            return
        self.store.push_undo()
        layer_title, _ = LAYER_DEFINITIONS.get(self.drawing_layer, ("New Module", "#3B82F6"))
        short_base = layer_title.split("(")[0].strip()
        count = sum(1 for p in self.active_system.planets if p.layer == self.drawing_layer) + 1
        title = f"{short_base} {count}" if count > 1 else short_base
        summary = (
            f"MODULE PURPOSE & ROLE:\n"
            f"This module handles '{layer_title}' responsibilities within the {self.active_system.project_name} project.\n\n"
            f"HOW IT WORKS & TECHNICAL FLOW:\n"
            f"• Edit this module's title, detailed responsibilities, and data flows from the right-hand inspector panel."
        )
        planet = self.active_system.add_planet_at_pos(
            scene_pos.x(),
            scene_pos.y(),
            title=title,
            summary=summary,
            layer=self.drawing_layer,
        )
        self.store.save()
        self.rebuild_scene()
        p_item = self.planet_item_map.get(planet.id)
        if p_item:
            animate_item_pop_in(p_item)
        self.select_object(planet, "planet", p_item)

    def place_satellite_on_planet(
        self,
        planet_item: Optional[PlanetGraphicsItem],
        scene_pos: Optional[QPointF],
    ) -> None:
        if not self.active_system:
            return
        target_p_item = planet_item or (
            self._find_nearest_planet_item(scene_pos) if scene_pos is not None else None
        )
        if target_p_item is None:
            return
        self.store.push_undo()
        p_node = target_p_item.planet
        badge_lbl, _ = TAG_BADGE_MAP.get(self.drawing_sat_tag, ("COMPONENT", "#10B981"))
        title = f"New {badge_lbl.title()} Item {len(p_node.satellites) + 1}"
        content = (
            "WHAT DOES IT DO?\n"
            "Describe the feature or source file represented by this sub-component.\n\n"
            "HOW IT WORKS & TECHNICAL DETAILS:\n"
            "• Update the title, technical workflow, and file reference from the right-hand inspector."
        )
        if scene_pos is not None:
            dx = scene_pos.x() - target_p_item.pos().x()
            dy = scene_pos.y() - target_p_item.pos().y()
            sat = p_node.add_satellite_at_offset(
                dx, dy, title=title, content=content, visual_tag=self.drawing_sat_tag
            )
        else:
            sat = p_node.add_satellite(
                title=title, content=content, visual_tag=self.drawing_sat_tag
            )
            sat.is_user_modified = True
        self.store.save()
        self.rebuild_scene()
        new_sat_item: Optional[SatelliteGraphicsItem] = None
        p_item = self.planet_item_map.get(p_node.id)
        if p_item:
            for s_item in p_item.satellite_items:
                if s_item.satellite.id == sat.id:
                    new_sat_item = s_item
                    animate_item_pop_in(s_item)
                    break
        self.select_object(sat, "satellite", new_sat_item)

    def place_note_at(self, scene_pos: QPointF) -> None:
        if not self.active_system:
            return
        self.store.push_undo()
        note = self.active_system.add_note(
            scene_pos.x(),
            scene_pos.y(),
            title=f"Architecture Note {len(self.active_system.notes) + 1}",
            content="Write data flow rules, TODO items, or important architectural decisions here.",
            color=self.drawing_note_color,
        )
        self.store.save()
        self.rebuild_scene()
        new_note_item: Optional[CanvasNoteGraphicsItem] = None
        for n_item in self.note_items:
            if n_item.note.id == note.id:
                new_note_item = n_item
                animate_item_pop_in(n_item)
                break
        self.select_object(note, "note", new_note_item)

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
        if (
            self.view_mode == "system"
            and event.button() == Qt.MouseButton.LeftButton
            and self.is_drawing_link
        ):
            scene_pos = self.mapToScene(event.position().toPoint())
            self.finish_interactive_link_drag(scene_pos)
        super().mouseReleaseEvent(event)

    def _on_animation_tick(self) -> None:
        if self.view_mode == "system" and self.orbit_motion_enabled:
            for p_item in self.planet_items:
                p_item.advance_orbit(1.0)
            for l_item in self.link_items:
                l_item.update_geometry()

    def _update_link_highlights(self) -> None:
        selected_planet_id: Optional[str] = None
        if isinstance(self.selected_node, PlanetNode):
            selected_planet_id = self.selected_node.id
        elif isinstance(self.selected_node, SatelliteNode) and self.active_system:
            parent_p = self.active_system.find_parent_planet(self.selected_node)
            if parent_p:
                selected_planet_id = parent_p.id

        for l_item in self.link_items:
            if selected_planet_id is None:
                l_item.is_highlighted = True
            else:
                l_item.is_highlighted = (
                    l_item.link.source_id == selected_planet_id
                    or l_item.link.target_id == selected_planet_id
                )
            l_item.update()

    # ------------------------------------------------------------------
    # Fuzzy Search and Highlighting
    # ------------------------------------------------------------------
    def set_search_query(self, query: str) -> None:
        self.search_query = query.strip().lower()
        self._apply_search_filter()

    def _apply_search_filter(self) -> None:
        q = self.search_query
        if self.view_mode == "system":
            if not q:
                for p_item in self.planet_items:
                    p_item.is_dimmed = False
                    p_item.update()
                    for s_item in p_item.satellite_items:
                        s_item.is_dimmed = False
                        s_item.update()
                return

            first_match_item: Optional[QGraphicsItem] = None
            for p_item in self.planet_items:
                p_match = (
                    q in p_item.planet.title.lower()
                    or q in p_item.planet.summary.lower()
                )
                any_sat_match = False
                for s_item in p_item.satellite_items:
                    s_match = (
                        q in s_item.satellite.title.lower()
                        or q in s_item.satellite.content.lower()
                    )
                    s_item.is_dimmed = not (s_match or p_match)
                    s_item.update()
                    if s_match:
                        any_sat_match = True
                        if first_match_item is None:
                            first_match_item = s_item

                p_item.is_dimmed = not (p_match or any_sat_match)
                p_item.update()
                if p_match and first_match_item is None:
                    first_match_item = p_item

            if first_match_item is not None:
                self.centerOn(first_match_item.scenePos())
        else:
            for g_item in self.galaxy_items:
                if not q:
                    g_item.is_dimmed = False
                else:
                    sys = g_item.system
                    match = (
                        q in sys.name.lower()
                        or q in sys.project_name.lower()
                        or q in sys.core_plan.lower()
                        or any(q in p.title.lower() for p in sys.planets)
                    )
                    g_item.is_dimmed = not match
                g_item.update()

    # ------------------------------------------------------------------
    # Guided Architecture Tour
    # ------------------------------------------------------------------
    def start_or_advance_tour(self) -> None:
        if not self.active_system:
            return
        if self.view_mode != "system":
            self.enter_solar_system(self.active_system)

        steps = self.active_system.tour_steps
        if not steps:
            self.active_system.rebuild_links_and_tour()
            steps = self.active_system.tour_steps

        total = len(steps) + 1  # +1 for the Project Core
        self.tour_index = (self.tour_index + 1) % total

        for p_item in self.planet_items:
            p_item.is_tour_active = False
            p_item.update()

        if self.tour_index == 0:
            self.resetTransform()
            self._current_scale = 1.0
            self.centerOn(0, 0)
            self.select_object(self.active_system, "sun", self.sun_item)
            self.tourStepChanged.emit(
                1, total, f"Core: {self.active_system.project_name or self.active_system.name}"
            )
        else:
            planet_id = steps[self.tour_index - 1]
            p_item = self.planet_item_map.get(planet_id)
            if p_item:
                p_item.is_tour_active = True
                p_item.update()
                self.resetTransform()
                self.scale(1.12, 1.12)
                self._current_scale = 1.12
                self.centerOn(p_item.scenePos())
                self.select_object(p_item.planet, "planet", p_item)
                self.tourStepChanged.emit(
                    self.tour_index + 1, total, p_item.planet.title
                )

    def stop_tour(self) -> None:
        self.tour_index = -1
        for p_item in self.planet_items:
            p_item.is_tour_active = False
            p_item.update()

    # ------------------------------------------------------------------
    # Selection and View Mode Transitions
    # ------------------------------------------------------------------
    def select_object(
        self,
        node_obj: object,
        node_type: str,
        graphics_item: Optional[QGraphicsItem] = None,
    ) -> None:
        self.selected_node = node_obj
        self._update_link_highlights()
        self.scene_obj.update()
        self.nodeSelected.emit(node_obj, node_type)

    def enter_solar_system(self, system: SolarSystem) -> None:
        """Enters the architecture view of the selected project and centers the canvas on it."""
        self.active_system = system
        self.selected_node = system
        self.view_mode = "system"
        self.tour_index = -1
        self.resetTransform()
        self._current_scale = 1.0
        self.rebuild_scene()
        self.centerOn(0, 0)
        self.viewModeChanged.emit("system")
        self.nodeSelected.emit(system, "sun")

    def switch_to_galaxy_view(self) -> None:
        """Switches to the All Projects (Source Sectors) overview."""
        self.view_mode = "galaxy"
        self.tour_index = -1
        self.resetTransform()
        self.rebuild_scene()

        items_rect = self.scene_obj.itemsBoundingRect()
        if not items_rect.isEmpty() and self.viewport().width() > 200:
            self.fitInView(
                items_rect.adjusted(-90, -90, 90, 90),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            scale_val = self.transform().m11()
            if scale_val > 1.0:
                self.resetTransform()
                self._current_scale = 0.85
                self.scale(0.85, 0.85)
                self.centerOn(items_rect.center())
            else:
                self._current_scale = max(0.30, scale_val)
        else:
            self._current_scale = 0.72
            self.scale(0.72, 0.72)
            self.centerOn(0, 0)

        self.viewModeChanged.emit("galaxy")

    def focus_on_sun(self) -> None:
        if self.view_mode != "system" and self.active_system:
            self.enter_solar_system(self.active_system)
        else:
            self.resetTransform()
            self._current_scale = 1.0
            self.centerOn(0, 0)
            if self.active_system:
                self.select_object(self.active_system, "sun", self.sun_item)

    def _find_galaxy_cluster_near(self, scene_pos: QPointF) -> Optional[SolarSystem]:
        """Finds the project card at or nearest to the given scene coordinates."""
        for item in self.scene_obj.items(scene_pos):
            if isinstance(item, GalaxyClusterItem):
                return item.system

        best_sys: Optional[SolarSystem] = None
        best_dist = 240.0
        for g_item in self.galaxy_items:
            d = math.hypot(
                g_item.pos().x() - scene_pos.x(),
                g_item.pos().y() - scene_pos.y(),
            )
            if d < best_dist:
                best_dist = d
                best_sys = g_item.system
        return best_sys

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return

        zoom_factor = 1.14 if delta > 0 else 1 / 1.14
        new_scale = self._current_scale * zoom_factor

        if 0.20 <= new_scale <= 3.5:
            self._current_scale = new_scale
            self.scale(zoom_factor, zoom_factor)

    def zoom_in(self) -> None:
        """Zooms the canvas in by one step."""
        factor = 1.15
        if self._current_scale * factor <= 3.5:
            self._current_scale *= factor
            self.scale(factor, factor)

    def zoom_out(self) -> None:
        """Zooms the canvas out by one step."""
        factor = 1.0 / 1.15
        if self._current_scale * factor >= 0.20:
            self._current_scale *= factor
            self.scale(factor, factor)

    def reset_zoom(self) -> None:
        """Resets zoom level and centers the scene."""
        self.resetTransform()
        self._current_scale = 1.0
        if self.view_mode == "system":
            self.focus_on_sun()
        else:
            self.switch_to_galaxy_view()

    def contextMenuEvent(self, event) -> None:
        """Opens a quick-add and action context menu when right-clicking on empty canvas space."""
        item = self.itemAt(event.pos())
        if item is not None and not isinstance(item, (GalaxySectorBannerItem,)):
            super().contextMenuEvent(event)
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background-color: #0F172A;
                color: #F8FAFC;
                border: 1px solid #334155;
                padding: 4px;
                border-radius: 6px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2563EB;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background-color: #1E293B;
                margin: 4px 0;
            }
            """
        )

        scene_pt = self.mapToScene(event.pos())

        if self.view_mode == "system" and self.active_system:
            act_add_p = menu.addAction("+  Add Module Here...")
            act_add_n = menu.addAction("+  Add Architecture Note Here...")
            menu.addSeparator()
            act_rebal = menu.addAction("Auto-Align Layout")
            act_ollama = menu.addAction(f"Run AI Verification ({self.active_system.name})")
            menu.addSeparator()
            act_back = menu.addAction("Switch to All Projects Overview")

            chosen = menu.exec(event.globalPos())
            if chosen == act_add_p:
                title, ok = QInputDialog.getText(
                    self,
                    "Add New Module",
                    "Name of the architectural module or layer to add:",
                )
                if ok and title.strip():
                    self.store.push_undo()
                    p = self.active_system.add_planet_at_pos(
                        scene_pt.x(),
                        scene_pt.y(),
                        title=title.strip(),
                        summary="New architectural layer description.",
                    )
                    self.store.save()
                    self.rebuild_scene()
                    self.select_object(p, "planet")
                    self.contextActionRequested.emit("refresh_tree", None)

            elif chosen == act_add_n:
                title, ok = QInputDialog.getText(
                    self,
                    "Add New Architecture Note",
                    "Enter note title:",
                    text="Architecture Note",
                )
                if ok and title.strip():
                    self.store.push_undo()
                    n = self.active_system.add_note(
                        scene_pt.x(),
                        scene_pt.y(),
                        title=title.strip(),
                        content="Write data flow rules or technical notes here.",
                    )
                    self.store.save()
                    self.rebuild_scene()
                    self.select_object(n, "note")
                    self.contextActionRequested.emit("refresh_tree", None)

            elif chosen == act_rebal:
                self.contextActionRequested.emit("rebalance", None)
            elif chosen == act_ollama:
                self.contextActionRequested.emit("ollama_enhance", self.active_system)
            elif chosen == act_back:
                self.switch_to_galaxy_view()

        else:
            act_new = menu.addAction("+  Design New Project...")
            act_scan = menu.addAction("Scan Helios 'projects/' Directory")
            act_merge = menu.addAction("Merge Duplicate Projects...")
            menu.addSeparator()
            act_sett = menu.addAction("Settings & Configuration...")

            chosen = menu.exec(event.globalPos())
            if chosen == act_new:
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
        if key in (Qt.Key.Key_Home, Qt.Key.Key_R):
            if self.view_mode == "system":
                self.focus_on_sun()
            else:
                self.switch_to_galaxy_view()
            event.accept()
            return
        super().keyPressEvent(event)
