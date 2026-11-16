"""`CanvasNoteGraphicsItem`: a draggable free-form note card on the canvas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QBrush, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.models import CanvasNote
from ui import theme
from ui.canvas.helpers import _canvas_menu, elide

if TYPE_CHECKING:  # annotations only: a runtime import would be an import cycle
    from ui.canvas.view import SpaceCanvasView


# ----------------------------------------------------------------------------
# Notes
# ----------------------------------------------------------------------------
class CanvasNoteGraphicsItem(QGraphicsObject):
    """Draggable free-form note card placed on the canvas."""

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
        self.setToolTip(f"{self.note.title}\n\n{self.note.content[:260]}")

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

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.cardRect()
        accent = theme.soft_color(self.note.color or theme.ACCENT)
        is_selected = self.canvas_view.selected_node is self.note

        painter.setBrush(QBrush(theme.qcolor(theme.HOVER if (is_selected or self._hovered) else theme.RAISED, 245)))
        painter.setPen(QPen(theme.qcolor(theme.ACCENT) if is_selected else theme.qcolor(theme.HAIRLINE), 1.2))
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawRoundedRect(QRectF(rect.left() + 10, rect.top() + 10, 3, 14), 1.5, 1.5)

        title_font = theme.font(theme.FS_SMALL, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(theme.qcolor(theme.TEXT))
        title_rect = QRectF(rect.left() + 18, rect.top() + 8, rect.width() - 26, 18)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         elide(self.note.title, title_font, title_rect.width()))

        painter.setFont(theme.font(theme.FS_CAPTION))
        painter.setPen(theme.qcolor(theme.TEXT_2))
        body_rect = QRectF(rect.left() + 12, rect.top() + 32, rect.width() - 22, rect.height() - 38)
        snippet = (self.note.content or "").strip()
        if len(snippet) > 140:
            snippet = snippet[:138].rstrip() + "…"
        painter.drawText(body_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, snippet)

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
        menu = _canvas_menu(self.canvas_view)
        act_del = menu.addAction("Delete note")
        chosen = menu.exec(event.screenPos())
        if chosen == act_del:
            self.canvas_view.contextActionRequested.emit("delete_node", self.note)
        event.accept()
