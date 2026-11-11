"""Background workers for long-running Helios scans, plus the canvas paint freezer.

Why this module exists
----------------------
Agent-history sync, codebase analysis and the ``projects/`` scan used to run on the
UI thread, freezing the window for the whole scan (and the startup scan ran inside
``HeliosMainWindow.__init__`` before the window was even shown). The workers below
run those scans on a ``QThread`` and report back through signals.

Thread-safety contract (the "busy state")
-----------------------------------------
``GalaxyStore`` is a plain Python object that the UI also reads (paint events, the
project tree, the inspector, the file watcher). A store-mutating worker therefore
owns the store exclusively while it runs. The main window enforces this with a
single busy state (``HeliosMainWindow._run_store_worker`` / ``_set_store_busy``):

* only one store-mutating worker may run at a time (a second start is rejected);
* every widget, menu button and shortcut that edits/saves the store or starts
  another scan is disabled; the window itself stays responsive (move, resize,
  repaint, status-bar progress);
* the live file watcher ignores change events;
* the canvas is frozen with :class:`CanvasPaintFreezer` (animation timer stopped,
  updates disabled, and paint events of the viewport swallowed so no item paint
  code walks ``system.planets`` while the worker rebuilds it);
* when the worker finishes (``finishedWork``, delivered on the UI thread through a
  queued connection) the widgets are re-enabled and the tree, canvas, anchor card
  and breadcrumb are rebuilt from the store.

Worker exceptions are caught in :meth:`StoreWorker.run` and reported through
``finishedWork(False, message, payload)`` so call sites can show them.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QEvent, QObject, QThread, Signal, Slot

from connectors.agent_importer import AgentHistoryImporter
from connectors.codebase_importer import CodebaseImporter
from connectors.ollama_enhancer import OllamaEnhancer
from core.models import GalaxyStore

HELIOS_ROOT = Path(__file__).resolve().parent.parent

FinishedCallback = Callable[[bool, str, object], None]


def _has_helios_codebase(store: GalaxyStore) -> bool:
    return any(
        s.source == "codebase" and (s.project_name or "").lower() == "helios"
        for s in list(store.systems)
    )


class StoreWorker(QThread):
    """Base class for workers that mutate the GalaxyStore off the UI thread.

    Subclasses implement :meth:`work`, which returns ``(ok, message, payload)``.
    ``finishedWork`` is always emitted exactly once, also when :meth:`work` raises.
    (The name avoids shadowing ``QThread.finished``.)
    """

    progressText = Signal(str)
    finishedWork = Signal(bool, str, object)  # (ok, message, payload)

    failure_prefix = "Background task failed"

    def __init__(self, store: GalaxyStore, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.result: Optional[tuple] = None

    def work(self) -> tuple:  # pragma: no cover - abstract
        raise NotImplementedError

    def run(self) -> None:
        try:
            ok, message, payload = self.work()
        except Exception as exc:
            payload = {"error": str(exc) or exc.__class__.__name__, "traceback": traceback.format_exc()}
            ok, message = False, f"{self.failure_prefix}: {payload['error']}"
        self.result = (bool(ok), str(message), payload)
        self.finishedWork.emit(bool(ok), str(message), payload)


class AgentSyncWorker(StoreWorker):
    """Everything the onboarding "Save & Apply" / startup scan / Sync button did synchronously.

    Steps (each optional): start the Ollama server, clear the store, scan the selected
    agent histories, analyze the Helios codebase itself if it is not mapped yet.

    Payload: ``{"updated": int, "ollama_started": Optional[bool],
    "helios_system": Optional[SolarSystem], "warnings": List[str], "sources": List[str]}``.
    Non-fatal problems (Ollama did not start, Helios self-analysis failed) are listed in
    ``warnings`` and appended to the message; exceptions from the scan itself fail the task.
    """

    failure_prefix = "Scanning AI session histories failed"

    def __init__(
        self,
        store: GalaxyStore,
        sources: Sequence[str],
        max_sessions: int = 25,
        start_ollama: bool = False,
        reset: bool = False,
        analyze_helios: bool = False,
        helios_dir: Optional[Path] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(store, parent)
        self.sources: List[str] = [str(s) for s in sources]
        self.max_sessions = int(max_sessions)
        self.start_ollama = bool(start_ollama)
        self.reset = bool(reset)
        self.analyze_helios = bool(analyze_helios)
        self.helios_dir = Path(helios_dir) if helios_dir else HELIOS_ROOT

    def work(self) -> tuple:
        payload: Dict[str, object] = {
            "updated": 0,
            "ollama_started": None,
            "helios_system": None,
            "warnings": [],
            "sources": list(self.sources),
        }
        warnings: List[str] = payload["warnings"]  # type: ignore[assignment]

        if self.start_ollama:
            self.progressText.emit("Starting the local Ollama service...")
            try:
                started = bool(OllamaEnhancer.ensure_server_running())
            except Exception as exc:
                started = False
                warnings.append(f"Ollama service could not be started: {exc}")
            else:
                if not started:
                    warnings.append("Ollama service could not be started (ollama serve is not responding).")
            payload["ollama_started"] = started

        if self.reset:
            self.progressText.emit("Clearing the existing graph...")
            self.store.clear_all()

        if self.sources:
            self.progressText.emit("Scanning selected AI session histories and building the architecture graph...")
            payload["updated"] = int(
                AgentHistoryImporter.scan_selected_sources(
                    self.store, list(self.sources), max_sessions=self.max_sessions
                )
                or 0
            )

        if self.analyze_helios and not _has_helios_codebase(self.store):
            self.progressText.emit("Analyzing the Helios codebase...")
            try:
                payload["helios_system"] = CodebaseImporter.analyze_directory(self.helios_dir, self.store)
            except Exception as exc:
                warnings.append(f"Helios codebase analysis failed: {exc}")

        message = f"AI session histories scanned: {payload['updated']} project(s) updated."
        if warnings:
            message += " Warning: " + " ".join(warnings)
        return True, message, payload


class CodebaseScanWorker(StoreWorker):
    """Analyzes one codebase folder (``folder`` given) or the Helios ``projects/`` folder.

    Payload for a folder: ``{"mode": "folder", "folder": Path, "system": Optional[SolarSystem]}``.
    Payload for projects: ``{"mode": "projects", "projects_dir": Path, "systems": List[SolarSystem]}``.
    "Nothing found" is a successful run with an empty result; only exceptions fail.
    """

    failure_prefix = "Codebase analysis failed"

    def __init__(
        self,
        store: GalaxyStore,
        folder: Optional[Path] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(store, parent)
        self.folder = Path(folder) if folder else None

    def work(self) -> tuple:
        if self.folder is not None:
            self.progressText.emit(f"Analyzing codebase '{self.folder.name}'...")
            system = CodebaseImporter.analyze_directory(self.folder, self.store)
            if system is None:
                msg = f"No supported source code or Markdown files were found in '{self.folder.name}'."
            else:
                msg = f"Codebase '{system.project_name}' analyzed."
            return True, msg, {"mode": "folder", "folder": self.folder, "system": system}

        self.progressText.emit("Scanning the Helios 'projects/' directory...")
        projects_dir = CodebaseImporter.get_helios_projects_dir()
        systems = list(CodebaseImporter.scan_helios_projects(self.store) or [])
        msg = f"Analyzed {len(systems)} project(s) in the Helios 'projects/' directory."
        return True, msg, {"mode": "projects", "projects_dir": projects_dir, "systems": systems}


class ModelDetectWorker(QThread):
    """Resolves the preferred Ollama model off the UI thread (HTTP call + disk scan)."""

    detected = Signal(object)  # Optional[str]

    def run(self) -> None:
        try:
            model = OllamaEnhancer.get_preferred_model()
        except Exception:
            model = None
        self.detected.emit(model)


class OllamaServerCheckWorker(QThread):
    """Checks off the UI thread whether the Ollama HTTP API answers (`is_server_running`).

    Used before starting automatic module descriptions so the UI never blocks on the
    HTTP probe. Emits ``checked(bool)`` exactly once. Does not touch the store.
    """

    checked = Signal(bool)

    def __init__(self, timeout: float = 1.5, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.timeout = float(timeout)

    def run(self) -> None:
        try:
            running = bool(OllamaEnhancer.is_server_running(timeout=self.timeout))
        except Exception:
            running = False
        self.checked.emit(running)


class CanvasPaintFreezer(QObject):
    """Freezes a SpaceCanvasView while a worker mutates the store.

    ``setUpdatesEnabled(False)`` stops programmatic repaints, but Qt still delivers
    window-system paint events (expose/resize). This object is therefore also an event
    filter on the canvas viewport that swallows ``QEvent.Paint`` while frozen, so no
    QGraphicsItem paint code (which reads the live node objects) runs during the scan.
    The animation timer is stopped and restarted only if it was active before.
    """

    def __init__(self, canvas, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self.frozen = False
        self._timer_was_active = False
        self.swallowed_paints = 0
        canvas.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if self.frozen and event.type() == QEvent.Type.Paint:
            self.swallowed_paints += 1
            return True
        return False

    def freeze(self) -> None:
        if self.frozen:
            return
        self.frozen = True
        timer = getattr(self.canvas, "timer", None)
        self._timer_was_active = bool(timer is not None and timer.isActive())
        if timer is not None:
            timer.stop()
        self.canvas.setUpdatesEnabled(False)

    def unfreeze(self) -> None:
        if not self.frozen:
            return
        self.frozen = False
        self.canvas.setUpdatesEnabled(True)
        timer = getattr(self.canvas, "timer", None)
        if timer is not None and self._timer_was_active:
            timer.start()
        self.canvas.viewport().update()


class _StandaloneWorkerRelay(QObject):
    """Receives a worker's finished signal on the UI thread when no main window hosts it."""

    def __init__(self, worker: StoreWorker, on_done: Optional[FinishedCallback], parent: QObject) -> None:
        super().__init__(parent)
        self.worker = worker
        self.on_done = on_done
        worker.finishedWork.connect(self._on_finished)

    @Slot(bool, str, object)
    def _on_finished(self, ok: bool, message: str, payload: object) -> None:
        self.worker.wait(5000)
        cb = self.on_done
        self.on_done = None
        self.worker.deleteLater()
        self.deleteLater()
        if cb:
            cb(ok, message, payload)


def find_worker_host(widget: Optional[QObject]) -> Optional[QObject]:
    """Returns the nearest ancestor (or Qt parent) that owns the store busy state."""
    obj = widget
    while obj is not None:
        if hasattr(obj, "_run_store_worker"):
            return obj
        obj = obj.parent()
    return None


def start_store_worker(
    widget: QObject,
    worker: StoreWorker,
    on_done: Optional[FinishedCallback] = None,
    busy_text: str = "Working...",
) -> bool:
    """Starts `worker` through the hosting main window's busy state if there is one.

    Returns False if the host rejected it (another store worker is running).
    Without a host (e.g. a standalone dialog) the worker runs on its own.
    """
    host = find_worker_host(widget)
    if host is not None:
        return bool(host._run_store_worker(worker, on_done, busy_text))
    worker.setParent(widget)
    _StandaloneWorkerRelay(worker, on_done, widget)
    worker.start()
    return True
