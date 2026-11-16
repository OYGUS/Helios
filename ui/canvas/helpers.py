"""Canvas constants and drawing / animation helpers shared by the graphics items and the view.

Tunables (`MAX_VISIBLE_MOONS`, `MOON_ROW`, label widths, dim opacities, drag thresholds),
`TAG_BADGE_MAP`, the pop-in / scale animations, `elide`, `planet_caption`,
`resolve_component_ref`, the tool-strip icons (`make_tool_icon`) and the link arrow /
label-chip painters. Imports nothing else from `ui.canvas`.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsObject, QGraphicsView, QMenu, QWidget

from core.models import PlanetNode, SatelliteNode, SolarSystem
from ui import theme


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
