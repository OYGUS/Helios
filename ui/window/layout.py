"""`LayoutMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

Construction of the window: top bar, project sidebar, canvas, inspector frame, central
stack with the integrations page, status bar, search controller and filter popover;
plus small UI helpers (sun / dot icons, integer settings, splitter sizes).

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _busy_lock_widgets, _dot_icon_cache, _splitter_save_timer, btn_add_project,
    btn_bc_galaxy, btn_bc_project, btn_connections, btn_filter, btn_fit_view,
    btn_group_by_project, btn_group_by_source, btn_rebalance, btn_settings,
    btn_show_details, btn_status_stop, btn_sync_agents, btn_toggle_orbit, btn_tour,
    btn_view_mode, busy_bar, canvas, central_stack, connections_view, filter_popover,
    hierarchy_tree, lbl_bc_sep1, lbl_bc_sep2, lbl_breadcrumb, lbl_ollama_status,
    lbl_skipped_link, main_splitter, search_ctl, search_input, status_bar, status_label,
    store, top_actions, tree_filter, tree_group_mode
Of those, assigned by this mixin:
    _busy_lock_widgets, _splitter_save_timer, btn_add_project, btn_bc_galaxy,
    btn_bc_project, btn_connections, btn_filter, btn_fit_view, btn_group_by_project,
    btn_group_by_source, btn_rebalance, btn_settings, btn_show_details, btn_status_stop,
    btn_sync_agents, btn_toggle_orbit, btn_tour, btn_view_mode, busy_bar, canvas,
    central_stack, connections_view, filter_popover, hierarchy_tree, lbl_bc_sep1,
    lbl_bc_sep2, lbl_breadcrumb, lbl_ollama_status, lbl_skipped_link, main_splitter,
    search_ctl, search_input, status_bar, status_label, top_actions, tree_filter, tree_group_mode
"""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QBrush, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from connectors import codebase_importer as codebase_importer_module
from ui import theme
from ui.filters import FilterPopover
from ui.inspector import ElidedLabel
from ui.overflow_row import OverflowRow
from ui.search import SearchController
from ui.space_canvas import SpaceCanvasView
from ui.window.integrations import AIConnectionsView


class LayoutMixin:
    """Window construction and small UI helpers."""

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("CentralRoot")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---------------------------------------------------------------
        # 1. Top bar: brand · breadcrumb · search · view toggles & actions
        # ---------------------------------------------------------------
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_bar.setFixedHeight(48)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 8, 12, 8)
        top_layout.setSpacing(4)

        brand_icon = QLabel()
        brand_icon.setPixmap(self._sun_pixmap(16))
        brand_icon.setFixedSize(18, 18)
        top_layout.addWidget(brand_icon)
        brand_label = theme.label("Helios", "brand")
        top_layout.addWidget(brand_label)
        top_layout.addSpacing(14)

        # Breadcrumb: All projects › Project › Module › Component
        self.btn_bc_galaxy = QPushButton("All projects")
        self.btn_bc_galaxy.setObjectName("CrumbBtn")
        self.btn_bc_galaxy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bc_galaxy.setToolTip("Show all projects (Esc)")
        self.btn_bc_galaxy.clicked.connect(lambda: self.canvas.switch_to_galaxy_view())
        top_layout.addWidget(self.btn_bc_galaxy)
        self.lbl_bc_sep1 = theme.label("›", "crumb-sep")
        top_layout.addWidget(self.lbl_bc_sep1)
        self.btn_bc_project = QPushButton("")
        self.btn_bc_project.setObjectName("CrumbBtn")
        self.btn_bc_project.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bc_project.setToolTip("Show the whole project")
        self.btn_bc_project.setMaximumWidth(220)
        self.btn_bc_project.clicked.connect(self._on_project_crumb_clicked)
        top_layout.addWidget(self.btn_bc_project)
        self.lbl_bc_sep2 = theme.label("›", "crumb-sep")
        top_layout.addWidget(self.lbl_bc_sep2)
        self.lbl_breadcrumb = ElidedLabel("")
        theme.set_role(self.lbl_breadcrumb, "crumb")
        self.lbl_breadcrumb.setMinimumWidth(40)
        top_layout.addWidget(self.lbl_breadcrumb, stretch=2)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchField")
        self.search_input.setPlaceholderText("Search the map  (Ctrl+F)")
        self.search_input.setToolTip("Find projects, modules, components, functions, classes, files and "
                                     "libraries. Up / Down to pick, Enter to open, Esc to close.")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(140)
        self.search_input.setMaximumWidth(340)
        self.search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Typing is debounced by the SearchController (ui/search.py), created below.
        top_layout.addWidget(self.search_input, stretch=3)
        top_layout.addSpacing(10)

        # The action buttons live in an OverflowRow: on a narrow window (or with wide fonts)
        # the least important ones move into its "More" menu instead of widening the window.
        self.top_actions = OverflowRow(top_bar, spacing=4)

        def top_button(text: str, tip: str, checkable: bool = False, priority: int = 0) -> QPushButton:
            btn = QPushButton(text)
            btn.setObjectName("QuietBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setCheckable(checkable)
            self.top_actions.add_button(btn, priority)
            return btn

        # Priorities: lower values move into "More" first.
        self.btn_toggle_orbit = top_button("Motion", "Let the modules orbit slowly (off by default)", True, 0)
        self.btn_toggle_orbit.clicked.connect(self._toggle_orbit_motion)
        self.btn_fit_view = top_button("Fit view", "Fit the whole map into the window  (F, Ctrl+0)", priority=2)
        self.btn_fit_view.clicked.connect(lambda: self.canvas.fit_to_view())
        self.btn_show_details = top_button(
            "Show all details", "Always show every module's components, not only for the selected one", True, 1
        )
        self.btn_show_details.setChecked(self.canvas_show_all_details_setting())
        self.btn_show_details.clicked.connect(self._toggle_show_all_details)
        self.btn_filter = top_button("Filter", "Choose which links and modules the map shows (only the view changes)",
                                     priority=4)
        self.btn_filter.clicked.connect(self._open_filter_popover)
        # The divider keeps its 4 px of air on each side inside its own box.
        sep_box = QWidget()
        sep_lay = QHBoxLayout(sep_box)
        sep_lay.setContentsMargins(4, 0, 4, 0)
        sep = QFrame()
        sep.setObjectName("Hairline")
        sep.setFixedSize(1, 18)
        sep_lay.addWidget(sep)
        sep_box.setFixedWidth(9)
        self.top_actions.add_separator(sep_box)
        self.btn_sync_agents = top_button("Sync", "Read the connected AI tools again and update the maps", priority=3)
        self.btn_sync_agents.clicked.connect(self._sync_agent_histories)
        self.btn_connections = top_button("Integrations", "Choose which AI tools and local models Helios uses", True, 6)
        self.btn_connections.clicked.connect(self._toggle_connections_page)
        self.btn_settings = top_button("Settings", "Ollama model, the 'projects/' folder and duplicate merging",
                                       priority=5)
        self.btn_settings.clicked.connect(self._open_settings_dialog)
        top_layout.addWidget(self.top_actions)

        # Kept as an attribute for older call sites; the breadcrumb replaces it visually.
        self.btn_view_mode = self.btn_bc_galaxy
        root_layout.addWidget(top_bar)

        # ---------------------------------------------------------------
        # 2. Body: projects list | canvas | inspector
        # ---------------------------------------------------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(3)
        self.main_splitter = splitter

        # --- Left: projects ---------------------------------------------
        left_sidebar = QFrame()
        left_sidebar.setObjectName("LeftPanel")
        left_sidebar.setMinimumWidth(220)
        left_sidebar.setMaximumWidth(400)
        left_layout = QVBoxLayout(left_sidebar)
        left_layout.setContentsMargins(12, 12, 8, 8)
        left_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        header_row.addWidget(theme.label("Projects", "section"), stretch=1)
        self.btn_add_project = QPushButton("Add project")
        self.btn_add_project.setObjectName("PrimaryBtn")
        self.btn_add_project.setProperty("menu", "true")
        self.btn_add_project.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_project.setToolTip("Analyze a folder, scan the 'projects/' folder or design a project")
        proj_menu = QMenu(self)
        act_analyze = proj_menu.addAction("Analyze a folder…")
        act_analyze.triggered.connect(self._analyze_codebase_folder)
        act_scan_p = proj_menu.addAction("Scan the 'projects/' folder")
        act_scan_p.triggered.connect(self._scan_helios_projects_directly)
        act_open_f = proj_menu.addAction("Open the 'projects/' folder")
        act_open_f.triggered.connect(self._open_helios_projects_folder)
        proj_menu.addSeparator()
        act_create_new = proj_menu.addAction("Design a new project…")
        act_create_new.triggered.connect(self._create_new_system)
        self.btn_add_project.setMenu(proj_menu)
        header_row.addWidget(self.btn_add_project)
        left_layout.addLayout(header_row)

        self.tree_filter = QLineEdit()
        self.tree_filter.setObjectName("SearchField")
        self.tree_filter.setPlaceholderText("Filter projects")
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.textChanged.connect(self._on_tree_filter_text_changed)
        left_layout.addWidget(self.tree_filter)

        seg = QFrame()
        seg.setObjectName("Segmented")
        seg_lay = QHBoxLayout(seg)
        seg_lay.setContentsMargins(2, 2, 2, 2)
        seg_lay.setSpacing(2)
        self.btn_group_by_project = QPushButton("By project")
        self.btn_group_by_project.setObjectName("SegBtn")
        self.btn_group_by_project.setCheckable(True)
        self.btn_group_by_project.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_group_by_project.setToolTip("One entry per project, even if several tools worked on it")
        self.btn_group_by_source = QPushButton("By source")
        self.btn_group_by_source.setObjectName("SegBtn")
        self.btn_group_by_source.setCheckable(True)
        self.btn_group_by_source.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_group_by_source.setToolTip("Group projects by the tool they came from")

        def _select_project_mode() -> None:
            self.tree_group_mode = "project"
            self._update_group_toggle_styles()
            self._populate_tree()

        def _select_source_mode() -> None:
            self.tree_group_mode = "source"
            self._update_group_toggle_styles()
            self._populate_tree()

        self.btn_group_by_project.clicked.connect(_select_project_mode)
        self.btn_group_by_source.clicked.connect(_select_source_mode)
        seg_lay.addWidget(self.btn_group_by_project, stretch=1)
        seg_lay.addWidget(self.btn_group_by_source, stretch=1)
        left_layout.addWidget(seg)

        self.hierarchy_tree = QTreeWidget()
        self.hierarchy_tree.setHeaderHidden(True)
        self.hierarchy_tree.setIndentation(16)
        self.hierarchy_tree.setIconSize(QSize(10, 10))
        self.hierarchy_tree.setUniformRowHeights(True)
        self.hierarchy_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.hierarchy_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.hierarchy_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.hierarchy_tree.viewport().setAutoFillBackground(False)
        left_layout.addWidget(self.hierarchy_tree, stretch=1)
        self._update_group_toggle_styles()
        splitter.addWidget(left_sidebar)

        # --- Centre: canvas -----------------------------------------------
        self.canvas = SpaceCanvasView(self.store, self)
        self.canvas.nodeSelected.connect(self._on_canvas_node_selected)
        self.canvas.viewModeChanged.connect(self._on_view_mode_changed)
        self.canvas.tourStepChanged.connect(self._on_tour_step_changed)
        self.canvas.contextActionRequested.connect(self._on_canvas_context_action)
        self.canvas.drawingToolChanged.connect(self._on_drawing_tool_changed)
        self.canvas.showAllDetailsChanged.connect(self._on_show_all_details_changed)
        self.canvas.mapFilterChanged.connect(self._on_map_filter_changed)
        self.canvas.setMinimumWidth(360)
        splitter.addWidget(self.canvas)
        # Older code paths toggled this; auto-align now lives in the canvas tool strip.
        self.btn_rebalance = self.canvas.btn_strip_align
        self.btn_tour = self.canvas.btn_strip_tour

        # --- Right: inspector ---------------------------------------------
        side_panel = QFrame()
        side_panel.setObjectName("Inspector")
        side_panel.setMinimumWidth(340)
        side_panel.setMaximumWidth(620)
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        self._build_project_header(side_layout)
        self._build_inspector_body(side_layout)
        splitter.addWidget(side_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        left_w = self._int_setting("sidebar_width", 260, 220, 400)
        right_w = self._int_setting("inspector_width", 390, 340, 620)
        splitter.setSizes([left_w, max(400, 1560 - left_w - right_w), right_w])
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(500)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        splitter.splitterMoved.connect(lambda *_: self._splitter_save_timer.start())

        # Central stack (0: studio, 1: integrations page)
        self.central_stack = QStackedWidget()
        self.central_stack.addWidget(splitter)
        self.connections_view = AIConnectionsView(
            store=self.store,
            parent=self,
            on_finish=self._on_connections_finished,
            on_cancel=self._on_connections_cancelled,
        )
        self.central_stack.addWidget(self.connections_view)
        root_layout.addWidget(self.central_stack, stretch=1)

        # ---------------------------------------------------------------
        # 3. Status bar (stays enabled while a worker is busy)
        # ---------------------------------------------------------------
        status_bar = QFrame()
        status_bar.setObjectName("StatusBar")
        status_bar.setFixedHeight(28)
        sb_lay = QHBoxLayout(status_bar)
        sb_lay.setContentsMargins(12, 0, 12, 0)
        sb_lay.setSpacing(8)
        self.busy_bar = QProgressBar()
        self.busy_bar.setObjectName("BusyBar")
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedWidth(72)
        self.busy_bar.setVisible(False)
        sb_lay.addWidget(self.busy_bar)
        self.status_label = ElidedLabel("Click a project to open it · drag to pan · scroll to zoom")
        self.status_label.setObjectName("StatusHint")
        theme.set_role(self.status_label, "caption")
        sb_lay.addWidget(self.status_label, stretch=1)
        self.btn_status_stop = QPushButton("Stop")
        self.btn_status_stop.setObjectName("LinkBtn")
        self.btn_status_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_status_stop.setToolTip("Stop Ollama (results written so far are kept)")
        self.btn_status_stop.clicked.connect(self._stop_ollama)
        self.btn_status_stop.setVisible(False)
        sb_lay.addWidget(self.btn_status_stop)
        self.lbl_skipped_link = QLabel("")
        self.lbl_skipped_link.setObjectName("SkippedLink")
        theme.set_role(self.lbl_skipped_link, "caption")
        self.lbl_skipped_link.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_skipped_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                                      | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.lbl_skipped_link.setOpenExternalLinks(False)
        self.lbl_skipped_link.linkActivated.connect(lambda *_: self._show_skipped_folders_dialog())
        self.lbl_skipped_link.setVisible(False)
        sb_lay.addWidget(self.lbl_skipped_link)
        self.lbl_ollama_status = ElidedLabel("")
        theme.set_role(self.lbl_ollama_status, "muted")
        self.lbl_ollama_status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.lbl_ollama_status.setFixedWidth(220)
        self.lbl_ollama_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sb_lay.addWidget(self.lbl_ollama_status)
        root_layout.addWidget(status_bar)
        self.status_bar = status_bar

        # Disabled as a group while a background worker owns the store (status bar stays live).
        self._busy_lock_widgets = [top_bar, left_sidebar, self.canvas, side_panel]

        self.search_ctl = SearchController(
            self.search_input,
            central,
            get_systems=lambda: self.store.systems,
            get_active_id=lambda: self.canvas.active_system.id if self.canvas.active_system else "",
            on_query=self._on_search_text_changed,
            on_choose=self._open_search_result,
            identity_of=codebase_importer_module.stored_identity,
        )
        self.filter_popover = FilterPopover(lambda: self.canvas.active_system, lambda: self.canvas.map_filter, self)
        self.filter_popover.changed.connect(lambda: self.canvas.apply_map_filter())
        self._on_map_filter_changed("")

        # Mouse clicks don't leave focus rings on buttons; keyboard (Tab) focus still shows them.
        for btn in self.findChildren(QAbstractButton):
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    @staticmethod
    def _sun_pixmap(size: int) -> QPixmap:
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(theme.qcolor(theme.ACCENT, 60)))
        p.drawEllipse(0, 0, size, size)
        p.setBrush(QBrush(theme.qcolor(theme.ACCENT)))
        inset = size * 0.22
        p.drawEllipse(int(inset), int(inset), int(size - 2 * inset), int(size - 2 * inset))
        p.end()
        return pm

    def _dot_icon(self, hex_color: str, hollow: bool = False) -> QIcon:
        key = f"{hex_color}|{hollow}"
        cached = self._dot_icon_cache.get(key)
        if cached is not None:
            return cached
        size = 10
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = theme.soft_color(hex_color)
        if hollow:
            p.setPen(QPen(color, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(2, 2, 6, 6)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(1, 1, 8, 8)
        p.end()
        icon = QIcon(pm)
        self._dot_icon_cache[key] = icon
        return icon

    def _int_setting(self, key: str, default: int, lo: int, hi: int) -> int:
        try:
            value = int(self.store.get_setting(key, default) or default)
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    def _save_splitter_sizes(self) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) == 3 and sizes[0] > 0 and sizes[2] > 0:
            try:
                self.store.set_setting("sidebar_width", int(sizes[0]))
                self.store.set_setting("inspector_width", int(sizes[2]))
            except Exception:
                pass

    def canvas_show_all_details_setting(self) -> bool:
        return bool(self.store.get_setting("canvas_show_all_details", False))
