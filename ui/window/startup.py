"""`StartupMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

Startup tasks (deferred sync, Ollama model detection, data-file warning, migration
notice), first-run onboarding, switching between the map and the integrations page,
the settings dialog and the visibility of the AI controls.

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _detected_ollama_model, _migration_notice, _migration_notice_due,
    _model_detect_worker, _startup_tasks_scheduled, _updating_ui, btn_connections,
    btn_ollama_single, canvas, central_stack, connections_view, lbl_ollama_status,
    status_label, store
Of those, assigned by this mixin:
    _detected_ollama_model, _migration_notice, _migration_notice_due,
    _model_detect_worker, _startup_tasks_scheduled, _updating_ui
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from ui.window.settings_dialog import HeliosSettingsDialog
from ui.workers import AgentSyncWorker, HELIOS_ROOT, ModelDetectWorker


class StartupMixin:
    """Startup tasks, onboarding, the integrations page switch, settings and AI controls."""

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._startup_tasks_scheduled:
            # Runs after the window is visible with the previously saved graph.
            self._startup_tasks_scheduled = True
            QTimer.singleShot(200, self._initial_auto_scan)
            if not self._detected_ollama_model:
                QTimer.singleShot(400, self._detect_ollama_model_async)

    def _store_load_error_text(self) -> str:
        err = getattr(self.store, "load_error", None)
        if not err:
            return ""
        backup = getattr(self.store, "backup_path", None)
        text = (
            f"Helios could not read its data file:\n{self.store.storage_path}\n\n"
            f"{err}\n\n"
            "Nothing was overwritten."
        )
        if backup:
            text += f"\nA backup of the unreadable file is kept at:\n{backup}"
        return text

    def _warn_store_load_error(self) -> None:
        text = self._store_load_error_text()
        if text:
            QMessageBox.warning(self, "Data file could not be read", text)

    def _detect_ollama_model_async(self) -> None:
        if self._detected_ollama_model or self._model_detect_worker is not None:
            return
        worker = ModelDetectWorker(self)
        worker.detected.connect(self._on_ollama_model_detected)
        self._model_detect_worker = worker
        worker.start()

    @Slot(object)
    def _on_ollama_model_detected(self, model: object) -> None:
        worker = self._model_detect_worker
        self._model_detect_worker = None
        if worker is not None:
            worker.wait(2000)
            worker.deleteLater()
        if model and not self._detected_ollama_model:
            self._detected_ollama_model = str(model)
            self._refresh_ai_controls()
            self._update_ollama_button_tooltip()
            if not self._is_ollama_running():
                self.btn_ollama_single.setText(self._ollama_button_label())

    def _check_first_run_onboarding(self) -> None:
        """Opens the full-window integrations view inside the main window on first launch."""
        if not self.store.get_setting("onboarding_completed", False):
            self._switch_to_page("connections")

    def _open_connections_dialog(self, is_first_run: bool = False) -> None:
        """Switches to the full-window integrations view inside the main window."""
        self._switch_to_page("connections")

    def _on_connections_finished(self) -> None:
        self._updating_ui = True
        self._populate_tree()
        self.canvas.rebuild_scene()
        self._refresh_sun_anchor_card()
        self._update_breadcrumb()
        self._updating_ui = False
        self._switch_to_page("studio")
        self.status_label.setText("AI integrations updated and architecture graph synchronized.")

    def _on_connections_cancelled(self) -> None:
        self._switch_to_page("studio")
        self.status_label.setText("Architecture Canvas — Codebase View.")

    def _toggle_connections_page(self) -> None:
        if hasattr(self, "central_stack") and self.central_stack.currentIndex() == 1:
            self._switch_to_page("studio")
        else:
            self._switch_to_page("connections")

    def _switch_to_page(self, page_name: str) -> None:
        if not hasattr(self, "central_stack"):
            return
        if page_name == "connections":
            self.btn_connections.setText("Back to map")
            self.btn_connections.setChecked(True)
            self.connections_view.refresh_state()
            self.central_stack.setCurrentIndex(1)
            self.status_label.setText("Integrations · choose the tools you want to connect.")
        else:
            self.btn_connections.setText("Integrations")
            self.btn_connections.setChecked(False)
            self.central_stack.setCurrentIndex(0)
            self.status_label.setText("Map view.")

    def _show_migration_notice(self) -> None:
        """One quiet status line after loading data written before format version 2. Without
        Ollama suggestions are not shown, so the notice waits until Ollama is available."""
        if not self._ai_available():
            self._migration_notice_due = bool(getattr(self, "_migration_notice", ""))
            return
        self._migration_notice_due = False
        notice, self._migration_notice = getattr(self, "_migration_notice", ""), ""
        if notice:
            self.status_label.setText(notice)
            self.status_label.setToolTip(notice)

    def _initial_auto_scan(self) -> None:
        if not self._initial_auto_scan_started():
            QTimer.singleShot(0, self._show_migration_notice)

    def _initial_auto_scan_started(self) -> bool:
        """Starts the startup sync; False when none runs (the caller then shows notices)."""
        if not self.store.get_setting("onboarding_completed", False):
            # Wait for the user to complete the first-run integrations view
            return False

        if getattr(self.store, "load_error", None):
            # Never rescan into (and save over) a store whose data file could not be read.
            return False

        # Runs after the window is shown (see showEvent) on a background worker.
        connected = self.store.get_setting("connected_sources", ["antigravity", "codebase"])
        sources = list(connected) if isinstance(connected, list) else []
        if not sources:
            return False
        worker = AgentSyncWorker(
            self.store,
            sources,
            max_sessions=25,
            analyze_helios="codebase" in sources,
            helios_dir=HELIOS_ROOT,
        )
        return bool(self._run_store_worker(
            worker, self._on_initial_auto_scan_finished, "Synchronizing connected AI session histories..."
        ))

    def _on_initial_auto_scan_finished(self, ok: bool, msg: str, payload: object) -> None:
        if ok:
            self.status_label.setText(f"Startup sync finished. {msg}")
        else:
            self.status_label.setText(msg)
        if getattr(self, "_migration_notice", ""):
            QTimer.singleShot(2500, self._show_migration_notice)

    def _get_model_status_text(self) -> str:
        """Status-bar text about the local model; "" (label hidden) when Ollama is off or
        no model was found, so a computer without Ollama shows no AI hints at all."""
        if not self._ai_available():
            return ""
        return f"Ollama: {self._detected_ollama_model}"

    def _set_ollama_status(self, text: str) -> None:
        if not hasattr(self, "lbl_ollama_status"):
            return
        self.lbl_ollama_status.setText(text)
        self.lbl_ollama_status.setVisible(bool(text))

    def _refresh_ai_controls(self) -> None:
        """Shows the AI controls only when Ollama is enabled and a model was found (the
        "Enhance with Ollama" button stays visible as "Stop Ollama" while a run is active)."""
        available = self._ai_available()
        if hasattr(self, "btn_ollama_single"):
            self.btn_ollama_single.setVisible(available or self._is_ollama_running())
        if hasattr(self, "canvas"):
            changed = self.canvas.ai_available != available
            self.canvas.ai_available = available
            if changed:
                self.canvas.refresh_project_texts()  # cards show suggestions only with Ollama
        if available and getattr(self, "_migration_notice_due", False):
            self._show_migration_notice()
        if not self._is_ollama_running():
            self._set_ollama_status(self._get_model_status_text())
        if hasattr(self, "lbl_purpose") and hasattr(self, "canvas"):
            self._refresh_purpose_block(self.canvas.active_system)

    def _open_settings_dialog(self) -> None:
        dlg = HeliosSettingsDialog(self.store, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            saved_m = self.store.get_setting("selected_ollama_model")
            if saved_m:
                self._detected_ollama_model = str(saved_m)
            self._refresh_ai_controls()
            self._update_ollama_button_tooltip()
            self._populate_tree()
            self.canvas.rebuild_scene()
            self._refresh_sun_anchor_card()
            self.status_label.setText("Settings saved.")
