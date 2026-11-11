"""Tests for the background workers (ui/workers.py) and the Ollama enhancer refactor.

No network, no real agent data: importer functions and the Ollama HTTP opener are
monkeypatched. Every test is guarded by a faulthandler watchdog so a hung event loop
aborts the run instead of hanging it.
"""

from __future__ import annotations

import faulthandler
import json
import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from connectors import ollama_enhancer as oe  # noqa: E402
from connectors.agent_importer import AgentHistoryImporter  # noqa: E402
from connectors.codebase_importer import CodebaseImporter  # noqa: E402
from core.models import GalaxyStore, SolarSystem  # noqa: E402
from ui.workers import AgentSyncWorker, CodebaseScanWorker  # noqa: E402

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


def wait_until(predicate, timeout: float = 10.0) -> None:
    """Spins the Qt event loop until predicate() is true or the timeout expires."""
    app = QtCore.QCoreApplication.instance()
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)


def run_worker(worker, timeout: float = 10.0):
    """Starts a StoreWorker and returns (progress_messages, (ok, message, payload))."""
    progress, results = [], []
    worker.progressText.connect(lambda m: progress.append(m))
    worker.finishedWork.connect(lambda ok, msg, payload: results.append((ok, msg, payload)))
    worker.start()
    wait_until(lambda: bool(results) and worker.isFinished(), timeout)
    return progress, results[0]


@pytest.fixture
def store(tmp_path):
    return GalaxyStore(storage_path=tmp_path / "galaxy.json")


def _new_system(name: str, source: str = "antigravity") -> SolarSystem:
    return SolarSystem(name=name, project_name=name, source=source)


# ----------------------------------------------------------------------
# AgentSyncWorker
# ----------------------------------------------------------------------
def test_agent_sync_worker_runs_all_steps_off_thread(qapp, store, monkeypatch, tmp_path):
    main_thread = threading.get_ident()
    calls = {}

    def fake_scan(st, sources, max_sessions=25):
        calls["scan"] = (list(sources), max_sessions, threading.get_ident())
        st.systems.append(_new_system("Alpha"))
        return 3

    def fake_analyze(folder, st):
        calls["analyze"] = Path(folder)
        sys_obj = _new_system("Helios", source="codebase")
        st.systems.append(sys_obj)
        return sys_obj

    monkeypatch.setattr(AgentHistoryImporter, "scan_selected_sources", staticmethod(fake_scan))
    monkeypatch.setattr(CodebaseImporter, "analyze_directory", staticmethod(fake_analyze))
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: False))

    store.systems.append(_new_system("Old"))
    worker = AgentSyncWorker(
        store,
        ["antigravity", "codebase", "ollama"],
        max_sessions=15,
        start_ollama=True,
        reset=True,
        analyze_helios=True,
        helios_dir=tmp_path,
    )
    progress, (ok, msg, payload) = run_worker(worker)

    assert ok is True
    assert calls["scan"][0] == ["antigravity", "codebase", "ollama"]
    assert calls["scan"][1] == 15
    assert calls["scan"][2] != main_thread, "scan must run on the worker thread"
    assert calls["analyze"] == tmp_path
    assert payload["updated"] == 3
    assert payload["ollama_started"] is False
    assert payload["helios_system"] is not None
    assert any("Ollama" in w for w in payload["warnings"])
    assert "3 project(s) updated" in msg
    names = [s.name for s in store.systems]
    assert "Old" not in names, "reset must clear the store"
    assert "Alpha" in names and "Helios" in names
    assert any("Starting the local Ollama service" in p for p in progress)


def test_agent_sync_worker_skips_helios_analysis_when_present(qapp, store, monkeypatch):
    called = []
    monkeypatch.setattr(AgentHistoryImporter, "scan_selected_sources", staticmethod(lambda st, s, max_sessions=25: 0))
    monkeypatch.setattr(
        CodebaseImporter, "analyze_directory", staticmethod(lambda f, st: called.append(f))
    )
    store.systems.append(_new_system("Helios", source="codebase"))
    _, (ok, _msg, payload) = run_worker(AgentSyncWorker(store, ["codebase"], analyze_helios=True))
    assert ok and called == [] and payload["helios_system"] is None


def test_agent_sync_worker_reports_scan_exception(qapp, store, monkeypatch):
    def boom(st, sources, max_sessions=25):
        raise RuntimeError("history folder unreadable")

    monkeypatch.setattr(AgentHistoryImporter, "scan_selected_sources", staticmethod(boom))
    worker = AgentSyncWorker(store, ["antigravity"])
    _, (ok, msg, payload) = run_worker(worker)
    assert ok is False
    assert "history folder unreadable" in msg
    assert "RuntimeError" in payload["traceback"]
    assert worker.isFinished()


def test_agent_sync_worker_helios_analysis_failure_is_a_warning(qapp, store, monkeypatch):
    monkeypatch.setattr(AgentHistoryImporter, "scan_selected_sources", staticmethod(lambda st, s, max_sessions=25: 1))

    def boom(folder, st):
        raise ValueError("bad ast")

    monkeypatch.setattr(CodebaseImporter, "analyze_directory", staticmethod(boom))
    _, (ok, msg, payload) = run_worker(AgentSyncWorker(store, ["codebase"], analyze_helios=True))
    assert ok is True
    assert any("bad ast" in w for w in payload["warnings"])
    assert "bad ast" in msg


# ----------------------------------------------------------------------
# CodebaseScanWorker
# ----------------------------------------------------------------------
def test_codebase_worker_folder_success_and_empty(qapp, store, monkeypatch, tmp_path):
    result = {"value": _new_system("Proj", source="codebase")}
    monkeypatch.setattr(CodebaseImporter, "analyze_directory", staticmethod(lambda f, st: result["value"]))
    _, (ok, msg, payload) = run_worker(CodebaseScanWorker(store, folder=tmp_path))
    assert ok and payload["mode"] == "folder" and payload["system"] is result["value"]
    assert payload["folder"] == tmp_path

    result["value"] = None
    _, (ok, msg, payload) = run_worker(CodebaseScanWorker(store, folder=tmp_path))
    assert ok and payload["system"] is None and "No supported source code" in msg


def test_codebase_worker_folder_exception(qapp, store, monkeypatch, tmp_path):
    def boom(folder, st):
        raise PermissionError("access denied")

    monkeypatch.setattr(CodebaseImporter, "analyze_directory", staticmethod(boom))
    _, (ok, msg, payload) = run_worker(CodebaseScanWorker(store, folder=tmp_path))
    assert ok is False and "access denied" in msg and "traceback" in payload


def test_codebase_worker_projects_scan(qapp, store, monkeypatch, tmp_path):
    found = [_new_system("P1", source="codebase"), _new_system("P2", source="codebase")]
    monkeypatch.setattr(CodebaseImporter, "get_helios_projects_dir", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(CodebaseImporter, "scan_helios_projects", staticmethod(lambda st: list(found)))
    _, (ok, msg, payload) = run_worker(CodebaseScanWorker(store))
    assert ok and payload["mode"] == "projects"
    assert payload["systems"] == found and payload["projects_dir"] == tmp_path
    assert "2 project(s)" in msg


# ----------------------------------------------------------------------
# Ollama enhancer: pure function, prompt budget, block format
# ----------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body: bytes = b"", lines=None, status: int = 200):
        self._body = body
        self._lines = lines or []
        self.status = status

    def read(self):
        return self._body

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return self.response


def _big_system(tmp_path: Path) -> SolarSystem:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "README.md").write_text("R" * 5000, encoding="utf-8")
    (ws / "main.py").write_text("print('x')", encoding="utf-8")
    system = SolarSystem(name="BigProject", project_name="BigProject", source="codebase")
    system.workspace_path = str(ws)
    system.core_plan = "PLAN " * 2000 + f"\n\n[{oe.OLLAMA_MARKER} — old-model]\n• old analysis"
    for i in range(20):
        planet = system.add_planet(
            title=f"Module {i}",
            summary="WHAT DOES IT DO?\n" + ("Handles important things. " * 120),
        )
        for j in range(8):
            planet.add_satellite(title=f"Sat {i}.{j}", content="c")
    return system


def test_build_enhanced_plan_is_pure_and_budgeted(monkeypatch, tmp_path):
    system = _big_system(tmp_path)
    original_plan = system.core_plan
    fake = _FakeOpener(_FakeResponse(json.dumps({"response": "Here is the summary:\n• **Does** things.\n• Works well."}).encode()))
    monkeypatch.setattr(oe, "_DIRECT_OPENER", fake)
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: True))

    snap = oe.snapshot_system(system)
    ok, msg, new_plan = oe.OllamaEnhancer.build_enhanced_plan(snap, model_name="gemma3:4b")

    assert ok, msg
    assert system.core_plan == original_plan, "pure function must not mutate the system"
    assert not getattr(system, "ollama_enhanced", False)

    body = json.loads(fake.requests[0].data.decode("utf-8"))
    prompt = body["prompt"]
    assert len(prompt) <= oe.MAX_PROMPT_CHARS
    assert prompt.endswith(oe.PROMPT_TASK_INSTRUCTIONS)
    assert "old analysis" not in prompt, "previous Ollama block must be stripped"
    assert "ARCHITECTURAL MODULES:" in prompt and "- Module 0:" in prompt
    assert "WHAT DOES IT DO?" not in prompt
    assert "DIRECTORY & FILE EVIDENCE:" in prompt
    assert body["options"]["num_ctx"] == 4096
    # each module summary is clipped to ~200 chars
    for line in prompt.splitlines():
        if line.startswith("- Module"):
            summary_part = line.split(": ", 1)[1].split(" (Sub-components:")[0]
            assert len(summary_part) <= oe.MAX_MODULE_SUMMARY_CHARS

    base = oe.strip_ollama_block(original_plan)
    assert new_plan == f"{base}\n\n[{oe.OLLAMA_MARKER} — gemma3:4b]\n• Does things.\n• Works well."
    assert "'BigProject' summary verified and enhanced with Ollama (gemma3:4b)." == msg


def test_enhance_system_wrapper_applies_result(monkeypatch, tmp_path):
    system = SolarSystem(name="Small", project_name="Small", source="codebase")
    system.core_plan = "Base plan."
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(_FakeResponse(b'{"response": "\\u2022 Thing."}')))
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: True))
    ok, msg = oe.OllamaEnhancer.enhance_system(system, model_name="m:1")
    assert ok
    assert system.core_plan == f"Base plan.\n\n[{oe.OLLAMA_MARKER} — m:1]\n• Thing."
    assert system.ollama_enhanced is True


def test_build_enhanced_plan_reports_empty_and_errors(monkeypatch):
    snap = oe.snapshot_system(SolarSystem(name="X", project_name="X", source="codebase"))
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: True))
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(_FakeResponse(b'{"response": ""}')))
    assert oe.OllamaEnhancer.build_enhanced_plan(snap, "m") == (False, "Ollama returned an empty response.", None)

    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: False))
    ok, msg, plan = oe.OllamaEnhancer.build_enhanced_plan(snap, "m")
    assert not ok and plan is None and "Failed to start Ollama" in msg


def test_ollama_enhance_worker_emits_results_without_mutating(qapp, monkeypatch):
    systems = [SolarSystem(name=f"S{i}", project_name=f"S{i}", source="codebase") for i in range(2)]
    for s in systems:
        s.core_plan = f"plan {s.name}"
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(_FakeResponse(b'{"response": "\\u2022 ok."}')))
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: True))

    worker = oe.OllamaEnhanceWorker(systems, model_name="m:1")
    systems[0].core_plan = "changed after snapshot"  # the worker must use its snapshot
    emitted, finished = [], []
    worker.systemEnhanced.connect(lambda sid, plan: emitted.append((sid, plan)))
    worker.finishedEnhance.connect(lambda ok, msg: finished.append((ok, msg)))
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())

    assert finished[0][0] is True
    assert [sid for sid, _ in emitted] == [s.id for s in systems]
    assert emitted[0][1].startswith("plan S0\n\n[")
    assert systems[0].core_plan == "changed after snapshot"
    assert systems[1].core_plan == "plan S1"
    assert worker.snapshot_for(systems[1].id).core_plan == "plan S1"


# ----------------------------------------------------------------------
# OllamaPullWorker
# ----------------------------------------------------------------------
def _run_pull(qapp, monkeypatch, lines):
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: True))
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(_FakeResponse(lines=lines)))
    worker = oe.OllamaPullWorker("tiny:1b")
    progress, finished = [], []
    worker.progressText.connect(lambda m: progress.append(m))
    worker.finishedPull.connect(lambda ok, msg: finished.append((ok, msg)))
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())
    return progress, finished[0]


def test_pull_worker_reports_stream_error(qapp, monkeypatch):
    lines = [
        b'{"status": "pulling manifest"}\n',
        b'{"error": "pull model manifest: file does not exist"}\n',
    ]
    progress, (ok, msg) = _run_pull(qapp, monkeypatch, lines)
    assert ok is False
    assert "file does not exist" in msg
    assert "pulling manifest" in progress


def test_pull_worker_success(qapp, monkeypatch):
    lines = [
        b'{"status": "downloading", "completed": 50, "total": 100}\n',
        b"not json\n",
        b'{"status": "success"}\n',
    ]
    progress, (ok, msg) = _run_pull(qapp, monkeypatch, lines)
    assert ok is True and "downloaded and registered" in msg
    assert "downloading: 50.0%" in progress


# ----------------------------------------------------------------------
# OllamaEnhanceWorker: module descriptions
# ----------------------------------------------------------------------
class _ScriptedOpener:
    """Returns/raises one scripted item per /api/generate request (last item repeats)."""

    def __init__(self, script, gate: threading.Event = None, block_after: int = None):
        self.script = list(script)
        self.prompts = []
        self.gate = gate
        self.block_after = block_after

    def open(self, req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        self.prompts.append(body["prompt"])
        n = len(self.prompts)
        if self.gate is not None and self.block_after is not None and n > self.block_after:
            self.gate.wait(10)
        item = self.script[min(n - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(json.dumps({"response": item}).encode("utf-8"))


def _module_system(n: int = 3) -> SolarSystem:
    system = SolarSystem(name="Nova", project_name="Nova", source="codebase")
    system.core_plan = "plan"
    for i in range(n):
        p = system.add_planet(title=f"mod{i}.py", summary=f"Module {i}.")
        p.kind = "file"
        p.add_satellite(title=f"func{i}()", content="")
    return system


def _run_module_worker(systems, **kw):
    worker = oe.OllamaEnhanceWorker(systems, model_name="m:1", summarize_project=False, **kw)
    described, progress, finished = [], [], []
    worker.moduleDescribed.connect(lambda sid, pid, text: described.append((sid, pid, text)))
    worker.progressText.connect(lambda m: progress.append(m))
    worker.finishedEnhance.connect(lambda ok, msg: finished.append((ok, msg)))
    return worker, described, progress, finished


def test_worker_emits_module_described_per_planet(qapp, monkeypatch):
    system = _module_system(3)
    opener = _ScriptedOpener(["Does a thing."])
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, described, progress, finished = _run_module_worker([system])
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())

    assert finished[0][0] is True
    assert [pid for _, pid, _ in described] == [p.id for p in system.planets]
    assert all(sid == system.id and text == "Does a thing." for sid, _, text in described)
    assert "Ollama (m:1) — Nova: module 2/3 mod1.py…" in progress
    assert "3 described, 0 skipped, 0 failed" in finished[0][1]
    assert all(p.ai_summary == "" for p in system.planets), "worker must not mutate planets"


def test_worker_also_runs_project_summary_first(qapp, monkeypatch):
    system = _module_system(1)
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _ScriptedOpener(["• Project bullet.", "Module text."]))
    monkeypatch.setattr(oe.OllamaEnhancer, "ensure_server_running", staticmethod(lambda: True))
    worker = oe.OllamaEnhanceWorker([system], model_name="m:1")
    enhanced, described, finished = [], [], []
    worker.systemEnhanced.connect(lambda sid, plan: enhanced.append(plan))
    worker.moduleDescribed.connect(lambda sid, pid, text: described.append(text))
    worker.finishedEnhance.connect(lambda ok, msg: finished.append((ok, msg)))
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())
    assert enhanced and enhanced[0].endswith("• Project bullet.")
    assert described == ["Module text."]
    assert finished[0][0] and "Enhanced core architecture summaries for 1/1" in finished[0][1]


def test_worker_skips_existing_ai_summary_unless_forced(qapp, monkeypatch):
    system = _module_system(3)
    system.planets[0].ai_summary = "already"
    empty = system.add_planet(title="", summary="")  # no evidence at all -> always skipped
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _ScriptedOpener(["New text."]))

    worker, described, _, finished = _run_module_worker([system])
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())
    ids = [pid for _, pid, _ in described]
    assert system.planets[0].id not in ids and empty.id not in ids and len(ids) == 2
    assert "2 described, 2 skipped, 0 failed" in finished[0][1]

    worker, described, _, finished = _run_module_worker([system], force=True)
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())
    ids = [pid for _, pid, _ in described]
    assert system.planets[0].id in ids and empty.id not in ids and len(ids) == 3


def test_worker_continues_after_one_failure_and_stops_after_three(qapp, monkeypatch):
    system = _module_system(6)
    err = TimeoutError("timed out")
    opener = _ScriptedOpener(["One.", err, "Three.", err, err, err, "Never."])
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, described, _, finished = _run_module_worker([system])
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())

    texts = [t for _, _, t in described]
    assert texts == ["One.", "Three."], "a single failure must not abort the run"
    assert len(opener.prompts) == 6, "must stop right after 3 consecutive failures"
    ok, msg = finished[0]
    assert ok is True, "results were produced, so they are kept"
    assert "2 described" in msg and "4 failed" in msg and "3 consecutive" in msg
    assert worker.stats == {"described": 2, "skipped": 0, "failed": 4}


def test_worker_all_failures_report_failure(qapp, monkeypatch):
    system = _module_system(5)
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _ScriptedOpener([ConnectionRefusedError("refused")]))
    worker, described, _, finished = _run_module_worker([system])
    worker.start()
    wait_until(lambda: bool(finished) and worker.isFinished())
    ok, msg = finished[0]
    assert ok is False and described == [] and "refused" in msg and "3 consecutive" in msg


def test_worker_interruption_stops_early_and_keeps_results(qapp, monkeypatch):
    system = _module_system(5)
    gate = threading.Event()
    opener = _ScriptedOpener(["Done."], gate=gate, block_after=2)
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, described, _, finished = _run_module_worker([system])
    worker.start()
    wait_until(lambda: len(described) == 2 and len(opener.prompts) == 3)
    t0 = time.monotonic()
    worker.requestInterruption()  # third request is still in flight (blocked)
    wait_until(lambda: bool(finished) and worker.isFinished(), timeout=5)
    gate.set()
    assert time.monotonic() - t0 < 2.0, "an in-flight request must not delay the stop"
    assert [pid for _, pid, _ in described] == [p.id for p in system.planets[:2]]
    ok, msg = finished[0]
    assert ok is True and "Stopped by user." in msg and "2 described" in msg
    assert len(opener.prompts) == 3
