"""`StoreWorkerMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

The window-wide busy state while a store-mutating background worker runs
(`_run_store_worker`, see ui/workers.py), rebuilding the views when it finishes,
and `closeEvent` (waits for running threads).

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _busy_active_id, _busy_lock_widgets, _canvas_freezer, _model_detect_worker,
    _ollama_server_check_worker, _ollama_worker, _selected_node, _shortcuts,
    _store_busy, _store_worker, _store_worker_done_cb, _updating_ui, canvas,
    connections_view, file_watcher, status_label, store
Of those, assigned by this mixin:
    _busy_active_id, _store_busy, _store_worker, _store_worker_done_cb, _updating_ui
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from ui.workers import AgentSyncWorker, CodebaseScanWorker, StoreWorker, wait_for_probe_workers


class StoreWorkerMixin:
    """Busy state of store-mutating background workers and the close handshake."""

    def is_store_busy(self) -> bool:
        return self._store_busy

    def _run_store_worker(self, worker: StoreWorker, on_done=None, busy_text: str = "Working...") -> bool:
        """Runs a store-mutating worker under the window-wide busy state.

        While the worker owns the store:
        - the toolbar, project tree, breadcrumb bar, canvas, inspector panel and all
          window shortcuts (undo/redo/delete, ...) are disabled, so nothing on the UI
          thread can edit/save the store or start another scan; the window itself still
          moves, resizes and repaints, and progress is shown in the status bar;
        - the canvas is frozen (CanvasPaintFreezer: animation timer stopped, updates
          disabled, viewport paint events swallowed) so no paint code iterates node
          lists the worker is replacing;
        - `_on_external_store_changed` ignores file-watcher events, and Ollama results
          are queued until the worker is done;
        - a second worker is rejected (returns False).
        On finish the UI is re-enabled and the tree/canvas/anchor card/breadcrumb are
        rebuilt before `on_done(ok, message, payload)` runs. `closeEvent` waits for a
        running worker instead of destroying it.
        """
        if self._store_worker is not None or self._store_busy:
            self.status_label.setText("Another scan is already running. Please wait for it to finish.")
            return False
        self._store_worker = worker
        self._store_worker_done_cb = on_done
        worker.setParent(self)
        worker.progressText.connect(self._on_store_worker_progress)
        worker.finishedWork.connect(self._on_store_worker_finished)
        self._set_store_busy(True, busy_text)
        worker.start()
        return True

    def _set_store_busy(self, busy: bool, text: str = "") -> None:
        if busy == self._store_busy:
            return
        self._store_busy = busy
        if busy:
            self._busy_active_id = self.canvas.active_system.id if self.canvas.active_system else None
            self._canvas_freezer.freeze()
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BusyCursor))
        for w in self._busy_lock_widgets:
            w.setEnabled(not busy)
        for sc in self._shortcuts:
            sc.setEnabled(not busy)
        if hasattr(self, "connections_view"):
            self.connections_view.set_busy(busy, text)
        if busy:
            if text:
                self.status_label.setText(text)
        else:
            self._canvas_freezer.unfreeze()
            QApplication.restoreOverrideCursor()
            self._schedule_queued_auto_purpose()
        self._update_busy_indicator()

    @Slot(str)
    def _on_store_worker_progress(self, msg: str) -> None:
        self.status_label.setText(msg)

    @Slot(bool, str, object)
    def _on_store_worker_finished(self, ok: bool, msg: str, payload: object) -> None:
        worker = self._store_worker
        on_done = self._store_worker_done_cb
        self._store_worker = None
        self._store_worker_done_cb = None
        was_sync = isinstance(worker, AgentSyncWorker)
        was_projects_scan = isinstance(worker, CodebaseScanWorker) and getattr(worker, "folder", None) is None
        if worker is not None:
            worker.wait(5000)  # finishedWork is the last thing run() does
            worker.deleteLater()

        self._set_store_busy(False)
        self._refresh_after_store_worker()
        self.status_label.setText(msg)
        try:
            if on_done:
                on_done(ok, msg, payload)
        finally:
            self._flush_pending_ollama_results()
            if was_sync and ok:
                self._update_skipped_folders_link()
            if ok and (was_sync or was_projects_scan):
                # Single-folder analyses do this from _on_codebase_folder_analyzed (purpose, then modules).
                self._maybe_auto_suggest_purposes()

    def _refresh_after_store_worker(self) -> None:
        """Rebuilds every store-derived view after a worker changed the store."""
        active = self.canvas.active_system
        if active is None or not any(s is active for s in self.store.systems):
            target = None
            if self._busy_active_id:
                target = next((s for s in self.store.systems if s.id == self._busy_active_id), None)
            if target is None and self.store.systems:
                target = self.store.systems[0]
            self.canvas.active_system = target
            if self.canvas.selected_node is not None and not self._node_in_store(self.canvas.selected_node):
                self.canvas.selected_node = target
        self._updating_ui = True
        try:
            self._populate_tree()
            self.canvas.rebuild_scene()
            self._refresh_sun_anchor_card()
            self._update_breadcrumb()
        finally:
            self._updating_ui = False
        # Inspector edits must never target objects the scan replaced.
        if self._selected_node is not None and not self._node_in_store(self._selected_node):
            if self.canvas.active_system:
                self._load_node_into_inspector(self.canvas.active_system, "sun")
        self._rearm_file_watcher()

    def _rearm_file_watcher(self) -> None:
        if (
            self.store.storage_path.exists()
            and str(self.store.storage_path) not in self.file_watcher.files()
        ):
            self.file_watcher.addPath(str(self.store.storage_path))

    def closeEvent(self, event) -> None:
        """Waits (bounded) for running background threads instead of destroying them."""
        if self._is_ollama_running():
            # Stops between (and during) Ollama requests, so closing is quick.
            self._ollama_worker.requestInterruption()
        threads = [
            t
            for t in (
                self._store_worker,
                self._ollama_worker,
                self._model_detect_worker,
                self._ollama_server_check_worker,
            )
            if t is not None and t.isRunning()
        ]
        # Short read-only probes (tool detection, model lists) are waited for too, so no
        # running QThread is destroyed with the window.
        wait_for_probe_workers(5000)
        if threads:
            self.status_label.setText("Waiting for background tasks to finish before closing...")
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
            try:
                for t in threads:
                    t.wait(15000)
            finally:
                QApplication.restoreOverrideCursor()
            if any(t.isRunning() for t in threads):
                event.ignore()
                self.status_label.setText(
                    "A background task is still running. Helios will close once it finishes — try closing again shortly."
                )
                return
        super().closeEvent(event)
