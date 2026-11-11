"""Tests for connectors.agent_importer with synthetic session logs in a fake home."""

from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path

import pytest

from connectors import agent_importer as ai
from connectors.agent_importer import AgentDetector, AgentHistoryImporter, _resolve_project_root
from connectors.codebase_importer import CodebaseImporter
from core.models import GalaxyStore, SolarSystem


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    return h


@pytest.fixture
def store(tmp_path):
    return GalaxyStore(storage_path=tmp_path / "store" / "galaxy.json")


def systems(store, source):
    return [s for s in store.systems if s.source == source]


def all_sats(system):
    return [s for p in system.planets for s in p.satellites]


def history(system):
    (h,) = [p for p in system.planets if p.kind == "history"]
    return h


def code_planets(system):
    return [p for p in system.planets if p.kind != "history"]


def write_file(root: Path, rel: str, text: str = "") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def make_assistant(root: Path) -> Path:
    """Flat 4-file project: asistan imports degerler, islemler, mikrofon; islemler/mikrofon import degerler."""
    write_file(root, "degerler.py", "# Settings\nNAME = 'NOVA'\nMODE = 'text'\n")
    write_file(root, "islemler.py", "import degerler\n\ndef tarayici_ac():\n    pass\n\ndef saati_goster():\n    pass\n")
    write_file(root, "mikrofon.py", "import degerler\n\ndef dinle():\n    pass\n")
    write_file(root, "asistan.py", "import degerler\nimport islemler\nif degerler.MODE == 'voice':\n    import mikrofon\n\ndef baslat():\n    pass\n")
    return root


def set_mtime(path: Path, ts: float):
    os.utime(path, (ts, ts))


def write_jsonl(path: Path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")


# ---- Claude Code helpers ---------------------------------------------------
def cc_user(text, cwd, **extra):
    return {"type": "user", "cwd": cwd, "isSidechain": False, "message": {"role": "user", "content": text}, **extra}


def cc_assistant(cwd, text="", tools=(), **extra):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for i, (name, inp) in enumerate(tools):
        content.append({"type": "tool_use", "id": f"t{i}", "name": name, "input": inp})
    return {"type": "assistant", "cwd": cwd, "isSidechain": False, "message": {"role": "assistant", "content": content}, **extra}


def cc_tool_result(cwd):
    return {"type": "user", "cwd": cwd, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t0", "content": "ok"}]}}


def cc_session(home, project_dir, name, entries):
    write_jsonl(home / ".claude" / "projects" / project_dir / f"{name}.jsonl", entries)


# ---- Codex helpers ---------------------------------------------------------
def codex_rollout(home, name, cwd, items, sub="sessions/2026/01/01"):
    entries = [{"type": "session_meta", "payload": {"id": name, "cwd": cwd}}] + items
    write_jsonl(home / ".codex" / sub / f"rollout-{name}.jsonl", entries)


def cx_msg(role, text):
    kind = "input_text" if role == "user" else "output_text"
    return {"type": "response_item", "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}}


def cx_patch(files):
    body = "*** Begin Patch\n" + "".join(f"*** Update File: {f}\n@@\n-a\n+b\n" for f in files) + "*** End Patch\n"
    return {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "input": body}}


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "cwd, name",
    [
        (r"C:\dev\myapp", "myapp"),
        (r"D:\code\shop", "shop"),
        ("/home/x/code/foo", "foo"),
        (r"C:\Users\x\OneDrive\Desktop\app", "app"),
        (r"C:\Users\x\Desktop\Work\clientA\site", "clientA-site"),
        (r"C:\Users\x\Desktop\Work\clientA\backend", "clientA-backend"),
    ],
)
def test_cwd_identity_windows_and_posix_strings(home, cwd, name):
    res = _resolve_project_root(cwd)
    assert res is not None
    key, root, display, _confirmed = res
    assert display == name
    assert root.rstrip("\\/").endswith(cwd.rstrip("\\/").split("\\")[-1].split("/")[-1])


@pytest.mark.parametrize(
    "cwd",
    [r"C:\Users\x", r"C:\Users\x\Desktop", "C:\\", "/home/x", r"C:\Users\x\.codex", "", r"C:\Users\x\Documents\Codex\2025-01-31", "relative\\path"],
)
def test_unresolvable_workspaces(home, cwd):
    assert _resolve_project_root(cwd) is None


def test_local_cwd_walks_up_to_marker(home, tmp_path):
    proj = tmp_path / "work" / "realproj"
    (proj / "src" / "pkg").mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    key, root, name, confirmed = _resolve_project_root(str(proj / "src" / "pkg"))
    assert Path(root) == proj and name == "realproj" and confirmed


def test_path_key_case_insensitive_for_windows():
    assert ai._path_key(r"C:\Users\x\Desktop\RegBot\\") == ai._path_key(r"c:\users\x\desktop\regbot")


def test_antigravity_root_from_tool_paths():
    paths = [
        r"C:\Users\x\Desktop\Work\clientA\site\api\pay.py",
        r"C:\Users\x\Desktop\Work\clientA\site\ui\index.html",
        r"C:\Users\x\.gemini\antigravity\brain\abc\task.md",
    ]
    root, confirmed = ai._root_from_tool_paths(paths)
    assert root.lower().endswith(r"clienta\site") and not confirmed
    # single deep file: generic module folder trimmed
    root2, _ = ai._root_from_tool_paths([r"D:\stuff\shop\api\routes.py"])
    assert root2.lower() == r"d:\stuff\shop"
    # cluster below a container
    root3, _ = ai._root_from_tool_paths([r"C:\Users\x\Desktop\a\one.py", r"C:\Users\x\Desktop\a\two.py", r"C:\Users\x\Desktop\b.txt"])
    assert root3.lower().endswith(r"desktop\a")



# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------
def test_claude_long_session_sidechain_and_tool_paths(home, store, tmp_path):
    proj = tmp_path / "work" / "myapp"
    write_file(proj, "services/checkout.py", "def checkout():\n    pass\n")
    write_file(proj, "services/cart.py", "from services.checkout import checkout\n")
    write_file(proj, "README.md", "# My app\nA shop.\n")
    cwd = str(proj)
    entries = []
    for i in range(80):  # 160 filler lines before the real work
        entries.append(cc_user(f"question number {i} about python decorators please explain", cwd))
        entries.append(cc_assistant(cwd, "Decorators wrap functions. " * 3))
    entries.append(cc_user("Implement the checkout flow for the shop", cwd))
    entries.append(
        cc_assistant(
            cwd,
            "## Checkout Service Design\nCreated the service.",
            tools=[("Write", {"file_path": f"{cwd}/services/checkout.py", "content": "x"}), ("Read", {"file_path": f"{cwd}/README.md"})],
        )
    )
    entries.append(cc_tool_result(cwd))
    entries.append(cc_assistant(cwd, "", tools=[("Edit", {"file_path": f"{cwd}/services/cart.py", "old_string": "a", "new_string": "b"})]))
    entries.append(cc_user("SIDECHAIN secret request", cwd, isSidechain=True))
    entries.append(cc_assistant(cwd, "x", tools=[("Write", {"file_path": f"{cwd}/sidechain_only.py"})], isSidechain=True))
    entries.append({"type": "user", "isMeta": True, "cwd": cwd, "message": {"role": "user", "content": "<command-name>/clear</command-name>"}})
    assert len(entries) > 120
    cc_session(home, "-work-myapp", "s1", entries)
    # a sub-agent transcript must be ignored entirely
    other = tmp_path / "work" / "otherproj"
    write_file(other, "a.py", "x = 1\n")
    cc_session(home, "-work-myapp", "agent-123", [cc_user("subagent task", str(other)), cc_assistant(str(other), "x", tools=[("Write", {"file_path": f"{other}/a.py"})])])

    AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
    (proj_sys,) = systems(store, "claude_code")
    assert proj_sys.project_name == "myapp"
    assert {p.title for p in code_planets(proj_sys)} == {"checkout.py", "cart.py", "Docs & Data"}
    (sat,) = history(proj_sys).satellites
    assert re.match(r"^\d{4}-\d{2}-\d{2} — checkout\.py, cart\.py$", sat.title)
    # the first 6 non-noise requests are quoted, the rest counted
    assert '"question number 0 about python decorators please explain"' in sat.content
    assert "+75 more requests" in sat.content
    assert "services/checkout.py" in sat.content and "services/cart.py" in sat.content
    assert "TOOL: Claude Code [claude_code]" in sat.content
    text = "".join(s.content + s.title for p in proj_sys.planets for s in p.satellites)
    assert "sidechain_only" not in text and "SIDECHAIN" not in text and "Checkout Service Design" not in text
    assert proj_sys.workspace_path == cwd
    assert proj_sys.source_id == "claude_code:" + ai._path_key(cwd)


def test_claude_parser_reads_beyond_120_lines(tmp_path):
    cwd = "/home/x/code/foo"
    entries = [cc_user(f"filler {i}", cwd) for i in range(300)]
    entries.append(cc_user("late request", cwd, timestamp="2026-11-03T10:00:00.000Z"))
    entries.append(cc_assistant(cwd, "done", tools=[("Write", {"file_path": "/home/x/code/foo/late.py"})]))
    path = tmp_path / "s.jsonl"
    write_jsonl(path, entries)
    data = AgentHistoryImporter._parse_claude_session(path)
    assert data.exchanges[-1].user == "late request"
    assert data.exchanges[-1].files == [("/home/x/code/foo/late.py", True)]
    assert data.cwd == cwd
    assert data.last_ts.startswith("2026-11-03") and data.log_path == str(path)


def test_helios_mention_does_not_capture_session(home, store, tmp_path):
    other = write_file(tmp_path / "dev" / "other", "parser.py", "def parse():\n    pass\n").parent
    cc_session(
        home,
        "proj",
        "s1",
        [
            cc_user("I love Helios, please refactor the parser module here", str(other)),
            cc_assistant(str(other), "## Parser Refactor\ndone", tools=[("Edit", {"file_path": f"{other}/parser.py"})]),
        ],
    )
    entries = [
        {"type": "user", "message": {"role": "user", "content": "how does helios work? see github.com/acme/helios"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "## Helios Overview\n" + "text " * 100}]}},
    ]
    cc_session(home, "no-cwd", "s2", entries)
    AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
    names = [s.project_name for s in systems(store, "claude_code")]
    assert names == ["other"]
    assert ai.LAST_SCAN_STATS["claude_code"]["skipped_no_workspace"] == 1


def test_no_github_url_attribution(home, store):
    codex_rollout(home, "a", "", [cx_msg("user", "Set up my ubuntu server, repo is github.com/me/coolrepo"), cx_msg("assistant", "## Nginx Reverse Proxy\n" + "x " * 300)])
    codex_rollout(home, "b", "", [cx_msg("user", "Fix nextcloud see github.com/me/coolrepo"), cx_msg("assistant", "## Nextcloud Cron\n" + "y " * 300)])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert systems(store, "codex") == []
    assert ai.LAST_SCAN_STATS["codex"]["skipped_no_workspace"] == 2


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------
def test_codex_rollout_parse_with_session_meta_cwd(home, store):
    items = [
        cx_msg("user", "<environment_context>cwd stuff</environment_context>"),
        cx_msg("user", "Add a REST endpoint for orders"),
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["bash", "-lc", "ls"], "workdir": r"D:\code\shop"})}},
        cx_patch(["api/orders.py", "api/__init__.py"]),
        cx_msg("assistant", "## Orders Endpoint\nAdded."),
    ]
    codex_rollout(home, "c1", r"D:\code\shop", items)
    data = AgentHistoryImporter._parse_codex_rollout(next((home / ".codex").rglob("*.jsonl")))
    assert data.cwd == r"D:\code\shop"
    assert len(data.exchanges) == 1
    assert (r"D:\code\shop\api\orders.py", True) in data.exchanges[0].files


def test_missing_workspace_creates_no_project_and_is_counted(home, store):
    codex_rollout(home, "c1", r"D:\code\shop", [cx_msg("user", "Add a REST endpoint for orders"), cx_patch(["api/orders.py"])])
    codex_rollout(home, "c2", "/nonexistent/helios-test/shop2", [cx_msg("user", "Add a REST endpoint for orders"), cx_patch(["api/orders.py"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert systems(store, "codex") == []
    st = ai.LAST_SCAN_STATS["codex"]
    assert st["skipped_missing_workspace"] == 2
    assert set(st["skipped_missing_workspace_paths"]) == {r"D:\code\shop", "/nonexistent/helios-test/shop2"}


def test_existing_folder_without_code_is_skipped(home, store, tmp_path):
    notes = write_file(tmp_path / "notes", "todo.md", "# Todo\n").parent
    codex_rollout(home, "n", str(notes), [cx_msg("user", "Write my todo list please"), cx_patch(["todo.md"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert systems(store, "codex") == []
    assert ai.LAST_SCAN_STATS["codex"]["skipped_no_code_paths"] == [str(notes)]


def test_agent_scan_maps_real_code_plus_one_history_planet(home, store, tmp_path):
    root = make_assistant(tmp_path / "Desktop" / "NOVA")
    chat_answer = "## Mevcut Durum Özeti\n- **Llama 3 (8B) Notları**\n### Önerdiğim İki Adım\nok"
    codex_rollout(home, "s1", str(root), [
        cx_msg("user", "düzelt bunu"),
        cx_patch(["islemler.py"]),
        cx_msg("assistant", chat_answer),
        cx_msg("user", "projeyi çalıştırsana ve asistan.py içindeki görevleri incele"),
        cx_patch(["asistan.py", "degerler.py"]),
        cx_msg("assistant", "12 test geçti\n## Yapılanlar"),
    ])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    (proj,) = systems(store, "codex")
    assert sorted(p.title for p in code_planets(proj)) == ["asistan.py", "degerler.py", "islemler.py", "mikrofon.py"]
    assert all(p.kind == "file" and p.file_path for p in code_planets(proj))
    assert "mikrofon.py" in {p.title for p in proj.planets}  # never touched in a session, still on the map
    tp = {p.id: p.title for p in proj.planets}
    assert {(tp[l.source_planet_id], tp[l.target_planet_id]) for l in proj.links} == {
        ("asistan.py", "degerler.py"), ("asistan.py", "islemler.py"), ("asistan.py", "mikrofon.py"), ("islemler.py", "degerler.py"), ("mikrofon.py", "degerler.py"),
    }
    h = history(proj)
    assert h.title == "Work History" and h.layer == "docs"
    assert "not architecture" in h.summary.lower()
    (sat,) = h.satellites
    assert re.match(r"^\d{4}-\d{2}-\d{2} — islemler\.py, asistan\.py \(\+1\)$", sat.title)
    assert "REQUESTS IN THIS SESSION" in sat.content and '"projeyi çalıştırsana' in sat.content
    chat_bits = ["Mevcut Durum", "Llama 3", "Önerdiğim", "düzelt bunu", "projeyi çalıştırsana", "12 test", "Yapılanlar"]
    titles = [p.title for p in proj.planets] + [s.title for p in proj.planets for s in p.satellites]
    assert not any(bit in t for bit in chat_bits for t in titles)
    assert "Work History" in proj.core_plan and "File-level map" in proj.core_plan


def test_history_most_recent_first_and_capped(home, store, tmp_path):
    root = make_assistant(tmp_path / "proj")
    base = 1_790_000_000
    for i in range(10):
        codex_rollout(home, f"s{i}", str(root), [cx_msg("user", f"change number {i} to the settings"), cx_patch(["degerler.py"])])
        set_mtime(next((home / ".codex").rglob(f"rollout-s{i}.jsonl")), base + i * 86400)
    AgentHistoryImporter.scan_codex(store, max_sessions=20)
    h = history(systems(store, "codex")[0])
    assert len(h.satellites) == ai.HISTORY_SESSION_CAP
    dates = [s.title[:10] for s in h.satellites]
    assert dates == sorted(dates, reverse=True)
    assert "change number 9" in h.satellites[0].content
    assert "+2 older not shown" in h.summary


def test_codex_dated_scratch_dir_skipped_but_nested_project_kept(home, store):
    scratch = home / "Documents" / "Codex" / "2026-09-22" / "tablo"
    write_file(scratch, "tablo.py", "print('table')\n")
    codex_rollout(home, "m", str(scratch), [cx_msg("user", "Build me a small table script"), cx_patch(["tablo.py"])])
    outer = home / "Documents" / "Codex" / "2026-09-19" / "outer"
    regbot = outer / "outputs" / "regbot"
    write_file(regbot, "package.json", '{"name": "regbot"}')
    write_file(regbot, "index.js", "const x = require('./lib');\n")
    write_file(regbot, "lib.js", "module.exports = 1;\n")
    codex_rollout(home, "w", str(outer), [cx_msg("user", "Create the reminder bot"), cx_patch(["outputs/regbot/index.js", "outputs/regbot/lib.js"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    projs = systems(store, "codex")
    assert [p.project_name for p in projs] == ["regbot"]
    assert Path(projs[0].workspace_path) == regbot
    st = ai.LAST_SCAN_STATS["codex"]
    # `outer` is not counted: its only session was re-routed to the nested real project
    assert st["skipped_scratch"] == 1
    assert st["skipped_scratch_paths"] == [str(scratch)]


def _tablo_like(home):
    """A dated Codex workspace full of copies / clones / backups of another project:
    many code files, package.json files only in nested folders, no marker at its root."""
    scratch = home / "Documents" / "Codex" / "2026-09-22" / "tablo"
    for copy in ("work/backups/dice-server-2026-09-20", "work/github-dice-server", "work/dice-server-fix"):
        write_file(scratch, f"{copy}/package.json", '{"name": "dice-server", "dependencies": {"express": "4"}}')
        for i in range(8):
            write_file(scratch, f"{copy}/src/m{i}.js", f"module.exports = {i};\n")
            write_file(scratch, f"{copy}/pages/p{i}.php", "<?php echo 1;\n")
    return scratch


def test_scratch_dir_with_many_nested_copies_is_not_a_project(home, store):
    scratch = _tablo_like(home)
    codex_rollout(home, "m", str(scratch), [cx_msg("user", "Compare the backups of dice-server and tell me the differences")])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert systems(store, "codex") == []
    st = ai.LAST_SCAN_STATS["codex"]
    assert st["skipped_scratch"] == 1 and st["skipped_scratch_paths"] == [str(scratch)]


def _stale_tablo_system(scratch):
    old = SolarSystem(name="tablo", project_name="tablo", source="codex", source_id="codex:x", workspace_path=str(scratch))
    old.add_planet(title="m0.js").kind = "file"
    ext = old.add_planet(title="External Libraries")
    ext.kind = "external"
    ext.is_user_modified = True  # set by the canvas auto-layout, not by the user
    pkg = ext.add_satellite(title="express")
    pkg.kind = "package"
    hist = old.add_planet(title="Work History")
    hist.kind = "history"
    hist.is_user_modified = True
    return old


def test_stale_scratch_system_removed_despite_auto_layout_flag(home, store):
    scratch = _tablo_like(home)
    old = _stale_tablo_system(scratch)
    store.add_system(old)
    codex_rollout(home, "m", str(scratch), [cx_msg("user", "Compare the backups of dice-server and tell me the differences")])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert old not in store.systems
    st = ai.LAST_SCAN_STATS["codex"]
    assert st["removed_stale"] == 1 and st["removed_stale_paths"] == [str(scratch)]


@pytest.mark.parametrize("user_thing", ["note", "link", "purpose"])
def test_stale_scratch_system_with_user_content_is_kept(home, store, user_thing):
    scratch = _tablo_like(home)
    old = _stale_tablo_system(scratch)
    if user_thing == "note":
        old.add_note(0.0, 0.0, title="remember this")
    elif user_thing == "link":
        a, b = old.planets[0], old.planets[1]
        link = old.add_link(a.id, b.id, label="mine")
        link.is_user_modified = True
    else:
        old.purpose, old.purpose_source = "My dice game.", "user"
    store.add_system(old)
    codex_rollout(home, "m", str(scratch), [cx_msg("user", "Compare the backups of dice-server and tell me the differences")])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert old in store.systems
    assert ai.LAST_SCAN_STATS["codex"]["removed_stale"] == 0


def test_first_session_requests_go_to_purpose_evidence_only(home, store, tmp_path):
    root = make_assistant(tmp_path / "Desktop" / "NOVA")
    codex_rollout(home, "late", str(root), [cx_msg("user", "Add a weather command to the assistant please"), cx_patch(["islemler.py"])])
    set_mtime(next((home / ".codex").rglob("rollout-late.jsonl")), 1_790_000_000)
    codex_rollout(home, "early", str(root), [
        cx_msg("user", "devam et"),
        cx_msg("user", "Build me a local voice assistant that reads my calendar out loud every morning"),
        cx_patch(["asistan.py"]),
    ])
    set_mtime(next((home / ".codex").rglob("rollout-early.jsonl")), 1_700_000_000)
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    (proj,) = systems(store, "codex")
    line = next(ln for ln in proj.purpose_evidence.splitlines() if ln.startswith("From the first sessions (what the user asked for):"))
    assert line.index("Build me a local voice assistant") < line.index("Add a weather command")
    assert "devam et" not in line
    assert proj.purpose_evidence.splitlines()[0] == line, "the session line comes first"
    titles =[p.title for p in proj.planets] + [s.title for p in proj.planets for s in p.satellites]
    assert not any(bit in t for t in titles for bit in ("voice assistant", "weather command"))
    assert "voice assistant" not in proj.core_plan
    # a later codebase scan keeps the session line
    CodebaseImporter.analyze_directory(root, store)
    assert "Build me a local voice assistant" in proj.purpose_evidence
    assert proj.purpose_evidence.splitlines()[0].startswith("From the first sessions (what the user asked for):")


def test_ignored_folder_is_skipped_and_counted(home, store, tmp_path):
    root = make_assistant(tmp_path / "Desktop" / "NOVA")
    store.set_setting("ignored_folders", [str(tmp_path / "Desktop")])
    codex_rollout(home, "s", str(root), [cx_msg("user", "Add the browser command to islemler"), cx_patch(["islemler.py"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert systems(store, "codex") == []
    st = ai.LAST_SCAN_STATS["codex"]
    assert st["skipped_ignored"] == 1 and st["skipped_ignored_paths"] == [str(root)]


def test_one_folder_across_codex_claude_and_codebase(home, store, tmp_path):
    root = make_assistant(tmp_path / "Desktop" / "ScanGuard")
    codex_rollout(home, "c", str(root), [cx_msg("user", "Add the browser command to islemler"), cx_patch(["islemler.py"])])
    cc_session(home, "-Desktop-ScanGuard", "k", [
        cc_user("Make mikrofon listen longer", str(root), timestamp="2026-11-04T09:00:00Z"),
        cc_assistant(str(root), "ok", tools=[("Edit", {"file_path": f"{root}/mikrofon.py"})]),
    ])
    set_mtime(next((home / ".codex").rglob("rollout-c.jsonl")), 1_790_000_000)  # 2026-09-21, older
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
    CodebaseImporter.analyze_directory(root, store)
    non_manual = [s for s in store.systems if s.source != "manual"]
    assert len(non_manual) == 1
    (proj,) = non_manual
    assert proj.source == "codex"
    assert set(proj.sources) == {"codex", "claude_code", "codebase"}
    h = history(proj)
    tools = {ai._sat_meta(s)[0] for s in h.satellites}
    assert tools == {"codex", "claude_code"}
    assert "[codex]" in h.summary and "[claude_code]" in h.summary
    assert h.satellites[0].title.startswith("2026-11-04 — mikrofon.py")


def test_codebase_first_then_agent_keeps_codebase_source(home, store, tmp_path):
    root = make_assistant(tmp_path / "proj")
    base = CodebaseImporter.analyze_directory(root, store)
    codex_rollout(home, "c", str(root), [cx_msg("user", "Add the browser command to islemler"), cx_patch(["islemler.py"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert [s for s in store.systems if s.source != "manual"] == [base]
    assert base.source == "codebase" and base.sources == ["codebase", "codex"]
    assert sum(1 for p in base.planets if p.kind == "history") == 1


def test_rescan_is_idempotent(home, store, tmp_path):
    root = make_assistant(tmp_path / "proj")
    codex_rollout(home, "c", str(root), [cx_msg("user", "Add the browser command to islemler"), cx_patch(["islemler.py"])])
    cc_session(home, "-proj", "k", [cc_user("Make mikrofon listen longer", str(root)), cc_assistant(str(root), "ok", tools=[("Edit", {"file_path": f"{root}/mikrofon.py"})])])

    def shape():
        (proj,) = [s for s in store.systems if s.source != "manual"]
        return proj, (len(proj.planets), sorted((p.title, len(p.satellites)) for p in proj.planets), len(proj.links))

    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
    first, s1 = shape()
    for _ in range(2):
        AgentHistoryImporter.scan_codex(store, max_sessions=10)
        AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
        CodebaseImporter.analyze_directory(root, store)
    again, s2 = shape()
    assert again is first and s1 == s2
    assert sum(1 for p in again.planets if p.kind == "history") == 1
    assert len(history(again).satellites) == 2


def test_rescan_keeps_ai_summary_user_planet_and_user_satellite(home, store, tmp_path):
    root = make_assistant(tmp_path / "proj")
    codex_rollout(home, "c", str(root), [cx_msg("user", "Add the browser command to islemler"), cx_patch(["islemler.py"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    (proj,) = systems(store, "codex")
    islemler = next(p for p in proj.planets if p.title == "islemler.py")
    islemler.ai_summary = "Written by a local LLM"
    mine = proj.add_planet(title="Ideas")
    mine.is_user_modified = True
    s = islemler.add_satellite(title="My note", content="mine")
    s.is_user_modified = True
    hist_note = history(proj).add_satellite(title="Retro", content="what I learned")
    hist_note.is_user_modified = True
    proj.core_plan += "\n\n[OLLAMA VERIFIED ARCHITECTURE ANALYSIS]\nverified"
    store.save()
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    assert systems(store, "codex") == [proj]
    eller2 = next(p for p in proj.planets if p.title == "islemler.py")
    assert eller2.ai_summary == "Written by a local LLM"
    assert any(x.title == "My note" for x in eller2.satellites)
    assert any(p.title == "Ideas" for p in proj.planets)
    assert any(x.title == "Retro" for x in history(proj).satellites)
    assert proj.core_plan.count("[OLLAMA VERIFIED ARCHITECTURE ANALYSIS") == 1


def test_legacy_entry_without_workspace_is_reused(home, store, tmp_path):
    legacy = SolarSystem(name="myapp", project_name="myapp", source="claude_code", source_id="claude_code:myapp")
    store.add_system(legacy)
    root = make_assistant(tmp_path / "work" / "myapp")
    cc_session(home, "m", "s1", [cc_user("Write the parser module", str(root)), cc_assistant(str(root), "ok", tools=[("Write", {"file_path": f"{root}/islemler.py"})])])
    AgentHistoryImporter.scan_claude_code(store)
    assert systems(store, "claude_code") == [legacy]
    assert legacy.source_id == "claude_code:myapp"  # kept stable
    assert legacy.workspace_path == str(root)
    assert "islemler.py" in {p.title for p in legacy.planets}


def test_stale_chat_maps_cleaned_up(home, store, tmp_path):
    root = make_assistant(tmp_path / "Desktop" / "ScanGuard")
    # older versions: one chat-derived map per tool for the same folder
    old_ag = SolarSystem(name="ScanGuard", project_name="ScanGuard", source="antigravity", source_id="antigravity:x", workspace_path=str(root))
    old_ag.add_planet(title="Core & Data Model").add_satellite(title="Mevcut Durum Özeti")
    old_cx = SolarSystem(name="ScanGuard", project_name="ScanGuard", source="codex", source_id="codex:x", workspace_path=str(root).upper() if os.name == "nt" else str(root))
    keep_p = old_cx.add_planet(title="UI & Visualization")
    keep_s = keep_p.add_satellite(title="My UI idea", content="user text")
    keep_s.is_user_modified = True
    # missing / scratch folders: plain old maps are removed, user-edited ones stay
    gone = SolarSystem(name="Gone", project_name="Gone", source="codex", workspace_path="/nonexistent/helios-test/gone")
    gone.add_planet(title="Architecture & Planning")
    kept = SolarSystem(name="Kept", project_name="Kept", source="codex", workspace_path="/nonexistent/helios-test/kept")
    kept.add_planet(title="Architecture & Planning").is_user_modified = True
    for s in (old_ag, old_cx, gone, kept):
        store.add_system(s)
    codex_rollout(home, "c", str(root), [cx_msg("user", "Add the browser command to islemler"), cx_patch(["islemler.py"])])
    codex_rollout(home, "g", "/nonexistent/helios-test/gone", [cx_msg("user", "Fix the gone project now"), cx_patch(["a.py"])])
    codex_rollout(home, "k", "/nonexistent/helios-test/kept", [cx_msg("user", "Fix the kept project now"), cx_patch(["a.py"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)

    folder_systems = [s for s in store.systems if s.workspace_path and ai._path_key(s.workspace_path) == ai._path_key(str(root))]
    assert folder_systems == [old_cx]  # the one with user edits is kept, the plain duplicate removed
    assert old_ag not in store.systems
    assert set(old_cx.sources) >= {"codex", "antigravity"}
    titles = [p.title for p in old_cx.planets] + [s.title for p in old_cx.planets for s in p.satellites]
    assert "Mevcut Durum Özeti" not in titles and "Core & Data Model" not in titles
    assert "My UI idea" in titles and "islemler.py" in titles
    assert gone not in store.systems and kept in store.systems
    st = ai.LAST_SCAN_STATS["codex"]
    assert st["removed_stale"] == 1 and st["merged_duplicates"] == 1
    assert st["removed_stale_paths"] == ["/nonexistent/helios-test/gone"]


def test_noise_filter_for_quoted_requests():
    assert ai._is_noise_or_trivial_message("add dark mode", "", used_tools=False)
    assert not ai._is_noise_or_trivial_message("add dark mode", "Done.", used_tools=True)
    assert not ai._is_noise_or_trivial_message("karanlık mod ekle", "x" * 500)
    assert ai._is_noise_or_trivial_message("devam et", "x" * 500, used_tools=True)
    assert ai._is_noise_or_trivial_message("continue", "", used_tools=True)
    assert not ai._is_noise_or_trivial_message("fix it so the login validates emails")


def test_trivial_requests_not_quoted(home, store, tmp_path):
    root = make_assistant(tmp_path / "proj")
    codex_rollout(home, "d", str(root), [cx_msg("user", "devam et"), cx_patch(["islemler.py"]), cx_msg("user", "add dark mode"), cx_patch(["asistan.py"])])
    AgentHistoryImporter.scan_codex(store, max_sessions=10)
    (sat,) = history(systems(store, "codex")[0]).satellites
    assert '"add dark mode"' in sat.content and '"devam et"' not in sat.content


def test_chat_title_extraction_removed():
    for name in ("_extract_clean_feature_title", "_build_components", "_populate_session_planets", "_score_layers", "_request_title"):
        assert not hasattr(ai, name)


def test_antigravity_scan_with_tool_paths_and_artifact(home, store, tmp_path):
    site = tmp_path / "Work" / "clientA" / "site"
    write_file(site, "api/pay.py", "def pay():\n    pass\n")
    write_file(site, "ui/checkout.html", "<html></html>\n")
    write_file(site, "requirements.txt", "stripe\n")
    s_dir = home / ".gemini" / "antigravity" / "brain" / "sess1"
    steps = [
        {"type": "USER_INPUT", "content": "<USER_REQUEST>Build the payment API for the client site</USER_REQUEST>"},
        {
            "type": "PLANNER_RESPONSE",
            "content": "## Payment API\nWorking.",
            "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": str(site / "api" / "pay.py"), "CodeContent": "import os\n"}},
                {"name": "replace_file_content", "args": {"TargetFile": str(site / "ui" / "checkout.html"), "TargetContent": "a/b.c", "ReplacementContent": "x"}},
                {"name": "view_file", "args": {"AbsolutePath": str(home / ".gemini" / "antigravity" / "brain" / "sess1" / "task.md")}},
            ],
        },
    ]
    write_jsonl(s_dir / ".system_generated" / "logs" / "transcript.jsonl", steps)
    (s_dir / "implementation_plan.md").write_text("# Payment Integration Plan\nUse Stripe checkout.\n", encoding="utf-8")
    AgentHistoryImporter.scan_antigravity(store)
    (proj,) = systems(store, "antigravity")
    assert proj.project_name == "clientA-site"
    assert {"pay.py", "checkout.html"} <= {p.title for p in proj.planets}
    h = history(proj)
    titles = [s.title for s in h.satellites]
    assert "Payment Integration Plan" in titles
    assert any(re.match(r"^\d{4}-\d{2}-\d{2} — pay\.py, checkout\.html$", t) for t in titles)
    assert "Payment API" not in titles
    assert "history only" in proj.core_plan


def test_detector_codex_counts_rollouts(home):
    codex_rollout(home, "a", "/x", [])
    codex_rollout(home, "b", "/x", [], sub="archived_sessions")
    (home / ".codex" / "config.toml").write_text("")
    (home / ".codex" / "log").mkdir()
    (home / ".codex" / "auth.json").write_text("{}")
    res = AgentDetector.detect_all()
    assert res["codex"]["count"] == 2
    assert set(res) == {"antigravity", "codex", "claude_code", "ollama", "codebase"}
    for key in ("name", "detected", "count", "status", "color", "path", "description", "tags"):
        assert key in res["codex"]


def test_one_bad_session_does_not_abort(home, store, monkeypatch, tmp_path):
    good = make_assistant(tmp_path / "good")
    codex_rollout(home, "good", str(good), [cx_msg("user", "Write the importer"), cx_patch(["islemler.py"])])
    codex_rollout(home, "bad", str(tmp_path / "bad"), [cx_msg("user", "x")])
    real = AgentHistoryImporter._parse_codex_rollout

    def flaky(path):
        if "bad" in path.name:
            raise RuntimeError("corrupt")
        return real(path)

    monkeypatch.setattr(AgentHistoryImporter, "_parse_codex_rollout", staticmethod(flaky))
    assert AgentHistoryImporter.scan_codex(store) == 1
    assert ai.LAST_SCAN_STATS["codex"]["sessions_failed"] == 1


def test_no_hardcoded_helios_special_cases():
    src = Path(ai.__file__).read_text(encoding="utf-8")
    assert "PROJECT_BLUEPRINTS" not in src
    assert '"helios"' not in src.lower()
