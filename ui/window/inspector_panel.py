"""`InspectorMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

The inspector body: construction, rendering the selected project / module /
component / link / note, the read-only AI description block and the edit
section's state (open, dirty, cancel).

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _edit_dirty, _selected_node, _selected_type, _store_busy, _updating_ui,
    ai_summary_box, btn_cancel_edit, btn_clear_ai_summary, btn_delete_node,
    btn_edit_toggle, btn_link_planet, btn_open_file, btn_quick_delete, btn_save_node,
    canvas, combo_meta, detail_card, edit_content, edit_section, edit_title,
    inspector_scroll, inspector_type_badge, lbl_ai_summary, lbl_ai_summary_header,
    lbl_content_header, lbl_meta, lbl_title_field, meta_row_widget, sections_layout,
    sections_widget, sel_chips_layout, sel_chips_widget, sel_subtitle, sel_title,
    status_label, store
Of those, assigned by this mixin:
    _edit_dirty, _selected_node, _selected_type, _updating_ui, ai_summary_box,
    btn_cancel_edit, btn_clear_ai_summary, btn_delete_node, btn_edit_toggle,
    btn_link_planet, btn_open_file, btn_quick_delete, btn_save_node, combo_meta,
    detail_card, edit_content, edit_section, edit_title, inspector_scroll,
    inspector_type_badge, lbl_ai_summary, lbl_ai_summary_header, lbl_content_header,
    lbl_meta, lbl_title_field, meta_row_widget, sections_layout, sections_widget,
    sel_chips_layout, sel_chips_widget, sel_subtitle, sel_title
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.models import (
    CanvasNote,
    LAYER_DEFINITIONS,
    PlanetLink,
    PlanetNode,
    RELATION_TYPES,
    SatelliteNode,
    SolarSystem,
    get_source_meta,
)
from ui import theme
from ui.inspector import (
    ExpandableText,
    KeyValueGrid,
    PlainRow,
    RowButton,
    chip,
    clear_layout,
    section_label,
    strip_repeated_text,
)
from ui.space_canvas import planet_caption, resolve_component_ref
from ui.window.constants import PLANET_COLOR_PRESETS, SATELLITE_TAG_PRESETS


class InspectorMixin:
    """The inspector body: rendering the selection and the edit section's state."""

    def _build_inspector_body(self, side_layout: QVBoxLayout) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        self.inspector_scroll = scroll
        body = QWidget()
        body.setObjectName("InspectorBody")
        self.detail_card = body
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(18, 14, 18, 18)
        body_lay.setSpacing(10)

        # Selection header: kind chips, title, subtitle, actions
        self.sel_chips_widget = QWidget()
        self.sel_chips_widget.setObjectName("Transparent")
        self.sel_chips_layout = QHBoxLayout(self.sel_chips_widget)
        self.sel_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.sel_chips_layout.setSpacing(6)
        body_lay.addWidget(self.sel_chips_widget)
        self.inspector_type_badge = self.sel_chips_widget  # compatibility alias

        self.sel_title = theme.label("", "heading", wrap=True)
        self.sel_title.setTextFormat(Qt.TextFormat.PlainText)
        self.sel_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_lay.addWidget(self.sel_title)
        self.sel_subtitle = theme.label("", "caption", wrap=True)
        self.sel_subtitle.setTextFormat(Qt.TextFormat.PlainText)
        body_lay.addWidget(self.sel_subtitle)

        actions = QHBoxLayout()
        actions.setContentsMargins(-6, 0, 0, 0)
        actions.setSpacing(2)
        self.btn_open_file = QPushButton("Open file")
        self.btn_open_file.setObjectName("QuietBtn")
        self.btn_open_file.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_file.setVisible(False)
        self.btn_open_file.clicked.connect(lambda: self._open_node_path(self._selected_node))
        actions.addWidget(self.btn_open_file)
        self.btn_link_planet = QPushButton("Add link")
        self.btn_link_planet.setObjectName("QuietBtn")
        self.btn_link_planet.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_link_planet.setToolTip("Link this module to another one")
        self.btn_link_planet.setVisible(False)
        self.btn_link_planet.clicked.connect(self._prompt_add_link_from_selected)
        actions.addWidget(self.btn_link_planet)
        self.btn_edit_toggle = QPushButton("Edit")
        self.btn_edit_toggle.setObjectName("QuietBtn")
        self.btn_edit_toggle.setCheckable(True)
        self.btn_edit_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_toggle.setToolTip("Edit the name, colour and description")
        self.btn_edit_toggle.toggled.connect(self._set_edit_section_open)
        self.btn_edit_toggle.setVisible(False)
        actions.addWidget(self.btn_edit_toggle)
        self.btn_quick_delete = QPushButton("Delete")
        self.btn_quick_delete.setObjectName("DangerBtn")
        self.btn_quick_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quick_delete.setVisible(False)
        self.btn_quick_delete.clicked.connect(self._delete_current_node)
        actions.addWidget(self.btn_quick_delete)
        actions.addStretch(1)
        body_lay.addLayout(actions)

        # Read-only AI description of a module (PlanetNode.ai_summary), kept apart from the
        # editable summary; never saved into `summary`.
        self.ai_summary_box = QFrame()
        self.ai_summary_box.setObjectName("AiSummaryBox")
        ai_box_layout = QVBoxLayout(self.ai_summary_box)
        ai_box_layout.setContentsMargins(12, 8, 8, 10)
        ai_box_layout.setSpacing(4)
        ai_head_row = QHBoxLayout()
        self.lbl_ai_summary_header = theme.label("AI description (Ollama — may be inaccurate)", "caption")
        ai_head_row.addWidget(self.lbl_ai_summary_header, stretch=1)
        self.btn_clear_ai_summary = QPushButton("Clear")
        self.btn_clear_ai_summary.setObjectName("QuietBtn")
        self.btn_clear_ai_summary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_ai_summary.setToolTip("Remove the AI-written description of this module")
        self.btn_clear_ai_summary.clicked.connect(self._clear_selected_ai_summary)
        ai_head_row.addWidget(self.btn_clear_ai_summary)
        ai_box_layout.addLayout(ai_head_row)
        self.lbl_ai_summary = theme.label("", "body", wrap=True)
        self.lbl_ai_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_ai_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_ai_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ai_box_layout.addWidget(self.lbl_ai_summary)
        self.ai_summary_box.setVisible(False)
        body_lay.addWidget(self.ai_summary_box)

        # Selection-dependent sections (About, Uses, Used by, Contains, Calls …)
        self.sections_widget = QWidget()
        self.sections_widget.setObjectName("Transparent")
        self.sections_layout = QVBoxLayout(self.sections_widget)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(4)
        body_lay.addWidget(self.sections_widget)

        # Edit section (collapsed by default)
        self.edit_section = QFrame()
        self.edit_section.setObjectName("EditSection")
        ed = QVBoxLayout(self.edit_section)
        ed.setContentsMargins(14, 12, 14, 14)
        ed.setSpacing(6)
        ed.addWidget(theme.label("Edit", "section"))
        self.lbl_title_field = theme.label("Name", "caption")
        ed.addWidget(self.lbl_title_field)
        self.edit_title = QLineEdit()
        self.edit_title.setPlaceholderText("Name")
        self.edit_title.textEdited.connect(self._mark_edit_dirty)
        ed.addWidget(self.edit_title)
        self.meta_row_widget = QWidget()
        self.meta_row_widget.setObjectName("Transparent")
        meta_row_layout = QVBoxLayout(self.meta_row_widget)
        meta_row_layout.setContentsMargins(0, 4, 0, 0)
        meta_row_layout.setSpacing(6)
        self.lbl_meta = theme.label("Colour", "caption")
        self.combo_meta = QComboBox()
        self.combo_meta.activated.connect(self._mark_edit_dirty)
        meta_row_layout.addWidget(self.lbl_meta)
        meta_row_layout.addWidget(self.combo_meta)
        ed.addWidget(self.meta_row_widget)
        self.lbl_content_header = theme.label("Description", "caption")
        self.lbl_content_header.setContentsMargins(0, 4, 0, 0)
        ed.addWidget(self.lbl_content_header)
        self.edit_content = QTextEdit()
        self.edit_content.setAcceptRichText(False)
        self.edit_content.setMinimumHeight(170)
        self.edit_content.setPlaceholderText("What it does and how it works…")
        self.edit_content.textChanged.connect(self._mark_edit_dirty)
        ed.addWidget(self.edit_content)
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.btn_save_node = QPushButton("Save")
        self.btn_save_node.setObjectName("SecondaryBtn")
        self.btn_save_node.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_node.clicked.connect(self._save_current_node)
        action_row.addWidget(self.btn_save_node)
        self.btn_cancel_edit = QPushButton("Cancel")
        self.btn_cancel_edit.setObjectName("QuietBtn")
        self.btn_cancel_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel_edit.clicked.connect(self._cancel_edit)
        action_row.addWidget(self.btn_cancel_edit)
        action_row.addStretch(1)
        self.btn_delete_node = QPushButton("Delete")
        self.btn_delete_node.setObjectName("DangerBtn")
        self.btn_delete_node.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete_node.clicked.connect(self._delete_current_node)
        action_row.addWidget(self.btn_delete_node)
        ed.addLayout(action_row)
        self.edit_section.setVisible(False)
        body_lay.addWidget(self.edit_section)
        body_lay.addStretch(1)

        scroll.setWidget(body)
        side_layout.addWidget(scroll, stretch=1)

    def _clear_inspector(self) -> None:
        """Empty inspector (no project left)."""
        self._updating_ui = True
        try:
            self._selected_node = None
            self._selected_type = "sun"
            clear_layout(self.sel_chips_layout)
            clear_layout(self.sections_layout)
            self.sel_chips_widget.setVisible(False)
            self.sel_title.setVisible(False)
            self.sel_subtitle.setVisible(False)
            for btn in (self.btn_open_file, self.btn_link_planet, self.btn_edit_toggle, self.btn_quick_delete):
                btn.setVisible(False)
            self.edit_section.setVisible(False)
            self.ai_summary_box.setVisible(False)
            self.sections_layout.addWidget(theme.label(
                "There are no projects yet. Use Add project to analyze a folder, or Sync to read your AI tools.",
                "muted", wrap=True))
        finally:
            self._updating_ui = False

    def _refresh_ai_summary_block(self, planet: Optional[object]) -> None:
        """Shows the read-only AI description block for a planet that has one, else hides it."""
        text = ""
        if isinstance(planet, PlanetNode):
            text = str(getattr(planet, "ai_summary", "") or "").strip()
        self.lbl_ai_summary.setText(text)
        self.ai_summary_box.setVisible(bool(text))

    def _clear_selected_ai_summary(self) -> None:
        planet = self._selected_node
        if not isinstance(planet, PlanetNode) or not str(planet.ai_summary or "").strip():
            return
        if self._store_busy:
            return
        self.store.push_undo()
        planet.ai_summary = ""  # does not touch summary or is_user_modified
        self._updating_ui = True
        try:
            self.store.save()
        finally:
            self._updating_ui = False
        self._refresh_ai_summary_block(planet)
        self.status_label.setText(f"AI description of '{planet.title}' cleared.")

    def _load_node_into_inspector(self, node_obj: object, node_type: str) -> None:
        """Shows the selection in the inspector (read view) and fills the hidden edit fields."""
        self._updating_ui = True
        try:
            self._selected_node = node_obj
            self._selected_type = node_type
            self.combo_meta.clear()
            self.btn_link_planet.setVisible(False)
            self.btn_open_file.setVisible(False)
            self.btn_quick_delete.setVisible(False)
            self.btn_edit_toggle.setVisible(True)
            self.btn_edit_toggle.setChecked(False)
            self.edit_section.setVisible(False)
            clear_layout(self.sel_chips_layout)
            clear_layout(self.sections_layout)
            self.sel_title.setVisible(True)
            self.sel_subtitle.setVisible(False)
            self._refresh_ai_summary_block(node_obj if node_type == "planet" else None)
            system = self.canvas.active_system

            if node_type == "sun" and isinstance(node_obj, SolarSystem):
                self._render_project(node_obj)
            elif node_type == "planet" and isinstance(node_obj, PlanetNode):
                self._render_module(node_obj, system)
            elif node_type == "satellite" and isinstance(node_obj, SatelliteNode):
                self._render_component(node_obj, system)
            elif node_type == "link" and isinstance(node_obj, PlanetLink):
                self._render_link(node_obj, system)
            elif node_type == "note" and isinstance(node_obj, CanvasNote):
                self._render_note(node_obj)
            self.sel_chips_layout.addStretch(1)
            self.sel_chips_widget.setVisible(self.sel_chips_layout.count() > 1)
            self._set_save_primary(False)
            self.inspector_scroll.verticalScrollBar().setValue(0)
        finally:
            self._updating_ui = False

    def _add_section(self, title: str, widgets: List[QWidget]) -> None:
        if not widgets:
            return
        self.sections_layout.addWidget(section_label(title))
        for w in widgets:
            self.sections_layout.addWidget(w)
        self.sections_layout.addSpacing(8)

    def _module_rows(self, planets: List[PlanetNode], limit: int = 80) -> List[QWidget]:
        rows: List[QWidget] = []
        for p in planets[:limit]:
            rows.append(RowButton(p.title, planet_caption(p), "●",
                                  on_click=lambda checked=False, t=p: self._select_and_center(t)))
        if len(planets) > limit:
            rows.append(theme.label(f"…and {len(planets) - limit} more", "muted"))
        return rows

    def _select_and_center(self, planet: PlanetNode) -> None:
        if self.canvas.view_mode != "system" and self.canvas.active_system:
            self.canvas.enter_solar_system(self.canvas.active_system)
        self.canvas.select_object(planet, "planet")
        self.canvas.center_on_node(planet)

    def _select_component(self, sat: SatelliteNode) -> None:
        if self.canvas.view_mode != "system" and self.canvas.active_system:
            self.canvas.enter_solar_system(self.canvas.active_system)
        self.canvas.select_object(sat, "satellite")
        parent = self.canvas.active_system.find_parent_planet(sat) if self.canvas.active_system else None
        if parent is not None:
            self.canvas.center_on_node(parent)

    def _render_project(self, system: SolarSystem) -> None:
        self.sel_title.setVisible(False)
        self.btn_open_file.setVisible(False)
        self.btn_edit_toggle.setText("Edit project")
        self.lbl_title_field.setText("Project name")
        self.edit_title.setText(system.project_name or system.name)
        self.lbl_content_header.setText("Overview")
        self.edit_content.setPlainText(system.core_plan)
        self.meta_row_widget.setVisible(False)
        self.btn_delete_node.setText("Remove project…")
        self.btn_delete_node.setEnabled(True)

        purpose = str(getattr(system, "purpose", "") or "").strip()
        # The purpose is shown once, in the pinned header above; it is not repeated here.
        about = strip_repeated_text(system.core_plan or "", purpose) if purpose else (system.core_plan or "")
        if about.strip():
            self._add_section("About", [ExpandableText(about, max_lines=8)])
        elif not purpose:
            self._add_section("About", [theme.label("No overview yet. Use Edit project to write one.", "muted", wrap=True)])
        sources = ", ".join(str(get_source_meta(s).get("title", s)) for s in (system.sources or [system.source]))
        n_comp = sum(len(p.satellites) for p in system.planets)
        facts = [("Sources", sources), ("Modules", str(len(system.planets))), ("Components", str(n_comp)),
                 ("Links", str(len(system.links)))]
        if system.workspace_path:
            facts.insert(1, ("Folder", system.workspace_path))
        if system.updated_at:
            facts.append(("Updated", str(system.updated_at).replace("T", " ")[:16]))
        self._add_section("Key facts", [KeyValueGrid(facts)])
        entries = [p for p in system.planets if bool(getattr(p, "is_entry", False))]
        if entries:
            self._add_section("Program starts in", self._module_rows(entries))
        main = [p for p in system.planets if p.kind not in ("history", "external")]
        quiet = [p for p in system.planets if p.kind in ("history", "external")]
        self._add_section(f"Modules ({len(main)})", self._module_rows(main))
        if quiet:
            self._add_section("Also in this project", self._module_rows(quiet))

    def _render_module(self, planet: PlanetNode, system: Optional[SolarSystem]) -> None:
        kind = str(getattr(planet, "kind", "") or "")
        self.sel_chips_layout.addWidget(chip(theme.PLANET_KIND_LABELS.get(kind, "Module")))
        layer_label = LAYER_DEFINITIONS.get(planet.layer, ("", ""))[0]
        if layer_label and kind not in ("history", "external"):
            self.sel_chips_layout.addWidget(chip(layer_label.split("(")[0].strip()))
        if bool(getattr(planet, "is_entry", False)):
            self.sel_chips_layout.addWidget(chip("Program starts here", "accent"))
        self.sel_title.setText(planet.title)
        sub = planet_caption(planet)
        if planet.file_path:
            sub = f"{sub} · {planet.file_path}" if sub else planet.file_path
        self.sel_subtitle.setText(sub)
        self.sel_subtitle.setVisible(bool(sub))
        if planet.file_path:
            self.btn_open_file.setText("Open folder" if kind == "dir" else "Open file")
            self.btn_open_file.setVisible(True)
        self.btn_link_planet.setVisible(True)
        self.btn_edit_toggle.setText("Edit")

        self.lbl_title_field.setText("Module name")
        self.edit_title.setText(planet.title)
        self.lbl_content_header.setText("Description")
        self.edit_content.setPlainText(planet.summary)
        self.meta_row_widget.setVisible(True)
        self.lbl_meta.setText("Colour")
        for label, hex_code in PLANET_COLOR_PRESETS:
            self.combo_meta.addItem(self._dot_icon(hex_code), label, hex_code)
        idx = self.combo_meta.findData(planet.color)
        if idx >= 0:
            self.combo_meta.setCurrentIndex(idx)
        self.btn_delete_node.setText("Delete module")
        self.btn_delete_node.setEnabled(True)

        if (planet.summary or "").strip():
            self._add_section("About", [ExpandableText(planet.summary, max_lines=8)])
        if system is not None:
            p_map = {p.id: p for p in system.planets}
            uses = [(p_map[l.target_id], l) for l in system.links if l.source_id == planet.id and l.target_id in p_map]
            used_by = [(p_map[l.source_id], l) for l in system.links if l.target_id == planet.id and l.source_id in p_map]
            self._add_section(f"Uses ({len(uses)})", [
                RowButton(t.title, l.label, "→", on_click=lambda checked=False, x=t: self._select_and_center(x))
                for t, l in uses
            ])
            self._add_section(f"Used by ({len(used_by)})", [
                RowButton(t.title, l.label, "←", on_click=lambda checked=False, x=t: self._select_and_center(x))
                for t, l in used_by
            ])
        sats = planet.satellites
        rows: List[QWidget] = []
        for sat in sats[:120]:
            sk = str(getattr(sat, "kind", "") or "")
            sig = str(getattr(sat, "signature", "") or "").strip()
            rows.append(RowButton(
                sat.title,
                sig or theme.SATELLITE_KIND_LABELS.get(sk, ""),
                theme.SATELLITE_KIND_GLYPHS.get(sk, "·"),
                on_click=lambda checked=False, x=sat: self._select_component(x),
                mono_secondary=bool(sig),
            ))
        if len(sats) > 120:
            rows.append(theme.label(f"…and {len(sats) - 120} more", "muted"))
        self._add_section(f"Contains ({len(sats)})", rows)

    def _ref_rows(self, refs: List[str], system: Optional[SolarSystem], glyph: str) -> List[QWidget]:
        rows: List[QWidget] = []
        for ref in refs:
            ref = str(ref)
            planet, sat = resolve_component_ref(system, ref)
            p_title, _, s_title = ref.partition("::")
            if sat is not None:
                rows.append(RowButton(sat.title, planet.title if planet else "", glyph,
                                      on_click=lambda checked=False, x=sat: self._select_component(x)))
            else:
                rows.append(PlainRow(s_title or ref, f"{p_title} · not on the map" if s_title else "not on the map",
                                     glyph, tooltip=f"{ref}\nThis component is not part of the map."))
        return rows

    def _render_component(self, sat: SatelliteNode, system: Optional[SolarSystem]) -> None:
        sk = str(getattr(sat, "kind", "") or "")
        self.sel_chips_layout.addWidget(chip(theme.SATELLITE_KIND_LABELS.get(sk, "Component")))
        parent = system.find_parent_planet(sat) if system else None
        if parent is not None:
            btn = QPushButton(f"in {parent.title}")
            btn.setObjectName("ChipBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.setToolTip("Show the module this component belongs to")
            btn.clicked.connect(lambda checked=False, x=parent: self._select_and_center(x))
            self.sel_chips_layout.addWidget(btn)
        self.sel_title.setText(sat.title)
        self.btn_edit_toggle.setText("Edit")

        self.lbl_title_field.setText("Component name")
        self.edit_title.setText(sat.title)
        self.lbl_content_header.setText("Description")
        self.edit_content.setPlainText(sat.content)
        self.meta_row_widget.setVisible(True)
        self.lbl_meta.setText("Type")
        for label, tag_code in SATELLITE_TAG_PRESETS:
            self.combo_meta.addItem(label, tag_code)
        idx = self.combo_meta.findData(sat.visual_tag)
        if idx >= 0:
            self.combo_meta.setCurrentIndex(idx)
        self.btn_delete_node.setText("Delete component")
        self.btn_delete_node.setEnabled(True)

        sig = str(getattr(sat, "signature", "") or "").strip()
        if sig:
            lbl = theme.label(sig, "mono", wrap=True)
            lbl.setTextFormat(Qt.TextFormat.PlainText)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.sections_layout.addWidget(lbl)
        if sat.file_path:
            line = int(getattr(sat, "line", 0) or 0)
            where = Path(sat.file_path).name + (f":{line}" if line else "")
            row = QWidget()
            row.setObjectName("Transparent")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(6)
            lbl = theme.label(f"Defined in {where}", "caption")
            lbl.setToolTip(sat.file_path)
            rl.addWidget(lbl)
            btn_open = QPushButton("Open")
            btn_open.setObjectName("LinkBtn")
            btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_open.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn_open.clicked.connect(lambda checked=False, x=sat: self._open_node_path(x))
            rl.addWidget(btn_open)
            rl.addStretch(1)
            self.sections_layout.addWidget(row)
            self.sections_layout.addSpacing(6)
        if (sat.content or "").strip():
            self._add_section("About", [ExpandableText(sat.content, max_lines=8)])
        calls = list(getattr(sat, "calls", []) or [])
        called_by = list(getattr(sat, "called_by", []) or [])
        self._add_section(f"Calls ({len(calls)})", self._ref_rows(calls, system, "→"))
        self._add_section(f"Called by ({len(called_by)})", self._ref_rows(called_by, system, "←"))

    def _render_link(self, link: PlanetLink, system: Optional[SolarSystem]) -> None:
        p_map = {p.id: p for p in system.planets} if system else {}
        src = p_map.get(link.source_planet_id)
        dst = p_map.get(link.target_planet_id)
        rel_label = RELATION_TYPES.get(link.relation_type, ("Link", ""))[0].split("(")[0].strip()
        self.sel_chips_layout.addWidget(chip("Link"))
        self.sel_chips_layout.addWidget(chip(rel_label))
        self.sel_title.setText(f"{src.title if src else '?'} → {dst.title if dst else '?'}")
        self.sel_subtitle.setText(link.label or "")
        self.sel_subtitle.setVisible(bool(link.label))
        self.btn_quick_delete.setText("Delete link")
        self.btn_quick_delete.setVisible(True)
        self.btn_edit_toggle.setText("Edit")

        self.lbl_title_field.setText("Label")
        self.edit_title.setText(link.label)
        self.lbl_content_header.setText("Notes on this link")
        self.edit_content.setPlainText(link.description or "")
        self.meta_row_widget.setVisible(True)
        self.lbl_meta.setText("Link type")
        for r_key, (r_label, _) in RELATION_TYPES.items():
            self.combo_meta.addItem(r_label, r_key)
        idx = self.combo_meta.findData(link.relation_type)
        if idx >= 0:
            self.combo_meta.setCurrentIndex(idx)
        self.btn_delete_node.setText("Delete link")
        self.btn_delete_node.setEnabled(True)

        rows: List[QWidget] = []
        if src is not None:
            rows.append(RowButton(src.title, "from", "●", on_click=lambda checked=False, x=src: self._select_and_center(x)))
        if dst is not None:
            rows.append(RowButton(dst.title, "to", "●", on_click=lambda checked=False, x=dst: self._select_and_center(x)))
        self._add_section("Modules", rows)
        if (link.description or "").strip():
            self._add_section("Notes", [ExpandableText(link.description, max_lines=8)])

    def _render_note(self, note: CanvasNote) -> None:
        self.sel_chips_layout.addWidget(chip("Note"))
        self.sel_title.setText(note.title)
        self.btn_quick_delete.setText("Delete note")
        self.btn_quick_delete.setVisible(True)
        self.btn_edit_toggle.setText("Edit")
        self.lbl_title_field.setText("Title")
        self.edit_title.setText(note.title)
        self.lbl_content_header.setText("Text")
        self.edit_content.setPlainText(note.content)
        self.meta_row_widget.setVisible(True)
        self.lbl_meta.setText("Colour")
        for label, hex_code in PLANET_COLOR_PRESETS:
            self.combo_meta.addItem(self._dot_icon(hex_code), label, hex_code)
        idx = self.combo_meta.findData(note.color)
        if idx >= 0:
            self.combo_meta.setCurrentIndex(idx)
        self.btn_delete_node.setText("Delete note")
        self.btn_delete_node.setEnabled(True)
        if (note.content or "").strip():
            self._add_section("Text", [ExpandableText(note.content, max_lines=12)])

    def _set_edit_section_open(self, open_: bool) -> None:
        self.edit_section.setVisible(bool(open_))
        if open_:
            QTimer.singleShot(0, lambda: self.inspector_scroll.ensureWidgetVisible(self.edit_section, 0, 40))
            self.edit_title.setFocus()

    def _mark_edit_dirty(self, *args) -> None:
        if self._updating_ui:
            return
        self._set_save_primary(True)

    def _set_save_primary(self, dirty: bool) -> None:
        self._edit_dirty = dirty
        name = "PrimaryBtn" if dirty else "SecondaryBtn"
        if self.btn_save_node.objectName() != name:
            self.btn_save_node.setObjectName(name)
            theme.repolish(self.btn_save_node)
        self.btn_save_node.setText("Save changes" if dirty else "Save")

    def _cancel_edit(self) -> None:
        if self._selected_node is not None:
            self._load_node_into_inspector(self._selected_node, self._selected_type)
