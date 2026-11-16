"""`HeliosMainWindow`: the Helios main window, assembled from responsibility mixins.

`__init__` (state, construction order, startup timers) stays here; every other method
lives in exactly one mixin module of this package and was moved there verbatim from
the former single-file ui/main_window.py. The mixins are plain classes listed before
`QMainWindow`, so `super()` inside them (``showEvent`` / ``closeEvent``) still reaches Qt,
and patching a method on the class or an instance works as before.

Mixin map (MRO order; each module's docstring lists the window attributes it uses):
   * startup.py           StartupMixin: Startup tasks, onboarding, the integrations page switch, settings and AI controls.
   * store_worker.py      StoreWorkerMixin: Busy state of store-mutating background workers and the close handshake.
   * history.py           HistoryMixin: Keyboard shortcuts, undo / redo and live reload of galaxy.json.
   * layout.py            LayoutMixin: Window construction and small UI helpers.
   * header.py            ProjectHeaderMixin: The pinned project header (construction, refresh, the “…” menu).
   * purpose.py           PurposeMixin: The project purpose block, its editor and suggestion actions.
   * tree.py              ProjectTreeMixin: The left project tree.
   * navigation.py        NavigationMixin: Canvas signals, breadcrumb, search, filter, export, tour and view toggles.
   * inspector_panel.py   InspectorMixin: The inspector body: rendering the selection and the edit section's state.
   * node_edits.py        NodeEditsMixin: Saving, deleting and adding nodes; designing a new project.
   * projects.py          ProjectActionsMixin: Project-level actions: remove, merge, analyze, scan, sync, open, copy as prompt.
   * ollama_runs.py       OllamaMixin: Ollama runs, automatic purpose suggestions and applying their results.

Construction order (unchanged): `LayoutMixin._build_ui` builds the top bar, sidebar,
canvas, inspector (`ProjectHeaderMixin._build_project_header`,
`InspectorMixin._build_inspector_body`), central stack and status bar; then the paint
freezer, shortcuts, file watcher, tree, header, AI controls and breadcrumb follow.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QShortcut
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget

from connectors.ollama_enhancer import OllamaEnhanceWorker
from core.models import GalaxyStore
from ui import theme
from ui.workers import CanvasPaintFreezer, ModelDetectWorker, OllamaServerCheckWorker, StoreWorker

from ui.window.startup import StartupMixin
from ui.window.store_worker import StoreWorkerMixin
from ui.window.history import HistoryMixin
from ui.window.layout import LayoutMixin
from ui.window.header import ProjectHeaderMixin
from ui.window.purpose import PurposeMixin
from ui.window.tree import ProjectTreeMixin
from ui.window.navigation import NavigationMixin
from ui.window.inspector_panel import InspectorMixin
from ui.window.node_edits import NodeEditsMixin
from ui.window.projects import ProjectActionsMixin
from ui.window.ollama_runs import OllamaMixin


class HeliosMainWindow(
    StartupMixin,
    StoreWorkerMixin,
    HistoryMixin,
    LayoutMixin,
    ProjectHeaderMixin,
    PurposeMixin,
    ProjectTreeMixin,
    NavigationMixin,
    InspectorMixin,
    NodeEditsMixin,
    ProjectActionsMixin,
    OllamaMixin,
    QMainWindow,
):
    """Helios Desktop Application Main Window."""

    def __init__(self, store: Optional[GalaxyStore] = None) -> None:
        super().__init__()
        self.store = store if store is not None else GalaxyStore()
        self.setWindowTitle("Helios")
        self.resize(1560, 930)
        theme.apply_app_theme(QApplication.instance())

        self._selected_node: Optional[object] = None
        self._selected_type: str = "sun"
        self._updating_ui = False
        self.tree_group_mode: str = "project"
        self._ollama_worker: Optional[OllamaEnhanceWorker] = None

        # Busy state: see `_run_store_worker` / ui/workers.py module docstring.
        self._store_busy = False
        self._store_worker: Optional[StoreWorker] = None
        self._store_worker_done_cb = None
        self._busy_active_id: Optional[str] = None
        self._pending_ollama_results: List[tuple] = []
        self._pending_ollama_finish: Optional[tuple] = None
        self._pending_ollama_module_results: List[tuple] = []
        self._pending_ollama_purpose_results: List[tuple] = []
        # Purpose results: (purpose, purpose_source) of each project when the run started,
        # and the ids whose user-written purpose the user agreed to replace.
        self._purpose_originals: Dict[str, str] = {}  # system id -> evidence key at the start
        self._purpose_rejections: List[tuple] = []  # (system id, message) of the current run
        self._ollama_stop_requested = False
        self._purpose_run: Optional[dict] = None
        self._auto_purpose_queued = False
        self._auto_purpose_then_modules: Optional[str] = None
        self._ollama_applied = 0
        # Ignore-list entries added by "Remove and never import" (undo stack depth, path).
        self._ignore_undo: List[tuple] = []
        self._ignore_redo: List[tuple] = []
        self._ollama_server_check_worker: Optional[OllamaServerCheckWorker] = None
        self._model_detect_worker: Optional[ModelDetectWorker] = None
        self._startup_tasks_scheduled = False
        self._shortcuts: List[QShortcut] = []
        self._busy_lock_widgets: List[QWidget] = []

        # The preferred-model lookup (HTTP + disk scan) is deferred until after the
        # window is shown (`_detect_ollama_model_async`) so startup never blocks on it.
        saved_m = self.store.get_setting("selected_ollama_model")
        self._detected_ollama_model: Optional[str] = str(saved_m) if saved_m else None

        self._skipped_details: List[tuple] = []
        self._skipped_dialog: Optional[QDialog] = None
        self._edit_dirty = False
        self._dot_icon_cache: Dict[str, QIcon] = {}

        self._build_ui()
        self._canvas_freezer = CanvasPaintFreezer(self.canvas, self)
        self._setup_shortcuts()
        self._setup_live_file_watcher()
        self._populate_tree()
        self._refresh_sun_anchor_card()
        self._refresh_ai_controls()
        self._update_breadcrumb()
        migrated = int(getattr(self.store, "migrated_purposes", 0) or 0)
        # Older data stored Ollama's answers as purposes; they are suggestions now. Shown
        # once, after the startup sync (`_show_migration_notice`), so it is not overwritten.
        self._migration_notice = (
            f"{migrated} project description{'s' if migrated != 1 else ''} written by Ollama "
            f"{'are' if migrated != 1 else 'is'} now shown as \u201cSuggested\u201d until you accept "
            f"{'them' if migrated != 1 else 'it'}." if migrated else ""
        )

        if self.canvas.active_system:
            self._load_node_into_inspector(self.canvas.active_system, "sun")
        else:
            self._clear_inspector()

        QTimer.singleShot(150, self._check_first_run_onboarding)
        if getattr(self.store, "load_error", None):
            QTimer.singleShot(0, self._warn_store_load_error)
