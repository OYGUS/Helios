"""Pure tests for the map helpers behind search, filter and export (no Qt).

core.map_facts (link kinds, stored "Defines:" names, relative paths), core.search_index
and core.markdown_export; plus the frozen-build fallback of GalaxyStore.load.
All project names and texts are invented.
"""

from __future__ import annotations

import json
import random
import sys
import time

import pytest

from core import map_facts as mf
from core import paths
from core.markdown_export import build_markdown, file_stem
from core.models import CanvasNote, GalaxyStore, PlanetLink, SolarSystem
from core.search_index import SearchIndex, find_targets

ROOT = "/work/space/orbit-demo"


def make_system(name="Orbit Demo", root=ROOT) -> SolarSystem:
    s = SolarSystem(name=name, project_name=name, source="codebase", workspace_path=root)
    s.identity = "Python desktop app · PySide6 · entry: app.py"
    s.core_plan = (
        "Purpose: Not written yet\n\n"
        "Map: File-level map: every code file is one module (3 code files; small or flat project).\n"
        "Links: 2 file links from 2 resolved project-local imports / includes / references\n"
        "Analysis: 3 Python files parsed with ast, 0 files scanned with pattern matching, 0 Markdown files\n"
        f"Folder: {root}"
    )
    app = s.add_planet(title="app.py", summary=f"app.py — Python file\nEntry point: has a main block\nPath: {root}/app.py")
    app.kind, app.is_entry, app.file_path, app.layer = "file", True, f"{root}/app.py", "architecture"
    run = app.add_satellite(title="launch()")
    run.kind, run.signature, run.line, run.file_path = "function", "def launch(speed: int) -> None", 4, f"{root}/app.py"
    run.calls = ["engine.py::Thruster.fire()"]
    eng = s.add_planet(title="engine.py", summary="engine.py — Python file")
    eng.kind, eng.file_path = "file", f"{root}/core/engine.py"
    th = eng.add_satellite(title="class Thruster")
    th.kind, th.signature, th.line, th.file_path = "class", "class Thruster", 2, f"{root}/core/engine.py"
    fire = eng.add_satellite(title="Thruster.fire()")
    fire.kind, fire.line = "method", 5
    fire.called_by = ["app.py::launch()"]
    pkg = s.add_planet(title="tools", summary="tools/ — directory\nFiles (3):\n• tools/gauge.py\n• tools/hidden_helper.py\n• tools/readme.md\nLayer: x")
    pkg.kind, pkg.file_path, pkg.layer = "dir", f"{root}/tools", "testing"
    g = pkg.add_satellite(title="gauge.py", content=(
        "gauge.py — Python file\nDefines: read_gauge(), class Dial, Dial.turn() +2 more\n"
        "Path: tools/gauge.py\nSource: Python ast"))
    g.kind, g.file_path = "file", f"{root}/tools/gauge.py"
    ext = s.add_planet(title="External Libraries", summary="External Libraries — 1 third-party package")
    ext.kind, ext.layer = "external", "integration"
    lib = ext.add_satellite(title="rocketlib", content="rocketlib — Python package\nDeclared in: requirements.txt (>=2)")
    lib.kind, lib.signature = "package", ">=2"
    hist = s.add_planet(title="Work History", summary="SESSION HISTORY (NOT ARCHITECTURE): secret chat words")
    hist.kind, hist.layer = "history", "docs"
    sess = hist.add_satellite(title="Please make the rocket faster", content="chat quote: make it faster")
    sess.kind = "session"
    s.links = [
        PlanetLink(source_planet_id=app.id, target_planet_id=eng.id, label="imports core.engine — calls fire()",
                   relation_type="depends_on"),
        PlanetLink(source_planet_id=pkg.id, target_planet_id=eng.id, label="2 imports (gauge.py → engine.py, …)",
                   relation_type="depends_on"),
        PlanetLink(source_planet_id=eng.id, target_planet_id=pkg.id, label="Data Flow", relation_type="flows_to",
                   is_user_modified=True),
    ]
    return s


# ----------------------------------------------------------------------------
# map_facts
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("label,relation,user,kind", [
    ("imports app.util — calls helper()", "depends_on", False, "imports"),
    ("includes header.php", "depends_on", False, "includes"),
    ("references style.css", "depends_on", False, "references"),
    ("uses App\\Models\\User", "depends_on", False, "uses"),
    ("main.py → paths.py: imports core.paths", "depends_on", False, "imports"),
    ("12 imports (a.py → b.py, …)", "depends_on", False, "imports"),
    ("1 import (a.py → b.py)", "depends_on", False, "imports"),
    ("3 edges (calls, imports)", "depends_on", False, "graph"),
    ("something the analysis wrote", "depends_on", False, "found"),
    ("renamed by me", "depends_on", True, "manual"),
    ("Data Flow", "flows_to", True, "manual"),
    ("uses", "flows_to", False, "manual"),  # added in the inspector / by the bridge
])
def test_link_kind_from_stored_label(label, relation, user, kind):
    link = PlanetLink(label=label, relation_type=relation, is_user_modified=user)
    assert mf.link_kind(link) == kind


def test_link_kind_counts_only_present_kinds_in_order():
    s = make_system()
    assert mf.link_kind_counts(s.links) == {"imports": 2, "manual": 1}


def test_defined_names_skips_counts_and_more():
    text = "x\nDefines: read_gauge(), class Dial, Dial.turn(), interface Port +2 more\nPath: a"
    assert mf.defined_names(text) == [("read_gauge()", "function"), ("Dial", "class"),
                                      ("Dial.turn()", "method"), ("Port", "class")]
    assert mf.defined_names("Defines: 1 function, 0 classes — Python ast") == []
    assert mf.defined_names("Defines: nothing found — Python ast") == []


def test_relative_path_and_scrub_never_keep_the_absolute_folder():
    assert mf.relative_path(f"{ROOT}/core/engine.py", ROOT) == "core/engine.py"
    assert mf.relative_path(ROOT, ROOT) == "."
    assert mf.relative_path("/elsewhere/x.py", ROOT) == "x.py"
    assert mf.relative_path("C:\\Work\\Proj\\a\\b.py", "c:\\work\\proj") == "a/b.py"
    assert mf.scrub_root(f"Folder: {ROOT}\nPath: {ROOT}/a.py", ROOT) == "Folder: .\nPath: a.py"


# ----------------------------------------------------------------------------
# search index
# ----------------------------------------------------------------------------
def test_index_matches_names_code_facts_files_and_libraries():
    s = make_system()
    ix = SearchIndex([s])
    def first(q):
        res, _ = ix.search(q, 5)
        return (res[0].kind, res[0].name, res[0].where) if res else None

    assert first("orbit demo") == ("project", "Orbit Demo", "5 modules")
    assert first("engine") == ("module", "engine.py", "Orbit Demo")
    assert first("app.py")[0] == "entry"
    assert first("launch") == ("function", "launch()", "Orbit Demo › app.py")
    assert first("thruster")[0] == "class"
    assert first("thruster.fire")[0] == "method"
    # From the stored "Defines:" line of a file component: leads to that component.
    res, _ = ix.search("read_gauge", 5)
    assert res[0].kind == "function" and res[0].where == "Orbit Demo › tools › gauge.py"
    sys_, planet, sat = find_targets([s], res[0])
    assert planet.title == "tools" and sat.title == "gauge.py"
    # Files listed under "Files (N):" but not shown as components lead to the module.
    res, _ = ix.search("hidden_helper", 5)
    assert res[0].kind == "file" and res[0].satellite_id == "" and res[0].planet_id == planet.id
    assert first("rocketlib")[0] == "library"
    # Names cut off by "+2 more" are not invented.
    assert ix.search("+2 more", 5)[1] == 0
    # The project's identity line matches too (as a weaker match).
    assert first("pyside6") == ("project", "Orbit Demo", "5 modules")
    # Entry modules match "entry point".
    assert any(e.kind == "entry" for e in ix.search("entry point", 5)[0])


def test_ranking_exact_then_prefix_and_active_project_first():
    a, b = make_system("Alpha"), make_system("Beta")
    ix = SearchIndex([a, b])
    res, total = ix.search("launch", 10)
    assert total == 2 and [e.system_id for e in res] == [a.id, b.id]
    res, _ = ix.search("launch", 10, active_system_id=b.id)
    assert res[0].system_id == b.id
    a.planets[0].add_satellite(title="launcher_setup()").kind = "function"
    ix = SearchIndex([a])
    names = [e.name for e in ix.search("launch", 10)[0]]
    assert names.index("launch()") < names.index("launcher_setup()")


def test_search_limit_total_and_dedupe():
    s = make_system()
    p = s.planets[0]
    for i in range(20):
        p.add_satellite(title=f"step_{i}()").kind = "function"
    ix = SearchIndex([s])
    res, total = ix.search("step_", 8)
    assert len(res) == 8 and total == 20
    keys = [(e.system_id, e.planet_id, e.satellite_id, e.kind, e.key) for e in ix.entries]
    assert len(keys) == len(set(keys))
    assert ix.search("   ", 8) == ([], 0)


def test_index_is_fast_for_a_large_store():
    systems = []
    for n in range(4):
        s = SolarSystem(name=f"Big{n}", project_name=f"Big{n}")
        for m in range(150):
            p = s.add_planet(title=f"module_{n}_{m}")
            for c in range(10):
                sat = p.add_satellite(title=f"comp_{m}_{c}.py",
                                      content=f"Defines: fn_{m}_{c}_a(), class K{m}{c}, fn_{m}_{c}_b()")
                sat.kind = "file"
        systems.append(s)
    t0 = time.perf_counter()
    ix = SearchIndex(systems)
    built = time.perf_counter() - t0
    t0 = time.perf_counter()
    for q in ("fn_1", "module_3_14", "k99", "zzz"):
        ix.search(q, 8)
    searched = (time.perf_counter() - t0) / 4
    assert len(ix) > 20000
    assert built < 2.0 and searched < 0.25, (built, searched)


# ----------------------------------------------------------------------------
# Markdown export
# ----------------------------------------------------------------------------
def test_markdown_document_golden():
    s = make_system()
    s.purpose, s.purpose_source = "Plans small rocket launches.", "user"
    s.purpose_suggestion = "A SUGGESTION THAT MUST NOT APPEAR"
    s.planets[0].ai_summary = "Starts the launch sequence."
    s.notes = [CanvasNote(title="Todo", content="Add a fuel gauge")]
    md = build_markdown(s)
    lines = md.splitlines()
    assert lines[0] == "# Orbit Demo"
    assert "**Purpose:** Plans small rocket launches. _(written by you)_" in lines
    assert "**What it is:** Python desktop app · PySide6 · entry: app.py _(from the code analysis)_" in lines
    assert "5 modules · 6 components · 3 links" in lines
    assert "SUGGESTION" not in md
    # Modules: entry first, then by title; quiet modules last.
    heads = [ln[4:] for ln in lines if ln.startswith("### ")]
    assert heads == ["app.py", "engine.py", "tools", "External Libraries", "Work History"]
    assert "- File · Layer: Architecture & Planning · Program starts here · Path: `app.py`" in lines
    assert "AI-written description (local Ollama model, prose only):" in lines
    assert "> Starts the launch sequence." in lines
    assert "- `launch()` — Function · `def launch(speed: int) -> None` · `app.py:4`" in lines
    assert "  - Calls: `engine.py › Thruster.fire()`" in lines
    assert "  - Defines: `read_gauge()`, `class Dial`, `Dial.turn()`" in lines
    # Links: every link, exactly as stored, sorted.
    i = lines.index("## Links (3)")
    assert lines[i + 2:i + 5] == [
        "- app.py → engine.py (Imports / imports core.engine — calls fire())",
        "- engine.py → tools (Added by hand / Data Flow)",
        "- tools → engine.py (Imports / 2 imports (gauge.py → engine.py, …))",
    ]
    assert "- `rocketlib` `>=2` — declared in requirements.txt (>=2)" in lines
    assert "## Entry points" in lines and any(ln.startswith("- app.py — ") for ln in lines)
    assert "- **Todo** — Add a fuel gauge" in lines
    # Work History: listed with its size only (chat text is never exported).
    assert "secret chat words" not in md and "make the rocket faster" not in md and "chat quote" not in md
    # No absolute project folder anywhere.
    assert ROOT not in md and "/work/space" not in md
    assert "- Analysis: 3 Python files parsed with ast, 0 files scanned with pattern matching, 0 Markdown files" in lines
    assert lines[-1].startswith("- ") and "Generated by Helios from the code analysis" in md


def test_markdown_purpose_missing_or_only_suggested_and_deterministic():
    s = make_system()
    s.purpose, s.purpose_source, s.purpose_suggestion = "", "", "Suggested text"
    md = build_markdown(s)
    assert "**Purpose:** _not written yet_" in md and "Suggested text" not in md
    shuffled = make_system()
    shuffled.id = s.id
    for a, b in zip(shuffled.planets, s.planets):
        a.id = b.id
        for sa, sb in zip(a.satellites, b.satellites):
            sa.id = sb.id
    for l1, l2 in zip(shuffled.links, s.links):
        l1.source_planet_id, l1.target_planet_id = l2.source_planet_id, l2.target_planet_id
    random.Random(3).shuffle(shuffled.planets)
    random.Random(4).shuffle(shuffled.links)
    shuffled.purpose_suggestion = "Suggested text"
    assert build_markdown(shuffled) == md == build_markdown(s)


def test_markdown_for_a_hand_drawn_project():
    s = SolarSystem(name="Sketch*Pad", project_name="Sketch*Pad", core_plan="My own plan.")
    p = s.add_planet(title="UI", summary="Buttons and windows")
    p.add_satellite(title="Toolbar", content="Holds the tools")
    md = build_markdown(s)
    assert md.startswith("# Sketch\\*Pad\n")
    assert "  - Notes (as written on the map):" in md and "    > Holds the tools" in md
    assert "_No links._" in md
    assert "not built by the code analysis" in md


def test_file_stem():
    assert file_stem("My App!") == "my-app"
    assert file_stem("Çiçek Sepeti") == "cicek-sepeti"
    assert file_stem("***") == "helios-map"


# ----------------------------------------------------------------------------
# GalaxyStore: corrupt file in a frozen build
# ----------------------------------------------------------------------------
@pytest.fixture
def frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "user"))


def test_frozen_corrupt_file_falls_back_to_no_projects(frozen, tmp_path):
    path = tmp_path / "g.json"
    path.write_text("{ not json", encoding="utf-8")
    store = GalaxyStore(path)
    assert store.systems == []
    assert store.load_error and store.backup_path is not None and store.backup_path.exists()
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_source_corrupt_file_still_shows_the_starter(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    path = tmp_path / "g.json"
    path.write_text(json.dumps({"systems": "nope"}), encoding="utf-8")
    store = GalaxyStore(path)
    assert [s.name for s in store.systems] == ["Helios"]


# ----------------------------------------------------------------------------
# Complete definition lists (`defined_names`) from the code analysis
# ----------------------------------------------------------------------------
from pathlib import Path  # noqa: E402

from connectors.codebase.constants import DEFINED_NAMES_CAP  # noqa: E402
from connectors.codebase_importer import CodebaseImporter  # noqa: E402
from core.models import PlanetNode, SatelliteNode  # noqa: E402


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _big_module(n_funcs: int) -> str:
    body = ['"""Generated helpers for a test."""', "", "class Gauge:",
            "    def __init__(self):", "        self.v = 0",
            "    def read_level(self):", "        return self.v",
            "    def _calibrate_hidden(self):", "        return 1", ""]
    body += [f"def helper_number_{i}():\n    return {i}\n" for i in range(n_funcs)]
    return "\n".join(body)


def _dir_project(tmp_path: Path) -> Path:
    root = tmp_path / "Fleet Tracker"
    for pkg in ("engine", "radio"):
        for i in range(8):
            _write(root, f"{pkg}/part_{i}.py", f"def part_{pkg}_{i}():\n    return {i}\n")
    _write(root, "engine/bigfile.py", _big_module(30))
    _write(root, "main.py", "from engine import bigfile\n\nif __name__ == '__main__':\n    bigfile.helper_number_1()\n")
    return root


def _store(tmp_path):
    st = GalaxyStore(storage_path=tmp_path / "data" / "galaxy.json")
    st.systems = []
    return st


def test_directory_map_stores_every_definition_and_search_finds_them(tmp_path):
    st = _store(tmp_path)
    system = CodebaseImporter.analyze_directory(_dir_project(tmp_path), st)
    engine = next(p for p in system.planets if p.title == "engine")
    big = next(s for s in engine.satellites if s.title == "bigfile.py")
    assert "+" in mf.content_field(big.content, "Defines")  # the display text is still clipped
    names = big.defined_names
    assert "helper_number_29()" in names and "class Gauge" in names
    assert "Gauge.read_level()" in names and "Gauge._calibrate_hidden()" in names
    assert not any("__init__" in n for n in names)
    ix = SearchIndex([system])
    res, _ = ix.search("helper_number_29", 5)
    assert res and res[0].kind == "function" and res[0].satellite_id == big.id
    res, _ = ix.search("_calibrate_hidden", 5)
    assert res and res[0].kind == "method" and res[0].name == "Gauge._calibrate_hidden()"
    # Persisted, loadable, and not a user change.
    st.save()
    raw = json.loads(st.file_path.read_text(encoding="utf-8"))
    assert any("helper_number_29()" in s.get("defined_names", [])
               for p in raw["systems"][0]["planets"] for s in p["satellites"])
    reloaded = GalaxyStore(storage_path=st.file_path)
    assert SearchIndex(reloaded.systems).search("helper_number_29", 5)[1] == 1
    assert not big.is_user_modified and not engine.is_user_modified


def test_file_level_module_keeps_definitions_not_shown_as_components(tmp_path):
    root = tmp_path / "tiny"
    _write(root, "tool.py", _big_module(40))
    st = _store(tmp_path)
    system = CodebaseImporter.analyze_directory(root, st)
    planet = next(p for p in system.planets if p.title == "tool.py")
    shown = {s.title for s in planet.satellites}
    assert "helper_number_39()" not in shown  # beyond the component cap
    assert "helper_number_39()" in planet.defined_names
    res, _ = SearchIndex([system]).search("helper_number_39", 5)
    assert res and res[0].planet_id == planet.id and res[0].satellite_id == ""


def test_rescan_replaces_the_list_and_keeps_it(tmp_path):
    root = _dir_project(tmp_path)
    st = _store(tmp_path)
    CodebaseImporter.analyze_directory(root, st)
    _write(root, "engine/bigfile.py", _big_module(30) + "\ndef brand_new_probe():\n    return 0\n")
    system = CodebaseImporter.analyze_directory(root, st)
    assert len(st.systems) == 1
    big = next(s for p in system.planets for s in p.satellites if s.title == "bigfile.py")
    assert "brand_new_probe()" in big.defined_names
    assert SearchIndex(st.systems).search("brand_new_probe", 5)[1] == 1


def test_defined_names_are_capped(tmp_path):
    root = tmp_path / "gen"
    _write(root, "huge.py", _big_module(DEFINED_NAMES_CAP + 50))
    st = _store(tmp_path)
    system = CodebaseImporter.analyze_directory(root, st)
    planet = next(p for p in system.planets if p.title == "huge.py")
    assert len(planet.defined_names) == DEFINED_NAMES_CAP


def test_old_data_without_the_field_still_uses_the_defines_line(tmp_path):
    path = tmp_path / "old.json"
    sat = {"title": "gauge.py", "kind": "file",
           "content": "gauge.py — Python file\nDefines: read_gauge(), class Dial +3 more\nPath: gauge.py"}
    path.write_text(json.dumps({"format_version": 2, "systems": [
        {"name": "Oldie", "planets": [{"title": "tools", "kind": "dir", "satellites": [sat]}]}]}), encoding="utf-8")
    st = GalaxyStore(storage_path=path)
    loaded = st.systems[0].planets[0].satellites[0]
    assert loaded.defined_names == []
    ix = SearchIndex(st.systems)
    assert ix.search("read_gauge", 5)[1] == 1 and ix.search("dial", 5)[0][0].kind == "class"


def test_classify_name_and_merge_keeps_the_longer_list():
    assert mf.classify_name("class Motor") == ("Motor", "class")
    assert mf.classify_name("interface Port") == ("Port", "class")
    assert mf.classify_name("Motor.spin()") == ("Motor.spin()", "method")
    assert mf.classify_name("run()") == ("run()", "function")
    assert mf.classify_name("3 functions") is None
    s = SolarSystem(name="Dup")
    a = s.add_planet(title="core")
    b = s.add_planet(title="Core")
    a.defined_names, b.defined_names = ["x()"], ["x()", "y()"]
    sa = a.add_satellite(title="m.py")
    sb = b.add_satellite(title="m.py")
    sa.defined_names, sb.defined_names = [], ["only_here()"]
    s.merge_duplicate_nodes()
    assert s.planets[0].defined_names == ["x()", "y()"]
    assert s.planets[0].satellites[0].defined_names == ["only_here()"]
    assert PlanetNode().defined_names == [] and SatelliteNode().defined_names == []
