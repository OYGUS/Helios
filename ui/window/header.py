"""`ProjectHeaderMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

The pinned project header of the inspector: construction (title, purpose block,
identity line, chips, actions, tour strip), its refresh and the header's “…” menu.

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _evidence_viewer, _purpose_edit_target, btn_copy_prompt, btn_edit_toggle,
    btn_ollama_single, btn_open_workspace, btn_project_more, btn_purpose_add,
    btn_purpose_cancel, btn_purpose_save, btn_suggest_ai, btn_suggestion_accept,
    btn_suggestion_dismiss, btn_suggestion_edit, canvas, edit_purpose, lbl_identity,
    lbl_project_counts, lbl_purpose, lbl_purpose_source, lbl_suggestion,
    lbl_suggestion_tag, project_chips_layout, project_chips_widget, purpose_edit_box,
    purpose_empty, purpose_view, related_sources_layout, related_sources_widget, store,
    suggestion_box, sun_card, sun_title_display, tour_card, tour_status_label
Of those, assigned by this mixin:
    _evidence_viewer, _purpose_edit_target, btn_copy_prompt, btn_ollama_single,
    btn_open_workspace, btn_project_more, btn_purpose_add, btn_purpose_cancel,
    btn_purpose_save, btn_suggest_ai, btn_suggestion_accept, btn_suggestion_dismiss,
    btn_suggestion_edit, edit_purpose, lbl_identity, lbl_project_counts, lbl_purpose,
    lbl_purpose_source, lbl_suggestion, lbl_suggestion_tag, project_chips_layout,
    project_chips_widget, purpose_edit_box, purpose_empty, purpose_view,
    related_sources_layout, related_sources_widget, suggestion_box, sun_card,
    sun_title_display, tour_card, tour_status_label
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from connectors import codebase_importer as codebase_importer_module
from core.models import SolarSystem, get_source_meta
from ui import theme
from ui.inspector import ClampedLabel, ElidedLabel, PurposeEditor, chip, clear_layout


class ProjectHeaderMixin:
    """The pinned project header (construction, refresh, the “…” menu)."""

    def _build_project_header(self, side_layout: QVBoxLayout) -> None:
        self.sun_card = QFrame()
        self.sun_card.setObjectName("ProjectHeader")
        lay = QVBoxLayout(self.sun_card)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        self.sun_title_display = ElidedLabel("")
        theme.set_role(self.sun_title_display, "title")
        title_row.addWidget(self.sun_title_display, 1)
        self.btn_project_more = QPushButton("…")
        self.btn_project_more.setObjectName("QuietBtn")
        self.btn_project_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_project_more.setToolTip("More project actions")
        self.btn_project_more.setFixedWidth(34)
        self.btn_project_more.clicked.connect(self._show_project_more_menu)
        title_row.addWidget(self.btn_project_more, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(title_row)
        self._build_purpose_block(lay)
        self._build_identity_line(lay)

        self.project_chips_widget = QWidget()
        self.project_chips_widget.setObjectName("Transparent")
        self.project_chips_layout = QHBoxLayout(self.project_chips_widget)
        self.project_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.project_chips_layout.setSpacing(6)
        lay.addWidget(self.project_chips_widget)

        self.lbl_project_counts = theme.label("", "caption")
        lay.addWidget(self.lbl_project_counts)

        # Counterparts of the same project from other sources.
        self.related_sources_widget = QWidget()
        self.related_sources_widget.setObjectName("Transparent")
        self.related_sources_layout = QHBoxLayout(self.related_sources_widget)
        self.related_sources_layout.setContentsMargins(0, 0, 0, 0)
        self.related_sources_layout.setSpacing(6)
        self.related_sources_widget.setVisible(False)
        lay.addWidget(self.related_sources_widget)

        actions = QHBoxLayout()
        actions.setContentsMargins(-6, 2, 0, 0)
        actions.setSpacing(2)
        self.btn_ollama_single = QPushButton(self._ollama_button_label())
        self.btn_ollama_single.setObjectName("OllamaBtn")
        self.btn_ollama_single.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_ollama_button_tooltip()
        self.btn_ollama_single.clicked.connect(self._on_ollama_button_clicked)
        actions.addWidget(self.btn_ollama_single)
        self.btn_open_workspace = QPushButton("Open folder")
        self.btn_open_workspace.setObjectName("QuietBtn")
        self.btn_open_workspace.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_workspace.setToolTip("Open the project's folder")
        self.btn_open_workspace.clicked.connect(lambda: self._open_node_path(self.canvas.active_system))
        actions.addWidget(self.btn_open_workspace)
        self.btn_copy_prompt = QPushButton("Copy prompt")
        self.btn_copy_prompt.setObjectName("QuietBtn")
        self.btn_copy_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_prompt.setToolTip("Copy the project overview, modules and links as a prompt for an AI assistant")
        self.btn_copy_prompt.clicked.connect(lambda: self._copy_project_as_prompt(self.canvas.active_system))
        actions.addWidget(self.btn_copy_prompt)
        actions.addStretch(1)
        lay.addLayout(actions)
        side_layout.addWidget(self.sun_card)

        # Guided tour strip (only while touring)
        self.tour_card = QFrame()
        self.tour_card.setObjectName("TourCard")
        self.tour_card.setVisible(False)
        tour_layout = QHBoxLayout(self.tour_card)
        tour_layout.setContentsMargins(12, 6, 8, 6)
        tour_layout.setSpacing(6)
        self.tour_status_label = ElidedLabel("Tour")
        theme.set_role(self.tour_status_label, "warn")
        tour_layout.addWidget(self.tour_status_label, stretch=1)
        btn_next_tour = QPushButton("Next")
        btn_next_tour.setObjectName("QuietBtn")
        btn_next_tour.clicked.connect(self._on_tour_clicked)
        tour_layout.addWidget(btn_next_tour)
        btn_stop_tour = QPushButton("Close")
        btn_stop_tour.setObjectName("QuietBtn")
        btn_stop_tour.clicked.connect(self._stop_tour)
        tour_layout.addWidget(btn_stop_tour)
        tour_wrap = QWidget()
        tour_wrap.setObjectName("Transparent")
        tw_lay = QVBoxLayout(tour_wrap)
        tw_lay.setContentsMargins(12, 8, 12, 0)
        tw_lay.addWidget(self.tour_card)
        side_layout.addWidget(tour_wrap)

    def _build_purpose_block(self, lay: QVBoxLayout) -> None:
        """What the project is for: text + source tag, a placeholder with actions, an inline editor."""
        self.purpose_view = QWidget()
        self.purpose_view.setObjectName("Transparent")
        pv = QVBoxLayout(self.purpose_view)
        pv.setContentsMargins(0, 0, 0, 2)
        pv.setSpacing(2)
        self.lbl_purpose = ClampedLabel("", max_lines=theme.PURPOSE_MAX_LINES)
        self.lbl_purpose.setObjectName("PurposeText")
        self.lbl_purpose.setCursor(Qt.CursorShape.IBeamCursor)
        self.lbl_purpose.idle_tooltip = "Click to edit what this project is for"
        self.lbl_purpose.clicked.connect(lambda: self._begin_purpose_edit())
        pv.addWidget(self.lbl_purpose)
        self.lbl_purpose_source = theme.label("", "muted")
        pv.addWidget(self.lbl_purpose_source)
        lay.addWidget(self.purpose_view)

        # An AI suggestion waiting for the user: a separate tinted box, never the purpose.
        self.suggestion_box = QFrame()
        self.suggestion_box.setObjectName("SuggestionBox")
        sb = QVBoxLayout(self.suggestion_box)
        sb.setContentsMargins(10, 7, 10, 6)
        sb.setSpacing(4)
        self.lbl_suggestion_tag = QLabel("Suggested")
        self.lbl_suggestion_tag.setObjectName("SuggestionTag")
        self.lbl_suggestion_tag.setToolTip(
            "Written by the local AI model from what was collected about this project. "
            "It is not the project's purpose until you accept it."
        )
        sb_head = QHBoxLayout()
        sb_head.setContentsMargins(0, 0, 0, 0)
        sb_head.addWidget(self.lbl_suggestion_tag)
        sb_head.addStretch(1)
        sb.addLayout(sb_head)
        # Shown in full (at most 2 sentences / 400 characters): never elided, so the user
        # can read everything they accept.
        self.lbl_suggestion = theme.label("", "", wrap=True)
        self.lbl_suggestion.setObjectName("SuggestionText")
        self.lbl_suggestion.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_suggestion.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_suggestion.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sb.addWidget(self.lbl_suggestion)
        sb_actions = QHBoxLayout()
        sb_actions.setContentsMargins(0, 2, 0, 0)
        sb_actions.setSpacing(4)
        self.btn_suggestion_accept = QPushButton("Accept")
        self.btn_suggestion_edit = QPushButton("Edit")
        self.btn_suggestion_dismiss = QPushButton("Dismiss")
        for btn, tip, slot in (
            (self.btn_suggestion_accept, "Use this sentence as the project's purpose", self._accept_purpose_suggestion),
            (self.btn_suggestion_edit, "Change the sentence before keeping it as your own", self._edit_purpose_suggestion),
            (self.btn_suggestion_dismiss, "Remove the suggestion (not suggested again unless the project changes)",
             self._dismiss_purpose_suggestion),
        ):
            btn.setObjectName("SuggestionBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda checked=False, f=slot: f())
            sb_actions.addWidget(btn)
        sb_actions.addStretch(1)
        sb.addLayout(sb_actions)
        self.suggestion_box.setVisible(False)
        lay.addWidget(self.suggestion_box)

        # Neither a purpose nor a suggestion: one muted line that opens the editor.
        self.purpose_empty = QWidget()
        self.purpose_empty.setObjectName("Transparent")
        pe = QHBoxLayout(self.purpose_empty)
        pe.setContentsMargins(0, 0, 0, 2)
        pe.setSpacing(12)
        self.btn_purpose_add = QPushButton("Add what this project is for")
        self.btn_purpose_add.setObjectName("PurposeAddBtn")
        self.btn_purpose_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_purpose_add.setToolTip("Describe in a sentence or two what this project is for")
        self.btn_purpose_add.clicked.connect(lambda checked=False: self._begin_purpose_edit())
        pe.addWidget(self.btn_purpose_add)
        self.btn_suggest_ai = QPushButton("Suggest with AI")
        self.btn_suggest_ai.setObjectName("LinkBtn")
        self.btn_suggest_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_suggest_ai.clicked.connect(lambda checked=False: self._suggest_purpose_with_ai(self.canvas.active_system))
        self.btn_suggest_ai.setVisible(False)
        pe.addWidget(self.btn_suggest_ai)
        pe.addStretch(1)
        lay.addWidget(self.purpose_empty)

        self.purpose_edit_box = QWidget()
        self.purpose_edit_box.setObjectName("Transparent")
        ed = QVBoxLayout(self.purpose_edit_box)
        ed.setContentsMargins(0, 2, 0, 4)
        ed.setSpacing(6)
        self.edit_purpose = PurposeEditor()
        self.edit_purpose.saveRequested.connect(self._save_purpose_edit)
        self.edit_purpose.cancelRequested.connect(self._cancel_purpose_edit)
        ed.addWidget(self.edit_purpose)
        ed_row = QHBoxLayout()
        ed_row.setContentsMargins(0, 0, 0, 0)
        ed_row.setSpacing(6)
        self.btn_purpose_save = QPushButton("Save")
        self.btn_purpose_save.setObjectName("SecondaryBtn")
        self.btn_purpose_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_purpose_save.setToolTip("Save (Ctrl+Enter)")
        self.btn_purpose_save.clicked.connect(lambda checked=False: self._save_purpose_edit())
        ed_row.addWidget(self.btn_purpose_save)
        self.btn_purpose_cancel = QPushButton("Cancel")
        self.btn_purpose_cancel.setObjectName("QuietBtn")
        self.btn_purpose_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_purpose_cancel.setToolTip("Cancel (Esc)")
        self.btn_purpose_cancel.clicked.connect(lambda checked=False: self._cancel_purpose_edit())
        ed_row.addWidget(self.btn_purpose_cancel)
        ed_row.addStretch(1)
        ed_row.addWidget(theme.label("Ctrl+Enter saves · Esc cancels", "muted"))
        ed.addLayout(ed_row)
        self.purpose_edit_box.setVisible(False)
        lay.addWidget(self.purpose_edit_box)
        self._purpose_edit_target: Optional[SolarSystem] = None
        self._evidence_viewer: Optional[QDialog] = None

    def _build_identity_line(self, lay: QVBoxLayout) -> None:
        """What the project IS (``SolarSystem.identity``): a muted, read-only fact line under
        the purpose block (also shown when the purpose is empty). Never labelled "Purpose"."""
        self.lbl_identity = theme.label("", "", wrap=True)
        self.lbl_identity.setObjectName("IdentityLine")
        self.lbl_identity.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_identity.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_identity.setContentsMargins(0, 2, 0, 2)
        self.lbl_identity.setToolTip(codebase_importer_module.IDENTITY_TOOLTIP)
        self.lbl_identity.setVisible(False)
        lay.addWidget(self.lbl_identity)

    def _refresh_identity_line(self, sys_obj: Optional[SolarSystem]) -> None:
        if not hasattr(self, "lbl_identity"):
            return
        text = codebase_importer_module.stored_identity(sys_obj) if sys_obj is not None else ""
        self.lbl_identity.setText(text)
        self.lbl_identity.setVisible(bool(text))

    def _refresh_sun_anchor_card(self) -> None:
        sys_obj = self.canvas.active_system
        clear_layout(self.project_chips_layout)
        clear_layout(self.related_sources_layout)
        self._refresh_purpose_block(sys_obj)
        self._refresh_identity_line(sys_obj)
        self.btn_project_more.setEnabled(sys_obj is not None)
        if not sys_obj:
            self.sun_title_display.setText("No project yet")
            self.lbl_project_counts.setText("Use Add project to analyze a folder.")
            self.related_sources_widget.setVisible(False)
            self.btn_open_workspace.setEnabled(False)
            self.btn_copy_prompt.setEnabled(False)
            if not self._is_ollama_running():
                self.btn_ollama_single.setEnabled(False)
            self.btn_edit_toggle.setVisible(False)
            return
        self.btn_copy_prompt.setEnabled(True)
        if not self._is_ollama_running():
            self.btn_ollama_single.setEnabled(True)
        self.sun_title_display.setText(sys_obj.project_name or sys_obj.name)
        self.btn_ollama_single.setVisible(self._ai_available() or self._is_ollama_running())
        sources = list(sys_obj.sources or [sys_obj.source])
        for src in sources:
            meta = get_source_meta(src)
            self.project_chips_layout.addWidget(chip(str(meta.get("short", src)), tooltip=str(meta["title"])))
        if sys_obj.ollama_enhanced:
            self.project_chips_layout.addWidget(chip("Ollama", "accent", "The overview was enhanced by a local Ollama model"))
        self.project_chips_layout.addStretch(1)
        n_mod, n_link = len(sys_obj.planets), len(sys_obj.links)
        self.lbl_project_counts.setText(
            f"{n_mod} module{'s' if n_mod != 1 else ''} · {n_link} link{'s' if n_link != 1 else ''}"
        )
        self.btn_open_workspace.setEnabled(bool(sys_obj.workspace_path))

        related_list = self.store.find_related_systems(sys_obj)
        if related_list:
            self.related_sources_layout.addWidget(theme.label("Also in", "muted"))
            for rel_sys in related_list:
                rel_meta = get_source_meta(rel_sys.source)
                btn = QPushButton(str(rel_meta["short"]))
                btn.setObjectName("ChipBtn")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
                btn.setToolTip(f"Open the {rel_meta['title']} version of '{rel_sys.project_name}'")
                btn.clicked.connect(lambda checked=False, s=rel_sys: self.canvas.enter_solar_system(s))
                self.related_sources_layout.addWidget(btn)
            self.related_sources_layout.addStretch(1)
            self.related_sources_widget.setVisible(True)
        else:
            self.related_sources_widget.setVisible(False)

    def _show_project_more_menu(self) -> None:
        system = self.canvas.active_system
        if system is None:
            return
        menu = QMenu(self)
        self._project_menu_actions(menu, system)
        menu.addSeparator()
        act_png = menu.addAction("Export map as image (.png)…")
        act_png.triggered.connect(lambda: self._export_map(system, "png"))
        act_md = menu.addAction("Export map as document (.md)…")
        act_md.triggered.connect(lambda: self._export_map(system, "md"))
        menu.addSeparator()
        act_rm = menu.addAction("Remove project…")
        act_rm.triggered.connect(lambda: self._remove_project(system))
        menu.setToolTipsVisible(True)
        menu.exec(self.btn_project_more.mapToGlobal(QPoint(0, self.btn_project_more.height())))
