"""`SunGraphicsItem`: the project core drawn as the sun at the centre of the map."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.models import SolarSystem
from connectors.codebase_importer import IDENTITY_TOOLTIP
from ui import theme
from ui.canvas.helpers import _canvas_menu, elide
from ui.canvas.overview_items import identity_snippet

if TYPE_CHECKING:  # annotations only: a runtime import would be an import cycle
    from ui.canvas.view import SpaceCanvasView


# ----------------------------------------------------------------------------
# The sun (project)
# ----------------------------------------------------------------------------
class SunGraphicsItem(QGraphicsObject):
    """The project at the centre, drawn as a calm amber sun."""

    def __init__(self, system: SolarSystem, canvas_view: "SpaceCanvasView") -> None:
        super().__init__()
        self.system = system
        self.canvas_view = canvas_view
        self.radius = 34.0
        self._hovered = False
        self._press_screen_pos = QPoint()
        self._name_font = theme.font(theme.FS_HEADING, QFont.Weight.DemiBold)

        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(8)
        self.setPos(0, 0)
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        proj_name = self.system.project_name or self.system.name
        purpose = " ".join(str(getattr(self.system, "purpose", "") or "").split())
        identity = identity_snippet(self.system)
        self.setToolTip((f"{proj_name}\n{purpose}" if purpose else
                         f"{proj_name}\nProject — click to see its overview")
                        + (f"\n{identity} ({IDENTITY_TOOLTIP.lower()})" if identity else ""))

    def name_rect(self) -> QRectF:
        txt = self.display_name()
        w = QFontMetricsF(self._name_font).horizontalAdvance(txt) + 6
        return QRectF(-w / 2.0, self.radius + 10, w, 22)

    def display_name(self) -> str:
        return elide(self.system.project_name or self.system.name, self._name_font, 220)

    def boundingRect(self) -> QRectF:
        g = self.radius * 2.4
        return QRectF(-g, -g, 2 * g, 2 * g).united(self.name_rect().adjusted(-6, -4, 6, 4))

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.radius + 6, self.radius + 6)
        path.addRect(self.name_rect())
        return path

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        is_selected = self.canvas_view.selected_node is self.system
        r = self.radius
        glow = QRadialGradient(QPointF(0, 0), r * 2.4)
        glow.setColorAt(0.0, theme.qcolor(theme.ACCENT, 70 if (is_selected or self._hovered) else 50))
        glow.setColorAt(0.45, theme.qcolor(theme.ACCENT, 18))
        glow.setColorAt(1.0, theme.qcolor(theme.ACCENT, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(0, 0), r * 2.4, r * 2.4)

        core = QRadialGradient(QPointF(-r * 0.3, -r * 0.35), r * 1.4)
        core.setColorAt(0.0, theme.qcolor(theme.ACCENT_HOVER).lighter(108))
        core.setColorAt(1.0, theme.qcolor(theme.ACCENT_PRESSED).darker(118))
        painter.setBrush(QBrush(core))
        painter.drawEllipse(QPointF(0, 0), r, r)
        if is_selected:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT_TEXT, 200), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), r + 6, r + 6)

        nr = self.name_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(theme.qcolor(theme.CANVAS_BG, 190)))
        painter.drawRoundedRect(nr, 5, 5)
        painter.setFont(self._name_font)
        painter.setPen(theme.qcolor(theme.TEXT))
        painter.drawText(nr, Qt.AlignmentFlag.AlignCenter, self.display_name())

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
        menu = _canvas_menu(self.canvas_view)
        act_ollama = menu.addAction("Enhance with Ollama") if getattr(self.canvas_view, "ai_available", False) else None
        act_add_p = menu.addAction("Add module…")
        act_open_dir = menu.addAction("Open project folder")
        act_open_dir.setEnabled(bool(self.system.workspace_path))
        act_copy_prompt = menu.addAction("Copy as AI prompt")
        act_export_png = menu.addAction("Export map as image (.png)…")
        act_export_md = menu.addAction("Export map as document (.md)…")
        menu.addSeparator()
        act_tour = menu.addAction("Guided tour")
        act_rebalance = menu.addAction("Auto-align layout")
        menu.addSeparator()
        act_galaxy = menu.addAction("All projects (Esc)")
        act_remove = menu.addAction("Remove project…")
        chosen = menu.exec(event.screenPos())
        if chosen is None:
            event.accept()
            return
        if chosen == act_remove:
            self.canvas_view.contextActionRequested.emit("remove_project", self.system)
        if act_ollama is not None and chosen == act_ollama:
            self.canvas_view.contextActionRequested.emit("ollama_enhance", self.system)
        elif chosen == act_add_p:
            self.canvas_view.contextActionRequested.emit("add_planet", self.system)
        elif chosen == act_open_dir:
            self.canvas_view.contextActionRequested.emit("open_path", self.system)
        elif chosen == act_copy_prompt:
            self.canvas_view.contextActionRequested.emit("copy_prompt", self.system)
        elif chosen in (act_export_png, act_export_md):
            action = "export_png" if chosen == act_export_png else "export_md"
            self.canvas_view.contextActionRequested.emit(action, self.system)
        elif chosen == act_tour:
            self.canvas_view.start_or_advance_tour()
        elif chosen == act_rebalance:
            self.canvas_view.contextActionRequested.emit("rebalance", self.system)
        elif chosen == act_galaxy:
            self.canvas_view.switch_to_galaxy_view()
        event.accept()
