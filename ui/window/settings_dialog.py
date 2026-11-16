"""`HeliosSettingsDialog`: Ollama model / language / auto-suggest settings, model download,
the 'projects/' folder scan, duplicate merging and the ignored-folders list."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from connectors.codebase_importer import CodebaseImporter
from connectors.ollama_enhancer import (
    DEFAULT_LANGUAGE,
    LANGUAGE_SETTING_KEY,
    OllamaPullWorker,
    SUPPORTED_LANGUAGES,
)
from core.models import GalaxyStore
from ui import theme
from ui.inspector import ElidedLabel, clear_layout
from ui.window.ignored_folders import ignored_folders, remove_ignored_folder
from ui.window.project_text import AUTO_PURPOSE_SETTING_KEY
from ui.workers import (
    CodebaseScanWorker,
    MergeDuplicatesWorker,
    OllamaModelsWorker,
    start_probe_worker,
    start_store_worker,
)


class HeliosSettingsDialog(QDialog):
    """Settings: local Ollama model, the 'projects/' folder scan and duplicate merging."""

    def __init__(self, store: GalaxyStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Settings — Helios")
        self.setMinimumSize(600, 540)
        self._pull_worker: Optional[OllamaPullWorker] = None
        # The model list comes from a probe thread (disk scan + HTTP): see `_refresh_models_list`.
        self._models_worker: Optional[OllamaModelsWorker] = None
        self._models_again = False
        self._models_loading = False
        self._build_ui()

    @staticmethod
    def _card(title: str, text: str = "") -> "tuple[QFrame, QVBoxLayout]":
        box = QFrame()
        box.setObjectName("Card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        lay.addWidget(theme.label(title, "heading"))
        if text:
            lay.addWidget(theme.label(text, "secondary", wrap=True))
        return box, lay

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("Transparent")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 20, 24, 12)
        layout.setSpacing(12)

        layout.addWidget(theme.label("Settings", "title"))

        # --- Local AI (Ollama) ---
        box_ai, lay_ai = self._card(
            "Local AI (Ollama)",
            "An optional local model writes short module descriptions. The map itself always comes from your code.",
        )
        row_mod = QHBoxLayout()
        row_mod.setSpacing(8)
        lbl_model = theme.label("Model", "caption")
        lbl_model.setFixedWidth(130)
        row_mod.addWidget(lbl_model)
        self.combo_model = QComboBox()
        self.combo_model.setMinimumWidth(220)
        self._refresh_models_list()
        row_mod.addWidget(self.combo_model, 1)
        btn_refresh_models = QPushButton("Refresh")
        btn_refresh_models.setToolTip("Look for installed Ollama models again")
        btn_refresh_models.clicked.connect(self._refresh_models_list)
        row_mod.addWidget(btn_refresh_models)
        lay_ai.addLayout(row_mod)

        row_lang = QHBoxLayout()
        row_lang.setSpacing(8)
        lbl_lang = theme.label("Description language", "caption")
        lbl_lang.setFixedWidth(130)
        row_lang.addWidget(lbl_lang)
        self.combo_language = QComboBox()
        self.combo_language.setToolTip("Language Ollama writes module and project descriptions in")
        for lang in SUPPORTED_LANGUAGES:
            self.combo_language.addItem(lang, lang)
        saved_lang = str(self.store.get_setting(LANGUAGE_SETTING_KEY, DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
        if self.combo_language.findData(saved_lang) < 0:
            self.combo_language.addItem(saved_lang, saved_lang)
        self.combo_language.setCurrentIndex(self.combo_language.findData(saved_lang))
        row_lang.addWidget(self.combo_language, 1)
        lay_ai.addLayout(row_lang)

        self.chk_disable_ollama = QCheckBox("Don't use Ollama (built-in analysis only)")
        self.chk_disable_ollama.setChecked(bool(self.store.get_setting("disable_ollama", False)))
        self.chk_disable_ollama.setToolTip(
            "When checked, Ollama is never called; Helios only uses its own code analysis."
        )
        lay_ai.addWidget(self.chk_disable_ollama)

        self.chk_auto_purpose = QCheckBox("Suggest project descriptions automatically with Ollama")
        self.chk_auto_purpose.setChecked(bool(self.store.get_setting(AUTO_PURPOSE_SETTING_KEY, True)))
        self.chk_auto_purpose.setToolTip(
            "After a scan, a project without a description gets a suggestion from the local model "
            "(one request per project). It is shown as “Suggested” until you accept, edit or "
            "dismiss it; a description you wrote yourself is never replaced."
        )
        lay_ai.addWidget(self.chk_auto_purpose)

        lay_ai.addSpacing(4)
        lay_ai.addWidget(theme.label("Download a model", "caption"))
        row_pull = QHBoxLayout()
        row_pull.setSpacing(8)
        self.edit_pull = QLineEdit()
        self.edit_pull.setPlaceholderText("e.g. qwen2.5-coder:3b")
        row_pull.addWidget(self.edit_pull, stretch=1)
        self.btn_pull = QPushButton("Download")
        self.btn_pull.clicked.connect(self._start_pull_model)
        row_pull.addWidget(self.btn_pull)
        lay_ai.addLayout(row_pull)

        row_sug = QHBoxLayout()
        row_sug.setSpacing(6)
        row_sug.addWidget(theme.label("Suggestions", "muted"))
        for sug_name in ("qwen2.5-coder:3b", "phi3.5:3.8b", "deepseek-r1:1.5b"):
            s_btn = QPushButton(sug_name)
            s_btn.setObjectName("ChipBtn")
            s_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            s_btn.clicked.connect(lambda chk=False, n=sug_name: self.edit_pull.setText(n))
            row_sug.addWidget(s_btn)
        row_sug.addStretch(1)
        lay_ai.addLayout(row_sug)

        self.lbl_pull_status = theme.label("", "warn", wrap=True)
        self.lbl_pull_status.setVisible(False)
        lay_ai.addWidget(self.lbl_pull_status)
        layout.addWidget(box_ai)

        # --- Integrations ---
        box_conn, lay_conn = self._card(
            "Integrations",
            "Choose which AI coding tools (Antigravity, OpenAI Codex, Claude Code) Helios reads projects from.",
        )
        row_conn = QHBoxLayout()
        row_conn.addStretch(1)
        btn_open_conn = QPushButton("Open integrations")
        btn_open_conn.clicked.connect(self._open_connections_wizard)
        row_conn.addWidget(btn_open_conn)
        lay_conn.addLayout(row_conn)
        layout.addWidget(box_conn)

        # --- projects/ folder ---
        helios_proj_dir = CodebaseImporter.get_helios_projects_dir()
        box_proj, lay_proj = self._card(
            "The 'projects/' folder",
            "Copy project folders here to map them all in one go.",
        )
        lbl_dir = theme.label(str(helios_proj_dir), "mono", wrap=True)
        lbl_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay_proj.addWidget(lbl_dir)
        row_dir = QHBoxLayout()
        row_dir.setSpacing(8)
        row_dir.addStretch(1)
        btn_open_folder = QPushButton("Open folder")
        btn_open_folder.clicked.connect(self._open_helios_projects_folder)
        row_dir.addWidget(btn_open_folder)
        self.btn_scan_batch = QPushButton("Scan folder")
        self.btn_scan_batch.clicked.connect(self._scan_helios_projects_folder)
        row_dir.addWidget(self.btn_scan_batch)
        lay_proj.addLayout(row_dir)
        layout.addWidget(box_proj)

        # --- duplicates ---
        box_dedup, lay_dedup = self._card(
            "Merge duplicates",
            "Combines projects with the same name from different sources and removes identical modules.",
        )
        row_dedup = QHBoxLayout()
        row_dedup.addStretch(1)
        self.btn_dedup = QPushButton("Merge duplicates")
        self.btn_dedup.setToolTip("Merges projects with the same name and duplicate modules or components")
        self.btn_dedup.clicked.connect(self._run_deduplication)
        row_dedup.addWidget(self.btn_dedup)
        lay_dedup.addLayout(row_dedup)
        layout.addWidget(box_dedup)

        # --- ignored folders ---
        box_ign, lay_ign = self._card(
            "Ignored folders",
            "Folders you removed with \u201cnever import this folder again\u201d. Scans skip them.",
        )
        self.ignored_list_layout = QVBoxLayout()
        self.ignored_list_layout.setContentsMargins(0, 0, 0, 0)
        self.ignored_list_layout.setSpacing(4)
        lay_ign.addLayout(self.ignored_list_layout)
        self._refresh_ignored_list()
        layout.addWidget(box_ign)
        layout.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(24, 10, 24, 18)
        footer.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        footer.addWidget(btn_close)
        btn_save = QPushButton("Save settings")
        btn_save.setObjectName("PrimaryBtn")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save_settings_and_close)
        footer.addWidget(btn_save)
        outer.addLayout(footer)

    def _refresh_ignored_list(self) -> None:
        clear_layout(self.ignored_list_layout)
        entries = ignored_folders(self.store)
        self.ignored_rows: List[QWidget] = []
        if not entries:
            self.ignored_list_layout.addWidget(theme.label("No folders are ignored.", "muted"))
            return
        for path in entries:
            row = QFrame()
            row.setObjectName("IgnoredRow")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 4, 4, 4)
            rl.setSpacing(8)
            lbl = ElidedLabel(path, mode=Qt.TextElideMode.ElideMiddle)
            theme.set_role(lbl, "mono")
            rl.addWidget(lbl, 1)
            btn = QPushButton("Stop ignoring")
            btn.setObjectName("QuietBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Let scans import this folder again:\n{path}")
            btn.clicked.connect(lambda checked=False, p=path: self._stop_ignoring(p))
            rl.addWidget(btn)
            row.setProperty("folder", path)
            self.ignored_rows.append(row)
            self.ignored_list_layout.addWidget(row)

    def _stop_ignoring(self, path: str) -> None:
        remove_ignored_folder(self.store, path)
        self._refresh_ignored_list()

    def _open_connections_wizard(self) -> None:
        self.reject()
        p = self.parent()
        if p and hasattr(p, "_switch_to_page"):
            p._switch_to_page("connections")

    def _refresh_models_list(self) -> None:
        """Looks for the installed models on a probe thread; the list is filled when it
        answers (until then the dialog shows "Looking for installed models…" the first
        time, or the previous list)."""
        if self._models_worker is not None:
            self._models_again = True
            return
        self._models_loading = True
        if self.combo_model.count() == 0:
            self.combo_model.addItem("Looking for installed models\u2026", None)
        saved_model = str(self.store.get_setting("selected_ollama_model", "") or "")
        worker = OllamaModelsWorker(saved_model)
        worker.listed.connect(self._on_models_listed)
        self._models_worker = worker
        start_probe_worker(worker)

    @Slot(object, object)
    def _on_models_listed(self, models: object, preferred: object) -> None:
        worker, self._models_worker = self._models_worker, None
        if worker is not None:
            worker.wait(2000)  # `listed` is the last thing run() does
        self._models_loading = False
        self._fill_models_list(list(models or []), preferred if isinstance(preferred, str) else None)
        if self._models_again:
            self._models_again = False
            self._refresh_models_list()

    def _fill_models_list(self, models: List[str], preferred: Optional[str]) -> None:
        self.combo_model.clear()
        saved_model = str(self.store.get_setting("selected_ollama_model", "") or "")
        active_found = False

        if not models:
            self.combo_model.addItem("No installed model found", "")
            return

        for m in models:
            self.combo_model.addItem(m, m)
            if saved_model and m.lower() == saved_model.lower():
                self.combo_model.setCurrentIndex(self.combo_model.count() - 1)
                active_found = True

        if not active_found and self.combo_model.count() > 0:
            idx = self.combo_model.findText(preferred or "")
            if idx >= 0:
                self.combo_model.setCurrentIndex(idx)

    def _start_pull_model(self) -> None:
        target_name = self.edit_pull.text().strip()
        if not target_name:
            QMessageBox.warning(self, "Model name required", "Please enter the model name to download (e.g., qwen2.5-coder:3b).")
            return

        self.btn_pull.setEnabled(False)
        self.lbl_pull_status.setVisible(True)
        self.lbl_pull_status.setText(f"Starting download for '{target_name}'...")

        self._pull_worker = OllamaPullWorker(target_name, parent=self)
        self._pull_worker.progressText.connect(self.lbl_pull_status.setText)
        self._pull_worker.finishedPull.connect(self._on_pull_finished)
        self._pull_worker.start()

    def _on_pull_finished(self, ok: bool, msg: str) -> None:
        self.btn_pull.setEnabled(True)
        self.lbl_pull_status.setText(msg)
        if ok:
            self._refresh_models_list()
            QMessageBox.information(self, "Download complete", msg)
        else:
            QMessageBox.warning(self, "Download failed", msg)

    def _run_deduplication(self) -> None:
        """Merges duplicates on a store worker (the window's busy state owns the store)."""
        worker = MergeDuplicatesWorker(self.store)
        self._scan_running = True
        self.setEnabled(False)
        self.setCursor(Qt.CursorShape.BusyCursor)
        if not start_store_worker(self, worker, self._on_deduplication_finished, "Merging duplicates..."):
            self._scan_running = False
            self.setEnabled(True)
            self.unsetCursor()
            QMessageBox.information(
                self,
                "Scan already running",
                "Another scan is running. Please wait for it to finish.",
            )

    def _on_deduplication_finished(self, ok: bool, msg: str, payload: object) -> None:
        self._scan_running = False
        self.setEnabled(True)
        self.unsetCursor()
        if not ok:
            QMessageBox.warning(self, "Merge failed", msg)
            return
        data = payload if isinstance(payload, dict) else {}
        proj_count, mod_count = int(data.get("projects", 0) or 0), int(data.get("nodes", 0) or 0)

        if proj_count > 0 or mod_count > 0:
            parts = []
            if proj_count > 0:
                parts.append(f"{proj_count} duplicate project(s)")
            if mod_count > 0:
                parts.append(f"{mod_count} module/component node(s)")
            summary = " and ".join(parts)
            QMessageBox.information(
                self,
                "Merge complete",
                f"Successfully merged {summary}!",
            )
        else:
            QMessageBox.information(
                self,
                "No duplicates found",
                "No duplicate projects or modules were found.",
            )

    def _open_helios_projects_folder(self) -> None:
        p_dir = CodebaseImporter.get_helios_projects_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(p_dir))
            else:
                subprocess.Popen(["xdg-open", str(p_dir)])
        except Exception as exc:
            QMessageBox.warning(self, "Could not open folder", f"Error: {exc}")

    def reject(self) -> None:
        # Keep the dialog open while its background scan owns the store.
        if getattr(self, "_scan_running", False):
            return
        super().reject()

    def _scan_helios_projects_folder(self) -> None:
        worker = CodebaseScanWorker(self.store)
        self._scan_running = True
        self.setEnabled(False)
        self.setCursor(Qt.CursorShape.BusyCursor)
        if not start_store_worker(self, worker, self._on_projects_scan_finished, "Scanning the Helios 'projects/' directory..."):
            self._scan_running = False
            self.setEnabled(True)
            self.unsetCursor()
            QMessageBox.information(
                self,
                "Scan already running",
                "Another scan is running. Please wait for it to finish.",
            )

    def _on_projects_scan_finished(self, ok: bool, msg: str, payload: object) -> None:
        self._scan_running = False
        self.setEnabled(True)
        self.unsetCursor()
        if not ok:
            QMessageBox.warning(self, "Scan failed", msg)
            return
        data = payload if isinstance(payload, dict) else {}
        results = data.get("systems") or []
        p_dir = data.get("projects_dir") or CodebaseImporter.get_helios_projects_dir()
        if results:
            QMessageBox.information(
                self,
                "Projects scanned",
                f"Analyzed {len(results)} project(s) in the Helios 'projects/' folder and generated their architecture maps!",
            )
            self.accept()
        else:
            QMessageBox.information(
                self,
                "No projects found",
                f"No project directories were found inside '{p_dir}'.\n\n"
                f"Copy any project folders you want to inspect into this directory.",
            )

    def _save_settings_and_close(self) -> None:
        # While the model list is still loading, the stored model is kept as it is.
        chosen_m = None if self._models_loading else (self.combo_model.currentData() or self.combo_model.currentText())
        if chosen_m:
            self.store.set_setting("selected_ollama_model", str(chosen_m))
        self.store.set_setting("disable_ollama", self.chk_disable_ollama.isChecked())
        self.store.set_setting(AUTO_PURPOSE_SETTING_KEY, self.chk_auto_purpose.isChecked())
        self.store.set_setting(
            LANGUAGE_SETTING_KEY, str(self.combo_language.currentData() or DEFAULT_LANGUAGE)
        )
        self.accept()
