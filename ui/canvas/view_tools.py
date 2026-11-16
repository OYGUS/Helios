"""Drawing tools of `SpaceCanvasView` (`DrawingToolsMixin`) and `TOOL_SPECS`.

The docked tool strip and its contextual option bar, the live drawing preview
(`drawForeground`) and the actions that add modules, components, links and notes.
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QPushButton,
    QToolButton,
    QVBoxLayout,
)

from core.models import LAYER_DEFINITIONS, PlanetNode, RELATION_TYPES
from ui import theme
from ui.canvas.component_items import SatelliteGraphicsItem
from ui.canvas.helpers import TAG_BADGE_MAP, _arrow_head, _draw_chip, animate_item_pop_in, make_tool_icon
from ui.canvas.module_item import PlanetGraphicsItem
from ui.canvas.note_item import CanvasNoteGraphicsItem


TOOL_SPECS = [
    ("select", "Select and move", "1", "Select items, drag to move them, drag empty space to pan"),
    ("add_planet", "Add module", "2", "Click on the canvas to place a new module"),
    ("add_satellite", "Add component", "3", "Click near a module to add a component to it"),
    ("draw_link", "Draw link", "4", "Click a module, then another, to link them"),
    ("add_note", "Add note", "5", "Click on the canvas to place a note"),
    ("eraser", "Eraser", "6", "Click a module, component, link or note to delete it"),
]


class DrawingToolsMixin:
    """Tool strip, option bar, live drawing preview and add module / component / link / note.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

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
    # Interactive drawing & placement
    # ------------------------------------------------------------------
    def _find_planet_item_at(self, scene_pos: QPointF) -> Optional[PlanetGraphicsItem]:
        for item in self.scene_obj.items(scene_pos):
            if isinstance(item, PlanetGraphicsItem) and item.isVisible():
                return item
        for p_item in self.planet_items:
            if p_item.isVisible() and math.hypot(p_item.pos().x() - scene_pos.x(), p_item.pos().y() - scene_pos.y()) <= p_item.radius + 16:
                return p_item
        return None

    def _find_nearest_planet_item(self, scene_pos: QPointF) -> Optional[PlanetGraphicsItem]:
        best_item: Optional[PlanetGraphicsItem] = None
        best_d = 1e9
        for p_item in self.planet_items:
            if not p_item.isVisible():
                continue
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
