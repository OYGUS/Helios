"""Helios interactive architecture canvas (QGraphicsView & QGraphicsScene).

A calm solar system with progressive disclosure:

- The project is the sun, modules are planets on orbits, components are moons and links
  are thin arrows between planets.
- By default only the sun, the planets (title + small caption) and low-contrast links are
  drawn. A planet shows its moons (with labels laid out on a fan facing away from the sun)
  when it is selected or hovered, or always when "Show all details" is on.
- Selecting a planet highlights its links (with labels) and dims everything unrelated;
  selecting a moon also highlights the planets that own the components it calls / is
  called by.
- All-projects overview: one calm card per project, grouped by source under quiet headers.
- Drawing tools live in a compact vertical icon strip docked at the left edge.

Colours come from :mod:`ui.theme`; stored model colours are only mapped to softened display
colours, never rewritten. Planet positions are computed for display (evenly spread, history /
external modules on the outermost orbit) unless the user moved a planet by hand
(``is_user_modified``), in which case the stored angle / radius are used.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
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
    QToolButton,
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
from connectors.codebase_importer import IDENTITY_TOOLTIP, stored_identity
from connectors.purpose_suggestion import pending_suggestion
from ui import theme
from ui.inspector import wrap_lines

# Display names for the hand-made component tags (stored values stay unchanged).
TAG_BADGE_MAP: Dict[str, Tuple[str, str]] = {
    "idea": ("Idea", "#F59E0B"),
    "code": ("Code", "#10B981"),
    "diagram": ("Diagram", "#3B82F6"),
    "thought": ("Note", "#A855F7"),
}

SHOW_ALL_DETAILS_KEY = "canvas_show_all_details"
MAX_VISIBLE_MOONS = 12
MOON_ROW = 20.0
MOON_LABEL_W = 150.0
PLANET_LABEL_W = 168.0
QUIET_KINDS = ("history", "external")
DIM_OPACITY = 0.35
# A press becomes a drag after this many screen px; it is saved (is_user_modified) only if
# the item really ended up this far (view px) from where it started.
DRAG_START_PX = 6
DRAG_COMMIT_PX = 4.0
SEARCH_DIM_OPACITY = 0.22

_PLURALS = {
    "function": "functions",
    "class": "classes",
    "method": "methods",
    "constants": "constants",
    "file": "files",
    "package": "packages",
    "session": "sessions",
    "document": "documents",
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


def _view_distance_px(view: QGraphicsView, a: QPointF, b: QPointF) -> float:
    """Distance between two item-local/scene points in view pixels (current zoom)."""
    scale = abs(view.transform().m11()) or 1.0
    return math.hypot(b.x() - a.x(), b.y() - a.y()) * scale


def elide(text: str, font: QFont, width: float) -> str:
    return QFontMetricsF(font).elidedText(text or "", Qt.TextElideMode.ElideRight, width)


def planet_caption(planet: PlanetNode) -> str:
    """Small caption under a planet title, e.g. "8 functions" or "5 files"."""
    sats = planet.satellites
    n = len(sats)
    if n == 0:
        return ""
    kinds = {str(getattr(s, "kind", "") or "") for s in sats}
    if len(kinds) == 1:
        k = next(iter(kinds))
        if k in _PLURALS:
            word = k if (n == 1 and k != "constants") else _PLURALS[k]
            return f"{n} {word}"
    return f"{n} component" if n == 1 else f"{n} components"


def resolve_component_ref(system: Optional[SolarSystem], ref: str) -> Tuple[Optional[PlanetNode], Optional[SatelliteNode]]:
    """Resolves a ``"<module title>::<component title>"`` reference inside a project."""
    if system is None or not ref or "::" not in ref:
        return None, None
    p_title, s_title = ref.split("::", 1)
    p_title, s_title = p_title.strip(), s_title.strip()
    planet = next((p for p in system.planets if p.title == p_title), None)
    if planet is None:
        planet = next((p for p in system.planets if p.title.lower() == p_title.lower()), None)
    if planet is None:
        return None, None
    sat = next((s for s in planet.satellites if s.title == s_title), None)
    if sat is None:
        sat = next((s for s in planet.satellites if s.title.lower() == s_title.lower()), None)
    return planet, sat


def _canvas_menu(parent: Optional[QWidget]) -> QMenu:
    """Context menus are parented to the canvas so they inherit the application theme."""
    return QMenu(parent)


# ----------------------------------------------------------------------------
# Tool icons (drawn with QPainter: no image assets, crisp on HiDPI)
# ----------------------------------------------------------------------------
def _draw_tool_glyph(p: QPainter, kind: str, color: QColor, s: float) -> None:
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    c = s / 2.0
    if kind == "select":
        poly = QPolygonF([QPointF(s * 0.28, s * 0.18), QPointF(s * 0.28, s * 0.8), QPointF(s * 0.44, s * 0.64),
                          QPointF(s * 0.56, s * 0.86), QPointF(s * 0.65, s * 0.81), QPointF(s * 0.53, s * 0.6),
                          QPointF(s * 0.74, s * 0.58)])
        p.drawPolygon(poly)
    elif kind == "add_planet":
        p.drawEllipse(QPointF(c, c), s * 0.32, s * 0.32)
        p.drawLine(QPointF(c - s * 0.13, c), QPointF(c + s * 0.13, c))
        p.drawLine(QPointF(c, c - s * 0.13), QPointF(c, c + s * 0.13))
    elif kind == "add_satellite":
        p.drawEllipse(QPointF(s * 0.42, s * 0.58), s * 0.22, s * 0.22)
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(s * 0.76, s * 0.26), s * 0.09, s * 0.09)
    elif kind == "draw_link":
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(s * 0.22, s * 0.78), s * 0.08, s * 0.08)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(s * 0.28, s * 0.72), QPointF(s * 0.76, s * 0.24))
        p.drawLine(QPointF(s * 0.76, s * 0.24), QPointF(s * 0.52, s * 0.25))
        p.drawLine(QPointF(s * 0.76, s * 0.24), QPointF(s * 0.75, s * 0.48))
    elif kind == "add_note":
        p.drawRoundedRect(QRectF(s * 0.2, s * 0.2, s * 0.6, s * 0.6), 2.5, 2.5)
        p.drawLine(QPointF(s * 0.33, s * 0.4), QPointF(s * 0.67, s * 0.4))
        p.drawLine(QPointF(s * 0.33, s * 0.56), QPointF(s * 0.58, s * 0.56))
    elif kind == "eraser":
        p.save()
        p.translate(c, c)
        p.rotate(-40)
        p.drawRoundedRect(QRectF(-s * 0.3, -s * 0.15, s * 0.6, s * 0.3), 2.5, 2.5)
        p.drawLine(QPointF(-s * 0.05, -s * 0.15), QPointF(-s * 0.05, s * 0.15))
        p.restore()
    elif kind == "tour":
        p.drawEllipse(QPointF(c, c), s * 0.32, s * 0.32)
        p.setBrush(QBrush(color))
        p.drawPolygon(QPolygonF([QPointF(s * 0.43, s * 0.36), QPointF(s * 0.43, s * 0.64), QPointF(s * 0.66, s * 0.5)]))
    elif kind == "align":
        p.drawEllipse(QPointF(c, c), s * 0.3, s * 0.3)
        p.setBrush(QBrush(color))
        for ang in (-90, 30, 150):
            r = math.radians(ang)
            p.drawEllipse(QPointF(c + math.cos(r) * s * 0.3, c + math.sin(r) * s * 0.3), s * 0.07, s * 0.07)


def make_tool_icon(kind: str, size: int = 20) -> QIcon:
    icon = QIcon()
    for state, col in ((QIcon.State.Off, theme.TEXT_2), (QIcon.State.On, theme.ACCENT_TEXT)):
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _draw_tool_glyph(p, kind, QColor(col), float(size))
        p.end()
        icon.addPixmap(pm, QIcon.Mode.Normal, state)
        icon.addPixmap(pm, QIcon.Mode.Active, state)
    return icon


def _arrow_head(end: QPointF, ctrl: QPointF, size: float) -> QPolygonF:
    tx = end.x() - ctrl.x()
    ty = end.y() - ctrl.y()
    t_len = math.hypot(tx, ty) or 1.0
    tx /= t_len
    ty /= t_len
    left_pt = QPointF(end.x() - tx * size - ty * (size * 0.5), end.y() - ty * size + tx * (size * 0.5))
    right_pt = QPointF(end.x() - tx * size + ty * (size * 0.5), end.y() - ty * size - tx * (size * 0.5))
    return QPolygonF([end, left_pt, right_pt])


def _draw_chip(painter: QPainter, center: QPointF, text: str, *, accent: bool = False, max_w: float = 180.0) -> QRectF:
    """Draws a small rounded label chip centred at ``center``; returns its rect."""
    f = theme.font(theme.FS_CAPTION, QFont.Weight.Medium)
    txt = elide(text, f, max_w)
    w = QFontMetricsF(f).horizontalAdvance(txt) + 14.0
    rect = QRectF(center.x() - w / 2.0, center.y() - 9.0, w, 18.0)
    painter.setPen(QPen(theme.qcolor(theme.ACCENT, 90) if accent else theme.qcolor(theme.HAIRLINE), 1.0))
    painter.setBrush(QBrush(theme.qcolor(theme.RAISED, 240)))
    painter.drawRoundedRect(rect, 9.0, 9.0)
    painter.setFont(f)
    painter.setPen(theme.qcolor(theme.ACCENT_TEXT if accent else theme.TEXT))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)
    return rect


def chip_rect_for(center: QPointF, text: str, max_w: float = 180.0) -> QRectF:
    f = theme.font(theme.FS_CAPTION, QFont.Weight.Medium)
    txt = elide(text, f, max_w)
    w = QFontMetricsF(f).horizontalAdvance(txt) + 14.0
    return QRectF(center.x() - w / 2.0, center.y() - 9.0, w, 18.0)


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
        self.setZValue(4)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_reverse_flag()
        self._refresh_tooltip()

    def refresh_reverse_flag(self) -> None:
        self._has_reverse = False
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
        p1 = self.source_item.pos()
        p2 = self.target_item.pos()
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
        self._refresh_tooltip()

    def _refresh_tooltip(self) -> None:
        kind = theme.SATELLITE_KIND_LABELS.get(str(getattr(self.satellite, "kind", "") or ""), "Component")
        tip = f"{self.satellite.title}\n{kind}"
        sig = str(getattr(self.satellite, "signature", "") or "").strip()
        if sig:
            tip += f"\n{sig}"
        self.setToolTip(tip)

    def display_title(self) -> str:
        return elide(self.satellite.title, self._font, self.label_width)

    def _label_rect(self) -> QRectF:
        w = min(self.label_width, QFontMetricsF(self._font).horizontalAdvance(self.display_title()) + 2)
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

    def text(self) -> str:
        return f"+{self.count} more"

    def _rect(self) -> QRectF:
        w = QFontMetricsF(theme.font(theme.FS_CAPTION, QFont.Weight.Medium)).horizontalAdvance(self.text()) + 14
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

    def display_title(self) -> str:
        return elide(self.planet.title, self._title_font, PLANET_LABEL_W)

    def title_rect(self) -> QRectF:
        w = QFontMetricsF(self._title_font).horizontalAdvance(self.display_title()) + 4
        h_total = 18.0 + (15.0 if self._caption else 0.0)
        if self.label_above:
            top = -self.radius - 8 - h_total - self.label_dy
        else:
            top = self.radius + 7 + self.label_dy
        return QRectF(-w / 2.0, top, w, 18.0)

    def caption_rect(self) -> QRectF:
        tr = self.title_rect()
        w = QFontMetricsF(self._caption_font).horizontalAdvance(self._caption) + 4
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
        chip_w = QFontMetricsF(theme.font(theme.FS_CAPTION, QFont.Weight.Medium)).horizontalAdvance("+999 more") + 18
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
        elif chosen == act_tour:
            self.canvas_view.start_or_advance_tour()
        elif chosen == act_rebalance:
            self.canvas_view.contextActionRequested.emit("rebalance", self.system)
        elif chosen == act_galaxy:
            self.canvas_view.switch_to_galaxy_view()
        event.accept()


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


def suggestion_snippet(system: SolarSystem) -> str:
    """A pending AI purpose suggestion (``SolarSystem.purpose_suggestion``) as one line, or
    "". Never shown in the place or style of the purpose."""
    try:
        return pending_suggestion(system)
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
        self._suggestion_snippet = suggestion_snippet(self.system) if not self._purpose_snippet else ""
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


# ----------------------------------------------------------------------------
# Empty-state overlay
# ----------------------------------------------------------------------------
class EmptyStateOverlay(QFrame):
    """Short friendly hint with the primary action, shown instead of a blank canvas."""

    def __init__(self, canvas: "SpaceCanvasView") -> None:
        super().__init__(canvas)
        self.setObjectName("EmptyState")
        self.canvas = canvas
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(8)
        self.lbl_title = theme.label("", "heading")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_text = theme.label("", "secondary", wrap=True)
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_text)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.btn_primary = QPushButton("")
        self.btn_primary.setObjectName("PrimaryBtn")
        self.btn_primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_secondary = QPushButton("")
        self.btn_secondary.setObjectName("SecondaryBtn")
        self.btn_secondary.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.btn_secondary)
        row.addWidget(self.btn_primary)
        row.addStretch(1)
        lay.addSpacing(6)
        lay.addLayout(row)
        self.btn_primary.clicked.connect(lambda: self._run(self._primary_action))
        self.btn_secondary.clicked.connect(lambda: self._run(self._secondary_action))
        self._primary_action = ""
        self._secondary_action = ""
        self.mode = ""
        self.setFixedWidth(420)
        self.hide()

    def _run(self, action: str) -> None:
        if action == "add_module_tool":
            self.canvas.set_drawing_tool("add_planet")
        elif action:
            self.canvas.contextActionRequested.emit(action, None)

    def show_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "no_projects":
            self.lbl_title.setText("No projects yet")
            self.lbl_text.setText("Pick a folder with code and Helios draws its map. "
                                  "You can also sketch a project by hand.")
            self.btn_primary.setText("Analyze a folder…")
            self._primary_action = "analyze_folder"
            self.btn_secondary.setText("Design a project…")
            self._secondary_action = "create_new_project"
        else:
            self.lbl_title.setText("This project has no modules yet")
            self.lbl_text.setText("Add a module by hand, or analyze a folder to build the map from code.")
            self.btn_primary.setText("Add a module")
            self._primary_action = "add_module_tool"
            self.btn_secondary.setText("Analyze a folder…")
            self._secondary_action = "analyze_folder"
        self.adjustSize()
        self.show()
        self.raise_()


# ----------------------------------------------------------------------------
# The view
# ----------------------------------------------------------------------------
TOOL_SPECS = [
    ("select", "Select and move", "1", "Select items, drag to move them, drag empty space to pan"),
    ("add_planet", "Add module", "2", "Click on the canvas to place a new module"),
    ("add_satellite", "Add component", "3", "Click near a module to add a component to it"),
    ("draw_link", "Draw link", "4", "Click a module, then another, to link them"),
    ("add_note", "Add note", "5", "Click on the canvas to place a note"),
    ("eraser", "Eraser", "6", "Click a module, component, link or note to delete it"),
]


class SpaceCanvasView(QGraphicsView):
    """Zoomable, pannable interactive architecture canvas."""

    nodeSelected = Signal(object, str)  # (node_obj, "sun" | "planet" | "satellite" | "link" | "note")
    viewModeChanged = Signal(str)  # "system" | "galaxy"
    tourStepChanged = Signal(int, int, str)  # (current_idx, total, step_title)
    contextActionRequested = Signal(str, object)  # (action_name, target_obj)
    drawingToolChanged = Signal(str, str)  # (tool_key, status_hint)
    showAllDetailsChanged = Signal(bool)

    IDLE_TICK_MS = 1000
    MOTION_TICK_MS = 33

    def __init__(self, store: GalaxyStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.view_mode: str = "system"
        self.active_system: Optional[SolarSystem] = self.store.systems[0] if self.store.systems else None
        self.selected_node: Optional[object] = self.active_system

        self.scene_obj = QGraphicsScene(self)
        self.scene_obj.setSceneRect(-3200, -3200, 6400, 6400)
        self.setScene(self.scene_obj)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setBackgroundBrush(QBrush(theme.qcolor(theme.CANVAS_BG)))
        # Set by the main window: Ollama is enabled and a model was found. Without it the
        # context menus show no AI entries.
        self.ai_available = False

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        # Only exposed regions are repainted (no full-viewport repaint per update).
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

        self.orbit_motion_enabled = False
        self.show_all_details = bool(self.store.get_setting(SHOW_ALL_DETAILS_KEY, False))
        self._auto_fit = False
        self._press_on_empty = None
        self._current_scale = 1.0
        self.search_query: str = ""
        self.tour_index: int = -1

        self.drawing_tool: str = "select"
        self.drawing_layer: str = "core"
        self.drawing_sat_tag: str = "code"
        self.drawing_relation_type: str = "flows_to"
        self.drawing_note_color: str = "#F59E0B"

        self.is_drawing_link: bool = False
        self._link_source_item: Optional[PlanetGraphicsItem] = None
        self._draft_cursor_pos: Optional[QPointF] = None
        self._tool_buttons: Dict[str, QToolButton] = {}
        self._suboption_buttons: List[QPushButton] = []

        self.sun_item: Optional[SunGraphicsItem] = None
        self.planet_items: List[PlanetGraphicsItem] = []
        self.planet_item_map: Dict[str, PlanetGraphicsItem] = {}
        self.link_items: List[ConstellationLinkItem] = []
        self.note_items: List[CanvasNoteGraphicsItem] = []
        self.galaxy_items: List[GalaxyClusterItem] = []
        self.sector_banners: List[GalaxySectorBannerItem] = []
        self.hovered_galaxy_system: Optional[SolarSystem] = None
        self.hovered_planet_item: Optional[PlanetGraphicsItem] = None
        self._search_revealed: Set[str] = set()

        self._hover_hide_timer = QTimer(self)
        self._hover_hide_timer.setSingleShot(True)
        self._hover_hide_timer.setInterval(260)
        self._hover_hide_timer.timeout.connect(self._on_hover_hide_timeout)

        self._setup_drawing_toolbar()
        self.empty_overlay = EmptyStateOverlay(self)

        # Stays active for the busy-state freezer; idles at 1 Hz while motion is off.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_animation_tick)
        self.timer.start(self.IDLE_TICK_MS)

        self.rebuild_scene()

    # ------------------------------------------------------------------
    # Docked tool strip (left edge) + contextual option bar
    # ------------------------------------------------------------------
    def _setup_drawing_toolbar(self) -> None:
        self.drawing_toolbar = QFrame(self)
        self.drawing_toolbar.setObjectName("ToolStrip")
        tb_layout = QVBoxLayout(self.drawing_toolbar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(2)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        for t_key, t_name, shortcut_key, tip in TOOL_SPECS:
            btn = QToolButton()
            btn.setObjectName("ToolBtn")
            btn.setIcon(make_tool_icon(t_key))
            btn.setIconSize(QSize(20, 20))
            btn.setCheckable(True)
            btn.setChecked(t_key == "select")
            btn.setFixedSize(34, 34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{t_name}  ({shortcut_key})\n{tip}")
            btn.setAccessibleName(t_name)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.clicked.connect(lambda checked=False, k=t_key: self.set_drawing_tool(k))
            self._tool_group.addButton(btn)
            tb_layout.addWidget(btn)
            self._tool_buttons[t_key] = btn

        sep = QFrame()
        sep.setObjectName("Hairline")
        tb_layout.addSpacing(2)
        tb_layout.addWidget(sep)
        tb_layout.addSpacing(2)
        self.btn_strip_tour = QToolButton()
        self.btn_strip_tour.setObjectName("ToolBtn")
        self.btn_strip_tour.setIcon(make_tool_icon("tour"))
        self.btn_strip_tour.setIconSize(QSize(20, 20))
        self.btn_strip_tour.setFixedSize(34, 34)
        self.btn_strip_tour.setToolTip("Guided tour\nStep through the modules, from the project outward")
        self.btn_strip_tour.setAccessibleName("Guided tour")
        self.btn_strip_tour.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_strip_tour.clicked.connect(self.start_or_advance_tour)
        tb_layout.addWidget(self.btn_strip_tour)
        self.btn_strip_align = QToolButton()
        self.btn_strip_align.setObjectName("ToolBtn")
        self.btn_strip_align.setIcon(make_tool_icon("align"))
        self.btn_strip_align.setIconSize(QSize(20, 20))
        self.btn_strip_align.setFixedSize(34, 34)
        self.btn_strip_align.setToolTip("Auto-align layout\nPut moved modules back on evenly spaced orbits")
        self.btn_strip_align.setAccessibleName("Auto-align layout")
        self.btn_strip_align.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_strip_align.clicked.connect(lambda: self.contextActionRequested.emit("rebalance", None))
        tb_layout.addWidget(self.btn_strip_align)

        self.drawing_subbar = QFrame(self)
        self.drawing_subbar.setObjectName("DrawingSubBar")
        self.subbar_layout = QHBoxLayout(self.drawing_subbar)
        self.subbar_layout.setContentsMargins(10, 4, 6, 4)
        self.subbar_layout.setSpacing(4)
        self.drawing_subbar.setVisible(False)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        if hasattr(self, "drawing_toolbar"):
            self.drawing_toolbar.adjustSize()
            self.drawing_toolbar.move(10, 10)
        if hasattr(self, "drawing_subbar"):
            self.drawing_subbar.adjustSize()
            self.drawing_subbar.move(58, 10)
        if hasattr(self, "empty_overlay") and self.empty_overlay.isVisible():
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

    def set_drawing_tool(self, tool_key: str) -> None:
        """Switches the active drawing tool and updates the contextual option bar."""
        if self.view_mode != "system" and tool_key != "select":
            if self.active_system:
                self.enter_solar_system(self.active_system)

        self.drawing_tool = tool_key
        self.is_drawing_link = False
        self._link_source_item = None

        for k, btn in self._tool_buttons.items():
            btn.setChecked(k == tool_key)

        if tool_key == "select":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

        self._rebuild_subbar_options()
        self._update_empty_overlay()
        self.scene_obj.update()

        hints = {
            "select": "Select and move · Shift+drag from a module to draw a link",
            "add_planet": "Add module · choose a layer above, then click on the canvas · Esc to stop",
            "add_satellite": "Add component · click near a module · Esc to stop",
            "draw_link": "Draw link · click the first module, then the second (or drag) · Esc to stop",
            "add_note": "Add note · click on the canvas · Esc to stop",
            "eraser": "Eraser · click a module, component, link or note to delete it · Esc to stop",
        }
        self.drawingToolChanged.emit(tool_key, hints.get(tool_key, ""))

    def _rebuild_subbar_options(self) -> None:
        while self.subbar_layout.count():
            child = self.subbar_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._suboption_buttons = []

        if self.view_mode != "system" or self.drawing_tool == "select":
            self.drawing_subbar.setVisible(False)
            return

        def add_option(text: str, active: bool, slot) -> None:
            btn = QPushButton(text)
            btn.setObjectName("SubOptBtn")
            btn.setCheckable(True)
            btn.setChecked(active)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.clicked.connect(slot)
            self.subbar_layout.addWidget(btn)
            self._suboption_buttons.append(btn)

        def add_label(text: str) -> None:
            lbl = theme.label(text, "caption")
            self.subbar_layout.addWidget(lbl)
            self.subbar_layout.addSpacing(4)

        if self.drawing_tool == "add_planet":
            add_label("Layer")
            for l_key, l_name in (("ui", "UI"), ("core", "Core / data"), ("integration", "API / integration"),
                                  ("architecture", "Architecture"), ("testing", "Testing")):
                add_option(l_name, self.drawing_layer == l_key, lambda checked=False, k=l_key: self._set_drawing_layer(k))
        elif self.drawing_tool == "add_satellite":
            add_label("Type")
            for t_key, (t_badge, _) in TAG_BADGE_MAP.items():
                add_option(t_badge, self.drawing_sat_tag == t_key, lambda checked=False, k=t_key: self._set_drawing_sat_tag(k))
        elif self.drawing_tool == "draw_link":
            add_label("Link type")
            for r_key, (r_label, _) in RELATION_TYPES.items():
                add_option(r_label.split("(")[0].strip(), self.drawing_relation_type == r_key,
                           lambda checked=False, k=r_key: self._set_drawing_relation_type(k))
        elif self.drawing_tool == "add_note":
            add_label("Colour")
            for c_hex, c_name in (("#F59E0B", "Amber"), ("#3B82F6", "Blue"), ("#10B981", "Green"),
                                  ("#8B5CF6", "Purple"), ("#F43F5E", "Rose")):
                add_option(c_name, self.drawing_note_color == c_hex, lambda checked=False, c=c_hex: self._set_drawing_note_color(c))
        elif self.drawing_tool == "eraser":
            add_label("Click anything to delete it · Esc to stop")

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
    # Settings-backed toggles
    # ------------------------------------------------------------------
    def set_show_all_details(self, enabled: bool) -> None:
        """Shows every module's components all the time (persisted in settings)."""
        enabled = bool(enabled)
        changed = enabled != self.show_all_details
        self.show_all_details = enabled
        try:
            self.store.set_setting(SHOW_ALL_DETAILS_KEY, enabled)
        except Exception:
            pass
        self._refresh_moon_reveal(animate=False)
        self.scene_obj.update()
        if changed:
            self.showAllDetailsChanged.emit(enabled)

    def set_orbit_motion(self, enabled: bool) -> None:
        self.orbit_motion_enabled = bool(enabled)
        self.timer.setInterval(self.MOTION_TICK_MS if self.orbit_motion_enabled else self.IDLE_TICK_MS)

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

    # ------------------------------------------------------------------
    # Live drawing preview (ghost node & link arrow)
    # ------------------------------------------------------------------
    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self.view_mode != "system" or not self.active_system or self._draft_cursor_pos is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pt = self._draft_cursor_pos
        accent = theme.qcolor(theme.ACCENT)

        if self.drawing_tool == "add_planet":
            dist = max(125.0, min(950.0, math.hypot(pt.x(), pt.y())))
            ang = math.atan2(pt.y(), pt.x())
            snap_pt = QPointF(math.cos(ang) * dist, math.sin(ang) * dist)
            layer_title, color_hex = LAYER_DEFINITIONS.get(self.drawing_layer, ("Module", "#3B82F6"))
            c = theme.soft_color(color_hex)
            painter.setPen(QPen(theme.qcolor(theme.TEXT_2, 40), 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), dist, dist)
            painter.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 70)))
            painter.setPen(QPen(c, 1.5, Qt.PenStyle.DashLine))
            painter.drawEllipse(snap_pt, 22.0, 22.0)
            _draw_chip(painter, QPointF(snap_pt.x(), snap_pt.y() + 40), f"New module · {layer_title.split('(')[0].strip()}")

        elif self.drawing_tool == "add_satellite":
            nearest_p = self._find_nearest_planet_item(pt)
            if nearest_p is not None:
                p_pos = nearest_p.pos()
                dx = pt.x() - p_pos.x()
                dy = pt.y() - p_pos.y()
                dist = max(40.0, min(260.0, math.hypot(dx, dy)))
                ang = math.atan2(dy, dx)
                sat_pt = QPointF(p_pos.x() + math.cos(ang) * dist, p_pos.y() + math.sin(ang) * dist)
                painter.setPen(QPen(theme.qcolor(theme.TEXT_2, 90), 1.0, Qt.PenStyle.DashLine))
                painter.drawLine(p_pos, sat_pt)
                painter.setBrush(QBrush(theme.qcolor(theme.ACCENT, 90)))
                painter.setPen(QPen(accent, 1.4))
                painter.drawEllipse(sat_pt, 6.0, 6.0)
                _draw_chip(painter, QPointF(sat_pt.x(), sat_pt.y() + 20), f"New component in {nearest_p.planet.title}")

        elif self.is_drawing_link and self._link_source_item is not None:
            p1 = self._link_source_item.pos()
            hover_target = self._find_planet_item_at(pt)
            valid_target = hover_target is not None and hover_target is not self._link_source_item
            p2 = hover_target.pos() if valid_target else pt
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent, 1.6))
            painter.drawEllipse(p1, self._link_source_item.radius + 6, self._link_source_item.radius + 6)
            if valid_target:
                painter.setPen(QPen(theme.qcolor(theme.SUCCESS), 1.8))
                painter.drawEllipse(p2, hover_target.radius + 6, hover_target.radius + 6)
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            dist = math.hypot(dx, dy)
            if dist > 20:
                ux = dx / dist
                uy = dy / dist
                start = QPointF(p1.x() + ux * (self._link_source_item.radius + 5), p1.y() + uy * (self._link_source_item.radius + 5))
                end = QPointF(p2.x() - ux * (hover_target.radius + 6), p2.y() - uy * (hover_target.radius + 6)) if valid_target else p2
                mid = QPointF((start.x() + end.x()) * 0.5, (start.y() + end.y()) * 0.5)
                ctrl = QPointF(mid.x() - uy * min(24.0, dist * 0.08), mid.y() + ux * min(24.0, dist * 0.08))
                path = QPainterPath(start)
                path.quadTo(ctrl, end)
                painter.setPen(QPen(accent, 1.6, Qt.PenStyle.DashLine))
                painter.drawPath(path)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(accent))
                painter.drawPolygon(_arrow_head(end, ctrl, 8.0))
                rel_title, _ = RELATION_TYPES.get(self.drawing_relation_type, ("Data Flow", ""))
                _draw_chip(painter, path.pointAtPercent(0.5), rel_title.split("(")[0].strip(), accent=True)

        elif self.drawing_tool == "add_note":
            n_rect = QRectF(pt.x() - 107.5, pt.y() - 54.0, 215.0, 108.0)
            painter.setBrush(QBrush(theme.qcolor(theme.RAISED, 170)))
            painter.setPen(QPen(theme.soft_color(self.drawing_note_color), 1.4, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(n_rect, 8, 8)
            painter.setFont(theme.font(theme.FS_SMALL))
            painter.setPen(theme.qcolor(theme.TEXT_2))
            painter.drawText(n_rect, Qt.AlignmentFlag.AlignCenter, "Click to place a note")

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

            self.resolve_label_collisions()
            self._update_focus_state()
            if self.search_query:
                self._apply_search_filter(center=False)
        else:
            self._build_galaxy_cards()
            if self.search_query:
                self._apply_search_filter(center=False)
        self._update_empty_overlay()

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

    def _update_empty_overlay(self) -> None:
        if not hasattr(self, "empty_overlay"):
            return
        mode = ""
        if not self.store.systems:
            mode = "no_projects"
        elif self.view_mode == "system" and self.active_system is not None and not self.active_system.planets \
                and self.drawing_tool == "select":
            mode = "no_modules"
        if mode:
            self.empty_overlay.show_mode(mode)
            self._reposition_overlays()
        else:
            self.empty_overlay.hide()

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

    def fit_to_view(self) -> None:
        """Zooms and centres so the whole map (or all project cards) is visible."""
        rect = QRectF()
        if self.view_mode == "system":
            items: Sequence[QGraphicsItem] = [*self.planet_items, *self.note_items]
            if self.sun_item is not None:
                rect = self.sun_item.sceneBoundingRect()
            for it in items:
                rect = rect.united(it.sceneBoundingRect())
            for p_item in self.planet_items:
                if p_item.moons_revealed:
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
                top = min(self.planet_items, key=lambda it: it.pos().y())
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

    def center_on_node(self, node: object) -> None:
        if isinstance(node, PlanetNode):
            item = self.planet_item_map.get(node.id)
            if item is not None:
                self.centerOn(item.scenePos())

    # ------------------------------------------------------------------
    # Hover / focus / moon reveal
    # ------------------------------------------------------------------
    def selected_planet_id(self) -> Optional[str]:
        sel = self.selected_node
        if isinstance(sel, PlanetNode):
            return sel.id
        if isinstance(sel, SatelliteNode) and self.active_system:
            parent_p = self.active_system.find_parent_planet(sel)
            if parent_p:
                return parent_p.id
        return None

    def has_focus_selection(self) -> bool:
        return isinstance(self.selected_node, (PlanetNode, SatelliteNode, PlanetLink))

    def notify_planet_hovered(self, p_item: PlanetGraphicsItem, entered: bool) -> None:
        if entered:
            self._hover_hide_timer.stop()
            if self.hovered_planet_item is not p_item:
                self.hovered_planet_item = p_item
                self._refresh_moon_reveal(animate=self.orbit_motion_enabled)
                self.viewport().update()
        else:
            self._hover_hide_timer.start()

    def _on_hover_hide_timeout(self) -> None:
        if self.hovered_planet_item is not None:
            item = self.hovered_planet_item
            under_mouse = item.isUnderMouse() or any(s.isUnderMouse() for s in item.satellite_items if s.isVisible())
            if under_mouse:
                return
            self.hovered_planet_item = None
            self._refresh_moon_reveal(animate=False)
            self.viewport().update()

    def _refresh_moon_reveal(self, animate: bool = False) -> None:
        sel_pid = self.selected_planet_id()
        for p_item in self.planet_items:
            reveal = (
                self.show_all_details
                or p_item.planet.id == sel_pid
                or p_item is self.hovered_planet_item
                or p_item.planet.id in self._search_revealed
            )
            p_item.set_moons_revealed(reveal, animate=animate and reveal)

    def _update_focus_state(self) -> None:
        """Highlights the selection's links and dims unrelated modules and links."""
        sel = self.selected_node
        focus_pid = self.selected_planet_id()
        related_pids: Set[str] = set()
        related_sats: Set[int] = set()
        focus_link: Optional[PlanetLink] = sel if isinstance(sel, PlanetLink) else None

        if isinstance(sel, SatelliteNode) and self.active_system:
            for ref in list(getattr(sel, "calls", []) or []) + list(getattr(sel, "called_by", []) or []):
                planet, sat = resolve_component_ref(self.active_system, str(ref))
                if planet is not None:
                    related_pids.add(planet.id)
                if sat is not None:
                    related_sats.add(id(sat))

        keep: Set[str] = set()
        if focus_pid:
            keep.add(focus_pid)
            keep |= related_pids
            for l_item in self.link_items:
                if l_item.link.source_id == focus_pid:
                    keep.add(l_item.link.target_id)
                elif l_item.link.target_id == focus_pid:
                    keep.add(l_item.link.source_id)
        elif focus_link is not None:
            keep = {focus_link.source_id, focus_link.target_id}
        has_focus = bool(keep)

        for p_item in self.planet_items:
            pid = p_item.planet.id
            p_item.focus_dimmed = has_focus and pid not in keep
            p_item.is_related = pid in related_pids and pid != focus_pid
            p_item.refresh_opacity()
            for s_item in p_item.satellite_items:
                s_item.is_related = id(s_item.satellite) in related_sats
            p_item.update()

        for l_item in self.link_items:
            link = l_item.link
            if focus_pid:
                hl = link.source_id == focus_pid or link.target_id == focus_pid
            elif focus_link is not None:
                hl = link is focus_link
            else:
                hl = False
            l_item.is_highlighted = hl
            l_item.is_dimmed = has_focus and not hl
            l_item.prepareGeometryChange()
            l_item.update()

        self._refresh_moon_reveal(animate=self.orbit_motion_enabled)
        self._assign_link_label_positions()

    def _update_link_highlights(self) -> None:  # kept for compatibility
        self._update_focus_state()

    # ------------------------------------------------------------------
    # Interactive drawing & placement
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
            f"This module handles '{layer_title}' work in the {self.active_system.project_name} project.\n\n"
            "Edit its name and description in the panel on the right."
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

    def place_satellite_on_planet(self, planet_item: Optional[PlanetGraphicsItem], scene_pos: Optional[QPointF]) -> None:
        if not self.active_system:
            return
        target_p_item = planet_item or (self._find_nearest_planet_item(scene_pos) if scene_pos is not None else None)
        if target_p_item is None:
            return
        self.store.push_undo()
        p_node = target_p_item.planet
        badge_lbl, _ = TAG_BADGE_MAP.get(self.drawing_sat_tag, ("Component", "#10B981"))
        title = f"New {badge_lbl.lower()} item {len(p_node.satellites) + 1}"
        content = "Describe what this component does and how it works."
        if scene_pos is not None:
            dx = scene_pos.x() - target_p_item.pos().x()
            dy = scene_pos.y() - target_p_item.pos().y()
            sat = p_node.add_satellite_at_offset(dx, dy, title=title, content=content, visual_tag=self.drawing_sat_tag)
        else:
            sat = p_node.add_satellite(title=title, content=content, visual_tag=self.drawing_sat_tag)
            sat.is_user_modified = True
        self.store.save()
        self.rebuild_scene()
        new_sat_item: Optional[SatelliteGraphicsItem] = None
        p_item = self.planet_item_map.get(p_node.id)
        if p_item:
            for s_item in p_item.satellite_items:
                if s_item.satellite.id == sat.id:
                    new_sat_item = s_item
                    break
        self.select_object(sat, "satellite", new_sat_item)

    def place_note_at(self, scene_pos: QPointF) -> None:
        if not self.active_system:
            return
        self.store.push_undo()
        note = self.active_system.add_note(
            scene_pos.x(),
            scene_pos.y(),
            title=f"Note {len(self.active_system.notes) + 1}",
            content="Write rules, to-dos or decisions here.",
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
            if self.drawing_tool == "select" and self.itemAt(event.position().toPoint()) is None:
                self._press_on_empty = event.position().toPoint()

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
        if self.view_mode == "system" and event.button() == Qt.MouseButton.LeftButton and self.is_drawing_link:
            scene_pos = self.mapToScene(event.position().toPoint())
            self.finish_interactive_link_drag(scene_pos)
        press = getattr(self, "_press_on_empty", None)
        self._press_on_empty = None
        if (
            press is not None
            and event.button() == Qt.MouseButton.LeftButton
            and (event.position().toPoint() - press).manhattanLength() < 5
            and self.active_system is not None
            and self.has_focus_selection()
        ):
            # A click on empty space clears the focus (back to the project overview).
            self.select_object(self.active_system, "sun", self.sun_item)
        super().mouseReleaseEvent(event)

    def _on_animation_tick(self) -> None:
        if self.view_mode == "system" and self.orbit_motion_enabled:
            for p_item in self.planet_items:
                p_item.advance_orbit(1.0)
            for l_item in self.link_items:
                l_item.update_geometry()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def set_search_query(self, query: str) -> None:
        self.search_query = query.strip().lower()
        self._apply_search_filter()

    def _apply_search_filter(self, center: bool = True) -> None:
        q = self.search_query
        if self.view_mode == "system":
            self._search_revealed = set()
            if not q:
                for p_item in self.planet_items:
                    p_item.is_dimmed = False
                    p_item.refresh_opacity()
                    for s_item in p_item.satellite_items:
                        s_item.is_dimmed = False
                        s_item.setOpacity(1.0)
                    p_item.update()
                self._refresh_moon_reveal()
                return

            first_match_item: Optional[QGraphicsItem] = None
            for p_item in self.planet_items:
                p_match = q in p_item.planet.title.lower() or q in (p_item.planet.summary or "").lower()
                any_sat_match = False
                for s_item in p_item.satellite_items:
                    s_match = q in s_item.satellite.title.lower() or q in (s_item.satellite.content or "").lower()
                    s_item.is_dimmed = not (s_match or p_match)
                    s_item.setOpacity(0.3 if s_item.is_dimmed else 1.0)
                    if s_match:
                        any_sat_match = True
                        if first_match_item is None:
                            first_match_item = p_item
                if any_sat_match:
                    self._search_revealed.add(p_item.planet.id)
                p_item.is_dimmed = not (p_match or any_sat_match)
                p_item.refresh_opacity()
                p_item.update()
                if p_match and first_match_item is None:
                    first_match_item = p_item
            self._refresh_moon_reveal()
            if center and first_match_item is not None:
                self.centerOn(first_match_item.scenePos())
        else:
            for g_item in self.galaxy_items:
                if not q:
                    g_item.is_dimmed = False
                else:
                    sys_ = g_item.system
                    match = (
                        q in sys_.name.lower()
                        or q in sys_.project_name.lower()
                        or q in str(getattr(sys_, "purpose", "") or "").lower()
                        or q in str(getattr(g_item, "_identity_snippet", "") or "").lower()
                        or q in sys_.core_plan.lower()
                        or any(q in p.title.lower() for p in sys_.planets)
                    )
                    g_item.is_dimmed = not match
                g_item.update()

    # ------------------------------------------------------------------
    # Guided tour
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

        total = len(steps) + 1
        self.tour_index = (self.tour_index + 1) % total

        for p_item in self.planet_items:
            p_item.is_tour_active = False
            p_item.update()

        if self.tour_index == 0:
            self.fit_to_view()
            self.select_object(self.active_system, "sun", self.sun_item)
            self.tourStepChanged.emit(1, total, self.active_system.project_name or self.active_system.name)
        else:
            planet_id = steps[self.tour_index - 1]
            p_item = self.planet_item_map.get(planet_id)
            if p_item:
                p_item.is_tour_active = True
                p_item.update()
                self._auto_fit = False
                self.resetTransform()
                self.scale(1.1, 1.1)
                self._current_scale = 1.1
                self.centerOn(p_item.scenePos())
                self.select_object(p_item.planet, "planet", p_item)
                self.tourStepChanged.emit(self.tour_index + 1, total, p_item.planet.title)

    def stop_tour(self) -> None:
        self.tour_index = -1
        for p_item in self.planet_items:
            p_item.is_tour_active = False
            p_item.update()

    # ------------------------------------------------------------------
    # Selection and view mode transitions
    # ------------------------------------------------------------------
    def select_object(self, node_obj: object, node_type: str, graphics_item: Optional[QGraphicsItem] = None) -> None:
        self.selected_node = node_obj
        self._update_focus_state()
        self.scene_obj.update()
        self._ensure_selection_visible()
        self.nodeSelected.emit(node_obj, node_type)

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

    def contextMenuEvent(self, event) -> None:
        """Quick-add and view actions when right-clicking empty canvas space."""
        item = self.itemAt(event.pos())
        if item is not None and not isinstance(item, (GalaxySectorBannerItem,)):
            super().contextMenuEvent(event)
            return

        menu = _canvas_menu(self)
        scene_pt = self.mapToScene(event.pos())

        if self.view_mode == "system" and self.active_system:
            act_add_p = menu.addAction("Add module here…")
            act_add_n = menu.addAction("Add note here…")
            menu.addSeparator()
            act_fit = menu.addAction("Fit to view")
            act_details = menu.addAction("Show all details")
            act_details.setCheckable(True)
            act_details.setChecked(self.show_all_details)
            act_rebal = menu.addAction("Auto-align layout")
            act_tour = menu.addAction("Guided tour")
            act_ollama = menu.addAction("Enhance with Ollama") if self.ai_available else None
            menu.addSeparator()
            act_back = menu.addAction("All projects")

            chosen = menu.exec(event.globalPos())
            if chosen == act_add_p:
                title, ok = QInputDialog.getText(self, "Add module", "Module name:")
                if ok and title.strip():
                    self.store.push_undo()
                    p = self.active_system.add_planet_at_pos(
                        scene_pt.x(), scene_pt.y(), title=title.strip(), summary="New module."
                    )
                    self.store.save()
                    self.rebuild_scene()
                    self.select_object(p, "planet")
                    self.contextActionRequested.emit("refresh_tree", None)
            elif chosen == act_add_n:
                title, ok = QInputDialog.getText(self, "Add note", "Note title:", text="Note")
                if ok and title.strip():
                    self.store.push_undo()
                    n = self.active_system.add_note(
                        scene_pt.x(), scene_pt.y(), title=title.strip(), content="Write rules or notes here."
                    )
                    self.store.save()
                    self.rebuild_scene()
                    self.select_object(n, "note")
                    self.contextActionRequested.emit("refresh_tree", None)
            elif chosen == act_fit:
                self.fit_to_view()
            elif chosen == act_details:
                self.set_show_all_details(not self.show_all_details)
            elif chosen == act_rebal:
                self.contextActionRequested.emit("rebalance", None)
            elif chosen == act_tour:
                self.start_or_advance_tour()
            elif act_ollama is not None and chosen == act_ollama:
                self.contextActionRequested.emit("ollama_enhance", self.active_system)
            elif chosen == act_back:
                self.switch_to_galaxy_view()
        else:
            act_analyze = menu.addAction("Analyze a folder…")
            act_new = menu.addAction("Design a new project…")
            act_scan = menu.addAction("Scan the 'projects/' folder")
            act_merge = menu.addAction("Merge duplicate projects…")
            menu.addSeparator()
            act_sett = menu.addAction("Settings…")

            chosen = menu.exec(event.globalPos())
            if chosen == act_analyze:
                self.contextActionRequested.emit("analyze_folder", None)
            elif chosen == act_new:
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
        if key in (Qt.Key.Key_Home, Qt.Key.Key_R, Qt.Key.Key_F):
            self.fit_to_view()
            event.accept()
            return
        super().keyPressEvent(event)
