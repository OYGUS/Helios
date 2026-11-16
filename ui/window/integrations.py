"""The integrations page: `IntegrationCardWidget` (one source / Ollama row),
`AIConnectionsView` (the full-window page inside the main window, which starts the
`AgentSyncWorker`) and `AIConnectionsDialog` (the same view in a standalone dialog)."""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import paths as app_paths
from core.models import GalaxyStore
from ui import theme
from ui.workers import (
    AgentSyncWorker,
    HELIOS_ROOT,
    ToolDetectWorker,
    find_worker_host,
    start_probe_worker,
    start_store_worker,
)


class IntegrationCardWidget(QFrame):
    """Clean, modern data source and integration row card."""

    def __init__(
        self,
        tool_key: str,
        info: Dict[str, object],
        is_connected: bool,
        parent: Optional[QWidget] = None,
        on_toggle: Optional[callable] = None,
    ) -> None:
        super().__init__(parent)
        self.tool_key = tool_key
        self.info = info
        self.is_locked = tool_key == "codebase"
        self._is_connected = True if self.is_locked else is_connected
        self.on_toggle = on_toggle
        self.color = str(info.get("color", "#3B82F6"))
        self.setObjectName("IntegrationCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.combo_ollama: Optional[QComboBox] = None
        self._build_ui()
        self._apply_state()

    def is_connected(self) -> bool:
        return self._is_connected

    def set_connected(self, val: bool) -> None:
        if self.is_locked:
            return
        self._is_connected = val
        self._apply_state()
        if self.on_toggle:
            self.on_toggle(self.tool_key, self._is_connected)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.is_locked:
            self.set_connected(not self._is_connected)
        super().mousePressEvent(event)

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(14)

        # Left: monogram badge in the (softened) source colour
        lbl_code = QLabel(str(self.info.get("icon", "AI")))
        lbl_code.setObjectName("Monogram")
        lbl_code.setFixedSize(36, 36)
        lbl_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Per-source colour: the only inline colour here, derived from the theme mapping.
        lbl_code.setStyleSheet(f"color: {theme.soft_color_hex(self.color)};")
        main_layout.addWidget(lbl_code)

        # Middle: name, status and description
        mid_box = QVBoxLayout()
        mid_box.setSpacing(2)
        mid_box.setContentsMargins(0, 0, 0, 0)
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        lbl_name = QLabel(str(self.info.get("name", "Source")))
        lbl_name.setProperty("role", "body")
        name_font = lbl_name.font()
        name_font.setWeight(QFont.Weight.DemiBold)
        lbl_name.setFont(name_font)
        top_row.addWidget(lbl_name)
        self.lbl_status = QLabel("")
        top_row.addWidget(self.lbl_status)
        path_hint = str(self.info.get("path", ""))
        if path_hint and path_hint != "None":
            lbl_path = theme.label(path_hint, "mono")
            lbl_path.setToolTip(path_hint)
            top_row.addWidget(lbl_path)
        top_row.addStretch(1)
        mid_box.addLayout(top_row)
        desc_text = str(self.info.get("description", ""))
        if desc_text:
            mid_box.addWidget(theme.label(desc_text, "caption", wrap=True))
        main_layout.addLayout(mid_box, stretch=1)

        # Right: Ollama model selector (if available) + connect toggle
        right_row = QHBoxLayout()
        right_row.setSpacing(8)
        right_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        if self.tool_key == "ollama":
            models = self.info.get("models", [])
            if models:
                self.combo_ollama = QComboBox()
                self.combo_ollama.setMinimumWidth(150)
                for m in models:
                    self.combo_ollama.addItem(m, m)
                right_row.addWidget(self.combo_ollama)
        self.btn_toggle = QPushButton()
        self.btn_toggle.setObjectName("ToggleBtn")
        self.btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle.clicked.connect(lambda: self.set_connected(not self._is_connected))
        right_row.addWidget(self.btn_toggle)
        main_layout.addLayout(right_row)

    def _apply_state(self) -> None:
        is_detected = bool(self.info.get("detected", False))
        if hasattr(self, "lbl_status"):
            self.lbl_status.setText(f"•  {self.info.get('status', '')}")
            theme.set_role(self.lbl_status, "ok" if is_detected else "muted")
        if self.is_locked:
            state, text = "locked", "Built-in"
        elif self._is_connected:
            state, text = "on", "Connected"
        else:
            state, text = "off", "Connect"
        self.setProperty("state", state)
        self.btn_toggle.setProperty("state", state)
        self.btn_toggle.setText(text)
        theme.repolish(self)
        theme.repolish(self.btn_toggle)


class AIConnectionsView(QWidget):
    """Full-window view inside the main window for managing AI agent and codebase integrations."""

    def __init__(
        self,
        store: GalaxyStore,
        parent: Optional[QWidget] = None,
        on_finish: Optional[callable] = None,
        on_cancel: Optional[callable] = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_finish = on_finish
        self.on_cancel = on_cancel
        self.cards: Dict[str, IntegrationCardWidget] = {}
        # Detection (home-folder probes, PATH lookups, an HTTP check of Ollama) runs on a
        # probe thread; the cards are added when its first answer arrives.
        self.detected_tools: Dict[str, Dict[str, object]] = {}
        self._detect_worker: Optional[ToolDetectWorker] = None
        self._detect_again = False
        self._refresh_requested = False
        self._busy = False
        self.setObjectName("AIConnectionsView")
        self._build_ui()
        self._start_detection()

    def is_detecting(self) -> bool:
        return self._detect_worker is not None

    def _start_detection(self) -> None:
        if self._detect_worker is not None:
            self._detect_again = True  # answer the latest request too
            return
        worker = ToolDetectWorker()
        worker.detected.connect(self._on_tools_detected)
        self._detect_worker = worker
        start_probe_worker(worker)

    @Slot(object)
    def _on_tools_detected(self, tools: object) -> None:
        worker, self._detect_worker = self._detect_worker, None
        if worker is not None:
            worker.wait(2000)  # `detected` is the last thing run() does
        if isinstance(tools, dict):
            self.detected_tools = tools
            if not self.cards:
                self._add_cards()
            if self._refresh_requested:
                self._refresh_requested = False
                self._apply_detected_state()
        if self._detect_again:
            self._detect_again = False
            self._start_detection()

    def refresh_state(self) -> None:
        """Refreshes detected tools when the integrations view is opened (in the
        background: the cards are updated when the detection answers)."""
        self._refresh_requested = True
        self._start_detection()

    def _apply_detected_state(self) -> None:
        ready_count = sum(1 for v in self.detected_tools.values() if v.get("detected"))
        self.lbl_stats.setText(f"{ready_count} sources detected")
        saved_sources = self.store.get_setting("connected_sources", None)
        disable_ollama = bool(self.store.get_setting("disable_ollama", False))

        for k, card in self.cards.items():
            if k in self.detected_tools:
                card.info = self.detected_tools[k]
                if saved_sources is not None and len(saved_sources) > 0:
                    if k == "ollama":
                        card.set_connected(not disable_ollama and bool(card.info.get("detected")))
                    elif k != "codebase":
                        card.set_connected(k in saved_sources)
                card._apply_state()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        scroll.viewport().setAutoFillBackground(False)

        container = QWidget()
        container.setObjectName("Transparent")
        container.setMaximumWidth(820)
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(32, 32, 32, 32)
        c_layout.setSpacing(16)

        # 1. Top Header Row
        header_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        title_lbl = theme.label("Integrations", "title")
        title_col.addWidget(title_lbl)

        sub_lbl = theme.label(
            "Choose which AI coding tools Helios reads project folders from, and whether a local "
            "Ollama model writes module descriptions.",
            "secondary",
            wrap=True,
        )
        title_col.addWidget(sub_lbl)
        header_row.addLayout(title_col, stretch=1)

        self.lbl_stats = theme.label("Looking for AI tools\u2026", "caption")
        header_row.addWidget(self.lbl_stats, alignment=Qt.AlignmentFlag.AlignTop)

        c_layout.addLayout(header_row)

        # 2. Integration Cards List (filled by `_add_cards` once detection has answered)
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setSpacing(8)
        c_layout.addLayout(self._cards_layout)
        self._finish_build_ui(c_layout, scroll, container, outer_layout)

    def _add_cards(self) -> None:
        """One card per detected tool (as the view always built them), and the count."""
        ready_count = sum(1 for v in self.detected_tools.values() if v.get("detected"))
        self.lbl_stats.setText(f"{ready_count} sources detected")
        saved_sources = self.store.get_setting("connected_sources", None)
        disable_ollama = bool(self.store.get_setting("disable_ollama", False))

        for tool_key, info in self.detected_tools.items():
            if tool_key == "codebase":
                is_conn = True
            elif saved_sources is not None and len(saved_sources) > 0:
                if tool_key == "ollama":
                    is_conn = not disable_ollama and bool(info.get("detected"))
                else:
                    is_conn = tool_key in saved_sources
            else:
                if tool_key == "claude_code":
                    is_conn = False
                else:
                    is_conn = bool(info.get("detected"))

            card = IntegrationCardWidget(
                tool_key=tool_key,
                info=info,
                is_connected=is_conn,
                on_toggle=self._on_card_toggled,
            )
            card.setEnabled(not self._busy)
            self.cards[tool_key] = card
            self._cards_layout.addWidget(card)

    def _finish_build_ui(self, c_layout: QVBoxLayout, scroll: QScrollArea, container: QWidget,
                         outer_layout: QVBoxLayout) -> None:
        # 3. Bottom Options Bar
        opt_box = QFrame()
        opt_box.setObjectName("Card")
        opt_lay = QHBoxLayout(opt_box)
        opt_lay.setContentsMargins(16, 10, 16, 10)
        opt_lay.setSpacing(10)

        self.chk_reset_projects = QCheckBox("Clear the map and rebuild it from the selected sources")
        self.chk_reset_projects.setChecked(not bool(self.store.get_setting("onboarding_completed", False)))
        opt_lay.addWidget(self.chk_reset_projects)
        opt_lay.addStretch(1)

        lbl_limit = theme.label("Sessions to read", "caption")
        opt_lay.addWidget(lbl_limit)

        self.combo_max_sessions = QComboBox()
        self.combo_max_sessions.addItems(["15 sessions", "25 sessions", "50 sessions", "All"])
        self.combo_max_sessions.setCurrentIndex(1)
        opt_lay.addWidget(self.combo_max_sessions)
        c_layout.addWidget(opt_box)

        # Status message
        self.lbl_status = theme.label("", "warn", wrap=True)
        self.lbl_status.setVisible(False)
        c_layout.addWidget(self.lbl_status)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_back = QPushButton("Back to the map")
        btn_back.clicked.connect(self._on_skip)
        btn_row.addWidget(btn_back)
        self.btn_back = btn_back

        btn_row.addStretch(1)

        self.btn_connect = QPushButton("Save and apply")
        self.btn_connect.setObjectName("PrimaryBtn")
        self.btn_connect.clicked.connect(self._on_connect)
        btn_row.addWidget(self.btn_connect)
        c_layout.addLayout(btn_row)

        c_layout.addStretch(1)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll, stretch=1)

    def _on_card_toggled(self, tool_key: str, state: bool) -> None:
        pass

    def _get_max_sessions(self) -> int:
        idx = self.combo_max_sessions.currentIndex()
        if idx == 0:
            return 15
        elif idx == 1:
            return 25
        elif idx == 2:
            return 50
        return 120

    def _on_connect(self) -> None:
        if not self.cards and self.is_detecting():
            self.lbl_status.setVisible(True)
            self.lbl_status.setText("Still looking for AI tools on this computer\u2026 try again in a moment.")
            return
        selected = [k for k, card in self.cards.items() if card.is_connected()]
        if not selected:
            QMessageBox.warning(self, "No source selected", "Please connect at least one AI source or the Codebase engine.")
            return

        # Save Ollama preference (the service itself is started by the worker if selected)
        if "ollama" in selected:
            self.store.set_setting("disable_ollama", False)
            ollama_card = self.cards.get("ollama")
            if ollama_card and ollama_card.combo_ollama:
                chosen_m = ollama_card.combo_ollama.currentText()
                if chosen_m:
                    self.store.set_setting("selected_ollama_model", chosen_m)
        else:
            self.store.set_setting("disable_ollama", True)

        busy_text = "Scanning selected AI session histories and building the architecture graph..."
        worker = AgentSyncWorker(
            self.store,
            selected,
            max_sessions=self._get_max_sessions(),
            start_ollama="ollama" in selected,
            reset=self.chk_reset_projects.isChecked(),
            analyze_helios="codebase" in selected,
            helios_dir=HELIOS_ROOT,
        )
        self._pending_selected = list(selected)
        self.set_busy(True, busy_text)
        worker.progressText.connect(self.lbl_status.setText)
        if not start_store_worker(self, worker, self._on_connect_finished, busy_text):
            self._on_worker_rejected()

    def set_busy(self, busy: bool, text: str = "") -> None:
        """Disables the integration controls while a background scan runs."""
        self._busy = busy
        for card in self.cards.values():
            card.setEnabled(not busy)
        for w in (self.chk_reset_projects, self.combo_max_sessions, self.btn_connect, self.btn_back):
            w.setEnabled(not busy)
        if busy:
            self.lbl_status.setVisible(True)
            self.lbl_status.setText(text)
        else:
            self.lbl_status.setVisible(False)

    def _on_worker_rejected(self) -> None:
        host = find_worker_host(self)
        self.set_busy(bool(host is not None and host.is_store_busy()))
        self.lbl_status.setVisible(True)
        self.lbl_status.setText("Another scan is already running. Please wait for it to finish.")

    def _host_status(self, text: str) -> None:
        host = self.window()
        lbl = getattr(host, "status_label", None)
        if lbl is not None:
            lbl.setText(text)

    def _on_connect_finished(self, ok: bool, msg: str, payload: object) -> None:
        self.set_busy(False)
        if not ok:
            self.lbl_status.setVisible(True)
            self.lbl_status.setText(msg)
            QMessageBox.warning(self, "Scan failed", msg)
            return

        selected = getattr(self, "_pending_selected", [])
        self.store.set_setting("connected_sources", selected)
        self.store.set_setting("onboarding_completed", True)

        if self.on_finish:
            self.on_finish()
        warnings = payload.get("warnings") if isinstance(payload, dict) else None
        if warnings:
            self._host_status("AI integrations updated. Warning: " + " ".join(str(w) for w in warnings))

    def _on_skip(self) -> None:
        reset = self.chk_reset_projects.isChecked()
        has_helios_code = any(
            s.source == "codebase" and s.project_name.lower() == "helios"
            for s in self.store.systems
        )
        # A frozen build has no Helios source folder: nothing to analyze, so no such message.
        helios_dir = app_paths.source_dir()
        analyze = helios_dir is not None and (reset or not has_helios_code)
        if not analyze and not (reset and self.store.systems):
            self._on_skip_finished(True, "", None)
            return
        busy_text = "Analyzing the Helios codebase..." if analyze else "Clearing the map..."
        worker = AgentSyncWorker(
            self.store,
            [],
            reset=reset,
            analyze_helios=analyze,
            helios_dir=helios_dir,
        )
        self.set_busy(True, busy_text)
        worker.progressText.connect(self.lbl_status.setText)
        if not start_store_worker(self, worker, self._on_skip_finished, busy_text):
            self._on_worker_rejected()

    def _on_skip_finished(self, ok: bool, msg: str, payload: object) -> None:
        self.set_busy(False)
        self.store.set_setting("connected_sources", ["codebase", "manual"])
        self.store.set_setting("onboarding_completed", True)
        if self.on_cancel:
            self.on_cancel()
        elif self.on_finish:
            self.on_finish()
        warnings = payload.get("warnings") if isinstance(payload, dict) else None
        if not ok:
            self._host_status(msg)
        elif warnings:
            self._host_status("Warning: " + " ".join(str(w) for w in warnings))


class AIConnectionsDialog(QDialog):
    """Dialog wrapper hosting AIConnectionsView for standalone dialog invocation."""

    def __init__(
        self,
        store: GalaxyStore,
        parent: Optional[QWidget] = None,
        is_first_run: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Integrations — Helios")
        self.resize(840, 720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = AIConnectionsView(
            store=store,
            parent=self,
            on_finish=self.accept,
            on_cancel=self.reject,
        )
        lay.addWidget(self.view)
