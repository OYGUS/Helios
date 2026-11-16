"""`OllamaMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

Everything that starts or receives Ollama background work: automatic purpose
suggestions after scans, “Enhance with Ollama” runs and module descriptions, the
server checks, applying queued results on the UI thread and the run's finish.
Tests that fake the workers patch `OllamaEnhanceWorker` / `OllamaServerCheckWorker`
on THIS module (it is the only reader of both names).

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _auto_purpose_queued, _auto_purpose_then_modules, _detected_ollama_model,
    _ollama_applied, _ollama_server_check_worker, _ollama_skipped,
    _ollama_stop_requested, _ollama_worker, _pending_ollama_finish,
    _pending_ollama_module_results, _pending_ollama_purpose_results,
    _pending_ollama_results, _purpose_originals, _purpose_rejections, _purpose_run,
    _purpose_run_manual, _selected_node, _selected_type, _store_busy, _updating_ui,
    btn_ollama_all, btn_ollama_single, btn_status_stop, busy_bar, canvas, status_label,
    store
Of those, assigned by this mixin:
    _auto_purpose_queued, _auto_purpose_then_modules, _ollama_applied,
    _ollama_server_check_worker, _ollama_skipped, _ollama_stop_requested,
    _ollama_worker, _pending_ollama_finish, _pending_ollama_module_results,
    _pending_ollama_purpose_results, _pending_ollama_results, _purpose_originals,
    _purpose_rejections, _purpose_run, _purpose_run_manual, _updating_ui
"""

from __future__ import annotations

import inspect
import re
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QMessageBox

from connectors.ollama_enhancer import (
    DEFAULT_LANGUAGE,
    LANGUAGE_SETTING_KEY,
    OllamaEnhanceWorker,
)
from connectors.purpose_suggestion import (
    PROTECTED_SOURCES as PROTECTED_PURPOSE_SOURCES,
    check_suggestion_text,
    classify_purpose_failure,
    current_evidence_key,
    needs_auto_suggestion,
    purpose_failure_detail,
    set_purpose_suggestion,
)
from core.models import PlanetNode, SolarSystem
from ui.window.project_text import AUTO_PURPOSE_SETTING_KEY
from ui.workers import ModelDetectWorker, OllamaServerCheckWorker, start_probe_worker


class OllamaMixin:
    """Ollama runs, automatic purpose suggestions and applying their results."""

    def _eligible_purpose_systems(self) -> List[SolarSystem]:
        """Projects an automatic run asks about (`purpose_suggestion.needs_auto_suggestion`)."""
        return [s for s in self.store.systems if needs_auto_suggestion(s)]

    def _auto_purpose_allowed(self) -> bool:
        return self._ai_available() and bool(self.store.get_setting(AUTO_PURPOSE_SETTING_KEY, True))

    def _maybe_auto_suggest_purposes(self, then_modules_id: Optional[str] = None) -> None:
        """After a successful scan: ask Ollama for purpose suggestions (one request per project
        that `needs_auto_suggestion`). The answers are suggestions, never purposes.

        Needs Ollama enabled, a known model, the "suggest automatically" setting and a server
        that answers (checked off the UI thread). Queued while a scan, another Ollama run or
        a server check is active. ``then_modules_id``: describe that project's modules after.
        """
        if not self._auto_purpose_allowed():
            self._continue_with_module_descriptions(then_modules_id)
            return
        if self._store_busy or self._is_ollama_running() or self._ollama_server_check_worker is not None:
            self._auto_purpose_queued = True
            if then_modules_id:
                self._auto_purpose_then_modules = then_modules_id
            return
        if not self._eligible_purpose_systems():
            self._continue_with_module_descriptions(then_modules_id)
            return
        self._auto_purpose_queued = False
        checker = OllamaServerCheckWorker(parent=self)
        checker.checked.connect(
            lambda running, sid=then_modules_id: self._on_auto_purpose_server_checked(running, sid)
        )
        self._ollama_server_check_worker = checker
        checker.start()

    def _on_auto_purpose_server_checked(self, running: bool, then_modules_id: Optional[str]) -> None:
        checker = self._ollama_server_check_worker
        self._ollama_server_check_worker = None
        if checker is not None:
            checker.wait(2000)
            checker.deleteLater()
        if not running:
            self._schedule_queued_auto_purpose()
            return
        if self._store_busy or self._is_ollama_running():
            self._auto_purpose_queued = True
            if then_modules_id:
                self._auto_purpose_then_modules = then_modules_id
            return
        if not self._auto_purpose_allowed():
            return
        targets = self._eligible_purpose_systems()
        started = bool(targets) and self._start_ollama_worker(
            targets, str(self._detected_ollama_model), force=False, summarize_project=False,
            write_purpose=True, purpose_only=True, purpose_then_modules=then_modules_id,
        )
        if not started:
            self._continue_with_module_descriptions(then_modules_id)

    def _continue_with_module_descriptions(self, system_id: Optional[str]) -> None:
        if not system_id:
            return
        system = next((s for s in self.store.systems if s.id == system_id), None)
        if system is not None:
            self._maybe_auto_describe_modules(system)

    def _schedule_queued_auto_purpose(self) -> None:
        if getattr(self, "_auto_purpose_queued", False):
            QTimer.singleShot(0, self._run_queued_auto_purpose)

    def _run_queued_auto_purpose(self) -> None:
        if not self._auto_purpose_queued:
            return
        if self._store_busy or self._is_ollama_running() or self._ollama_server_check_worker is not None:
            return  # stays queued; retried when that work ends
        then_id = self._auto_purpose_then_modules
        self._auto_purpose_queued = False
        self._auto_purpose_then_modules = None
        self._maybe_auto_suggest_purposes(then_id)

    def _purpose_status_text(self) -> str:
        run = self._purpose_run or {}
        total = int(run.get("total", 0) or 0)
        if total == 1 and run.get("names"):
            return f"Ollama is suggesting a description for '{run['names'][0]}'…"
        current = min(total, max(int(run.get("done", 0)), int(run.get("current", 0))))
        return f"Ollama is suggesting project descriptions ({current}/{total})…"

    def _enhance_active_project_with_ollama(self) -> None:
        if not self.canvas.active_system:
            return
        self._run_ollama_for_systems([self.canvas.active_system])

    def _enhance_all_projects_with_ollama(self) -> None:
        if not self.store.systems:
            return
        self._run_ollama_for_systems(list(self.store.systems))

    def _ollama_button_label(self) -> str:
        return "Enhance with Ollama"

    def _update_ollama_button_tooltip(self) -> None:
        if not hasattr(self, "btn_ollama_single"):
            return
        model = self._detected_ollama_model
        base = "Let a local Ollama model describe this project and its modules (click again to stop)."
        self.btn_ollama_single.setToolTip(f"{base}\nModel: {model}" if model else base)

    def _update_busy_indicator(self) -> None:
        if hasattr(self, "busy_bar"):
            self.busy_bar.setVisible(bool(self._store_busy or self._is_ollama_running()))
        if hasattr(self, "btn_status_stop"):
            self.btn_status_stop.setVisible(self._is_ollama_running())

    def _is_ollama_running(self) -> bool:
        worker = self._ollama_worker
        return bool(worker is not None and hasattr(worker, "isRunning") and worker.isRunning())

    def _on_ollama_button_clicked(self) -> None:
        """Starts Ollama for the active project, or stops the run in progress ("Stop Ollama")."""
        if self._is_ollama_running():
            self._stop_ollama()
            return
        self._enhance_active_project_with_ollama()

    def _stop_ollama(self) -> None:
        if not self._is_ollama_running():
            return
        self._ollama_worker.requestInterruption()
        self.btn_ollama_single.setEnabled(False)
        self._ollama_stop_requested = True
        self._auto_purpose_queued = False  # a stop also cancels descriptions waiting to start
        self._auto_purpose_then_modules = None
        self.status_label.setText("Stopping Ollama... results produced so far are kept.")

    def _run_ollama_for_systems(self, systems: List[SolarSystem]) -> None:
        if self.store.get_setting("disable_ollama", False):
            QMessageBox.information(
                self,
                "Ollama is turned off",
                "Ollama is turned off in Settings (\"Don't use Ollama\").\n"
                "Helios determines project summaries using its own rule-based deterministic engine.",
            )
            return

        if self._ollama_worker and self._ollama_worker.isRunning():
            QMessageBox.information(
                self,
                "Ollama is already running",
                "Ollama is already describing a project in the background.",
            )
            return

        if self._store_busy:
            self.status_label.setText("A scan is running. Start Ollama once it has finished.")
            return

        model = self._detected_ollama_model
        if not model:
            # The preferred-model lookup (HTTP + disk scan) runs on a probe thread; the run
            # continues when it answers.
            if getattr(self, "_run_model_worker", None) is not None:
                return  # already looking; that run continues when the model is known
            worker = ModelDetectWorker()
            worker.detected.connect(self._on_run_model_detected)
            self._run_model_worker = worker
            self._run_model_systems = list(systems)
            self.status_label.setText("Looking for an installed Ollama model...")
            start_probe_worker(worker)
            return
        self._continue_ollama_run(systems, model)

    @Slot(object)
    def _on_run_model_detected(self, model: object) -> None:
        worker, self._run_model_worker = getattr(self, "_run_model_worker", None), None
        systems, self._run_model_systems = getattr(self, "_run_model_systems", []), []
        if worker is not None:
            worker.wait(2000)  # `detected` is the last thing run() does
        if self._ollama_worker and self._ollama_worker.isRunning():
            return
        if self._store_busy:
            self.status_label.setText("A scan is running. Start Ollama once it has finished.")
            return
        self._continue_ollama_run(systems, model)

    def _continue_ollama_run(self, systems: List[SolarSystem], model: object) -> None:
        """The rest of `_run_ollama_for_systems` once the model is known."""
        model = str(model) if model else ""
        if not model:
            QMessageBox.warning(
                self,
                "No Ollama model found",
                "No installed model was found on this computer.\n"
                "You can download one in Settings.",
            )
            return

        force = False
        if any(str(getattr(p, "ai_summary", "") or "").strip() for s in systems for p in s.planets):
            answer = QMessageBox.question(
                self,
                "Write the AI descriptions again?",
                "Some modules already have an AI description.\n\n"
                "Yes: regenerate the descriptions of all modules.\n"
                "No: only describe modules that do not have one yet.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            force = answer == QMessageBox.StandardButton.Yes

        self.status_label.setText(f"Preparing Ollama ({model}) service...")
        # A full run also fills missing project purposes (never a user-written one).
        self._start_ollama_worker(systems, model, force=force, summarize_project=True, write_purpose=True)

    @staticmethod
    def _ollama_worker_params() -> "tuple[set, bool]":
        """Constructor parameters of the current OllamaEnhanceWorker (names, accepts **kwargs)."""
        try:
            sig = inspect.signature(OllamaEnhanceWorker)
        except (TypeError, ValueError):
            return set(), False
        names = {n for n, prm in sig.parameters.items()
                 if prm.kind not in (prm.VAR_POSITIONAL, prm.VAR_KEYWORD)}
        var_kw = any(prm.kind == prm.VAR_KEYWORD for prm in sig.parameters.values())
        return names, var_kw

    def _start_ollama_worker(
        self,
        systems: List[SolarSystem],
        model: str,
        force: bool = False,
        summarize_project: bool = True,
        write_purpose: Optional[bool] = None,
        purpose_only: bool = False,
        purpose_then_modules: Optional[str] = None,
        purpose_manual: bool = False,
    ) -> bool:
        """Creates and starts the Ollama worker (snapshots are taken here, on the UI thread).

        ``write_purpose`` / ``purpose_only`` / ``purpose_manual`` are passed only if the
        worker accepts them; a purpose-only run is not started with a worker that cannot do
        it. Purpose answers are only ever stored as suggestions. Returns True if a worker
        was started.
        """
        names, var_kw = self._ollama_worker_params()
        extra: Dict[str, object] = {}
        if write_purpose is not None and ("write_purpose" in names or var_kw):
            extra["write_purpose"] = bool(write_purpose)
        if purpose_only:
            if "purpose_only" not in names and not var_kw:
                self.status_label.setText(
                    "This version of the Ollama helper cannot suggest project descriptions yet."
                )
                return False
            extra["purpose_only"] = True
        if purpose_manual and ("purpose_manual" in names or var_kw):
            extra["purpose_manual"] = True

        if hasattr(self, "btn_ollama_single"):
            # While running, the Ollama button becomes "Stop Ollama".
            self.btn_ollama_single.setText("Stop Ollama")
            self.btn_ollama_single.setEnabled(True)
        if hasattr(self, "btn_ollama_all"):
            self.btn_ollama_all.setEnabled(False)

        # The worker snapshots the systems here, on the UI thread, and only emits new
        # core plans / module texts / purpose suggestions; they are applied on the UI thread
        # (_on_ollama_system_enhanced / _on_ollama_module_described / _on_ollama_purpose_suggested).
        # A suggestion is dropped when the project's evidence changed while Ollama was working.
        self._purpose_originals = {s.id: current_evidence_key(s) for s in systems}
        self._purpose_rejections = []
        self._ollama_stop_requested = False
        self._ollama_applied = 0
        self._purpose_run = None
        self._purpose_run_manual = bool(purpose_manual)
        if purpose_only:
            self._purpose_run = {
                "total": len(systems), "done": 0, "current": 0,
                "names": [s.project_name or s.name for s in systems],
                "then_modules": purpose_then_modules,
                "manual": bool(purpose_manual),
            }
        language = str(self.store.get_setting(LANGUAGE_SETTING_KEY, DEFAULT_LANGUAGE) or DEFAULT_LANGUAGE)
        self._ollama_worker = OllamaEnhanceWorker(
            systems,
            model_name=model,
            parent=self,
            force=force,
            language=language,
            summarize_project=summarize_project,
            **extra,
        )
        self._ollama_worker.progressText.connect(self._on_ollama_progress)
        self._ollama_worker.systemEnhanced.connect(self._on_ollama_system_enhanced)
        self._ollama_worker.moduleDescribed.connect(self._on_ollama_module_described)
        if hasattr(self._ollama_worker, "purposeSuggested"):
            self._ollama_worker.purposeSuggested.connect(self._on_ollama_purpose_suggested)
        if hasattr(self._ollama_worker, "purposeRejected"):
            self._ollama_worker.purposeRejected.connect(self._on_ollama_purpose_rejected)
        self._ollama_worker.finishedEnhance.connect(self._on_ollama_finished)
        self._ollama_worker.start()
        if self._purpose_run is not None:
            self.status_label.setText(self._purpose_status_text())
        self._update_busy_indicator()
        self._refresh_purpose_block(self.canvas.active_system)
        return True

    def _maybe_auto_describe_modules(self, system: SolarSystem) -> None:
        """After a single-folder analysis: describe its modules in the background if possible.

        Requires Ollama to be enabled, a known model and a responding server; the server
        check runs off the UI thread (OllamaServerCheckWorker). Never used after bulk syncs.
        """
        if self.store.get_setting("disable_ollama", False) or not self._detected_ollama_model:
            return
        if self._is_ollama_running() or self._ollama_server_check_worker is not None:
            return
        if not any(p for p in system.planets):
            return
        system_id = system.id
        checker = OllamaServerCheckWorker(parent=self)
        checker.checked.connect(lambda running, sid=system_id: self._on_auto_describe_server_checked(sid, running))
        self._ollama_server_check_worker = checker
        checker.start()

    def _on_auto_describe_server_checked(self, system_id: str, running: bool) -> None:
        checker = self._ollama_server_check_worker
        self._ollama_server_check_worker = None
        if checker is not None:
            checker.wait(2000)
            checker.deleteLater()
        self._schedule_queued_auto_purpose()
        if not running or self._store_busy or self._is_ollama_running():
            return
        if self.store.get_setting("disable_ollama", False) or not self._detected_ollama_model:
            return
        system = next((s for s in self.store.systems if s.id == system_id), None)
        if system is None:
            return
        self._start_ollama_worker([system], self._detected_ollama_model, force=False, summarize_project=False,
                                  write_purpose=False)
        self.status_label.setText(
            f"Ollama ({self._detected_ollama_model}) is describing the modules of "
            f"'{system.project_name or system.name}' in the background. Click 'Stop Ollama' to stop."
        )

    @Slot(str)
    def _on_ollama_progress(self, msg: str) -> None:
        if self._purpose_run is not None:
            m = re.search(r"\((\d+)\s*/\s*(\d+)\)", str(msg))
            if m:
                self._purpose_run["current"] = max(0, int(m.group(1)) - 1)
            msg = self._purpose_status_text()
        if not self._store_busy:
            self.status_label.setText(msg)
        self._set_ollama_status(msg)

    @Slot(str, str)
    def _on_ollama_purpose_rejected(self, system_id: str, message: str) -> None:
        self._purpose_rejections.append((str(system_id), str(message or "")))
        if self._purpose_run is not None:
            self._purpose_run["done"] = int(self._purpose_run.get("done", 0)) + 1

    @Slot(str, str)
    def _on_ollama_purpose_suggested(self, system_id: str, text: str) -> None:
        self._pending_ollama_purpose_results.append((str(system_id), str(text or "")))
        if self._purpose_run is not None:
            self._purpose_run["done"] = int(self._purpose_run.get("done", 0)) + 1
            if not self._store_busy:
                self.status_label.setText(self._purpose_status_text())
        if not self._store_busy:
            self._apply_pending_ollama_results()

    @Slot(str, str)
    def _on_ollama_system_enhanced(self, system_id: str, new_plan: str) -> None:
        worker = self._ollama_worker
        snap = worker.snapshot_for(system_id) if worker is not None else None
        original_plan = snap.core_plan if snap is not None else None
        self._pending_ollama_results.append((system_id, original_plan, new_plan))
        if not self._store_busy:
            self._apply_pending_ollama_results()

    @Slot(str, str, str)
    def _on_ollama_module_described(self, system_id: str, planet_id: str, text: str) -> None:
        worker = self._ollama_worker
        ps = None
        if worker is not None and hasattr(worker, "planet_snapshot_for"):
            ps = worker.planet_snapshot_for(system_id, planet_id)
        original_title = ps.title if ps is not None else None
        self._pending_ollama_module_results.append((system_id, planet_id, original_title, text))
        if not self._store_busy:
            self._apply_pending_ollama_results()

    def _apply_pending_ollama_results(self) -> int:
        """Applies queued Ollama results on the UI thread; returns how many were skipped."""
        skipped = 0
        pending, self._pending_ollama_results = self._pending_ollama_results, []
        for system_id, original_plan, new_plan in pending:
            target = next((s for s in self.store.systems if s.id == system_id), None)
            if target is None or (original_plan is not None and target.core_plan != original_plan):
                # Project deleted or its summary edited/rescanned while Ollama was running.
                skipped += 1
                continue
            target.core_plan = new_plan
            target.ollama_enhanced = True
        module_pending, self._pending_ollama_module_results = self._pending_ollama_module_results, []
        for system_id, planet_id, original_title, text in module_pending:
            target = next((s for s in self.store.systems if s.id == system_id), None)
            planet = next((p for p in target.planets if p.id == planet_id), None) if target else None
            if planet is None or (original_title is not None and planet.title != original_title):
                # Module removed or renamed while Ollama was running: drop silently.
                skipped += 1
                continue
            # Prose only: the structure (nodes/links) and user-edit flags stay untouched.
            planet.ai_summary = text
            self._ollama_applied += 1
            if self._selected_node is planet:
                self._refresh_ai_summary_block(planet)
        purpose_pending, self._pending_ollama_purpose_results = self._pending_ollama_purpose_results, []
        changed: List[str] = []
        manual = bool(getattr(self, "_purpose_run_manual", False))
        for system_id, text in purpose_pending:
            target = next((s for s in self.store.systems if s.id == system_id), None)
            if target is None:
                skipped += 1
                continue
            original = self._purpose_originals.get(system_id)
            if original is not None and current_evidence_key(target) != original:
                skipped += 1  # re-scanned while Ollama was working: made from old evidence
                continue
            # Only ever a suggestion; refused for "user" / "readme" purposes, dismissed
            # evidence (automatic runs) and answers that fail the quality check.
            if set_purpose_suggestion(target, text, manual=manual):
                self._ollama_applied += 1
                changed.append(system_id)
            elif manual and (target.purpose_source or "") not in PROTECTED_PURPOSE_SOURCES:
                quality = check_suggestion_text(target, text)
                if not quality.ok:
                    self._purpose_rejections.append(
                        (system_id, f"No suggestion: {quality.detail} (reason: {quality.reason})."))
                else:
                    skipped += 1
            else:
                skipped += 1
        if changed:
            self._after_purpose_changed(changed)
        self._ollama_skipped = getattr(self, "_ollama_skipped", 0) + skipped
        return skipped

    def _flush_pending_ollama_results(self) -> None:
        if self._store_busy:
            return
        if self._pending_ollama_results or self._pending_ollama_module_results or self._pending_ollama_purpose_results:
            self._apply_pending_ollama_results()
        if self._pending_ollama_finish is not None:
            ok, msg = self._pending_ollama_finish
            self._pending_ollama_finish = None
            self._on_ollama_finished(ok, msg)

    @Slot(bool, str)
    def _on_ollama_finished(self, ok: bool, msg: str) -> None:
        if self._store_busy:
            # A store worker owns the store; finish (apply + save) once it is done.
            self._pending_ollama_finish = (ok, msg)
            return
        worker = self._ollama_worker
        if worker is not None and self.sender() is worker:
            worker.wait(2000)
        self._apply_pending_ollama_results()
        skipped = getattr(self, "_ollama_skipped", 0)
        self._ollama_skipped = 0
        if skipped:
            msg = f"{msg} ({skipped} result(s) not applied because the project changed meanwhile.)"
        if hasattr(self, "btn_ollama_single"):
            self.btn_ollama_single.setEnabled(True)
            self.btn_ollama_single.setText(self._ollama_button_label())
        if hasattr(self, "btn_ollama_all"):
            self.btn_ollama_all.setEnabled(True)
        self._refresh_ai_controls()
        purpose_run, self._purpose_run = self._purpose_run, None
        rejections, self._purpose_rejections = self._purpose_rejections, []
        stopped = self._ollama_stop_requested
        self._ollama_stop_requested = False
        applied = self._ollama_applied
        if purpose_run is not None and purpose_run.get("manual"):
            # One quiet line for a manual "Suggest with AI" request.
            if applied:
                msg = "Suggestion ready: accept, edit or dismiss it above the map."
            elif rejections and not stopped:
                last = rejections[-1][1]
                kind = classify_purpose_failure(last)
                if kind == "unknown":
                    msg = "No suggestion: the evidence was not enough to tell what this project is for."
                elif kind == "rejected":
                    detail = purpose_failure_detail(last) or "the answer did not match the evidence"
                    msg = f"No suggestion: {detail}."
        elif purpose_run is not None:
            # Automatic runs: rejections are silent; only report what was suggested.
            msg = (f"Ollama suggested {applied} project description{'s' if applied != 1 else ''} "
                   "(shown as “Suggested” until you accept them)." if applied else "")
        self.status_label.setText(msg)
        self._update_busy_indicator()
        self._ollama_applied = 0
        self._refresh_purpose_block(self.canvas.active_system)
        if purpose_run is not None and purpose_run.get("then_modules") and not stopped:
            # Order after a single-folder analysis: the purpose first, then the modules.
            QTimer.singleShot(0, lambda sid=purpose_run["then_modules"]: self._continue_with_module_descriptions(sid))
        else:
            self._schedule_queued_auto_purpose()

        if ok or applied:
            # Saved once per run (not per module).
            self._updating_ui = True
            try:
                self.store.save()
            except Exception as exc:
                self.status_label.setText(f"{msg} Saving failed: {exc}")
            finally:
                self._updating_ui = False
            self._refresh_sun_anchor_card()
            self.canvas.rebuild_scene()
            if self._selected_type == "sun" and self.canvas.active_system:
                self._load_node_into_inspector(self.canvas.active_system, "sun")
            elif self._selected_type == "planet" and isinstance(self._selected_node, PlanetNode):
                self._refresh_ai_summary_block(self._selected_node)
