"""Pure tests for the Ollama module-description helpers (connectors/ollama_enhancer.py).

No Ollama server and no real HTTP: the module-level `_DIRECT_OPENER` is replaced by a
fake. Files are created under `tmp_path` only. A faulthandler watchdog aborts a hung run.
"""

from __future__ import annotations

import faulthandler
import io
import json
import os
import urllib.error

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from connectors import ollama_enhancer as oe  # noqa: E402
from core.models import PlanetNode, SolarSystem  # noqa: E402

TEST_TIMEOUT_S = 60


@pytest.fixture(autouse=True)
def hard_timeout():
    faulthandler.dump_traceback_later(TEST_TIMEOUT_S, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, answer: str = "", error: Exception = None):
        self.answer = answer
        self.error = error
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        return _FakeResponse(json.dumps({"response": self.answer}).encode("utf-8"))

    def last_body(self) -> dict:
        return json.loads(self.requests[-1].data.decode("utf-8"))


def _planet(**kw) -> oe.PlanetSnapshot:
    base = dict(
        id="p1", title="mikrofon.py", kind="file", layer="core", file_path="",
        summary="", satellite_titles=(),
    )
    base.update(kw)
    return oe.PlanetSnapshot(**base)


PY_SOURCE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Listens to the microphone.
"""Speech input for the assistant.

Wraps Whisper.
"""
import os
from pathlib import Path as P
from .audio import record


def listen(seconds: int = 5) -> str:
    """Records audio and returns the transcript.

    More text.
    """
    return ""


async def stream(device=None):
    pass


class Ear(Base):
    """Keeps the microphone open."""

    def open(self):
        pass

    def close(self):
        pass


if __name__ == "__main__":
    listen()
'''


# ----------------------------------------------------------------------
# Evidence
# ----------------------------------------------------------------------
def test_python_outline_lists_imports_and_defs(tmp_path):
    f = tmp_path / "mikrofon.py"
    f.write_text(PY_SOURCE, encoding="utf-8")
    ev = oe.gather_module_evidence(_planet(file_path=str(f)))
    assert "File: mikrofon.py" in ev
    assert "Speech input for the assistant." in ev
    assert "# Listens to the microphone." in ev
    assert "coding" not in ev and "#!/usr/bin/env" not in ev
    assert "import os" in ev and "from pathlib import Path as P" in ev and "from .audio import record" in ev
    assert "def listen(seconds: int=5) -> str  # Records audio and returns the transcript." in ev
    assert "More text." not in ev, "only the first docstring line of a def"
    assert "async def stream(device=None)" in ev
    assert "class Ear(Base)  # Keeps the microphone open." in ev
    assert "methods: open, close" in ev
    assert "__main__" in ev
    assert "return \"\"" not in ev, "outline, not raw source"


def test_python_syntax_error_falls_back_to_generic(tmp_path):
    f = tmp_path / "broken.py"
    f.write_text("def ok():\n    pass\ndef broken(:\n", encoding="utf-8")
    ev = oe.gather_module_evidence(_planet(file_path=str(f)))
    assert "First lines:" in ev and "def broken(:" in ev


def test_generic_outline_for_other_languages(tmp_path):
    lines = [f"// filler {i}" for i in range(40)]
    lines += ["export function render(view) {", "  return 1;", "}", "const handler = async (e) => {", "class Widget {"]
    f = tmp_path / "view.js"
    f.write_text("\n".join(lines), encoding="utf-8")
    ev = oe.gather_module_evidence(_planet(file_path=str(f), title="view.js"))
    assert "// filler 0" in ev and "// filler 39" not in ev
    assert "export function render(view) {" in ev
    assert "const handler = async (e) => {" in ev and "class Widget {" in ev
    assert "return 1;" not in ev


def test_missing_and_huge_files_are_tolerated(tmp_path):
    assert oe.gather_module_evidence(_planet(file_path=r"C:\Users\someone\proj\mikrofon.py")) == ""
    assert oe.gather_module_evidence(_planet(file_path=str(tmp_path / "nope.py"))) == ""
    big = tmp_path / "big.py"
    big.write_bytes(b"x = 1\n" * (oe.MAX_FILE_READ_BYTES // 6 + 10))
    assert big.stat().st_size > oe.MAX_FILE_READ_BYTES
    assert oe.gather_module_evidence(_planet(file_path=str(big))) == ""
    # a prompt can still be built from titles only
    prompt = oe.build_module_prompt("P", _planet(file_path=str(big), satellite_titles=("listen()",)), "", "English")
    assert "listen()" in prompt and prompt.endswith(oe.module_task_instructions("file", "English"))


def test_dir_evidence_lists_files_with_first_doc_line_inside_dir_only(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "a.py").write_text('"""Parses config files."""\nimport os\n', encoding="utf-8")
    (d / "b.ts").write_text("// Renders the toolbar\nexport const x = 1;\n", encoding="utf-8")
    outside = tmp_path / "secret.py"
    outside.write_text('"""OUTSIDE SECRET"""\n', encoding="utf-8")
    ps = _planet(
        kind="dir", title="pkg", file_path=str(d),
        satellite_titles=("a.py", "b.ts", "secret.py", "gone.py"),
        satellite_paths=(str(d / "a.py"), str(d / "b.ts"), str(outside), str(d / "gone.py")),
    )
    ev = oe.gather_module_evidence(ps)
    assert "- a.py: Parses config files." in ev
    assert "- b.ts: Renders the toolbar" in ev
    assert "- secret.py" in ev and "OUTSIDE SECRET" not in ev
    assert "- gone.py" in ev


def test_history_prompt_uses_session_contents():
    ps = _planet(
        kind="history", title="Session history", file_path="",
        satellite_titles=("Fix login", "Add export"),
        satellite_contents=("Requests: fix the login redirect. Files: auth.py", "Requests: CSV export. Files: export.py"),
    )
    ev = oe.gather_module_evidence(ps)
    prompt = oe.build_module_prompt("Shop", ps, ev, "English")
    assert "fix the login redirect" in prompt and "export.py" in prompt
    assert prompt.endswith(oe.HISTORY_TASK_INSTRUCTIONS.format(language="English"))
    assert "up to 3 short bullet points" in prompt


def test_layer_planet_has_no_file_evidence():
    ps = _planet(kind="layer", title="UI Layer", satellite_titles=("Canvas", "Inspector"))
    assert oe.gather_module_evidence(ps) == ""
    assert "Canvas, Inspector" in oe.build_module_prompt("P", ps, "", "English")


# ----------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------
def test_prompt_keeps_instructions_and_cap_with_huge_file(tmp_path):
    f = tmp_path / "huge.py"
    body = '"""' + "Doc " * 3000 + '"""\n' + "".join(f"import mod{i}\n" for i in range(500))
    body += "".join(f"def func_{i}(a, b, c):\n    '''Does thing {i}.'''\n    pass\n" for i in range(2000))
    f.write_text(body, encoding="utf-8")
    ps = _planet(
        file_path=str(f), title="T" * 500, summary="S " * 5000,
        satellite_titles=tuple(f"func_{i}()" for i in range(40)),
    )
    ev = oe.gather_module_evidence(ps)
    prompt = oe.build_module_prompt("Project " * 100, ps, ev + "E" * 50000, "Türkçe")
    assert len(prompt) <= oe.MAX_PROMPT_CHARS
    instr = oe.MODULE_TASK_INSTRUCTIONS.format(language="Türkçe")
    assert prompt.endswith(instr), "task instructions must be intact"
    for fragment in (
        "Explain in 1-3 short sentences what this module/file does and its role in the project",
        "based ONLY on the evidence above",
        "Do not invent features, files or dependencies",
        "If the evidence is insufficient, say only what is visible, without guessing.",
        "No preamble, no markdown headings.",
        "Write in Türkçe.",
    ):
        assert fragment in prompt
    assert "EVIDENCE:" in prompt and "import mod0" in prompt


def test_language_string_appears_in_prompt():
    ps = _planet(satellite_titles=("x()",))
    assert "Write in English." in oe.build_module_prompt("P", ps, "", "English")
    assert "Write in Türkçe." in oe.build_module_prompt("P", ps, "", "Türkçe")
    assert "Write in Deutsch." in oe.build_module_prompt("P", ps, "", "Deutsch")
    assert "Write in English." in oe.build_module_prompt("P", ps, "", "")
    assert "\n- Write in Bad Name." in oe.build_module_prompt("P", ps, "", "Bad\nName")


# ----------------------------------------------------------------------
# Output cleaning
# ----------------------------------------------------------------------
def test_clean_module_text_strips_markdown_prefix_and_caps():
    raw = "Here is the description:\n## Overview\n**mikrofon.py** records *audio* and `returns` text. It is used by the assistant"
    assert oe.clean_module_text(raw) == "mikrofon.py records audio and returns text."

    long = " ".join(f"Sentence number {i} explains a detail." for i in range(60))
    out = oe.clean_module_text(long)
    assert len(out) <= oe.MAX_MODULE_OUTPUT_CHARS and out.endswith(".")

    no_stop = "word " * 300
    out2 = oe.clean_module_text(no_stop)
    assert len(out2) <= oe.MAX_MODULE_OUTPUT_CHARS and out2.endswith("…")


def test_clean_history_keeps_up_to_three_bullets():
    raw = "Sure, here you go:\n- Fixed login.\n* Added export.\n1. Wrote tests.\n- Refactored UI."
    assert oe.clean_module_text(raw, history=True) == "• Fixed login.\n• Added export.\n• Wrote tests."


def test_garbage_and_echo_are_rejected():
    assert oe.module_text_problem("", oe.clean_module_text("")) is not None
    assert oe.module_text_problem("...!!", oe.clean_module_text("... !! ---")) is not None
    echo = "YOUR TASK:\nExplain in 1-3 short sentences what this module/file does"
    assert "echo" in oe.module_text_problem(echo, oe.clean_module_text(echo))
    echo2 = "This module does X. Do not invent features, files or dependencies that are not in the evidence."
    assert oe.module_text_problem(echo2, oe.clean_module_text(echo2)) is not None
    fine = "Records audio from the microphone and returns a transcript."
    assert oe.module_text_problem(fine, oe.clean_module_text(fine)) is None


# ----------------------------------------------------------------------
# describe_module (fake HTTP)
# ----------------------------------------------------------------------
def test_describe_module_with_fake_opener(monkeypatch, tmp_path):
    f = tmp_path / "mikrofon.py"
    f.write_text(PY_SOURCE, encoding="utf-8")
    fake = _FakeOpener("Okay.\n**Records** speech from the microphone and returns text. It feeds the")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", fake)
    ok, msg, text = oe.describe_module(_planet(file_path=str(f)), "gemma3:4b", "Türkçe", project_name="NOVA")
    assert ok, msg
    assert text == "Records speech from the microphone and returns text."
    body = fake.last_body()
    assert body["model"] == "gemma3:4b" and body["stream"] is False
    assert body["options"]["num_ctx"] == 4096
    assert body["options"]["num_predict"] == oe.MODULE_NUM_PREDICT
    assert body["options"]["temperature"] <= 0.2
    assert "'NOVA'" in body["prompt"] and "Write in Türkçe." in body["prompt"]
    assert "def listen(" in body["prompt"]
    assert len(body["prompt"]) <= oe.MAX_PROMPT_CHARS


def test_describe_module_http_error_and_echo_fail(monkeypatch):
    err = urllib.error.HTTPError(
        "http://x/api/generate", 404, "Not Found", {}, io.BytesIO(b'{"error": "model not found"}')
    )
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(error=err))
    ok, msg, text = oe.describe_module(_planet(satellite_titles=("a()",)), "m", "English")
    assert not ok and text is None and "model not found" in msg

    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(error=TimeoutError("timed out")))
    ok, msg, text = oe.describe_module(_planet(satellite_titles=("a()",)), "m", "English")
    assert not ok and text is None and "timed out" in msg

    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener("YOUR TASK:\nExplain in 1-3 short sentences"))
    ok, msg, text = oe.describe_module(_planet(satellite_titles=("a()",)), "m", "English")
    assert not ok and text is None

    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener("   "))
    ok, msg, text = oe.describe_module(_planet(satellite_titles=("a()",)), "m", "English")
    assert not ok and "empty" in msg


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------
def test_snapshot_carries_planet_fields_and_is_detached():
    system = SolarSystem(name="P", project_name="P", source="codebase")
    planet = system.add_planet(title="mikrofon.py", summary="Speech input.")
    planet.kind, planet.file_path, planet.ai_summary = "file", "/x/mikrofon.py", "old"
    planet.add_satellite(title="listen()", content="c")
    hist = system.add_planet(title="History", summary="")
    hist.kind = "history"
    hist.add_satellite(title="S1", content="Requests: fix bug " * 100)

    snap = oe.snapshot_system(system)
    ps = snap.planet_snapshot(planet.id)
    assert (ps.title, ps.kind, ps.file_path, ps.ai_summary) == ("mikrofon.py", "file", "/x/mikrofon.py", "old")
    assert ps.satellite_titles == ("listen()",) and ps.satellite_contents == ()
    hs = snap.planet_snapshot(hist.id)
    assert hs.satellite_contents and len(hs.satellite_contents[0]) <= oe.MAX_SNAPSHOT_SAT_CONTENT_CHARS
    planet.title = "renamed"
    assert snap.planet_snapshot(planet.id).title == "mikrofon.py"
    assert not oe.snapshot_planet(PlanetNode(title="", summary="")).has_evidence
    # the project-level prompt is unchanged by the new field
    assert snap.modules[0][0] == "mikrofon.py"


# ----------------------------------------------------------------------
# Project purpose
# ----------------------------------------------------------------------
import threading  # noqa: E402
import time  # noqa: E402

# Strong evidence (see purpose_evidence.assess_purpose_evidence): two descriptive lines.
VOICE_EVIDENCE = (
    "Header comment (degerler.py): NOVA ORTAK YAPILANDIRMA DOSYASI\n"
    "Header comment (islemler.py): Kullanıcının komutla açıp kapattığı odak modu bilgisi\n"
    'Text in code (degerler.py, KARSILAMA_METNI): "Merhaba, ben NOVA. … yardımcı bir sesli asistanım."\n'
    "Capabilities implied by libraries: speech-to-text (faster_whisper), LLM / AI model (ollama)\n"
    "Names in code: tarayici_ac(), saati_goster()"
)


def test_purpose_prompt_has_instructions_evidence_and_cap():
    prompt = oe.build_purpose_prompt("NOVA", VOICE_EVIDENCE, "English")
    assert "Below is evidence about the project 'NOVA'" in prompt
    assert "EVIDENCE:\n" + VOICE_EVIDENCE in prompt
    assert prompt.endswith(oe.purpose_task_instructions("English"))
    assert "answer exactly: UNKNOWN" in prompt and "Write in English." in prompt
    huge = oe.build_purpose_prompt("X" * 500, "evidence line\n" * 2000, "Türkçe")
    assert len(huge) <= oe.MAX_PURPOSE_PROMPT_CHARS
    assert huge.endswith(oe.purpose_task_instructions("Türkçe")) and "Write in Türkçe." in huge
    assert "EVIDENCE:" not in oe.build_purpose_prompt("P", "  ", "English")


def test_unknown_answer_detection():
    for raw in ("UNKNOWN", "unknown.", "**UNKNOWN**", "  Unknown!  ", "UNKNOWN - too little evidence"):
        assert oe.is_unknown_answer(raw), raw
    assert not oe.is_unknown_answer("A local voice assistant. Purpose unknown otherwise.")


def test_clean_purpose_text_tidies_but_never_shortens():
    # clean_purpose_text used to cut the answer to 2 sentences, which hid over-long answers
    # from the quality check (purpose_quality, "too_long"); now it only tidies.
    raw = "Sure! Here it is:\n**A local voice assistant** that listens. It opens apps. It also tells time. Extra."
    text = oe.clean_purpose_text(raw)
    assert text == "A local voice assistant that listens. It opens apps. It also tells time. Extra."
    assert oe.clean_purpose_text("Here is the purpose: A drawing app. It saves.") == "A drawing app. It saves."
    assert oe.clean_purpose_text("Okay,\nA drawing app for kids. It saves pictures.") == "A drawing app for kids. It saves pictures."
    assert oe.clean_purpose_text("Okay, a quiz maker for classes.") == "a quiz maker for classes."
    assert oe.clean_purpose_text("  - A   `drawing`\n  app.  ") == "A drawing app."
    assert len(oe.clean_purpose_text("word " * 300)) > oe.MAX_PURPOSE_OUTPUT_CHARS


def test_describe_purpose_with_fake_opener(monkeypatch):
    fake = _FakeOpener("A local voice assistant that listens through the microphone and answers with a local AI model.")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", fake)
    ok, msg, text = oe.describe_purpose("NOVA", VOICE_EVIDENCE, "m:1", "English")
    assert ok and text.startswith("A local voice assistant") and "NOVA" in msg
    body = fake.last_body()
    assert body["model"] == "m:1" and body["stream"] is False
    assert body["options"] == {"temperature": oe.PURPOSE_TEMPERATURE, "num_ctx": 4096, "num_predict": oe.PURPOSE_NUM_PREDICT}
    assert body["prompt"] == oe.build_purpose_prompt("NOVA", VOICE_EVIDENCE, "English")


# Strong evidence (an own description is worth 2 points on its own).
STRONG_EV = "Own description (package.json): A small tool that renames holiday photos by date.\nNames in code: x()"
# An answer that passes purpose_quality.check_purpose_suggestion for STRONG_EV (it shares
# words with the evidence and is not generic); "A tool." / "A thing." are now rejected.
GOOD_PURPOSE = "A photo renamer that sorts holiday pictures by date."


def test_describe_purpose_rejects_unknown_empty_echo_and_errors(monkeypatch):
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener("UNKNOWN."))
    ok, msg, text = oe.describe_purpose("P", STRONG_EV, "m")
    assert ok is False and text is None and "UNKNOWN" in msg
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener("   "))
    assert oe.describe_purpose("P", STRONG_EV, "m")[0] is False
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener("In 1-2 sentences, say what this project is and what it is used for"))
    ok, msg, _ = oe.describe_purpose("P", STRONG_EV, "m")
    assert ok is False and "echoed" in msg
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(error=TimeoutError("timed out")))
    ok, msg, _ = oe.describe_purpose("P", STRONG_EV, "m")
    assert ok is False and "timed out" in msg
    calls = _FakeOpener("x")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", calls)
    assert oe.describe_purpose("P", "", "m")[0] is False and calls.requests == []


def test_module_prompt_includes_purpose_line():
    ps = _planet(satellite_titles=("x()",))
    prompt = oe.build_module_prompt("P", ps, "", "English", project_purpose="A local voice assistant.")
    assert "\nProject purpose: A local voice assistant.\n" in prompt
    assert "Project purpose" not in oe.build_module_prompt("P", ps, "", "English")
    long_prompt = oe.build_module_prompt("P", ps, "", "English", project_purpose="word " * 500)
    line = next(ln for ln in long_prompt.splitlines() if ln.startswith("Project purpose: "))
    assert len(line) <= len("Project purpose: ") + oe.MAX_PURPOSE_CONTEXT_CHARS


def test_snapshot_carries_purpose_fields():
    s = SolarSystem(name="S")
    s.purpose, s.purpose_source, s.purpose_evidence = "p", "ai", "ev"
    snap = oe.snapshot_system(s)
    assert (snap.purpose, snap.purpose_source, snap.purpose_evidence) == ("p", "ai", "ev")


# --- worker ---------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _wait(predicate, timeout=10.0):
    from PySide6 import QtCore

    app = QtCore.QCoreApplication.instance()
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)


class _PromptOpener:
    def __init__(self, answers, gate=None, block_after=None):
        self.answers = list(answers)
        self.prompts = []
        self.gate, self.block_after = gate, block_after

    def open(self, req, timeout=None):
        self.prompts.append(json.loads(req.data.decode("utf-8"))["prompt"])
        n = len(self.prompts)
        if self.gate is not None and n > self.block_after:
            self.gate.wait(10)
        item = self.answers[min(n - 1, len(self.answers) - 1)]
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(json.dumps({"response": item}).encode("utf-8"))


def _purpose_systems():
    out = []
    for name, src, purpose, ev in (
        ("Empty", "", "", STRONG_EV.replace("x()", "a()")),
        ("Ai", "ai", "Old AI text.", STRONG_EV.replace("x()", "b()")),
        ("Readme", "readme", "Own words.", STRONG_EV.replace("x()", "c()")),
        ("User", "user", "Typed by me.", STRONG_EV.replace("x()", "d()")),
        ("NoEvidence", "", "", ""),
    ):
        s = SolarSystem(name=name, project_name=name)
        s.purpose, s.purpose_source, s.purpose_evidence = purpose, src, ev
        p = s.add_planet(title=f"{name.lower()}.py", summary="Module.")
        p.kind = "file"
        out.append(s)
    return out


def _run(systems, **kw):
    worker = oe.OllamaEnhanceWorker(systems, model_name="m:1", **kw)
    written, progress, finished, described = [], [], [], []
    # Changed: the worker emits purposeSuggested (a suggestion, never the purpose).
    worker.purposeSuggested.connect(lambda sid, text: written.append((sid, text)))
    worker.rejected_ids = []
    worker.purposeRejected.connect(lambda sid, msg: worker.rejected_ids.append(sid))
    worker.moduleDescribed.connect(lambda sid, pid, text: described.append(pid))
    worker.progressText.connect(progress.append)
    worker.finishedEnhance.connect(lambda ok, msg: finished.append((ok, msg)))
    return worker, written, progress, finished, described


def test_worker_purpose_only_rules(qapp, monkeypatch):
    systems = _purpose_systems()
    opener = _PromptOpener([GOOD_PURPOSE])
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, written, progress, finished, described = _run(systems, purpose_only=True)
    worker.start()
    _wait(lambda: bool(finished) and worker.isFinished())
    assert [sid for sid, _ in written] == [systems[0].id]  # only the empty one
    assert len(opener.prompts) == 1 and "Names in code: a()" in opener.prompts[0]
    assert described == []
    assert "Ollama (m:1) — suggesting a purpose 1/1: Empty…" in progress
    ok, msg = finished[0]
    assert ok and "Purpose suggestions (m:1): 1 suggested, 4 skipped, 0 failed." in msg and "Module descriptions" not in msg
    assert systems[0].purpose == "", "the worker must not mutate systems"


def test_worker_manual_suggests_for_empty_and_ai_but_never_readme_or_user(qapp, monkeypatch):
    # Changed: `force` no longer applies to the purpose step. A manual request
    # (purpose_manual=True, "Suggest with AI") asks for empty and accepted-"ai" purposes,
    # never for "readme" or "user" ones.
    systems = _purpose_systems()
    opener = _PromptOpener([GOOD_PURPOSE])
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, written, _p, finished, _d = _run(systems, purpose_only=True, purpose_manual=True)
    worker.start()
    _wait(lambda: bool(finished) and worker.isFinished())
    assert [sid for sid, _ in written] == [s.id for s in systems[:2]]
    assert len(opener.prompts) == 2
    assert "2 suggested, 3 skipped, 0 failed" in finished[0][1]
    # force alone (module descriptions) does not make the purpose step rewrite anything.
    opener2 = _PromptOpener([GOOD_PURPOSE])
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener2)
    worker2, written2, _p2, finished2, _d2 = _run(systems, purpose_only=True, force=True)
    worker2.start()
    _wait(lambda: bool(finished2) and worker2.isFinished())
    assert [sid for sid, _ in written2] == [systems[0].id]


def test_worker_unknown_is_failed_not_emitted(qapp, monkeypatch):
    systems = _purpose_systems()[:1]
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _PromptOpener(["UNKNOWN"]))
    worker, written, _p, finished, _d = _run(systems, purpose_only=True)
    worker.start()
    _wait(lambda: bool(finished) and worker.isFinished())
    assert written == [] and worker.rejected_ids == [systems[0].id]
    ok, msg = finished[0]
    assert ok is False and "UNKNOWN" in msg and "0 suggested, 0 skipped, 1 failed" in msg


def test_worker_purpose_runs_before_modules_but_suggestion_is_not_module_context(qapp, monkeypatch):
    # Changed: the answer is only a suggestion, so module prompts no longer get it as
    # "Project purpose: ..." context (only an accepted purpose is context).
    systems = _purpose_systems()[:1]
    opener = _PromptOpener([GOOD_PURPOSE, "Module text."])
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, written, _p, finished, described = _run(systems, summarize_project=False)
    worker.start()
    _wait(lambda: bool(finished) and worker.isFinished())
    assert written == [(systems[0].id, GOOD_PURPOSE)]
    assert len(opener.prompts) == 2
    assert opener.prompts[0].startswith("Below is evidence about the project 'Empty'")
    assert "Project purpose:" not in opener.prompts[1]
    assert described == [systems[0].planets[0].id]
    assert "1 suggested" in finished[0][1] and "1 described" in finished[0][1]


def test_worker_write_purpose_false_makes_no_purpose_request(qapp, monkeypatch):
    systems = _purpose_systems()[:1]
    opener = _PromptOpener(["Module text."])
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, written, _p, finished, _d = _run(systems, summarize_project=False, write_purpose=False)
    worker.start()
    _wait(lambda: bool(finished) and worker.isFinished())
    assert written == [] and len(opener.prompts) == 1 and "Purpose suggestions" not in finished[0][1]


def test_worker_purpose_interruption(qapp, monkeypatch):
    systems = []
    for i in range(5):
        s = SolarSystem(name=f"S{i}", project_name=f"S{i}")
        s.purpose_evidence = STRONG_EV.replace("x()", f"f{i}()")
        systems.append(s)
    gate = threading.Event()
    opener = _PromptOpener([GOOD_PURPOSE], gate=gate, block_after=2)
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    worker, written, _p, finished, _d = _run(systems, purpose_only=True)
    worker.start()
    _wait(lambda: len(written) == 2 and len(opener.prompts) == 3)
    worker.requestInterruption()
    _wait(lambda: bool(finished) and worker.isFinished(), timeout=5)
    gate.set()
    ok, msg = finished[0]
    assert ok and "Stopped by user." in msg and "2 suggested" in msg
    assert len(opener.prompts) == 3
