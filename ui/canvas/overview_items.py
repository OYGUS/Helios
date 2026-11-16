"""All-projects overview: `GalaxySectorBannerItem` (source header), `GalaxyClusterItem`
(one project card) and the one-line project text snippets (`purpose_snippet`,
`suggestion_snippet`, `identity_snippet`) also used by the project core (sun) tooltip."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QBrush, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QStyleOptionGraphicsItem, QWidget

from core.models import SOURCE_CATEGORIES, SolarSystem
from connectors.codebase_importer import IDENTITY_TOOLTIP, stored_identity
from connectors.purpose_suggestion import pending_suggestion
from ui import theme
from ui.inspector import wrap_lines
from ui.canvas.helpers import SEARCH_DIM_OPACITY, _canvas_menu, elide

if TYPE_CHECKING:  # annotations only: a runtime import would be an import cycle
    from ui.canvas.view import SpaceCanvasView


# ----------------------------------------------------------------------------
# All-projects overview
# ----------------------------------------------------------------------------
class GalaxySectorBannerItem(QGraphicsItem):
    """Quiet section header above the project cards of one source."""

    def __init__(self, source_key: str, bounds_rect: QRectF, project_count: int) -> None:
        super().__init__()
        self.source_key = source_key
        self.cat_info = SOURCE_CATEGORIES.get(source_key, SOURCE_CATEGORIES["manual"])
        self.project_count = project_count
        self.box_rect = bounds_rect  # header row rect
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(1)

    def boundingRect(self) -> QRectF:
        return self.box_rect.adjusted(-4, -4, 4, 4)

    def shape(self) -> QPainterPath:
        return QPainterPath()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.box_rect
        color = theme.soft_color(str(self.cat_info["color"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(rect.left() + 5, rect.center().y()), 4, 4)
        f = theme.font(theme.FS_HEADING, QFont.Weight.DemiBold)
        painter.setFont(f)
        painter.setPen(theme.qcolor(theme.TEXT_2))
        title = str(self.cat_info["title"])
        painter.drawText(QRectF(rect.left() + 16, rect.top(), rect.width() - 16, rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        tw = QFontMetricsF(f).horizontalAdvance(title)
        painter.setFont(theme.font(theme.FS_SMALL))
        painter.setPen(theme.qcolor(theme.MUTED))
        painter.drawText(QRectF(rect.left() + 24 + tw, rect.top(), 120, rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(self.project_count))


def purpose_snippet(system: SolarSystem) -> str:
    """What the project is for (``SolarSystem.purpose``), as one line; "" when not written yet.

    Never falls back to README bodies or generated overview text.
    """
    return " ".join(str(getattr(system, "purpose", "") or "").split())


def suggestion_snippet(system: SolarSystem, ai_available: bool = False) -> str:
    """A pending AI purpose suggestion (``SolarSystem.purpose_suggestion``) as one line, or
    "" (always "" without Ollama, see ``pending_suggestion``). Never shown in the place or
    style of the purpose."""
    try:
        return pending_suggestion(system, ai_available=ai_available)
    except Exception:
        return ""


def identity_snippet(system: SolarSystem) -> str:
    """What the project IS (``SolarSystem.identity``, or the value derived from the stored
    analysis facts), as one line; "" when nothing is known."""
    try:
        return " ".join(stored_identity(system).split())
    except Exception:
        return ""


class GalaxyClusterItem(QGraphicsObject):
    """One calm project card in the all-projects overview."""

    CARD_W = 292.0
    CARD_H = 128.0 + theme.IDENTITY_CARD_LINE_H + 2.0
    NO_PURPOSE_TEXT = "No description yet"

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
        self.refresh_texts()

    def refresh_texts(self) -> None:
        self._purpose_snippet = purpose_snippet(self.system)
        ai_available = bool(getattr(self.canvas_view, "ai_available", False))
        self._suggestion_snippet = (suggestion_snippet(self.system, ai_available)
                                    if not self._purpose_snippet else "")
        self._identity_snippet = identity_snippet(self.system)
        identity = f"\n{self._identity_snippet} ({IDENTITY_TOOLTIP.lower()})" if self._identity_snippet else ""
        suggested = (f"\nSuggested (not accepted yet): {self._suggestion_snippet}"
                     if self._suggestion_snippet else "")
        self.setToolTip(f"{self.system.project_name or self.system.name}\n"
                        f"{self._purpose_snippet or self.NO_PURPOSE_TEXT + '.'}{suggested}{identity}\n\nClick to open")
        self.update()

    def suggestion_text(self) -> str:
        """The muted "Suggested: …" line drawn when there is no purpose, or ""."""
        if not self._suggestion_snippet:
            return ""
        font = theme.font(theme.FS_SMALL)
        font.setItalic(True)
        return elide(f"Suggested: {self._suggestion_snippet}", font, self.CARD_W - 32.0)

    def description_lines(self) -> List[str]:
        """The (at most two) purpose lines drawn on the card."""
        if not self._purpose_snippet:
            return []
        width = self.CARD_W - 32.0
        return wrap_lines(self._purpose_snippet, theme.font(theme.FS_SMALL), width, 2)

    def identity_text(self) -> str:
        """The identity line drawn on the card (elided to the card width), or ""."""
        if not self._identity_snippet:
            return ""
        return elide(self._identity_snippet, theme.font(theme.IDENTITY_FS), self.CARD_W - 32.0)

    def _extract_purpose_snippet(self) -> str:  # kept for compatibility
        return purpose_snippet(self.system)

    def cardRect(self) -> QRectF:
        return QRectF(-self.CARD_W / 2.0, -self.CARD_H / 2.0, self.CARD_W, self.CARD_H)

    def boundingRect(self) -> QRectF:
        return self.cardRect().adjusted(-4, -4, 4, 4)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(self.cardRect(), 10, 10)
        return path

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: Optional[QWidget] = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(SEARCH_DIM_OPACITY if self.is_dimmed else 1.0)
        rect = self.cardRect()
        is_active = self.canvas_view.active_system is self.system

        painter.setBrush(QBrush(theme.qcolor(theme.HOVER if self._hovered else theme.RAISED)))
        if is_active:
            painter.setPen(QPen(theme.qcolor(theme.ACCENT, 150), 1.2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 10, 10)

        pad = 16.0
        name_font = theme.font(theme.FS_HEADING, QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(theme.qcolor(theme.TEXT))
        name_rect = QRectF(rect.left() + pad, rect.top() + 12, rect.width() - 2 * pad, 22)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         elide(self.system.project_name or self.system.name, name_font, name_rect.width()))

        # Source chips: a dot + short name for every source of the project.
        chip_font = theme.font(theme.FS_CAPTION)
        fm = QFontMetricsF(chip_font)
        x = rect.left() + pad
        y = rect.top() + 38
        sources = list(self.system.sources or [self.system.source])
        for src in sources:
            meta = SOURCE_CATEGORIES.get(src, SOURCE_CATEGORIES["manual"])
            txt = str(meta.get("short", src))
            w = fm.horizontalAdvance(txt) + 22
            if x + w > rect.right() - pad - 30 and src is not sources[-1]:
                rest = len(sources) - sources.index(src)
                painter.setFont(chip_font)
                painter.setPen(theme.qcolor(theme.MUTED))
                painter.drawText(QRectF(x, y, 30, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"+{rest}")
                break
            chip = QRectF(x, y, w, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(theme.qcolor(theme.BG, 160)))
            painter.drawRoundedRect(chip, 9, 9)
            painter.setBrush(QBrush(theme.soft_color(str(meta["color"]))))
            painter.drawEllipse(QPointF(chip.left() + 9, chip.center().y()), 3, 3)
            painter.setFont(chip_font)
            painter.setPen(theme.qcolor(theme.TEXT_2))
            painter.drawText(QRectF(chip.left() + 16, chip.top(), w - 18, 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
            x += w + 6

        body_font = theme.font(theme.FS_SMALL)
        painter.setFont(body_font)
        lines = self.description_lines()
        line_h = 17.0
        if lines:
            painter.setPen(theme.qcolor(theme.TEXT_2))
            for i, ln in enumerate(lines):
                painter.drawText(QRectF(rect.left() + pad, rect.top() + 62 + i * line_h, rect.width() - 2 * pad, line_h),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, ln)
        elif self._suggestion_snippet:
            # Not the purpose: one muted italic line with a "Suggested:" prefix.
            italic = theme.font(theme.FS_SMALL)
            italic.setItalic(True)
            painter.setFont(italic)
            painter.setPen(theme.qcolor(theme.SUGGESTION_CARD_COLOR))
            painter.drawText(QRectF(rect.left() + pad, rect.top() + 62, rect.width() - 2 * pad, line_h),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.suggestion_text())

        # Identity line (derived from the code analysis): a fixed slot under the two purpose lines.
        identity = self.identity_text()
        if identity:
            painter.setFont(theme.font(theme.IDENTITY_FS))
            painter.setPen(theme.qcolor(theme.IDENTITY_COLOR))
            painter.drawText(QRectF(rect.left() + pad, rect.top() + 62 + 2 * line_h + 2, rect.width() - 2 * pad,
                                    theme.IDENTITY_CARD_LINE_H),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, identity)

        p_count = len(self.system.planets)
        l_count = len(self.system.links)
        meta_txt = f"{p_count} module{'s' if p_count != 1 else ''} · {l_count} link{'s' if l_count != 1 else ''}"
        painter.setFont(theme.font(theme.FS_CAPTION))
        painter.setPen(theme.qcolor(theme.MUTED))
        painter.drawText(QRectF(rect.left() + pad, rect.bottom() - 28, rect.width() - 2 * pad, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, meta_txt)
        if self._hovered:
            painter.setPen(theme.qcolor(theme.ACCENT_TEXT))
            painter.drawText(QRectF(rect.left() + pad, rect.bottom() - 28, rect.width() - 2 * pad, 18),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "Open →")

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
        menu = _canvas_menu(self.canvas_view)
        act_enter = menu.addAction("Open project")
        act_ollama = menu.addAction("Enhance with Ollama") if getattr(self.canvas_view, "ai_available", False) else None
        act_open_dir = menu.addAction("Open project folder")
        act_open_dir.setEnabled(bool(self.system.workspace_path))
        act_copy = menu.addAction("Copy as AI prompt")
        menu.addSeparator()
        act_remove = menu.addAction("Remove project…")
        chosen = menu.exec(event.screenPos())
        if chosen == act_remove:
            self.canvas_view.contextActionRequested.emit("remove_project", self.system)
        elif chosen == act_enter:
            target_system = self.system
            QTimer.singleShot(0, lambda s=target_system: self.canvas_view.enter_solar_system(s))
        elif act_ollama is not None and chosen == act_ollama:
            self.canvas_view.contextActionRequested.emit("ollama_enhance", self.system)
        elif chosen == act_open_dir:
            self.canvas_view.contextActionRequested.emit("open_path", self.system)
        elif chosen == act_copy:
            self.canvas_view.contextActionRequested.emit("copy_prompt", self.system)
        event.accept()
