"""Offscreen smoke tests for HeliosMainWindow: deferred startup scan, busy state, warnings.

All dialogs, file pickers, home-directory scans, importer calls and Ollama calls are
monkeypatched; a faulthandler watchdog aborts the run if an event loop hangs.
"""

from __future__ import annotations

import faulthandler
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:  # pragma: no cover
    pytest.skip("PySide6 is not available", allow_module_level=True)

from connectors import ollama_enhancer as oe  # noqa: E402
from connectors.agent_importer import AgentDetector, AgentHistoryImporter  # noqa: E402
from connectors.codebase_importer import CodebaseImporter  # noqa: E402
from core.models import GalaxyStore, SolarSystem  # noqa: E402

TEST_TIMEOUT_S = 60


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def hard_timeout():
    faulthandler.dump_traceback_later(TEST_TIMEOUT_S, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


def spin(seconds: float) -> None:
    app = QtCore.QCoreApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 10.0) -> None:
    app = QtCore.QCoreApplication.instance()
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return QtWidgets.QMessageBox.StandardButton.Ok


FAKE_DETECTED = {
    "antigravity": {"name": "Antigravity", "detected": True, "status": "Ready", "icon": "AG"},
    "codebase": {"name": "Codebase", "detected": True, "status": "Ready", "icon": "CB"},
    "ollama": {"name": "Ollama", "detected": False, "status": "Not found", "icon": "OL", "models": []},
}


@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    """Isolated environment: temp home, fake importers, recorded message boxes."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    boxes = {name: Recorder() for name in ("information", "warning", "question", "critical")}
    for name, rec in boxes.items():
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(rec))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))

    scans = []
    gate = {"event": None}

    def fake_scan(store, sources, max_sessions=25):
        scans.append((list(sources), max_sessions, threading.get_ident()))
        if gate["event"] is not None:
            gate["event"].wait(10)
        s = SolarSystem(name="Scanned", project_name="Scanned", source="antigravity")
        store.systems.append(s)
        return 1

    monkeypatch.setattr(AgentHistoryImporter, "scan_selected_sources", staticmethod(fake_scan))
    monkeypatch.setattr(CodebaseImporter, "analyze_directory", staticmethod(lambda folder, store: None))
    monkeypatch.setattr(CodebaseImporter, "scan_helios_projects", staticmethod(lambda store: []))
    monkeypatch.setattr(CodebaseImporter, "get_helios_projects_dir", staticmethod(lambda: tmp_path / "projects"))
    monkeypatch.setattr(AgentDetector, "detect_all", staticmethod(lambda: {k: dict(v) for k, v in FAKE_DETECTED.items()}))
    monkeypatch.setattr(oe.OllamaEnhancer, "get_preferred_model", staticmethod(lambda chosen_model=None: None))
    monkeypatch.setattr(oe.OllamaEnhancer, "detect_installed_models", staticmethod(lambda: []))
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: False))
    monkeypatch.setattr(oe.OllamaEnhancer, "is_server_running", staticmethod(lambda timeout=1.5: False))

    windows = []

    def make_window(store=None, onboarded=True):
        from ui.main_window import HeliosMainWindow

        if store is None:
            store = GalaxyStore(storage_path=tmp_path / "galaxy.json")
        if onboarded:
            store.set_setting("onboarding_completed", True)
            store.set_setting("connected_sources", ["antigravity"])
        win = HeliosMainWindow(store=store)
        windows.append(win)
        return win

    yield {"make_window": make_window, "boxes": boxes, "scans": scans, "gate": gate, "tmp": tmp_path}

    if gate["event"] is not None:
        gate["event"].set()
    for w in windows:
        if w._ollama_worker is not None and hasattr(w._ollama_worker, "requestInterruption"):
            w._ollama_worker.requestInterruption()
        for t in (w._store_worker, w._ollama_worker, w._model_detect_worker, w._ollama_server_check_worker):
            if t is not None and hasattr(t, "wait"):
                t.wait(5000)
        w.close()
        w.deleteLater()
    spin(0.05)
    # Never leave an override cursor behind for the next test.
    while QtWidgets.QApplication.overrideCursor() is not None:
        QtWidgets.QApplication.restoreOverrideCursor()


def _editing_widgets(win):
    # btn_delete_node is excluded: the inspector disables it on its own for the last project.
    return [win.hierarchy_tree, win.canvas, win.edit_title, win.btn_save_node, win.btn_sync_agents]


def test_window_builds_and_startup_scan_runs_after_show(env):
    win = env["make_window"]()
    assert env["scans"] == [], "the startup scan must not run inside __init__"
    win.show()
    spin(1.0)
    wait_until(lambda: env["scans"] and not win.is_store_busy())

    assert env["scans"][0][0] == ["antigravity"] and env["scans"][0][1] == 25
    assert env["scans"][0][2] != threading.get_ident(), "scan ran on the UI thread"
    assert win.hierarchy_tree.topLevelItemCount() > 0
    assert win.canvas.active_system is not None
    assert any(s.name == "Scanned" for s in win.store.systems)
    assert "Startup sync finished" in win.status_label.text()
    assert all(w.isEnabled() for w in _editing_widgets(win))
    assert env["boxes"]["warning"].calls == []


def test_busy_state_disables_editing_and_freezes_canvas(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.3)

    win._set_store_busy(True, "busy...")
    assert win.is_store_busy()
    assert not any(w.isEnabled() for w in _editing_widgets(win))
    assert all(not sc.isEnabled() for sc in win._shortcuts) and len(win._shortcuts) >= 6
    assert not win.canvas.timer.isActive()
    assert not win.canvas.updatesEnabled()
    assert win._canvas_freezer.frozen
    paint = QtGui.QPaintEvent(QtCore.QRect(0, 0, 10, 10))
    assert win._canvas_freezer.eventFilter(win.canvas.viewport(), paint) is True
    assert win.status_label.text() == "busy..."
    assert win.status_label.isEnabled(), "status bar must stay live for progress"
    assert not win.connections_view.btn_connect.isEnabled()
    # Live file-watcher events are ignored while busy (would otherwise reload the store).
    loads = []
    win.store.load = lambda: loads.append(1) or True
    win._on_external_store_changed(str(win.store.storage_path))
    assert loads == []

    win._set_store_busy(False)
    assert not win.is_store_busy()
    assert all(w.isEnabled() for w in _editing_widgets(win))
    assert all(sc.isEnabled() for sc in win._shortcuts)
    assert win.canvas.timer.isActive()
    assert win.canvas.updatesEnabled()
    assert win._canvas_freezer.eventFilter(win.canvas.viewport(), paint) is False


def test_worker_busy_lifecycle_rejects_second_worker_and_refreshes(env):
    from ui.workers import AgentSyncWorker

    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.3)
    env["gate"]["event"] = threading.Event()

    win._sync_agent_histories()
    wait_until(lambda: bool(env["scans"]))
    assert win.is_store_busy()
    assert not win.hierarchy_tree.isEnabled()
    second = AgentSyncWorker(win.store, ["antigravity"])
    assert win._run_store_worker(second) is False
    assert "already running" in win.status_label.text()

    env["gate"]["event"].set()
    wait_until(lambda: not win.is_store_busy())
    assert win.hierarchy_tree.isEnabled()
    assert win.canvas.updatesEnabled() and win.canvas.timer.isActive()
    titles = []
    it = QtWidgets.QTreeWidgetItemIterator(win.hierarchy_tree)
    while it.value():
        titles.append(it.value().text(0))
        it += 1
    assert any("Scanned" in t for t in titles), titles
    info = env["boxes"]["information"].calls
    assert info and info[-1][1] == "AI tools synced"


def test_worker_failure_is_shown_to_user(env, monkeypatch):
    def boom(store, sources, max_sessions=25):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(AgentHistoryImporter, "scan_selected_sources", staticmethod(boom))
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    win._sync_agent_histories()
    wait_until(lambda: not win.is_store_busy() and win._store_worker is None)
    warnings = env["boxes"]["warning"].calls
    assert warnings and "disk on fire" in warnings[-1][2]
    assert "disk on fire" in win.status_label.text()
    assert win.hierarchy_tree.isEnabled()


def test_close_waits_for_running_worker(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    env["gate"]["event"] = threading.Event()
    win._sync_agent_histories()
    wait_until(lambda: bool(env["scans"]))
    worker = win._store_worker
    threading.Timer(0.3, env["gate"]["event"].set).start()
    win.close()
    assert worker.isFinished(), "closeEvent must wait for the worker instead of destroying it"


def test_load_error_warning_on_startup(env):
    store = GalaxyStore(storage_path=env["tmp"] / "galaxy.json")
    store.load_error = "x"
    store.backup_path = env["tmp"] / "galaxy.corrupt-test.json"
    win = env["make_window"](store=store)
    win.show()
    spin(0.5)
    calls = env["boxes"]["warning"].calls
    assert calls, "a warning must be shown when the data file could not be read"
    text = calls[0][2]
    assert "Nothing was overwritten" in text and "galaxy.corrupt-test.json" in text
    assert env["scans"] == [], "no startup rescan over an unreadable data file"


def test_corrupt_file_on_disk_and_live_reload(env):
    path = env["tmp"] / "galaxy.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = GalaxyStore(storage_path=path)
    if getattr(store, "load_error", None) is None:
        pytest.skip("GalaxyStore.load_error not implemented in this checkout")
    win = env["make_window"](store=store)
    win.show()
    spin(0.4)
    assert env["boxes"]["warning"].calls

    # Live reload of a still-broken file: status text, graph left untouched.
    before = [s.id for s in win.store.systems]
    path.write_text("{ still broken", encoding="utf-8")
    win._on_external_store_changed(str(path))
    assert "could not be read" in win.status_label.text()
    assert "Nothing was overwritten" in win.status_label.text()
    assert [s.id for s in win.store.systems] == before


def test_live_reload_exception_is_surfaced(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    win.store.is_own_write = lambda: False

    def broken_load():
        raise OSError("sharing violation")

    win.store.load = broken_load
    win._on_external_store_changed(str(win.store.storage_path))
    assert "sharing violation" in win.status_label.text()


def test_onboarding_connect_runs_in_background_and_finishes(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.4)  # first-run onboarding switches to the integrations page
    view = win.connections_view
    env["gate"]["event"] = threading.Event()
    view._on_connect()
    wait_until(lambda: bool(env["scans"]))
    assert win.is_store_busy()
    assert not view.btn_connect.isEnabled() and view.lbl_status.isVisibleTo(view)
    env["gate"]["event"].set()
    wait_until(lambda: not win.is_store_busy())
    spin(0.1)
    assert win.store.get_setting("onboarding_completed", False) is True
    assert "antigravity" in win.store.get_setting("connected_sources", [])
    assert win.central_stack.currentIndex() == 0
    assert view.btn_connect.isEnabled()


def test_ollama_results_are_applied_on_ui_thread_and_queued_while_busy(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    target = win.store.systems[0]
    target.core_plan = "base"

    class FakeWorker:
        def snapshot_for(self, sid):
            return oe.snapshot_system(target) if sid == target.id else None

    win._ollama_worker = FakeWorker()
    win._set_store_busy(True, "busy")
    win._on_ollama_system_enhanced(target.id, "base\n\n[X]\nnew")
    win._on_ollama_finished(True, "done")
    assert target.core_plan == "base", "must not mutate the store while a worker owns it"
    win._set_store_busy(False)
    win._flush_pending_ollama_results()
    assert target.core_plan == "base\n\n[X]\nnew" and target.ollama_enhanced is True
    assert win.status_label.text() == "done"

    # A project edited meanwhile is not overwritten.
    target.core_plan = "user edited"
    win._pending_ollama_results.append((target.id, "base\n\n[X]\nnew", "other"))
    win._on_ollama_finished(True, "done2")
    assert target.core_plan == "user edited"
    assert "not applied" in win.status_label.text()
    win._ollama_worker = None


def test_settings_dialog_projects_scan_uses_window_busy_state(env, monkeypatch):
    from ui.main_window import HeliosSettingsDialog

    gate = threading.Event()
    found = SolarSystem(name="InProjects", project_name="InProjects", source="codebase")

    def fake_projects_scan(store):
        gate.wait(10)
        store.systems.append(found)
        return [found]

    monkeypatch.setattr(CodebaseImporter, "scan_helios_projects", staticmethod(fake_projects_scan))
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    dlg = HeliosSettingsDialog(win.store, win)
    dlg.show()
    dlg._scan_helios_projects_folder()
    assert win.is_store_busy() and not dlg.isEnabled()
    dlg.reject()  # ignored while the scan owns the store
    assert dlg.isVisible()
    gate.set()
    wait_until(lambda: not win.is_store_busy())
    spin(0.05)
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert env["boxes"]["information"].calls[-1][1] == "Projects scanned"
    assert any(s is found for s in win.store.systems)


# ----------------------------------------------------------------------
# Ollama module descriptions (PlanetNode.ai_summary)
# ----------------------------------------------------------------------
import json  # noqa: E402

from core.models import PlanetNode  # noqa: E402


class _ModuleFakeWorker:
    """Stands in for OllamaEnhanceWorker when calling the result slots directly."""

    def __init__(self, system):
        self.snap = oe.snapshot_system(system)

    def snapshot_for(self, sid):
        return self.snap if sid == self.snap.id else None

    def planet_snapshot_for(self, sid, pid):
        snap = self.snapshot_for(sid)
        return snap.planet_snapshot(pid) if snap else None


def _window_with_planets(env, n=2):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system = win.store.systems[0]
    system.planets.clear()
    system.links.clear()
    planets = []
    for i in range(n):
        p = system.add_planet(title=f"mod{i}.py", summary=f"Deterministic summary {i}.")
        p.kind = "file"
        p.add_satellite(title=f"f{i}()", content="")
        planets.append(p)
    win.canvas.active_system = system
    return win, system, planets


class _GenOpener:
    def __init__(self, answer="Describes the module.", gate=None):
        self.answer, self.gate, self.count = answer, gate, 0

    def open(self, req, timeout=None):
        self.count += 1
        if self.gate is not None:
            self.gate.wait(10)

        class R:
            status = 200

            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False

        return R(json.dumps({"response": self.answer}).encode())


def test_module_result_sets_ai_summary_only_and_drops_stale(env):
    win, system, (p0, p1) = _window_with_planets(env)
    win._ollama_worker = _ModuleFakeWorker(system)
    p1.title = "renamed.py"  # renamed after the snapshot -> dropped
    win._on_ollama_module_described(system.id, p0.id, "AI text 0.")
    win._on_ollama_module_described(system.id, p1.id, "AI text 1.")
    assert p0.ai_summary == "AI text 0."
    assert p0.summary == "Deterministic summary 0." and p0.is_user_modified is False
    assert p1.ai_summary == ""

    # Removed meanwhile -> dropped silently and counted; queued while busy.
    win._set_store_busy(True, "busy")
    win._on_ollama_module_described(system.id, p0.id, "newer")
    assert p0.ai_summary == "AI text 0.", "must not mutate the store while a worker owns it"
    system.planets.remove(p0)
    win._set_store_busy(False)
    win._on_ollama_finished(True, "done")
    assert p0.ai_summary == "AI text 0."
    assert "2 result(s) not applied" in win.status_label.text()
    assert json.loads(win.store.storage_path.read_text(encoding="utf-8")), "saved once at the end"
    win._ollama_worker = None


def test_inspector_ai_block_visibility_save_and_clear(env):
    win, system, (p0, p1) = _window_with_planets(env)
    p0.ai_summary = "Records audio."
    win._load_node_into_inspector(p0, "planet")
    assert not win.ai_summary_box.isHidden()
    assert win.lbl_ai_summary.text() == "Records audio."
    assert "may be inaccurate" in win.lbl_ai_summary_header.text()
    assert "Records audio." not in win.edit_content.toPlainText()

    win._load_node_into_inspector(p1, "planet")
    assert win.ai_summary_box.isHidden()
    win._load_node_into_inspector(system, "sun")
    assert win.ai_summary_box.isHidden()

    win._load_node_into_inspector(p0, "planet")
    win.edit_content.setPlainText("User summary.")
    win._save_current_node()
    assert p0.summary == "User summary." and p0.ai_summary == "Records audio."

    p0.is_user_modified = False
    win._load_node_into_inspector(p0, "planet")
    win.btn_clear_ai_summary.click()
    assert p0.ai_summary == "" and p0.is_user_modified is False
    assert win.ai_summary_box.isHidden()


def test_prompt_export_contains_ai_line(env):
    win, system, (p0, p1) = _window_with_planets(env)
    p0.ai_summary = "Records audio\nfrom the mic."
    win._copy_project_as_prompt(system)
    text = QtWidgets.QApplication.clipboard().text()
    assert "AI description (Ollama, may be inaccurate): Records audio from the mic." in text
    assert text.count("AI description (Ollama") == 1


def test_settings_language_combo_round_trips(env):
    from ui.main_window import HeliosSettingsDialog

    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.1)
    dlg = HeliosSettingsDialog(win.store, win)
    assert dlg.combo_language.currentData() == "English"
    dlg.combo_language.setCurrentIndex(dlg.combo_language.findData("Türkçe"))
    dlg._save_settings_and_close()
    assert win.store.get_setting("ollama_language") == "Türkçe"
    dlg2 = HeliosSettingsDialog(win.store, win)
    assert dlg2.combo_language.currentData() == "Türkçe"
    dlg.deleteLater()
    dlg2.deleteLater()


def test_enhance_asks_regenerate_and_stop_button(env, monkeypatch):
    win, system, (p0, p1) = _window_with_planets(env)
    p0.ai_summary = "old"
    gate = threading.Event()
    opener = _GenOpener("Fresh text.", gate=gate)
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: True))
    env["boxes"]["question"].calls.clear()
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *a, **k: env["boxes"]["question"](*a) and QtWidgets.QMessageBox.StandardButton.Yes),
    )
    win._detected_ollama_model = "m:1"
    win._enhance_active_project_with_ollama()
    assert env["boxes"]["question"].calls, "must ask whether to regenerate"
    assert win._ollama_worker.force is True and win._ollama_worker.language == "English"
    assert win.btn_ollama_single.text() == "Stop Ollama"
    wait_until(lambda: opener.count >= 1)
    win.btn_ollama_single.click()  # requests interruption
    wait_until(lambda: not win._ollama_worker.isRunning(), timeout=5)
    gate.set()
    wait_until(lambda: win.btn_ollama_single.text().startswith("Enhance with Ollama"))
    assert win.btn_ollama_single.isEnabled()
    assert p0.ai_summary == "old"


def test_auto_describe_after_folder_analysis(env, monkeypatch):
    win, system, (p0, p1) = _window_with_planets(env)
    monkeypatch.setattr(oe.OllamaEnhancer, "is_server_running", staticmethod(lambda timeout=1.5: True))
    opener = _GenOpener("Auto text.")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    p1.ai_summary = "keep me"
    win._detected_ollama_model = "m:1"
    win._on_codebase_folder_analyzed(True, "ok", {"system": system, "folder": env["tmp"]})
    wait_until(lambda: win._ollama_worker is not None, timeout=5)
    assert win._ollama_worker.force is False and win._ollama_worker.summarize_project is False
    assert "Stop Ollama" in win.status_label.text() or win._ollama_worker.isFinished()
    wait_until(lambda: win._ollama_worker.isFinished() and p0.ai_summary == "Auto text.")
    wait_until(lambda: win.btn_ollama_single.text().startswith("Enhance with Ollama"))
    assert p1.ai_summary == "keep me" and opener.count == 1
    saved = json.loads(win.store.storage_path.read_text(encoding="utf-8"))
    assert "Auto text." in json.dumps(saved)


def test_no_auto_describe_when_disabled_or_server_down(env, monkeypatch):
    win, system, _ = _window_with_planets(env)
    win._detected_ollama_model = "m:1"
    win.store.set_setting("disable_ollama", True)
    win._on_codebase_folder_analyzed(True, "ok", {"system": system, "folder": env["tmp"]})
    assert win._ollama_server_check_worker is None and win._ollama_worker is None

    win.store.set_setting("disable_ollama", False)
    win._on_codebase_folder_analyzed(True, "ok", {"system": system, "folder": env["tmp"]})
    assert win._ollama_server_check_worker is not None  # is_server_running -> False (env)
    wait_until(lambda: win._ollama_server_check_worker is None)
    spin(0.1)
    assert win._ollama_worker is None


def test_close_interrupts_running_ollama_quickly(env, monkeypatch):
    win, system, _ = _window_with_planets(env)
    gate = threading.Event()
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _GenOpener("x.", gate=gate))
    win._detected_ollama_model = "m:1"
    win._start_ollama_worker([system], "m:1", summarize_project=False)
    worker = win._ollama_worker
    wait_until(lambda: worker.isRunning())
    t0 = time.monotonic()
    win.close()
    assert worker.isFinished() and time.monotonic() - t0 < 3.0
    gate.set()


# ----------------------------------------------------------------------
# Redesign: progressive disclosure, focus, inspector, status bar, vocabulary
# ----------------------------------------------------------------------
import re  # noqa: E402

from core.models import PlanetLink, SatelliteNode  # noqa: E402


def _rich_system(win):
    """A project with links, an entry module, call references and an old-style text."""
    system = win.store.systems[0]
    system.planets.clear()
    system.links.clear()
    a = system.add_planet(title="main.py", summary="MODULE PURPOSE & ROLE:\n**Starts** the app.\n• point one")
    a.kind, a.is_entry = "file", True
    b = system.add_planet(title="ears.py", summary="Listens.")
    b.kind = "file"
    c = system.add_planet(title="other.py", summary="Unrelated.")
    c.kind = "file"
    sa = a.add_satellite(title="run()")
    sa.kind, sa.signature, sa.line = "function", "def run() -> None", 3
    sb = b.add_satellite(title="listen()")
    sb.kind = "function"
    c.add_satellite(title="noop()").kind = "function"
    sa.calls = ["ears.py::listen()", "ghost.py::missing()"]
    sb.called_by = ["main.py::run()"]
    system.add_link(a.id, b.id, label="imports ears")
    return system, a, b, c, sa, sb


def _open(win, system):
    win.canvas.enter_solar_system(system)
    spin(0.05)


def test_moons_hidden_by_default_and_shown_on_selection(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, b, c, sa, sb = _rich_system(win)
    win.canvas.set_show_all_details(False)
    _open(win, system)
    items = win.canvas.planet_item_map
    assert all(not s.isVisible() for it in items.values() for s in it.satellite_items)
    win.canvas.select_object(a, "planet")
    assert all(s.isVisible() for s in items[a.id].satellite_items)
    assert all(not s.isVisible() for s in items[c.id].satellite_items)
    # Selecting a component keeps its module's components visible.
    win.canvas.select_object(sb, "satellite")
    assert all(s.isVisible() for s in items[b.id].satellite_items)


def test_many_moons_show_more_chip_and_labels_do_not_overlap(env):
    from ui.space_canvas import MAX_VISIBLE_MOONS

    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, *_ = _rich_system(win)
    for i in range(20):
        a.add_satellite(title=f"a_really_long_function_name_number_{i}_that_needs_eliding()")
    _open(win, system)
    win.canvas.select_object(a, "planet")
    item = win.canvas.planet_item_map[a.id]
    visible = [s for s in item.satellite_items if s.isVisible()]
    assert len(visible) == MAX_VISIBLE_MOONS - 1
    assert item.more_chip.isVisible() and item.more_chip.text() == f"+{21 - len(visible)} more"
    rects = [s.mapRectToScene(s._label_rect()) for s in visible]
    title = item.scene_label_rect()
    for i, r in enumerate(rects):
        assert not r.intersects(title)
        for r2 in rects[i + 1:]:
            assert not r.intersects(r2)
    assert any(s.display_title().endswith("…") for s in visible)


def test_show_all_details_toggle_is_persisted(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, b, c, *_ = _rich_system(win)
    _open(win, system)
    win.btn_show_details.click()
    assert win.canvas.show_all_details is True
    assert win.store.get_setting("canvas_show_all_details") is True
    assert all(s.isVisible() for s in win.canvas.planet_item_map[c.id].satellite_items)
    win2 = env["make_window"](store=win.store, onboarded=False)
    assert win2.canvas.show_all_details is True and win2.btn_show_details.isChecked()
    win.btn_show_details.click()
    assert win.store.get_setting("canvas_show_all_details") is False


def test_selection_dims_unrelated_modules_and_links(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, b, c, sa, sb = _rich_system(win)
    _open(win, system)
    items = win.canvas.planet_item_map
    assert all(it.opacity() == 1.0 for it in items.values())
    win.canvas.select_object(a, "planet")
    assert items[a.id].opacity() == 1.0 and items[b.id].opacity() == 1.0
    assert items[c.id].opacity() < 0.5
    link_item = win.canvas.link_items[0]
    assert link_item.is_highlighted and link_item.label_visible()
    win.canvas.select_object(c, "planet")
    assert link_item.is_dimmed and not link_item.label_visible()
    # A component highlights the modules owning the components it calls.
    win.canvas.select_object(sa, "satellite")
    assert items[b.id].is_related and items[c.id].opacity() < 0.5
    win.canvas.select_object(system, "sun")
    assert all(it.opacity() == 1.0 for it in items.values())


def _rows(win, cls_name="RowButton"):
    from ui.inspector import PlainRow, RowButton

    cls = RowButton if cls_name == "RowButton" else PlainRow
    return [w for w in win.sections_widget.findChildren(cls) if not w.isHidden()]


def test_inspector_module_lists_and_uses_row_selects_target(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, b, c, sa, sb = _rich_system(win)
    _open(win, system)
    win.canvas.select_object(a, "planet")
    titles = [r.text() for r in _rows(win)]
    assert "ears.py" in titles and "run()" in titles
    uses = next(r for r in _rows(win) if r.text() == "ears.py")
    uses.click()
    assert win.canvas.selected_node is b and win._selected_node is b
    used_by = [r.text() for r in _rows(win)]
    assert "main.py" in used_by and "listen()" in used_by
    # Generated old-style text is shown in sentence case without raw asterisks.
    win.canvas.select_object(a, "planet")
    from ui.inspector import ExpandableText

    about = win.sections_widget.findChildren(ExpandableText)[0].label.text()
    assert "Module purpose &amp; role" in about and "**" not in about and "MODULE PURPOSE" not in about
    assert a.summary.startswith("MODULE PURPOSE & ROLE:"), "display only: stored text untouched"


def test_inspector_component_calls_and_unresolvable_reference(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, b, c, sa, sb = _rich_system(win)
    _open(win, system)
    win.canvas.select_object(sa, "satellite")
    rows = _rows(win)
    assert any(r.text() == "listen()" for r in rows)
    plain = _rows(win, "PlainRow")
    assert any("missing()" in p.title_label.text() for p in plain)
    texts = [w.text() for w in win.sections_widget.findChildren(QtWidgets.QLabel)]
    assert any("def run() -> None" in t for t in texts)
    next(r for r in rows if r.text() == "listen()").click()
    assert win.canvas.selected_node is sb
    assert any(r.text() == "run()" for r in _rows(win))  # Called by


def test_edit_section_collapsed_and_save_keeps_ai_summary_separate(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, *_ = _rich_system(win)
    a.ai_summary = "AI words."
    _open(win, system)
    win.canvas.select_object(a, "planet")
    assert win.edit_section.isHidden()
    win.btn_edit_toggle.click()
    assert not win.edit_section.isHidden()
    win.edit_content.setPlainText("Hand-written summary.")
    assert win.btn_save_node.objectName() == "PrimaryBtn"
    win.btn_save_node.click()
    assert a.summary == "Hand-written summary." and a.ai_summary == "AI words." and a.is_user_modified
    assert win.edit_section.isHidden()
    assert win.store.undo() and next(p for p in win.store.systems[0].planets if p.id == a.id).summary != "Hand-written summary."


def test_old_data_system_renders_without_error(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    old = SolarSystem(name="Old", project_name="Old map", source="antigravity",
                      core_plan="PROJECT PURPOSE & OVERVIEW (WHAT IT DOES):\n**Builds** things.")
    p = old.add_planet(title="The user wants the assistant to listen to the microphone all the time", summary="")
    for k in range(15):
        p.add_satellite(title=f"Asked the agent to do step {k} of the plan carefully")
    old.add_link(p.id, old.add_planet(title="Another sentence title").id, label="")
    win.store.systems.append(old)
    _open(win, old)
    win.canvas.select_object(p, "planet")
    win.canvas.select_object(p.satellites[0], "satellite")
    win.canvas.select_object(old.links[0], "link")
    win.canvas.set_show_all_details(True)
    win.canvas.switch_to_galaxy_view()
    win.grab()
    win.canvas.set_show_all_details(False)


def test_skipped_folders_status_link(env, monkeypatch):
    from connectors import agent_importer

    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    monkeypatch.setattr(agent_importer, "LAST_SCAN_STATS", {
        "codex": {"sessions_found": 4, "skipped_scratch": 2, "skipped_scratch_paths": ["C:\\a\\tablo", "C:\\b"],
                  "skipped_no_code": "x", "skipped_weird": 1},
        "antigravity": "not a dict",
    })
    win._sync_agent_histories()
    wait_until(lambda: not win.is_store_busy() and win._store_worker is None)
    assert not win.lbl_skipped_link.isHidden()
    assert "3 folders skipped" in win.lbl_skipped_link.text()
    win._show_skipped_folders_dialog()
    dlg = win._skipped_dialog
    assert dlg is not None and dlg.isVisible() and not dlg.isModal()
    texts = " ".join(l.text() for l in dlg.findChildren(QtWidgets.QLabel))
    assert "tablo" in texts and "temporary work folders" in texts.lower()
    dlg.close()
    monkeypatch.setattr(agent_importer, "LAST_SCAN_STATS", {})
    win._update_skipped_folders_link()
    assert win.lbl_skipped_link.isHidden()


_FORBIDDEN = re.compile(r"\b(Planet|Planets|Sun|Satellite|Satellites|Moon|Moons|Core Plan)\b")


def _visible_texts(root):
    out = []
    for w in [root] + root.findChildren(QtWidgets.QWidget):
        for attr in ("text", "toolTip", "placeholderText", "windowTitle"):
            fn = getattr(w, attr, None)
            if callable(fn):
                try:
                    val = fn()
                except TypeError:
                    continue
                if isinstance(val, str) and val:
                    out.append((type(w).__name__, val))
    return out


def test_no_solar_vocabulary_in_visible_text(env):
    from ui.main_window import HeliosSettingsDialog, NewProjectStudioDialog

    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, a, b, c, sa, sb = _rich_system(win)
    _open(win, system)
    roots = [win]
    for node, kind in ((system, "sun"), (a, "planet"), (sa, "satellite"), (system.links[0], "link")):
        win.canvas.select_object(node, kind)
        bad = [(n, t) for n, t in _visible_texts(win) if _FORBIDDEN.search(t)]
        assert not bad, bad
    win.canvas.switch_to_galaxy_view()
    for r in roots + [HeliosSettingsDialog(win.store, win), NewProjectStudioDialog(win)]:
        bad = [(n, t) for n, t in _visible_texts(r) if _FORBIDDEN.search(t)]
        assert not bad, bad
    win.canvas.enter_solar_system(system)
    for it in win.canvas.scene_obj.items():
        assert not _FORBIDDEN.search(it.toolTip() or ""), it.toolTip()


def test_window_fits_1366x768(env):
    win = env["make_window"](onboarded=False)
    win.resize(1366, 768)
    win.show()
    spin(0.3)
    system, a, *_ = _rich_system(win)
    _open(win, system)
    win.canvas.select_object(a, "planet")
    spin(0.1)
    assert win.width() <= 1366 and win.height() <= 768, (win.width(), win.height())
    full = win.rect()
    for w in win.findChildren(QtWidgets.QWidget):
        if not w.isVisible() or w.isWindow() or isinstance(w, QtWidgets.QMenu):
            continue
        # Children of scroll areas may legitimately extend past the viewport.
        if any(isinstance(p, QtWidgets.QAbstractScrollArea) for p in _ancestors(w, win)):
            continue
        top_left = w.mapTo(win, QtCore.QPoint(0, 0))
        r = QtCore.QRect(top_left, w.size())
        assert full.contains(r), (type(w).__name__, w.objectName(), r, full)


def _ancestors(w, stop):
    p = w.parentWidget()
    while p is not None and p is not stop:
        yield p
        p = p.parentWidget()


def test_fit_view_and_idle_timer(env):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    system, *_ = _rich_system(win)
    _open(win, system)
    assert win.canvas.timer.interval() >= 500, "idle canvas must not tick at animation rate"
    win.btn_toggle_orbit.click()
    assert win.canvas.orbit_motion_enabled and win.canvas.timer.interval() < 100
    win.btn_toggle_orbit.click()
    assert win.canvas.timer.interval() >= 500
    win.canvas.zoom_in()
    win.canvas.zoom_in()
    win.btn_fit_view.click()
    vis = win.canvas.mapToScene(win.canvas.viewport().rect()).boundingRect()
    for it in win.canvas.planet_items:
        assert vis.contains(it.sceneBoundingRect())


# ----------------------------------------------------------------------
# Project purpose (what the project is for)
# ----------------------------------------------------------------------
from PySide6.QtCore import Signal as _Signal  # noqa: E402

from ui import main_window as mw  # noqa: E402


def _purpose_window(env, n_extra=0):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    win._switch_to_page("studio")  # the first-run onboarding page would hide the inspector
    system = win.store.systems[0]
    system.project_name = system.name = "NOVA"
    system.purpose, system.purpose_source, system.purpose_evidence = "", "", ""
    extras = []
    for i in range(n_extra):
        s = SolarSystem(name=f"P{i}", project_name=f"P{i}", source="codebase")
        s.add_planet(title="main.py", summary="x").kind = "file"
        win.store.systems.append(s)
        extras.append(s)
    win.canvas.enter_solar_system(system)
    spin(0.05)
    return win, system, extras


def test_purpose_header_text_source_tags_and_placeholder(env):
    win, system, _ = _purpose_window(env)
    text = "A local voice assistant that listens through the microphone and runs PC commands."
    expected = {
        "user": "Written by you",
        "readme": "From the project's README",
        "ai": "AI suggestion you accepted",  # changed: "ai" now means an accepted suggestion
    }
    for source, tag in expected.items():
        system.purpose, system.purpose_source = text, source
        win._refresh_sun_anchor_card()
        assert win.purpose_view.isVisible() and not win.purpose_empty.isVisible()
        assert win.lbl_purpose.text() == text
        assert win.lbl_purpose_source.text() == tag
    # A long purpose is clamped to three lines; the full text is in the tooltip.
    system.purpose = " ".join(["This sentence keeps going to fill the header."] * 12)
    win._refresh_sun_anchor_card()
    spin(0.05)
    assert win.lbl_purpose.clamped and len(win.lbl_purpose.shown_text().splitlines()) <= 3
    assert win.lbl_purpose.shown_text().endswith("…")
    assert system.purpose in win.lbl_purpose.toolTip()

    # Changed: the empty state is one muted "Add what this project is for" line, and the
    # AI button ("Suggest with AI", was "Ask Ollama") is hidden — not just disabled —
    # when Ollama is off or no model was found.
    system.purpose, system.purpose_source = "", ""
    win.store.set_setting("disable_ollama", True)
    win._refresh_sun_anchor_card()
    assert not win.purpose_view.isVisible() and win.purpose_empty.isVisible()
    assert win.btn_purpose_add.text() == "Add what this project is for" and win.btn_purpose_add.isEnabled()
    assert not win.btn_suggest_ai.isVisible() and not win.suggestion_box.isVisible()
    win.store.set_setting("disable_ollama", False)
    win._detected_ollama_model = None
    win._refresh_sun_anchor_card()
    assert not win.btn_suggest_ai.isVisible()
    win._detected_ollama_model = "m:1"
    win._refresh_sun_anchor_card()
    assert win.btn_suggest_ai.isVisible()
    assert not win.btn_suggest_ai.isEnabled() and "Re-scan" in win.btn_suggest_ai.toolTip()
    system.purpose_evidence = "README intro (README.md): a voice assistant"
    win._refresh_sun_anchor_card()
    assert win.btn_suggest_ai.isEnabled()
    # The purpose lives in the pinned header only; the body does not repeat it.
    system.purpose, system.purpose_source = "A voice assistant.", "ai"
    win._load_node_into_inspector(system, "sun")
    titles = [win.sections_layout.itemAt(i).widget().text()
              for i in range(win.sections_layout.count())
              if hasattr(win.sections_layout.itemAt(i).widget(), "text")]
    assert "What it does" not in titles


def test_purpose_inline_edit_saves_as_user_undo_and_clear(env):
    win, system, _ = _purpose_window(env)
    system.purpose, system.purpose_source = "Old AI text.", "ai"
    win.store.save()
    win._refresh_sun_anchor_card()
    flags_before = [p.is_user_modified for p in system.planets]
    win.lbl_purpose.clicked.emit()
    assert win.purpose_edit_box.isVisible() and win.edit_purpose.toPlainText() == "Old AI text."
    win.edit_purpose.setPlainText("My own words about the project.")
    QtTest.QTest.keyClick(win.edit_purpose, QtCore.Qt.Key.Key_Return, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert not win.purpose_edit_box.isVisible()
    assert system.purpose == "My own words about the project." and system.purpose_source == "user"
    assert system.is_user_modified is False, "a purpose edit marks nothing else modified"
    assert [p.is_user_modified for p in system.planets] == flags_before
    saved = json.loads(win.store.storage_path.read_text(encoding="utf-8"))
    assert saved["systems"][0]["purpose_source"] == "user"
    assert win.lbl_purpose_source.text() == "Written by you"

    win._on_undo()
    restored = next(s for s in win.store.systems if s.id == system.id)
    assert restored.purpose == "Old AI text." and restored.purpose_source == "ai"

    # Esc cancels without saving.
    win._begin_purpose_edit()
    win.edit_purpose.setPlainText("never saved")
    QtTest.QTest.keyClick(win.edit_purpose, QtCore.Qt.Key.Key_Escape)
    assert not win.purpose_edit_box.isVisible() and restored.purpose == "Old AI text."

    # Clearing resets the source so scans / the AI may fill it again.
    win._begin_purpose_edit()
    win.edit_purpose.setPlainText("   ")
    win.btn_purpose_save.click()
    assert restored.purpose == "" and restored.purpose_source == ""
    assert win.purpose_empty.isVisible()


def test_overview_cards_tree_tooltip_and_sun_tooltip_show_purpose(env):
    win, system, (other,) = _purpose_window(env, n_extra=1)
    system.purpose, system.purpose_source = "Listens and answers.", "ai"
    system.core_plan = "README usage: run python main.py\nsource codebase (no README found)"
    other.core_plan = "Usage: npm start"
    win._populate_tree()
    win.canvas.enter_solar_system(system)
    assert "Listens and answers." in win.canvas.sun_item.toolTip()
    win.canvas.switch_to_galaxy_view()
    cards = {c.system.id: c for c in win.canvas.galaxy_items}
    assert cards[system.id].description_lines() == ["Listens and answers."]
    assert cards[other.id]._purpose_snippet == "" and cards[other.id].description_lines() == []
    assert "No description yet" in cards[other.id].toolTip()
    assert "npm" not in cards[other.id].toolTip() and "README" not in cards[system.id].toolTip()
    long_text = " ".join(["word"] * 120)
    other.purpose = long_text
    win.canvas.refresh_project_texts({other.id})
    lines = cards[other.id].description_lines()
    assert len(lines) == 2 and lines[-1].endswith("…")
    win.canvas.viewport().grab()  # paints without errors
    tips = []
    root = win.hierarchy_tree.invisibleRootItem()
    for i in range(root.childCount()):
        tips.append(root.child(i).toolTip(0))
    assert any("Listens and answers." in t for t in tips)


def test_search_matches_project_purpose(env):
    win, system, (other,) = _purpose_window(env, n_extra=1)
    system.purpose = "A voice assistant for the desktop."
    win._populate_tree()
    win.search_input.setText("voice assistant")
    root = win.hierarchy_tree.invisibleRootItem()
    visible = [root.child(i).text(0) for i in range(root.childCount()) if not root.child(i).isHidden()]
    assert visible == ["NOVA"]
    win.canvas.switch_to_galaxy_view()
    dimmed = {c.system.id: c.is_dimmed for c in win.canvas.galaxy_items}
    assert dimmed[system.id] is False and dimmed[other.id] is True
    win.search_input.clear()


def test_prompt_export_starts_with_purpose_line(env):
    win, system, (p0, p1) = _window_with_planets(env)
    system.purpose = "Records audio and answers."
    p0.ai_summary = "Records audio."
    win._copy_project_as_prompt(system)
    lines = QtWidgets.QApplication.clipboard().text().splitlines()
    assert lines[0].startswith("# PROJECT ARCHITECTURAL CONTEXT")
    assert lines[1] == "Purpose: Records audio and answers."
    system.purpose = ""
    win._copy_project_as_prompt(system)
    assert "Purpose:" not in QtWidgets.QApplication.clipboard().text().splitlines()[1]


def test_weak_evidence_disables_suggest_with_ai_and_explains(env):
    # Changed: "Ask Ollama" is now "Suggest with AI" (btn_suggest_ai / _suggest_purpose_with_ai).
    win, system, _ = _purpose_window(env)
    win._detected_ollama_model = "m:1"
    system.purpose_evidence = ('From the first sessions (what the user asked for): "sunucuyu başlat"\n'
                               "Names in code: a()\nEntry point: run.py")
    win._refresh_sun_anchor_card()
    assert win.btn_suggest_ai.isVisible() and not win.btn_suggest_ai.isEnabled()
    assert "Weak evidence (0 points, 2 needed)" in win.btn_suggest_ai.toolTip()
    assert win._suggest_purpose_with_ai(system) is False and win._ollama_worker is None
    assert "Weak evidence" in win.status_label.text()
    assert system not in win._eligible_purpose_systems()
    viewer = win._show_purpose_evidence(system)
    assert viewer.strength_label.text().startswith("Weak evidence (0 points, 2 needed)")
    viewer.close()
    system.purpose_evidence = "Own description (package.json): A local voice assistant.\nNames in code: a()"
    win._refresh_sun_anchor_card()
    assert win.btn_suggest_ai.isEnabled()
    assert system in win._eligible_purpose_systems()
    viewer = win._show_purpose_evidence(system)
    assert viewer.strength_label.text() == (
        "Strong evidence (2 points, 2 needed): own description in package.json.")
    viewer.close()


def test_evidence_viewer_with_text_and_empty_note(env):
    win, system, _ = _purpose_window(env)
    system.purpose_evidence = "README.md: NOVA is a voice assistant.\nEntry point: asistan.py"
    viewer = win._show_purpose_evidence(system)
    assert viewer is not None and not viewer.isModal() and viewer.isVisible()
    assert viewer.text_view.toPlainText().startswith("README.md: NOVA")
    assert viewer.text_view.isReadOnly()
    # One muted line says how strong the evidence is ("README.md: ..." is not a known label).
    assert viewer.strength_label is not None
    assert viewer.strength_label.text().startswith("Weak evidence (0 points, 2 needed)")
    system.purpose_evidence = ""
    viewer2 = win._show_purpose_evidence(system)
    assert viewer2.text_view is None and "Re-scan the project" in viewer2.empty_label.text()
    assert viewer2.strength_label is None
    viewer2.close()


class _FakeCheckWorker(QtCore.QObject):
    checked = _Signal(bool)
    answer = True
    created = []

    def __init__(self, timeout=1.5, parent=None):
        super().__init__(parent)
        _FakeCheckWorker.created.append(self)

    def start(self):
        QtCore.QTimer.singleShot(0, lambda: self.checked.emit(_FakeCheckWorker.answer))

    def wait(self, *a):
        return True

    def isRunning(self):
        return False


class _FakeOllamaWorker(QtCore.QObject):
    """Stands in for the colleague's OllamaEnhanceWorker (same names, no network)."""

    progressText = _Signal(str)
    systemEnhanced = _Signal(str, str)
    moduleDescribed = _Signal(str, str, str)
    purposeSuggested = _Signal(str, str)
    purposeRejected = _Signal(str, str)
    finishedEnhance = _Signal(bool, str)
    created = []

    def __init__(self, systems, model_name=None, parent=None, force=False, language=None,
                 describe_modules=True, summarize_project=True, write_purpose=True, purpose_only=False,
                 purpose_manual=False):
        super().__init__(parent)
        self.systems = list(systems)
        self.ids = [s.id for s in systems]
        self.force, self.write_purpose, self.purpose_only = force, write_purpose, purpose_only
        self.purpose_manual = purpose_manual
        self.summarize_project = summarize_project
        self.running = False
        _FakeOllamaWorker.created.append(self)

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def isFinished(self):
        return not self.running

    def wait(self, *a):
        return True

    def requestInterruption(self):
        self.running = False

    def snapshot_for(self, sid):
        return None

    def finish(self, ok=True, msg="done"):
        self.running = False
        self.finishedEnhance.emit(ok, msg)


@pytest.fixture
def fake_ollama(monkeypatch):
    _FakeCheckWorker.created = []
    _FakeCheckWorker.answer = True
    _FakeOllamaWorker.created = []
    monkeypatch.setattr(mw, "OllamaServerCheckWorker", _FakeCheckWorker)
    monkeypatch.setattr(mw, "OllamaEnhanceWorker", _FakeOllamaWorker)
    return {"check": _FakeCheckWorker, "worker": _FakeOllamaWorker}


def _purpose_targets(win):
    base = win.store.systems[0]
    base.purpose, base.purpose_source, base.purpose_evidence = "", "", ""
    mk = []
    for name, evidence, purpose, source in (
        ("Eligible", "README intro (README.md): a chat bot", "", ""),
        ("UserWritten", "README intro (README.md): x", "Mine.", "user"),
        ("NoEvidence", "", "", ""),
        ("AiEmpty", "README intro (README.md): runs things\nEntry point: run.py", "", "ai"),
        ("Weak", "Names in code: a()\nEntry point: run.py", "", ""),  # weak: never sent
        ("HasReadme", "README intro (README.md): y", "From readme.", "readme"),
    ):
        s = SolarSystem(name=name, project_name=name, source="codebase")
        s.purpose_evidence, s.purpose, s.purpose_source = evidence, purpose, source
        win.store.systems.append(s)
        mk.append(s)
    win.store.save()
    return {s.project_name: s for s in mk}


def test_auto_purpose_after_sync_targets_eligible_and_stores_suggestions(env, fake_ollama):
    # Changed: the answers are stored as suggestions (purpose_suggestion); the purpose stays empty.
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    by = _purpose_targets(win)
    win._detected_ollama_model = "m:1"
    win._sync_agent_histories()
    wait_until(lambda: not win.is_store_busy())
    wait_until(lambda: fake_ollama["worker"].created, timeout=5)
    worker = fake_ollama["worker"].created[-1]
    assert worker.purpose_only is True and worker.write_purpose is True and worker.force is False
    assert sorted(s.project_name for s in worker.systems) == ["AiEmpty", "Eligible"]
    assert win.btn_status_stop.isVisible()
    assert worker.purpose_manual is False
    assert "suggesting project descriptions (0/2)" in win.status_label.text()

    worker.purposeSuggested.emit(by["Eligible"].id, "  A chat bot\nfor Discord.  ")
    assert "(1/2)" in win.status_label.text()
    by["AiEmpty"].purpose, by["AiEmpty"].purpose_source = "Typed meanwhile.", "user"
    worker.purposeSuggested.emit(by["AiEmpty"].id, "AI text")
    worker.finish(True, "done")
    assert by["Eligible"].purpose == "" and by["Eligible"].purpose_source == ""
    assert by["Eligible"].purpose_suggestion == "A chat bot for Discord."
    assert by["AiEmpty"].purpose == "Typed meanwhile." and by["AiEmpty"].purpose_source == "user"
    assert by["AiEmpty"].purpose_suggestion == ""
    assert by["UserWritten"].purpose == "Mine."
    saved = json.loads(win.store.storage_path.read_text(encoding="utf-8"))
    row = next(s for s in saved["systems"] if s["id"] == by["Eligible"].id)
    assert row["purpose_suggestion"] == "A chat bot for Discord." and row["purpose"] == ""
    assert saved["format_version"] == 2
    assert not win.btn_status_stop.isVisible()
    assert "Suggested" in win.status_label.text()


def test_auto_purpose_respects_settings_server_and_busy_state(env, fake_ollama):
    win = env["make_window"](onboarded=False)
    win.show()
    spin(0.2)
    _purpose_targets(win)
    win._detected_ollama_model = "m:1"
    win.store.set_setting("disable_ollama", True)
    win._maybe_auto_suggest_purposes()
    win.store.set_setting("disable_ollama", False)
    win.store.set_setting("ollama_auto_purpose", False)
    win._maybe_auto_suggest_purposes()
    assert fake_ollama["check"].created == [] and win._ollama_server_check_worker is None
    win.store.set_setting("ollama_auto_purpose", True)

    fake_ollama["check"].answer = False  # server down
    win._maybe_auto_suggest_purposes()
    wait_until(lambda: win._ollama_server_check_worker is None)
    spin(0.05)
    assert fake_ollama["worker"].created == []

    fake_ollama["check"].answer = True
    win._set_store_busy(True, "busy")
    win._maybe_auto_suggest_purposes()
    assert win._ollama_server_check_worker is None and win._auto_purpose_queued
    spin(0.05)
    assert fake_ollama["worker"].created == []
    win._set_store_busy(False)
    wait_until(lambda: fake_ollama["worker"].created, timeout=5)
    worker = fake_ollama["worker"].created[-1]
    # While that run is active a second request is queued, not started.
    win._maybe_auto_suggest_purposes()
    assert len(fake_ollama["worker"].created) == 1 and win._auto_purpose_queued
    worker.finish(True, "done")


def test_suggest_with_ai_is_manual_stores_a_suggestion_and_never_touches_user_text(env, fake_ollama):
    # Changed (was "Ask Ollama forces one project and confirms user text"): the manual
    # request stores a suggestion, not the purpose; for a "user" purpose there is no AI
    # button and no request at all (no "replace yours?" dialog any more).
    win, system, _ = _purpose_window(env, n_extra=1)
    win._detected_ollama_model = "m:1"
    assert win._suggest_purpose_with_ai(system) is False and "Re-scan" in win.status_label.text()
    system.purpose_evidence = "README intro (README.md): voice assistant"
    assert win._suggest_purpose_with_ai(system) is True
    worker = fake_ollama["worker"].created[-1]
    assert worker.ids == [system.id] and worker.purpose_manual is True and worker.purpose_only is True
    assert "description for 'NOVA'" in win.status_label.text()
    worker.purposeSuggested.emit(system.id, "A voice assistant for the desktop.")
    worker.finish()
    assert system.purpose == "" and system.purpose_source == ""
    assert system.purpose_suggestion == "A voice assistant for the desktop."
    assert win.suggestion_box.isVisible() and not win.purpose_view.isVisible()
    assert "Suggestion ready" in win.status_label.text()

    system.purpose, system.purpose_source, system.purpose_suggestion = "Mine.", "user", ""
    env["boxes"]["question"].calls.clear()
    win._refresh_sun_anchor_card()
    assert not win.btn_suggest_ai.isVisible()
    assert win._suggest_purpose_with_ai(system) is False
    assert not env["boxes"]["question"].calls
    assert len(fake_ollama["worker"].created) == 1
    assert system.purpose == "Mine." and system.purpose_source == "user"


def test_manual_rejection_shows_one_quiet_status_line(env, fake_ollama):
    win, system, _ = _purpose_window(env)
    win._detected_ollama_model = "m:1"
    system.purpose_evidence = "README intro (README.md): voice assistant"
    assert win._suggest_purpose_with_ai(system)
    worker = fake_ollama["worker"].created[-1]
    worker.purposeRejected.emit(
        system.id, "No suggestion for 'NOVA': the answer did not match the evidence (reason: no_overlap).")
    worker.finish(False, "failed")
    assert win.status_label.text() == "No suggestion: the answer did not match the evidence."
    assert win._suggest_purpose_with_ai(system)
    worker = fake_ollama["worker"].created[-1]
    worker.purposeRejected.emit(system.id, "No suggestion for 'NOVA': the answer was too generic to say what "
                                           "the project is for (reason: generic).")
    worker.finish(False, "failed")
    assert win.status_label.text() == "No suggestion: the answer was too generic to say what the project is for."
    assert win._suggest_purpose_with_ai(system)
    worker = fake_ollama["worker"].created[-1]
    worker.purposeRejected.emit(system.id, "Ollama could not tell what 'NOVA' is for (answered UNKNOWN) (reason: unknown).")
    worker.finish(False, "failed")
    assert win.status_label.text().startswith("No suggestion:")
    assert system.purpose_suggestion == "" and system.purpose == ""
    # Automatic runs stay silent about rejections.
    win.store.set_setting("ollama_auto_purpose", True)
    win._maybe_auto_suggest_purposes()
    wait_until(lambda: len(fake_ollama["worker"].created) == 4, timeout=5)
    worker = fake_ollama["worker"].created[-1]
    assert worker.purpose_manual is False
    worker.purposeRejected.emit(system.id, "No suggestion for 'NOVA': the answer did not match the evidence (reason: no_overlap).")
    worker.finish(False, "x")
    assert "No suggestion" not in win.status_label.text()


def test_full_enhance_passes_write_purpose_but_never_overwrites_user(env, fake_ollama):
    win, system, _ = _purpose_window(env)
    win._detected_ollama_model = "m:1"
    system.purpose, system.purpose_source = "Mine.", "user"
    win._run_ollama_for_systems([system])
    worker = fake_ollama["worker"].created[-1]
    assert worker.write_purpose is True and worker.purpose_only is False
    worker.purposeSuggested.emit(system.id, "AI text.")
    worker.finish()
    assert system.purpose == "Mine." and system.purpose_source == "user"
    assert system.purpose_suggestion == ""  # changed: not even stored as a suggestion


def test_folder_analysis_writes_purpose_before_modules(env, fake_ollama, monkeypatch):
    win, system, _ = _purpose_window(env)
    system.purpose_evidence = "README intro (README.md): voice assistant"
    win._detected_ollama_model = "m:1"
    described = []
    monkeypatch.setattr(win, "_maybe_auto_describe_modules", lambda s: described.append(s.id))
    win._on_codebase_folder_analyzed(True, "ok", {"system": system, "folder": env["tmp"]})
    wait_until(lambda: fake_ollama["worker"].created, timeout=5)
    worker = fake_ollama["worker"].created[-1]
    assert worker.purpose_only and described == [], "modules must wait for the purpose"
    worker.purposeSuggested.emit(system.id, "A voice assistant that answers questions.")
    worker.finish()
    wait_until(lambda: described == [system.id], timeout=5)
    assert system.purpose == "" and system.purpose_suggestion == "A voice assistant that answers questions."  # changed: a suggestion


def test_purpose_only_not_started_with_an_old_worker(env, monkeypatch):
    class OldWorker(_FakeOllamaWorker):
        def __init__(self, systems, model_name=None, parent=None, force=False, language=None,
                     describe_modules=True, summarize_project=True):
            super().__init__(systems, model_name, parent, force, language, describe_modules, summarize_project)

    monkeypatch.setattr(mw, "OllamaEnhanceWorker", OldWorker)
    win, system, _ = _purpose_window(env)
    system.purpose_evidence = "README intro (README.md): x"
    win._detected_ollama_model = "m:1"
    assert win._suggest_purpose_with_ai(system) is False
    assert win._ollama_worker is None
    # The full run still starts (without the unknown keyword).
    win._run_ollama_for_systems([system])
    assert isinstance(win._ollama_worker, OldWorker)
    win._ollama_worker.finish()


# ----------------------------------------------------------------------
# Removing projects / ignored folders
# ----------------------------------------------------------------------
def test_remove_project_from_helios_and_undo(env, monkeypatch):
    win, system, (other,) = _purpose_window(env, n_extra=1)
    other.workspace_path = str(env["tmp"] / "tablo")
    monkeypatch.setattr(mw.RemoveProjectDialog, "ask", classmethod(lambda cls, s, p=None: cls.REMOVE))
    assert win._remove_project(other) is True
    assert all(s.id != other.id for s in win.store.systems)
    assert win.store.get_setting("ignored_folders", []) == []
    assert win.canvas.view_mode == "galaxy"
    saved = json.loads(win.store.storage_path.read_text(encoding="utf-8"))
    assert all(s["id"] != other.id for s in saved["systems"])
    win._on_undo()
    assert any(s.id == other.id for s in win.store.systems)


def test_remove_and_never_import_again_with_undo_redo(env, monkeypatch):
    win, system, (other,) = _purpose_window(env, n_extra=1)
    folder = env["tmp"] / "work" / ".." / "tablo"
    other.workspace_path = str(folder)
    monkeypatch.setattr(mw.RemoveProjectDialog, "ask", classmethod(lambda cls, s, p=None: cls.IGNORE))
    win._remove_project(other)
    ignored = win.store.get_setting("ignored_folders", [])
    assert ignored == [os.path.normpath(str(env["tmp"] / "tablo"))]
    assert mw.folder_is_ignored(win.store, env["tmp"] / "tablo")
    win._on_undo()
    assert any(s.id == other.id for s in win.store.systems)
    assert win.store.get_setting("ignored_folders", []) == []
    win._on_redo()
    assert all(s.id != other.id for s in win.store.systems)
    assert win.store.get_setting("ignored_folders", []) == ignored


def test_remove_dialog_choices(env, monkeypatch):
    win, system, _ = _purpose_window(env)
    system.workspace_path = ""
    dlg = mw.RemoveProjectDialog(system, win)
    assert dlg.btn_ignore is None and dlg.btn_remove.text() == "Remove from Helios"
    system.workspace_path = str(env["tmp"])
    dlg2 = mw.RemoveProjectDialog(system, win)
    assert dlg2.btn_ignore is not None
    dlg2.btn_ignore.click()
    assert dlg2.choice == mw.RemoveProjectDialog.IGNORE
    # Without a folder, "ignore" falls back to a plain removal.
    system.workspace_path = ""
    win.store.systems.append(SolarSystem(name="Keep", project_name="Keep", source="manual"))
    monkeypatch.setattr(mw.RemoveProjectDialog, "ask", classmethod(lambda cls, s, p=None: cls.IGNORE))
    win._remove_project(system)
    assert all(s.id != system.id for s in win.store.systems)
    assert win.store.get_setting("ignored_folders", []) == []


def test_remove_last_project_leaves_empty_state(env, monkeypatch):
    win, system, _ = _purpose_window(env)
    assert len(win.store.systems) == 1
    monkeypatch.setattr(mw.RemoveProjectDialog, "ask", classmethod(lambda cls, s, p=None: cls.REMOVE))
    win._remove_project(system)
    assert win.store.systems == []
    assert win.canvas.active_system is None
    assert win.canvas.empty_overlay.isVisible() and win.canvas.empty_overlay.mode == "no_projects"
    assert win.sun_title_display.text() == "No project yet"
    assert not win.purpose_view.isVisible() and not win.purpose_empty.isVisible()
    assert win.hierarchy_tree.topLevelItemCount() == 0
    win._on_undo()
    assert [s.id for s in win.store.systems] == [system.id]
    assert win.canvas.active_system is win.store.systems[0]


def test_settings_auto_purpose_checkbox_and_ignored_list(env):
    win, system, _ = _purpose_window(env)
    mw.add_ignored_folder(win.store, str(env["tmp"] / "a"))
    mw.add_ignored_folder(win.store, str(env["tmp"] / "b"))
    assert not mw.add_ignored_folder(win.store, str(env["tmp"] / "a" / "."))
    dlg = mw.HeliosSettingsDialog(win.store, win)
    assert dlg.chk_auto_purpose.isChecked()
    assert len(dlg.ignored_rows) == 2
    btn = dlg.ignored_rows[0].findChild(QtWidgets.QPushButton)
    assert btn.text() == "Stop ignoring"
    btn.click()
    assert win.store.get_setting("ignored_folders") == [os.path.normpath(str(env["tmp"] / "b"))]
    assert len(dlg.ignored_rows) == 1
    dlg.chk_auto_purpose.setChecked(False)
    dlg._save_settings_and_close()
    assert win.store.get_setting("ollama_auto_purpose") is False
    dlg.deleteLater()


def test_analyze_ignored_folder_is_intercepted(env, monkeypatch):
    win, system, _ = _purpose_window(env)
    folder = env["tmp"] / "tablo"
    folder.mkdir()
    mw.add_ignored_folder(win.store, folder)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(folder)))
    asked = []
    monkeypatch.setattr(win, "_confirm_analyze_ignored", lambda f: asked.append(f) or False)
    win._analyze_codebase_folder()
    assert asked == [folder] and not win.is_store_busy() and win._store_worker is None
    assert mw.folder_is_ignored(win.store, folder)
    monkeypatch.setattr(win, "_confirm_analyze_ignored", lambda f: True)
    win._analyze_codebase_folder()
    assert not mw.folder_is_ignored(win.store, folder)
    wait_until(lambda: not win.is_store_busy())
    assert env["boxes"]["warning"].calls[-1][1] == "Nothing to analyze"
    # A folder inside an ignored one is ignored too; "Stop ignoring" clears the covering entry.
    sub = folder / "inner"
    sub.mkdir()
    mw.add_ignored_folder(win.store, folder)
    assert mw.folder_is_ignored(win.store, sub)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(sub)))
    win._analyze_codebase_folder()
    assert win.store.get_setting("ignored_folders") == []
    wait_until(lambda: not win.is_store_busy())


def test_readme_markdown_lists_render_as_clean_rows():
    from ui.inspector import plain_text_view, split_blocks, strip_repeated_text

    text = "1. **Project Core (Core Plan):**\n2. Uses *Whisper* to listen\n- __Bold__ item"
    blocks = split_blocks(text)
    assert blocks[0] == ("numbered", "1\0Project Core (Core Plan)")
    view = plain_text_view(text)
    assert "*" not in view and "__" not in view and "1. Project Core (Core Plan)" in view
    about = "PROJECT PURPOSE & OVERVIEW (WHAT IT DOES):\nA voice assistant that runs commands.\n\nTECH STACK:\nPython"
    out = strip_repeated_text(about, "A voice assistant that runs commands.")
    assert "voice assistant" not in out and "PROJECT PURPOSE" not in out and "TECH STACK" in out
    assert "Purpose:" not in strip_repeated_text("Purpose: Not written yet\nEntry point: a.py", "Real.")


# ----------------------------------------------------------------------
# is_user_modified only changes on real user actions
# ----------------------------------------------------------------------
from PySide6 import QtTest  # noqa: E402


def _flags(system):
    out = {("system", system.id): system.is_user_modified}
    for p in system.planets:
        out[("planet", p.id)] = p.is_user_modified
        for s in p.satellites:
            out[("sat", s.id)] = s.is_user_modified
    for l in system.links:
        out[("link", l.id)] = l.is_user_modified
    return out


def _mouse(view, kind, pos, buttons):
    vp = view.viewport()
    btn = QtCore.Qt.MouseButton.LeftButton
    ev_type = {"press": QtCore.QEvent.Type.MouseButtonPress, "move": QtCore.QEvent.Type.MouseMove,
               "release": QtCore.QEvent.Type.MouseButtonRelease}[kind]
    gpos = vp.mapToGlobal(pos)
    ev = QtGui.QMouseEvent(ev_type, QtCore.QPointF(pos), QtCore.QPointF(gpos),
                           QtCore.Qt.MouseButton.NoButton if kind == "move" else btn,
                           buttons, QtCore.Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(vp, ev)


def _drag(view, item_scene_pos, delta):
    start = view.mapFromScene(item_scene_pos)
    left = QtCore.Qt.MouseButton.LeftButton
    _mouse(view, "press", start, left)
    steps = 6
    for i in range(1, steps + 1):
        pt = start + QtCore.QPoint(int(delta.x() * i / steps), int(delta.y() * i / steps))
        _mouse(view, "move", pt, left)
    end = start + delta
    _mouse(view, "release", end, QtCore.Qt.MouseButton.NoButton)
    spin(0.02)


def test_view_actions_never_mark_nodes_user_modified(env):
    win = env["make_window"](onboarded=False)
    win.resize(1400, 900)
    win.show()
    spin(0.2)
    system, a, b, c, sa, sb = _rich_system(win)
    ext = system.add_planet(title="External Libraries", summary="pkgs")
    ext.kind = "external"
    ext.add_satellite(title="numpy").kind = "package"
    hist = system.add_planet(title="Work history", summary="sessions")
    hist.kind = "history"
    for p in system.planets:
        p.is_user_modified = False
        for s in p.satellites:
            s.is_user_modified = False
    for l in system.links:
        l.is_user_modified = False
    before = _flags(system)
    canvas = win.canvas
    canvas.enter_solar_system(system)
    canvas.fit_to_view()
    win.btn_fit_view.click()
    for node, kind in ((a, "planet"), (sa, "satellite"), (ext, "planet"), (system, "sun")):
        canvas.select_object(node, kind)
    item = canvas.planet_item_map[b.id]
    canvas.notify_planet_hovered(item, True)
    canvas.notify_planet_hovered(item, False)
    win.btn_toggle_orbit.click()
    spin(0.25)
    win.btn_toggle_orbit.click()
    win.btn_show_details.click()
    win.btn_show_details.click()
    win.resize(1366, 768)
    spin(0.1)
    win.resize(1400, 900)
    spin(0.1)
    # A click without movement and a tiny jitter are not drags.
    _drag(canvas, canvas.planet_item_map[ext.id].scenePos(), QtCore.QPoint(0, 0))
    _drag(canvas, canvas.planet_item_map[c.id].scenePos(), QtCore.QPoint(2, 1))
    # Dragged out 40 px and back to (almost) where it started: nothing really moved.
    start = canvas.mapFromScene(canvas.planet_item_map[c.id].scenePos())
    left = QtCore.Qt.MouseButton.LeftButton
    _mouse(canvas, "press", start, left)
    for d in (10, 25, 40, 25, 10, 1):
        _mouse(canvas, "move", start + QtCore.QPoint(d, 0), left)
    _mouse(canvas, "release", start + QtCore.QPoint(1, 0), QtCore.Qt.MouseButton.NoButton)
    spin(0.02)
    canvas.rebuild_scene()
    assert _flags(system) == before

    # The drawing tool "add component" press on a module followed by a move is not a drag either.
    canvas.set_drawing_tool("add_satellite")
    _drag(canvas, canvas.planet_item_map[b.id].scenePos(), QtCore.QPoint(80, 40))
    canvas.set_drawing_tool("select")
    assert b.is_user_modified is False
    new_sat = b.satellites[-1]
    assert new_sat.is_user_modified is True, "a component created with the drawing tool counts as user-made"


def test_real_drag_marks_only_the_dragged_module(env):
    win = env["make_window"](onboarded=False)
    win.resize(1400, 900)
    win.show()
    spin(0.2)
    system, a, b, c, *_ = _rich_system(win)
    canvas = win.canvas
    canvas.enter_solar_system(system)
    spin(0.05)
    before = _flags(system)
    item = canvas.planet_item_map[c.id]
    old_angle = c.angle
    _drag(canvas, item.scenePos(), QtCore.QPoint(60, 50))
    assert c.is_user_modified is True and c.angle != old_angle
    after = _flags(system)
    changed = [k for k in after if after[k] != before[k]]
    assert changed == [("planet", c.id)]
    saved = json.loads(win.store.storage_path.read_text(encoding="utf-8"))
    sp = next(p for s in saved["systems"] for p in s["planets"] if p["id"] == c.id)
    assert sp["is_user_modified"] is True
    win._on_undo()
    restored = next(p for s in win.store.systems for p in s.planets if p.id == c.id)
    assert restored.is_user_modified is False


# ----------------------------------------------------------------------
# Component fan never covers the project (sun) or its title; fit zoom floor
# ----------------------------------------------------------------------
@pytest.mark.parametrize("angle", [0.0, 90.0, 180.0, 270.0])
def test_fan_labels_never_cover_the_sun_or_its_title(env, angle):
    win = env["make_window"](onboarded=False)
    win.resize(1400, 900)
    win.show()
    spin(0.2)
    system, a, *_ = _rich_system(win)
    for i in range(20):
        a.add_satellite(title=f"a_rather_long_component_name_{i}()")
    a.is_user_modified = True  # placed by hand close to the sun
    a.angle, a.orbit_radius = angle, 130.0
    _open(win, system)
    canvas = win.canvas
    canvas.select_object(a, "planet")
    item = canvas.planet_item_map[a.id]
    sun = canvas.sun_item
    r = sun.radius
    blocked = [QtCore.QRectF(-r, -r, 2 * r, 2 * r), sun.name_rect().translated(sun.pos())]
    visible = [s for s in item.satellite_items if s.isVisible()]
    assert visible, "some components must still be shown"
    for s in visible:
        rect = s.mapRectToScene(s._label_rect()).united(s.sceneBoundingRect())
        for b in blocked:
            assert not rect.intersects(b), (angle, s.satellite.title, rect, b)
    if item.more_chip.isVisible():
        chip = item.more_chip.sceneBoundingRect()
        assert not any(chip.intersects(b) for b in blocked)
        assert item.more_chip.count == 21 - len(visible)
    else:
        assert len(visible) == 21


def test_fan_of_a_module_right_of_the_sun_faces_outward(env):
    win = env["make_window"](onboarded=False)
    win.resize(1400, 900)
    win.show()
    spin(0.2)
    system, a, *_ = _rich_system(win)
    for i in range(10):
        a.add_satellite(title=f"comp_{i}()")
    a.is_user_modified = True
    a.angle, a.orbit_radius = 0.0, 260.0
    _open(win, system)
    win.canvas.select_object(a, "planet")
    item = win.canvas.planet_item_map[a.id]
    for s in item.satellite_items:
        if s.isVisible():
            assert s.label_side == 1 and s.pos().x() > 0


def test_fit_zoom_floor_keeps_titles_readable_at_1366x768(env):
    from ui import theme as th

    win = env["make_window"](onboarded=False)
    win.resize(1366, 768)
    win.show()
    spin(0.2)
    system = win.store.systems[0]
    system.planets.clear()
    system.links.clear()
    for i in range(30):
        p = system.add_planet(title=f"module_number_{i}.py", summary="x")
        p.kind = "file"
        p.add_satellite(title="f()")
    _open(win, system)
    win.canvas.fit_to_view()
    scale = win.canvas.transform().m11()
    assert scale * th.FS_BODY >= th.MAP_MIN_TITLE_PX - 1e-6
    vis = win.canvas.mapToScene(win.canvas.viewport().rect()).boundingRect()
    shown = [it for it in win.canvas.planet_items if vis.contains(it.scene_label_rect())]
    assert shown, "part of the map (the top of the ring) stays in view; the rest can be panned to"
    # A project that fits at a readable size is shown whole, sun included.
    system.planets[:] = system.planets[:6]
    _open(win, system)
    win.canvas.fit_to_view()
    vis = win.canvas.mapToScene(win.canvas.viewport().rect()).boundingRect()
    assert vis.contains(QtCore.QPointF(0, 0))
    assert all(vis.contains(it.sceneBoundingRect()) for it in win.canvas.planet_items)


def test_purpose_run_with_the_real_ollama_worker(env, monkeypatch):
    """Integration with connectors.ollama_enhancer.OllamaEnhanceWorker (fake HTTP opener)."""
    win, system, (other,) = _purpose_window(env, n_extra=1)
    system.purpose_evidence = "README intro (README.md): NOVA listens to the microphone and runs PC commands."
    other.purpose_evidence = "README intro (README.md): x"
    other.purpose, other.purpose_source = "Mine.", "user"
    opener = _GenOpener("A local voice assistant that runs PC commands.")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    monkeypatch.setattr(oe.OllamaEnhancer, "is_server_running", staticmethod(lambda timeout=1.5: True))
    win._detected_ollama_model = "m:1"
    win._maybe_auto_suggest_purposes()
    wait_until(lambda: win._ollama_worker is not None, timeout=5)
    assert isinstance(win._ollama_worker, oe.OllamaEnhanceWorker) and win._ollama_worker.purpose_only
    wait_until(lambda: win._ollama_worker.isFinished() and not win._is_ollama_running(), timeout=10)
    # Changed: the real worker's answer becomes a suggestion, never the purpose.
    wait_until(lambda: bool(system.purpose_suggestion), timeout=5)
    assert system.purpose_suggestion == "A local voice assistant that runs PC commands."
    assert system.purpose == "" and system.purpose_source == ""
    assert other.purpose == "Mine." and other.purpose_source == "user"
    assert opener.count == 1, "one request per eligible project"


def test_identity_line_in_header_cards_tooltips_and_search(env):
    from connectors.codebase_importer import IDENTITY_TOOLTIP

    win, system, (other,) = _purpose_window(env, n_extra=1)
    system.identity = "Python · faster_whisper, sounddevice, ollama · entry: asistan.py"
    system.purpose, system.purpose_source = "", ""
    # `other` has no identity field value: derived from its stored facts (old galaxy.json).
    other.purpose_evidence = "Project shape: JavaScript, 2 code files; web server / API (express)\nEntry point: server.js (x)"
    win._populate_tree()
    win.canvas.enter_solar_system(system)
    win._refresh_sun_anchor_card()
    spin(0.05)
    # Header: shown with an empty purpose too, muted, read-only, never called "Purpose".
    assert win.purpose_empty.isVisible() and win.lbl_identity.isVisible()
    assert win.lbl_identity.text() == system.identity
    assert win.lbl_identity.toolTip() == IDENTITY_TOOLTIP
    assert win.lbl_identity.objectName() == "IdentityLine" and "Purpose" not in win.lbl_identity.text()
    lay = win.sun_card.layout()
    order = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget() is not None]
    assert order.index(win.purpose_edit_box) < order.index(win.lbl_identity) < order.index(win.project_chips_widget)
    system.purpose, system.purpose_source = "Listens and answers.", "user"
    win._refresh_sun_anchor_card()
    assert win.purpose_view.isVisible() and win.lbl_identity.isVisible()
    assert win.lbl_purpose.text() == "Listens and answers."  # the purpose itself is unchanged
    assert system.identity in win.canvas.sun_item.toolTip()
    # Cards: both projects show it (the second one derived), tooltips too.
    win.canvas.switch_to_galaxy_view()
    cards = {c.system.id: c for c in win.canvas.galaxy_items}
    assert cards[system.id].identity_text().startswith("Python · faster_whisper")
    assert cards[other.id].identity_text() == "JavaScript web server · express · entry: server.js"
    assert other.identity == ""  # display-only derivation, nothing written
    assert "derived from the code analysis" in cards[other.id].toolTip()
    win.canvas.viewport().grab()  # paints without errors
    # Search matches the identity line (cards and tree).
    win.search_input.setText("web server")
    dimmed = {c.system.id: c.is_dimmed for c in win.canvas.galaxy_items}
    assert dimmed[other.id] is False and dimmed[system.id] is True
    root = win.hierarchy_tree.invisibleRootItem()
    visible = [root.child(i).text(0) for i in range(root.childCount()) if not root.child(i).isHidden()]
    assert visible == ["P0"]
    win.search_input.clear()
    # A manual project that was never scanned shows nothing extra.
    system.identity, system.source, system.purpose_evidence = "", "manual", ""
    system.planets = [p for p in system.planets if p.kind != "external"]
    win.canvas.enter_solar_system(system)
    win._refresh_sun_anchor_card()
    assert not win.lbl_identity.isVisible()


# ----------------------------------------------------------------------
# AI purpose suggestions in the header and on cards
# ----------------------------------------------------------------------
def _suggestion_window(env, ollama=False):
    win, system, extras = _purpose_window(env, n_extra=1)
    system.purpose_evidence = "Own description (package.json): A local voice assistant for the desktop."
    system.purpose_suggestion = "A local voice assistant that runs PC commands."
    win.store.set_setting("disable_ollama", not ollama)
    win._detected_ollama_model = "m:1" if ollama else None
    win._refresh_ai_controls()
    win._refresh_sun_anchor_card()
    spin(0.05)
    return win, system, extras


def test_header_shows_suggested_row_with_three_buttons_not_as_purpose(env):
    win, system, _ = _suggestion_window(env)
    assert win.suggestion_box.isVisible() and not win.purpose_view.isVisible() and not win.purpose_empty.isVisible()
    assert win.lbl_suggestion_tag.text() == "Suggested"
    # The full suggestion is shown (never elided), however long it is.
    long_text = ("A local voice assistant for the desktop that listens through the microphone, answers "
                 "questions and runs PC commands such as opening applications and changing the volume. "
                 "It keeps its settings in one file so microphone, voice and model can be changed later.")
    system.purpose_suggestion = long_text
    win._refresh_sun_anchor_card()
    spin(0.05)
    assert win.lbl_suggestion.text() == long_text and "…" not in win.lbl_suggestion.text()
    assert win.lbl_suggestion.height() >= win.lbl_suggestion.heightForWidth(win.lbl_suggestion.width()) - 1
    system.purpose_suggestion = "A local voice assistant that runs PC commands."
    win._refresh_sun_anchor_card()
    assert win.lbl_suggestion.text() == system.purpose_suggestion and win.lbl_purpose.text() == ""
    assert [b.text() for b in (win.btn_suggestion_accept, win.btn_suggestion_edit, win.btn_suggestion_dismiss)] == [
        "Accept", "Edit", "Dismiss"]
    assert all(b.isVisible() for b in (win.btn_suggestion_accept, win.btn_suggestion_edit, win.btn_suggestion_dismiss))
    lay = win.sun_card.layout()
    order = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget() is not None]
    assert order.index(win.purpose_view) < order.index(win.suggestion_box) < order.index(win.lbl_identity)
    # Never presented as the purpose: core plan line, search, tooltips, prompt copy.
    assert "voice assistant that runs" not in system.core_plan.splitlines()[0]
    assert "runs pc commands" not in mw._project_search_text(system)
    win._copy_project_as_prompt(system)
    assert "Purpose:" not in QtWidgets.QApplication.clipboard().text()
    assert system.purpose_suggestion not in win.canvas.sun_item.toolTip().split("\n")[1:2]


def test_accept_edit_dismiss_through_the_widgets(env):
    win, system, _ = _suggestion_window(env)
    win.store.save()
    text = system.purpose_suggestion
    wait_until(lambda: not win.is_store_busy())
    win.btn_suggestion_accept.click()
    assert (system.purpose, system.purpose_source, system.purpose_suggestion) == (text, "ai", "")
    assert win.purpose_view.isVisible() and not win.suggestion_box.isVisible()
    assert win.lbl_purpose_source.text() == "AI suggestion you accepted"
    assert system.is_user_modified is False
    saved = json.loads(win.store.storage_path.read_text(encoding="utf-8"))
    row = next(r for r in saved["systems"] if r["id"] == system.id)
    assert row["purpose_source"] == "ai" and row["purpose_suggestion"] == "" and saved["format_version"] == 2
    win._on_undo()
    system = next(s for s in win.store.systems if s.id == system.id)
    assert system.purpose == "" and system.purpose_suggestion == text

    # Edit: prefilled; Cancel keeps the suggestion; Save makes it the user's purpose.
    win.canvas.enter_solar_system(system)
    wait_until(lambda: win.canvas.active_system is system)
    spin(0.05)
    win._refresh_sun_anchor_card()
    win.btn_suggestion_edit.click()
    assert win.purpose_edit_box.isVisible() and win.edit_purpose.toPlainText() == text
    assert not win.suggestion_box.isVisible()
    win.btn_purpose_cancel.click()
    assert win.suggestion_box.isVisible() and system.purpose_suggestion == text and system.purpose == ""
    win.btn_suggestion_edit.click()
    win.edit_purpose.setPlainText("My voice assistant.")
    win.btn_purpose_save.click()
    assert (system.purpose, system.purpose_source, system.purpose_suggestion) == ("My voice assistant.", "user", "")
    assert win.lbl_purpose_source.text() == "Written by you" and not win.suggestion_box.isVisible()
    win._on_undo()
    system = next(s for s in win.store.systems if s.id == system.id)

    # Dismiss: gone, and no automatic suggestion for the same evidence.
    win.canvas.enter_solar_system(system)
    wait_until(lambda: win.canvas.active_system is system)
    spin(0.05)
    win._refresh_sun_anchor_card()
    win.btn_suggestion_dismiss.click()
    assert system.purpose_suggestion == "" and system.purpose == ""
    assert win.purpose_empty.isVisible() and not win.suggestion_box.isVisible()
    assert system not in win._eligible_purpose_systems()


def test_empty_header_shows_add_line_that_opens_editor(env):
    win, system, _ = _purpose_window(env)
    win.store.set_setting("disable_ollama", True)
    win._refresh_ai_controls()
    win._refresh_sun_anchor_card()
    assert win.purpose_empty.isVisible() and win.btn_purpose_add.text() == "Add what this project is for"
    win.btn_purpose_add.click()
    assert win.purpose_edit_box.isVisible() and win.edit_purpose.toPlainText() == ""


def test_ollama_off_means_no_ai_controls_anywhere(env):
    win, system, _ = _suggestion_window(env, ollama=False)
    assert not win.btn_ollama_single.isVisible()
    assert not win.btn_suggest_ai.isVisible()
    assert not win.lbl_ollama_status.isVisible() and win.lbl_ollama_status.text() == ""
    assert win.canvas.ai_available is False
    menu = QtWidgets.QMenu()
    win._project_menu_actions(menu, system)
    assert [a.text() for a in menu.actions()] == ["Edit purpose", "Show what this is based on"]
    # A pending suggestion is still shown (it is data, not an AI control).
    assert win.suggestion_box.isVisible()
    # On: the controls come back.
    win.store.set_setting("disable_ollama", False)
    win._detected_ollama_model = "m:1"
    win._refresh_ai_controls()
    win._refresh_sun_anchor_card()
    assert win.btn_ollama_single.isVisible() and win.canvas.ai_available is True
    assert win.lbl_ollama_status.text() == "Ollama: m:1"
    menu2 = QtWidgets.QMenu()
    win._project_menu_actions(menu2, system)
    assert "Suggest with AI" in [a.text() for a in menu2.actions()]
    system.purpose, system.purpose_source = "Own.", "readme"
    menu3 = QtWidgets.QMenu()
    win._project_menu_actions(menu3, system)
    assert "Suggest with AI" not in [a.text() for a in menu3.actions()]


def test_cards_show_suggestion_as_muted_suggested_line(env):
    win, system, (other,) = _suggestion_window(env)
    win.canvas.switch_to_galaxy_view()
    cards = {c.system.id: c for c in win.canvas.galaxy_items}
    card = cards[system.id]
    assert card.description_lines() == [] and card._purpose_snippet == ""
    assert card.suggestion_text().startswith("Suggested: A local voice")
    assert "Suggested (not accepted yet):" in card.toolTip()
    assert cards[other.id].suggestion_text() == ""
    win.canvas.viewport().grab()
    system.purpose, system.purpose_source = "Own.", "user"
    win.canvas.refresh_project_texts({system.id})
    assert card.suggestion_text() == "" and card.description_lines() == ["Own."]



def test_manual_answer_failing_the_local_check_says_why(env, fake_ollama):
    win, system, _ = _purpose_window(env)
    win._detected_ollama_model = "m:1"
    system.purpose_evidence = "README intro (README.md): NOVA is a voice assistant for the desktop."
    assert win._suggest_purpose_with_ai(system)
    worker = fake_ollama["worker"].created[-1]
    worker.purposeSuggested.emit(system.id, "A chess engine for online tournaments.")
    worker.finish(True, "done")
    assert system.purpose_suggestion == "" and system.purpose == ""
    assert win.status_label.text() == "No suggestion: the answer did not match the evidence."


def test_migration_notice_is_shown_once_after_startup(env, tmp_path):
    from dataclasses import asdict

    path = tmp_path / "old" / "galaxy.json"
    path.parent.mkdir()
    s = SolarSystem(name="Photos", project_name="Photos", source="codebase")
    s.purpose, s.purpose_source = "Renames holiday photos by date.", "ai"
    s.purpose_evidence = "Own description (package.json): A small tool that renames holiday photos by date."
    path.write_text(json.dumps({"systems": [asdict(s)]}), encoding="utf-8")
    store = GalaxyStore(storage_path=path)
    assert store.migrated_purposes == 1
    store.set_setting("onboarding_completed", True)
    store.set_setting("connected_sources", [])
    win = mw.HeliosMainWindow(store=store)
    win.show()
    wait_until(lambda: "now shown as" in win.status_label.text(), timeout=5)
    assert win.status_label.text().startswith("1 project description written by Ollama is now shown as")
    win.status_label.setText("other")
    spin(0.3)
    assert win.status_label.text() == "other", "one-time"
    win.close()
