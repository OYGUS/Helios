"""Tests for connectors.codebase_importer (synthetic projects in tmp_path)."""

from __future__ import annotations

import textwrap
from collections import Counter
from pathlib import Path

import pytest

from connectors import codebase_importer as ci
from connectors.codebase_importer import CodebaseImporter, _classify_file_layer, build_code_map
from core.models import GalaxyStore, SolarSystem


@pytest.fixture(autouse=True)
def _fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def store(tmp_path):
    return GalaxyStore(storage_path=tmp_path / "store" / "galaxy.json")


def write(root: Path, rel: str, text: str = "") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def links_by_title(system):
    t = {p.id: p.title for p in system.planets}
    return {(t[l.source_planet_id], t[l.target_planet_id]): l for l in system.links}


def planet(system, title):
    return next(p for p in system.planets if p.title == title)


def write_assistant_like(root: Path) -> Path:
    """A flat 4-file Python project shaped like a small voice assistant."""
    write(root, "degerler.py", """\
        import json
        import os

        # ==========================
        # SHARED CONFIGURATION FILE
        # Every speech and model option is kept here.
        # ==========================

        ASSISTANT_NAME = "NOVA"
        MODEL_NAME = "qwen2.5:3b"
        LISTEN_MODE = "text"
        ALLOWED_APPS = {"notepad": "notepad.exe"}
        """)
    write(root, "islemler.py", """\
        import subprocess
        from datetime import datetime
        import psutil
        import degerler

        focus_mode = False


        # 1. Opens the browser
        def tarayici_ac():
            subprocess.Popen(["chrome"])

        def saati_goster():
            \"\"\"Tells the current time.\"\"\"
            return datetime.now()

        def bildirim_kapat():
            pass

        def program_ac(isim):
            return degerler.ALLOWED_APPS.get(isim)

        def odak_acik_mi():
            return bool(psutil.pids())

        def odak_modu_aktif_mi():
            return focus_mode

        def odak_modu(durum="degistir"):
            return durum

        def gorev_yurut(fonksiyon_adi, parametre=""):
            return fonksiyon_adi
        """)
    write(root, "mikrofon.py", """\
        import numpy as np
        import degerler


        def ses_kaydet():
            return np.zeros(1)

        def metne_cevir(ses):
            return str(ses)

        def dinle_ve_yaz():
            return metne_cevir(ses_kaydet())
        """)
    write(root, "asistan.py", """\
        import sys
        import ollama
        import degerler
        import islemler

        if degerler.LISTEN_MODE == "voice":
            import mikrofon


        def girdi_oku():
            return input()

        def yanit_uret(mesaj):
            return ollama.chat(model=degerler.MODEL_NAME, messages=[mesaj])

        def baslat():
            islemler.gorev_yurut("saati_goster")
        """)
    return root


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rel, expected",
    [
        ("build.py", "architecture"),
        ("review.py", "core"),
        ("quick_sort.py", "core"),
        ("bottom.py", "core"),
        ("capital.ts", "core"),
        ("tests/test_window.py", "testing"),
        ("src/ui/test_dialog.py", "testing"),
        ("components/Button.test.tsx", "testing"),
        ("scripts/deploy.py", "architecture"),
        ("scripts/helpers.py", "architecture"),
        ("ui/main_window.py", "ui"),
        ("MainWindow.py", "ui"),
        ("connectors/live_bridge.py", "integration"),
        ("apiClient.ts", "integration"),
        ("core/models.py", "core"),
        ("main.py", "architecture"),
        ("styles/site.css", "ui"),
        ("README.md", "architecture"),
        ("docs/guide.md", "docs"),
    ],
)
def test_classifier_whole_tokens(rel, expected):
    assert _classify_file_layer(Path(rel)) == expected


def test_split_tokens_camel_and_separators():
    assert ci._split_tokens("quickSort-utils.v2_final") == ["quick", "sort", "utils", "v2", "final"]
    assert ci._split_tokens("HTTPServer") == ["http", "server"]


# ---------------------------------------------------------------------------
# Fixture project: real modules + real edges only
# ---------------------------------------------------------------------------
@pytest.fixture
def layered_project(tmp_path):
    root = tmp_path / "proj"
    write(root, "main.py", '"""Entry point."""\nfrom ui.window import Window\n\nWindow()\n')
    write(root, "core/__init__.py")
    write(root, "core/models.py", '"""Data model."""\nimport json\n\nclass Store:\n    def save(self):\n        return json.dumps({})\n')
    write(root, "core/util.py", "def helper():\n    return 1\n")
    write(root, "ui/__init__.py")
    write(root, "ui/window.py", "from core.models import Store\nfrom connectors import importer\n\nclass Window:\n    pass\n")
    write(root, "connectors/__init__.py")
    write(root, "connectors/importer.py", "from ..core import models\n\n\ndef run():\n    from core.util import helper\n    return helper()\n")
    # More than FILE_LEVEL_MAX_FILES code files, so this stays a directory map.
    for d in ("core", "ui", "connectors"):
        for i in range(3):
            write(root, f"{d}/extra{i}.py", f"VALUE_{i} = {i}\n")
    # Dependency / VCS folders that must never be descended into
    write(root, "node_modules/leftpad/index.js", "module.exports = 1;\n")
    write(root, "node_modules/leftpad/core/models.py", "import ui\n")
    write(root, ".git/hooks/pre-commit.py", "import core\n")
    write(root, "venv/lib/site.py", "import core\n")
    return root


def test_planets_are_real_directories(layered_project, store):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    titles = {p.title for p in system.planets}
    assert titles == {"Project Root", "core", "ui", "connectors"}
    assert all(p.kind == "dir" for p in system.planets)
    assert planet(system, "core").file_path == str((layered_project / "core").resolve())
    assert planet(system, "Project Root").file_path == str(layered_project.resolve())
    assert planet(system, "core").layer == "core"
    assert planet(system, "ui").layer == "ui"
    assert planet(system, "connectors").layer == "integration"


def test_edges_are_exactly_the_real_imports(layered_project, store):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    edges = links_by_title(system)
    assert set(edges) == {
        ("Project Root", "ui"),
        ("ui", "core"),
        ("ui", "connectors"),
        ("connectors", "core"),
    }
    assert not any(src == "core" for src, _ in edges)
    assert all(l.relation_type == "depends_on" for l in system.links)
    # relative import (from ..core import models) + import inside a function (core.util)
    label = edges[("connectors", "core")].label
    assert label.startswith("2 imports")
    assert "importer.py → models.py" in label or "importer.py → util.py" in label


def test_links_survive_store_roundtrip_without_invented_edges(layered_project, store, tmp_path):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    assert len(system.links) == 4
    reloaded = GalaxyStore(storage_path=store.storage_path)
    again = next(s for s in reloaded.systems if s.source == "codebase")
    assert {l.label for l in again.links} == {l.label for l in system.links}


def test_ignored_dirs_never_scanned(layered_project, store):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    all_paths = [s.file_path for p in system.planets for s in p.satellites]
    assert all("node_modules" not in fp and ".git" not in fp and "venv" not in fp for fp in all_paths)
    assert "node_modules" not in system.core_plan
    assert not any("node_modules" in p.summary or "venv/" in p.summary for p in system.planets)


def test_walk_never_opens_files_in_ignored_dirs(layered_project, monkeypatch):
    opened = []
    real_open = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", spy)
    files, _ = ci._walk_project(layered_project)
    assert {f.rel for f in files} >= {"core/models.py", "ui/window.py", "main.py"}
    assert not any("node_modules" in p or "/.git/" in p or "/venv/" in p for p in opened)
    # every analysed file is read exactly once
    assert len(opened) == len(set(opened))


def test_relative_import_resolution_levels(tmp_path):
    root = tmp_path / "rel"
    write(root, "pkg/__init__.py", "from . import a\n")
    write(root, "pkg/a.py", "from .b import thing\n")
    write(root, "pkg/b.py", "thing = 1\n")
    write(root, "pkg/sub/__init__.py")
    write(root, "pkg/sub/c.py", "from ..a import x\nfrom .. import b\nimport os\n")
    files, _ = ci._walk_project(root)
    rels = {f.rel for f in files}
    ci._resolve_python_imports(files, rels)
    deps = {f.rel: f.deps for f in files}
    assert deps["pkg/__init__.py"] == {"pkg/a.py"}
    assert deps["pkg/a.py"] == {"pkg/b.py"}
    assert deps["pkg/sub/c.py"] == {"pkg/a.py", "pkg/b.py"}
    c = next(f for f in files if f.rel == "pkg/sub/c.py")
    assert "os" in c.external_imports


def test_src_layout_descends_into_dominant_directory(tmp_path, store):
    root = tmp_path / "srcproj"
    write(root, "setup.py", "")
    for name in ("api", "ui", "db"):
        write(root, f"src/{name}/__init__.py")
        for i in range(4):
            write(root, f"src/{name}/m{i}.py", "x = 1\n")
    write(root, "src/api/routes.py", "from db import m0\n")
    system = CodebaseImporter.analyze_directory(root, store)
    titles = {p.title for p in system.planets}
    assert {"src/api", "src/ui", "src/db"} <= titles
    assert ("src/api", "src/db") in links_by_title(system)


def test_small_flat_project_is_file_level_with_layer_colours(tmp_path, store):
    root = tmp_path / "flat"
    write(root, "main.py", "import models\n")
    write(root, "models.py", "class A: pass\n")
    write(root, "main_window.py", "import models\n")
    write(root, "test_models.py", "import models\n")
    system = CodebaseImporter.analyze_directory(root, store)
    assert {p.title for p in system.planets} == {"main.py", "models.py", "main_window.py", "test_models.py"}
    assert {p.title: p.layer for p in system.planets} == {
        "main.py": "architecture", "models.py": "core", "main_window.py": "ui", "test_models.py": "testing",
    }
    assert set(links_by_title(system)) == {("main.py", "models.py"), ("main_window.py", "models.py"), ("test_models.py", "models.py")}
    assert "File-level map" in system.core_plan


def test_assistant_shaped_flat_project_file_level_map(tmp_path, store):
    root = write_assistant_like(tmp_path / "NOVA")
    system = CodebaseImporter.analyze_directory(root, store)
    assert sorted(p.title for p in system.planets if p.kind == "file") == ["asistan.py", "degerler.py", "islemler.py", "mikrofon.py"]
    assert [p.title for p in system.planets if p.kind != "file"] == [ci.EXTERNAL_PLANET_TITLE]
    for p in system.planets:
        if p.kind != "file":
            continue
        assert p.file_path == str(root.resolve() / p.title)
    assert set(links_by_title(system)) == {
        ("asistan.py", "degerler.py"), ("asistan.py", "islemler.py"), ("asistan.py", "mikrofon.py"),
        ("islemler.py", "degerler.py"), ("mikrofon.py", "degerler.py"),
    }
    assert not any(src == "degerler.py" for src, _ in links_by_title(system))
    assert all(l.relation_type == "depends_on" for l in system.links)
    assert links_by_title(system)[("asistan.py", "mikrofon.py")].label == "imports mikrofon"  # import inside an if-block
    islemler = planet(system, "islemler.py")
    assert [s.title for s in islemler.satellites] == [
        "tarayici_ac()", "saati_goster()", "bildirim_kapat()", "program_ac()",
        "odak_acik_mi()", "odak_modu_aktif_mi()", "odak_modu()", "gorev_yurut()",
    ]
    assert all(s.visual_tag == "code" and s.file_path == islemler.file_path for s in islemler.satellites)
    sat = {s.title: s for s in islemler.satellites}
    assert "def odak_modu(durum='degistir')" in sat["odak_modu()"].content
    assert "Tells the current time." in sat["saati_goster()"].content
    assert "Opens the browser" in sat["tarayici_ac()"].content  # comment above the definition
    assert "Location: islemler.py, line 10" in sat["tarayici_ac()"].content
    assert sat["odak_modu()"].kind == "function" and sat["odak_modu()"].line == 29
    assert sat["odak_modu()"].signature == "def odak_modu(durum='degistir')"
    # truthful summary: imports from the project + third-party names, how it was derived
    assert islemler.summary.splitlines()[0] == "islemler.py — Python file, 33 lines"
    assert "• degerler.py: imports degerler" in islemler.summary
    assert "External: psutil" in islemler.summary and "Standard library: datetime, subprocess" in islemler.summary
    assert "Python ast" in islemler.summary
    assert not any(line.isupper() and len(line) > 12 for line in islemler.summary.splitlines())  # no ALL-CAPS headings


def test_constants_only_module_gets_names_satellite(tmp_path, store):
    root = write_assistant_like(tmp_path / "NOVA")
    system = CodebaseImporter.analyze_directory(root, store)
    degerler = planet(system, "degerler.py")
    assert len(degerler.satellites) == 1
    names = degerler.satellites[0]
    assert names.title == "Top-level names (4)"
    for n in ("ASSISTANT_NAME", "MODEL_NAME", "LISTEN_MODE", "ALLOWED_APPS"):
        assert n in names.content
    assert names.kind == "constants"
    assert "values are not evaluated" in names.content
    assert "SHARED CONFIGURATION FILE" in degerler.summary  # first comment of the file
    assert "Used by:\n• asistan.py\n• islemler.py\n• mikrofon.py" in degerler.summary


def test_from_import_names_in_link_label(tmp_path, store):
    root = tmp_path / "names"
    write(root, "app.py", "from helpers import parse, render, load, save, extra\nfrom pkg import mod\n")
    write(root, "helpers.py", "def parse(): pass\n")
    write(root, "pkg/__init__.py")
    write(root, "pkg/mod.py", "X = 1\n")
    system = CodebaseImporter.analyze_directory(root, store)
    edges = links_by_title(system)
    assert edges[("app.py", "helpers.py")].label == "imports helpers (parse, render, load, save, …)"
    assert edges[("app.py", "mod.py")].label == "imports pkg.mod"


def test_docs_and_data_planet(tmp_path, store):
    root = tmp_path / "withdocs"
    write(root, "main.py", "print(1)\n")
    write(root, "README.md", "# Tool\nDoes things.\n## Usage\n")
    write(root, "requirements.txt", "requests\n")
    write(root, "config.json", '{"a": 1}')
    write(root, "package-lock.json", "{}")
    system = CodebaseImporter.analyze_directory(root, store)
    docs = planet(system, ci.DOCS_PLANET_TITLE)
    assert docs.kind == "layer" and docs.layer == "docs"
    assert {s.title for s in docs.satellites} == {"README.md", "requirements.txt", "config.json"}
    assert "not parsed" in next(s for s in docs.satellites if s.title == "config.json").content
    assert {p.title for p in system.planets} == {"main.py", ci.DOCS_PLANET_TITLE}
    # no Docs planet when there are no such files
    other = tmp_path / "nodocs"
    write(other, "a.py", "x = 1\n")
    assert [p.title for p in CodebaseImporter.analyze_directory(other, store).planets] == ["a.py"]


def test_flat_project_larger_than_planet_cap(tmp_path, store):
    root = tmp_path / "bigflat"
    write(root, "core.py", "def run():\n    pass\n" * 3)
    for i in range(20):
        write(root, f"mod{i:02d}.py", "import core\n" + "def f():\n    pass\n" * (i + 1))
    system = CodebaseImporter.analyze_directory(root, store)
    assert len(system.planets) == ci.MAX_PLANETS
    other = planet(system, ci.OTHER_FILES_PLANET_TITLE)
    assert other.kind == "layer"
    files = [p for p in system.planets if p.kind == "file"]
    assert len(files) == ci.MAX_PLANETS - 1
    assert "core.py" in {p.title for p in files}  # most-imported file keeps its own module
    shown = {p.title for p in files} | {s.title for s in other.satellites}
    assert shown == {"core.py"} | {f"mod{i:02d}.py" for i in range(20)}
    for i in range(20):
        assert f"mod{i:02d}.py" in shown
    edges = links_by_title(system)
    assert (ci.OTHER_FILES_PLANET_TITLE, "core.py") in edges
    assert edges[(ci.OTHER_FILES_PLANET_TITLE, "core.py")].label.startswith(f"{20 - (ci.MAX_PLANETS - 2)} imports")
    assert "did not fit as separate modules" in other.summary


def test_planet_cap_folds_into_other(tmp_path, store):
    root = tmp_path / "many"
    for i in range(15):
        for j in range(i + 1):
            write(root, f"mod{i:02d}/f{j}.py", "x = 1\n")
    system = CodebaseImporter.analyze_directory(root, store)
    assert len(system.planets) == ci.MAX_PLANETS
    other = planet(system, ci.OTHER_PLANET_TITLE)
    assert "mod00 (1 file)" in other.summary and "mod03 (4 files)" in other.summary


def test_satellite_cap_mentions_hidden_files(tmp_path, store, monkeypatch):
    monkeypatch.setattr(ci, "SATELLITE_CAP", 5)
    root = tmp_path / "big"
    write(root, "other/x.py", "")
    for i in range(6):
        write(root, f"other/y{i}.py", "")
    for i in range(9):
        write(root, f"pkg/file{i}.py", "def f():\n    pass\n" * (i + 1))
    system = CodebaseImporter.analyze_directory(root, store)
    pkg = planet(system, "pkg")
    assert len(pkg.satellites) == 5
    assert "+4 more files not shown" in pkg.summary
    for i in range(9):
        assert f"pkg/file{i}.py" in pkg.summary


def test_js_ts_relative_import_edges(tmp_path, store):
    root = tmp_path / "web"
    write(root, "package.json", '{"dependencies": {"react": "18"}}')
    write(root, "app/index.ts", "import { api } from '../lib/api';\nexport * from '../lib/util.js';\nconst x = require('../lib');\n")
    write(root, "lib/api.ts", "export const api = () => 1;\nimport('./lazy').then(() => 0);\n")
    write(root, "lib/util.ts", "export function util() {}\n")
    write(root, "lib/index.ts", "export {}\n")
    write(root, "lib/lazy.tsx", "export default function Lazy() { return null }\n")
    write(root, "ui/view.tsx", "import React from 'react';\n// import x from '../app/index';\n")
    system = CodebaseImporter.analyze_directory(root, store)
    edges = links_by_title(system)
    # small project -> file-level map; both index.ts files are titled by relative path
    assert set(edges) == {
        ("app/index.ts", "api.ts"), ("app/index.ts", "util.ts"), ("app/index.ts", "lib/index.ts"), ("api.ts", "lazy.tsx"),
    }
    assert edges[("app/index.ts", "api.ts")].label == "imports ../lib/api"
    assert "react" in planet(system, "view.tsx").summary
    lazy = planet(system, "lazy.tsx")
    assert [s.title for s in lazy.satellites] == ["Lazy()"]
    assert "pattern matching" in lazy.satellites[0].content


def test_rust_fn_and_go_receiver_methods_extracted(tmp_path):
    root = tmp_path / "poly"
    write(root, "src/lib.rs", "pub struct Engine;\nimpl Engine {\n    pub fn start(&self) {}\n}\nfn helper() -> u8 { 1 }\npub(crate) async fn fetch() {}\n")
    write(root, "svc/server.go", "package svc\n\ntype Server struct{}\n\nfunc (s *Server) Do(x int) error { return nil }\nfunc New() *Server { return &Server{} }\n")
    files, _ = ci._walk_project(root)
    rs = next(f for f in files if f.rel == "src/lib.rs")
    go = next(f for f in files if f.rel == "svc/server.go")
    assert {"fn start()", "fn helper()", "fn fetch()"} <= set(rs.functions)
    assert "type Engine" in rs.classes
    assert "func (*Server) Do()" in go.functions and "func New()" in go.functions
    assert "type Server" in go.classes
    assert rs.method == "regex"


def test_generated_text_is_truthful_for_non_python(tmp_path, store):
    root = tmp_path / "gosvc"
    write(root, "a/x.go", "package a\nfunc A() {}\n")
    write(root, "b/y.go", "package b\nfunc B() {}\n")
    system = CodebaseImporter.analyze_directory(root, store)
    text = system.core_plan + "".join(p.summary for p in system.planets)
    assert "via AST analysis" not in text
    assert "regex pattern matching" in text
    assert not system.links


def test_tech_detection_by_dependency_names(tmp_path):
    root = tmp_path / "tech"
    write(root, "package.json", '{"description": "next gen tool", "dependencies": {"next-themes": "1", "vue-fake-lib": "1"}}')
    write(
        root,
        "pyproject.toml",
        '[project]\nname = "demo"\ndescription = "the next big express spring thing"\ndependencies = ["requests>=2", "fastapi[all]"]\n'
        '[project.optional-dependencies]\nui = ["PySide6-Essentials"]\n',
    )
    types, stack, desc = ci._detect_project_technologies(root)
    joined = " ".join(stack)
    assert "Next.js" not in joined
    assert "Vue" not in joined
    assert "Express" not in joined and "Spring" not in joined
    assert "FastAPI" in joined and "PySide6" in joined
    assert desc == "next gen tool"


def test_tech_detection_exact_next_and_scoped_prefix(tmp_path):
    root = tmp_path / "tech2"
    write(root, "package.json", '{"dependencies": {"next": "14"}, "devDependencies": {"@nestjs/core": "10"}}')
    write(root, "requirements.txt", "# comment\nDjango==4.2  # web\n-r other.txt\nlangchain-community\n")
    _types, stack, _ = ci._detect_project_technologies(root)
    joined = " ".join(stack)
    assert "Next.js" in joined and "NestJS" in joined and "Django" in joined and "LangChain" in joined


def test_pyproject_regex_fallback_without_tomllib(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "_tomllib", None)
    text = '[project]\ndescription = "next"\ndependencies = [\n  "flask>=2",\n]\n[tool.poetry.dependencies]\npython = "^3.10"\npandas = "*"\n'
    names = set(ci._pyproject_dependency_names(text))
    assert {"flask", "pandas"} <= names
    assert "next" not in names and "python" not in names


def test_rescan_preserves_user_edits(layered_project, store):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    core = planet(system, "core")
    core.summary = "My own notes about core"
    core.is_user_modified = True
    sat = planet(system, "ui").add_satellite(title="My idea", content="user text")
    sat.is_user_modified = True
    edited_auto = planet(system, "connectors").satellites[0]
    edited_auto.content = "edited by user"
    edited_auto.is_user_modified = True
    edited_title = edited_auto.title
    store.save()

    again = CodebaseImporter.analyze_directory(layered_project, store)
    assert again is system
    assert len([s for s in store.systems if s.source == "codebase"]) == 1
    assert planet(again, "core").summary == "My own notes about core"
    assert any(s.title == "My idea" for s in planet(again, "ui").satellites)
    conn_sats = [s for s in planet(again, "connectors").satellites if s.title == edited_title]
    assert len(conn_sats) == 1 and conn_sats[0].content == "edited by user"


def test_rescan_keeps_ollama_block(layered_project, store):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    system.core_plan += "\n\n[OLLAMA VERIFIED ARCHITECTURE ANALYSIS]\nverified text"
    CodebaseImporter.analyze_directory(layered_project, store)
    assert system.core_plan.count("[OLLAMA VERIFIED ARCHITECTURE ANALYSIS") == 1
    assert "verified text" in system.core_plan


def test_same_folder_name_in_different_places_gives_two_systems(tmp_path, store):
    a = tmp_path / "clientA" / "app"
    b = tmp_path / "clientB" / "app"
    write(a, "main.py", "print(1)\n")
    write(b, "main.py", "print(2)\n")
    sa = CodebaseImporter.analyze_directory(a, store)
    sb = CodebaseImporter.analyze_directory(b, store)
    assert sa is not sb
    systems = [s for s in store.systems if s.source == "codebase"]
    assert len(systems) == 2
    assert sa.project_name != sb.project_name
    # re-scanning each keeps them separate
    assert CodebaseImporter.analyze_directory(a, store) is sa
    assert CodebaseImporter.analyze_directory(b, store) is sb
    assert len([s for s in store.systems if s.source == "codebase"]) == 2


def test_symlink_loop_is_not_followed(tmp_path, store):
    root = tmp_path / "loop"
    write(root, "pkg/a.py", "x = 1\n")
    write(root, "other/b.py", "x = 1\n")
    try:
        (root / "pkg" / "again").symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    system = CodebaseImporter.analyze_directory(root, store)
    assert sorted(p.title for p in system.planets) == ["a.py", "b.py"]


def test_large_files_counted_but_not_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "MAX_PARSE_BYTES", 50)
    root = tmp_path / "large"
    write(root, "big.py", "import os\n" * 20)
    files, skipped = ci._walk_project(root)
    assert skipped == 1 and files[0].method == "" and "larger than" in files[0].note


def test_ua_graph_import(tmp_path, store):
    root = tmp_path / "uaproj"
    nodes = [{"id": f"n{i}", "label": f"Node {i}", "layer": "api" if i % 2 else "ui"} for i in range(40)]
    edges = [{"source": "n1", "target": "n2", "type": "calls"}, {"source": "n3", "target": "n4", "type": "calls"}]
    import json

    write(root, ".ua/knowledge-graph.json", json.dumps({"projectName": "UA", "nodes": nodes, "edges": edges}))
    system = CodebaseImporter.analyze_directory(root, store)
    assert {p.title for p in system.planets} == {"Layer: Api", "Layer: Ui"}
    assert all(len(p.satellites) == 20 for p in system.planets)
    assert len(system.links) == 1 and system.links[0].label.startswith("2 edges")
    assert "not re-analysed" in system.core_plan


def test_scan_parent_collects_failures(tmp_path, store, monkeypatch):
    parent = tmp_path / "parent"
    write(parent, "good/main.py", "x = 1\n")
    write(parent, "bad/main.py", "x = 1\n")
    real = CodebaseImporter._build_system

    def boom(folder):
        if folder.name == "bad":
            raise RuntimeError("kaputt")
        return real(folder)

    monkeypatch.setattr(CodebaseImporter, "_build_system", staticmethod(boom))
    results = CodebaseImporter.scan_parent_projects_directory(parent, store)
    assert [s.project_name for s in results] == ["good"]
    assert len(ci.LAST_SCAN_FAILURES) == 1 and "kaputt" in ci.LAST_SCAN_FAILURES[0][1]


def test_no_blueprint_dependency():
    src = Path(ci.__file__).read_text(encoding="utf-8")
    assert "PROJECT_BLUEPRINTS" not in src


# ---------------------------------------------------------------------------
# build_code_map / identity across sources / re-scan rules
# ---------------------------------------------------------------------------
def test_build_code_map_fills_without_saving(tmp_path, store):
    root = write_assistant_like(tmp_path / "NOVA")
    system = SolarSystem(name="x", project_name="x", source="codex")
    before = len(store.systems)
    stats = build_code_map(root, system)
    assert stats.mode == "file" and stats.code_files == 4 and stats.planets == 5 and stats.links == 5  # + External Libraries
    assert "File-level map" in stats.core_plan
    assert len(store.systems) == before and system.source == "codex" and system.core_plan != stats.core_plan
    empty = tmp_path / "empty"
    empty.mkdir()
    assert build_code_map(empty, SolarSystem(name="e")).code_files == 0


def _agent_system(root, source="codex", name=None):
    s = SolarSystem(name=name or root.name, project_name=name or root.name, source=source,
                    source_id=f"{source}:{root}", workspace_path=str(root))
    h = s.add_planet(title=ci.HISTORY_PLANET_TITLE, layer="docs")
    h.kind = "history"
    h.add_satellite(title="2026-11-03 — asistan.py", content="TOOL: OpenAI Codex [codex]\nKIND: session")
    return s


def test_analyze_updates_agent_system_for_same_folder(tmp_path, store):
    root = write_assistant_like(tmp_path / "NOVA").resolve()
    agent = store.add_system(_agent_system(root))
    system = CodebaseImporter.analyze_directory(root, store)
    assert system is agent
    assert system.source == "codex" and system.sources == ["codex", "codebase"]
    assert [s for s in store.systems if s.source != "manual"] == [agent]
    kinds = Counter(p.kind for p in system.planets)
    assert kinds == {"file": 4, "history": 1, "external": 1}
    assert "Work History" in system.core_plan and "history only" in system.core_plan
    again = CodebaseImporter.analyze_directory(root, store)
    assert again is agent and Counter(p.kind for p in again.planets) == kinds and len(again.links) == 5


def test_windows_style_paths_match_case_insensitively():
    assert ci.workspace_key(r"C:\Users\X\Desktop\GuildKeeper\\") == ci.workspace_key(r"c:\users\x\desktop\guildkeeper")
    assert ci.workspace_key("/home/a/Proj") != ci.workspace_key("/home/a/proj")


def test_rescan_keeps_ai_summary_and_user_planet(tmp_path, store):
    root = write_assistant_like(tmp_path / "NOVA")
    system = CodebaseImporter.analyze_directory(root, store)
    planet(system, "islemler.py").ai_summary = "LLM prose about islemler"
    mine = system.add_planet(title="My plan", summary="mine")
    mine.is_user_modified = True
    kul = planet(system, "mikrofon.py")
    kul.is_user_modified, kul.color, kul.summary = True, "#123456", "my mikrofon notes"
    store.save()
    counts = (len(system.planets), sum(len(p.satellites) for p in system.planets), len(system.links))
    again = CodebaseImporter.analyze_directory(root, store)
    assert planet(again, "islemler.py").ai_summary == "LLM prose about islemler"
    assert planet(again, "My plan").summary == "mine"
    assert planet(again, "mikrofon.py").color == "#123456" and planet(again, "mikrofon.py").summary == "my mikrofon notes"
    assert (len(again.planets), sum(len(p.satellites) for p in again.planets), len(again.links)) == counts
    reloaded = GalaxyStore(storage_path=store.storage_path)
    assert planet(next(s for s in reloaded.systems if s.source == "codebase"), "islemler.py").ai_summary == "LLM prose about islemler"


def test_stale_duplicates_merged_and_removed(tmp_path, store):
    root = write_assistant_like(tmp_path / "NOVA").resolve()
    plain = store.add_system(_agent_system(root, "antigravity"))
    edited = _agent_system(root, "claude_code")
    old = edited.add_planet(title="Core & Data Model")
    note = old.add_satellite(title="My idea", content="user text")
    note.is_user_modified = True
    store.add_system(edited)
    locked = _agent_system(root, "codex")
    locked.is_user_modified = True  # e.g. the user edited its core plan
    store.add_system(locked)
    manual = store.add_system(SolarSystem(name="NOVA", project_name="NOVA", source="manual", workspace_path=str(root)))

    system = CodebaseImporter.analyze_directory(root, store)
    assert system is edited  # no codebase system yet: the one with user edits wins
    assert plain not in store.systems  # nothing user-made left -> removed
    assert locked in store.systems  # still user-modified -> kept
    assert manual in store.systems  # manual systems are never touched
    assert set(system.sources) >= {"claude_code", "antigravity", "codex", "codebase"}
    assert any(s.title == "My idea" for p in system.planets for s in p.satellites)
    assert sum(1 for p in system.planets if p.kind == "history") == 1


# ---------------------------------------------------------------------------
# Call relations, classes, entry points, externals (Python)
# ---------------------------------------------------------------------------
def sats(system):
    return {f"{p.title}::{s.title}": s for p in system.planets for s in p.satellites}


def write_assistant_calls(root: Path) -> Path:
    """Assistant-shaped project with real cross-file calls."""
    write(root, "requirements.txt", "ollama>=0.1.6\n")
    write(root, "degerler.py", "MODE = 'voice'\nMODEL = 'qwen'\n")
    write(root, "islemler.py", """\
        import psutil
        import degerler

        def odak_modu(aktif: bool = False) -> str:
            return str(aktif)

        def gorev_yurut(ad, parametre=""):
            if ad == "odak":
                return odak_modu(True)
            return ad
        """)
    write(root, "mikrofon.py", """\
        import numpy as np

        def ses_kaydet():
            return np.zeros(1)

        def dinle_ve_yaz():
            return str(ses_kaydet())
        """)
    write(root, "asistan.py", """\
        import json
        import os
        import ollama
        import degerler
        from islemler import gorev_yurut
        import islemler

        if degerler.MODE == "voice":
            import mikrofon


        def cevapla(m):
            out = ollama.chat(model=degerler.MODEL, messages=[m])
            json.dumps(out)
            return gorev_yurut(m)


        def baslat():
            girdi = mikrofon.dinle_ve_yaz()
            islemler.odak_modu(True)
            print(cevapla(girdi))
            os.getcwd()


        if __name__ == "__main__":
            baslat()
        """)
    return root


def test_assistant_calls_entry_and_external(tmp_path, store):
    root = write_assistant_calls(tmp_path / "NOVA")
    system = CodebaseImporter.analyze_directory(root, store)
    s = sats(system)
    baslat = s["asistan.py::baslat()"]
    assert baslat.calls == ["asistan.py::cevapla()", "islemler.py::odak_modu()", "mikrofon.py::dinle_ve_yaz()"]
    assert baslat.called_by == ["asistan.py::(module code)"]
    assert s["asistan.py::cevapla()"].calls == ["islemler.py::gorev_yurut()"]  # via `from islemler import gorev_yurut`
    assert s["mikrofon.py::dinle_ve_yaz()"].called_by == ["asistan.py::baslat()"]
    assert s["mikrofon.py::dinle_ve_yaz()"].calls == ["mikrofon.py::ses_kaydet()"]
    assert s["islemler.py::odak_modu()"].called_by == ["asistan.py::baslat()", "islemler.py::gorev_yurut()"]
    assert s["asistan.py::(module code)"].kind == "function" and s["asistan.py::(module code)"].calls == ["asistan.py::baslat()"]
    assert "Calls: asistan.py::cevapla(), islemler.py::odak_modu(), mikrofon.py::dinle_ve_yaz()" in baslat.content
    assert "Called by: asistan.py::baslat()" in s["mikrofon.py::dinle_ve_yaz()"].content
    assert "(module code)" not in {x.title for x in planet(system, "islemler.py").satellites}  # no module-level calls there
    # links name what is used
    edges = links_by_title(system)
    assert edges[("asistan.py", "islemler.py")].label == "imports islemler — calls gorev_yurut(), odak_modu()"
    assert edges[("asistan.py", "mikrofon.py")].label == "imports mikrofon — calls dinle_ve_yaz()"
    assert edges[("asistan.py", "degerler.py")].label == "imports degerler"
    # entry point
    assert [p.title for p in system.planets if p.is_entry] == ["asistan.py"]
    assert 'Entry point: has an `if __name__ == "__main__"` block and is not imported by other project files' in planet(system, "asistan.py").summary
    assert "Entry point: asistan.py" in system.core_plan
    # external libraries: third-party only, no links to it
    ext = planet(system, ci.EXTERNAL_PLANET_TITLE)
    assert ext.kind == "external" and ext.layer == "integration"
    assert sorted(x.title for x in ext.satellites) == ["numpy", "ollama", "psutil"]
    assert all(x.kind == "package" for x in ext.satellites)
    ol = next(x for x in ext.satellites if x.title == "ollama")
    assert "Imported by (1): asistan.py" in ol.content and "Declared in: requirements.txt (>=0.1.6)" in ol.content
    assert "Declared: no" in next(x for x in ext.satellites if x.title == "psutil").content
    assert not any(ext.id in (l.source_planet_id, l.target_planet_id) for l in system.links)
    assert "External: ollama" in planet(system, "asistan.py").summary


def test_python_alias_imports_and_packages(tmp_path):
    root = tmp_path / "alias"
    write(root, "helpers.py", "def run():\n    pass\n")
    write(root, "pkg/__init__.py", "from .mod import thing\n")
    write(root, "pkg/mod.py", "def thing():\n    pass\n\ndef other():\n    pass\n")
    write(root, "app.py", """\
        import helpers as h
        import pkg.mod
        import pkg.mod as pm
        from pkg.mod import other as o
        from pkg import thing

        def a():
            h.run()

        def b():
            pkg.mod.thing()

        def c():
            pm.other()
            o()
            thing()
        """)
    system = SolarSystem(name="alias")
    build_code_map(root, system)
    s = sats(system)
    assert s["app.py::a()"].calls == ["helpers.py::run()"]
    assert s["app.py::b()"].calls == ["mod.py::thing()"]
    assert s["app.py::c()"].calls == ["mod.py::other()", "mod.py::thing()"]  # `thing` via the package re-export


def write_classes(root: Path) -> Path:
    write(root, "models.py", '''\
        from dataclasses import dataclass, field
        from enum import Enum


        class Base:
            """Common base."""

            def save(self) -> bool:
                return True


        @dataclass
        class Store(Base):
            """Keeps items."""

            name: str
            items: list = field(default_factory=list)

            def add(self, item: str, count: int = 1) -> None:
                self._check(item)
                self.save()

            def _check(self, item):
                pass

            @staticmethod
            def create(path: str) -> "Store":
                return Store(path)

            @classmethod
            def load(cls, path):
                return cls.create(path)

            @property
            def size(self) -> int:
                return len(self.items)

            def __repr__(self):
                return self.name


        class Color(Enum):
            RED = 1
            BLUE = 2


        class Unrelated:
            def save(self):
                return False
        ''')
    write(root, "service.py", """\
        from models import Store
        import models


        def process(thing):
            st = Store("x")
            st.add("a")
            thing.save()
            other = models.Store.create("p")
            other.add("b")
            again = Store("y")
            again = make()
            again.add("c")


        def make():
            return None
        """)
    return root


def test_self_constructor_and_local_inference(tmp_path):
    root = write_classes(tmp_path / "cls")
    system = SolarSystem(name="cls")
    build_code_map(root, system)
    s = sats(system)
    # Store has 4 public methods (add, create, load, size) -> shown as method sub-components
    for m in ("Store.add()", "Store.create()", "Store.load()", "Store.size()"):
        assert s[f"models.py::{m}"].kind == "method"
    assert "models.py::Store._check()" not in s and "models.py::Store.__repr__()" not in s
    process = s["service.py::process()"]
    assert process.calls == [
        "models.py::Store.add()",  # st = Store(...); st.add()
        "models.py::Store.create()",  # models.Store.create()
        "models.py::class Store",  # constructor
        "service.py::make()",
    ]
    # `thing.save()` (parameter) and `other.add()` (not a constructor) are not guessed by name
    assert "models.py::Base.save()" not in process.calls and "models.py::Unrelated.save()" not in process.calls
    add = s["models.py::Store.add()"]
    assert add.calls == ["models.py::Base.save()", "models.py::class Store"]  # self.save() from the base; self._check() -> class
    assert s["models.py::Store.load()"].calls == ["models.py::Store.create()"]  # cls.create()
    assert s["models.py::Store.create()"].calls == ["models.py::class Store"]
    assert add.signature == "add(self, item: str, count: int = 1) -> None" and add.line == 19


def test_class_satellite_details(tmp_path):
    root = write_classes(tmp_path / "cls")
    system = SolarSystem(name="cls")
    build_code_map(root, system)
    store_sat = sats(system)["models.py::class Store"]
    c = store_sat.content
    assert store_sat.kind == "class" and store_sat.signature == "class Store(Base)" and store_sat.line == 13
    assert "Kind: dataclass" in c and "Decorators: @dataclass" in c and "About: Keeps items." in c
    assert "Inherits from: models.py::class Base" in c
    assert "Fields (2): name: str, items: list = field(default_facto…" in c  # defaults abbreviated
    assert "Methods (6, 4 public):" in c
    assert "• add(self, item: str, count: int = 1) -> None" in c
    assert "• @staticmethod create(path: str) -> 'Store'" in c
    assert "• @classmethod load(cls, path)" in c and "• @property size(self) -> int" in c
    assert "• _check(self, item) [private]" in c and "• __repr__(self) [dunder]" in c
    color = sats(system)["models.py::class Color"].content
    assert "Kind: Enum" in color and "Members (2): RED, BLUE" in color
    assert "Inherits from: Enum (not in this project)" in color


def test_method_satellites_thresholds(tmp_path):
    root = tmp_path / "thr"
    many = "\n".join(f"    def m{i}(self):\n        pass" for i in range(11))
    write(root, "big_class.py", f"class Big:\n{many}\n")  # 11 public methods > 10
    funcs = "\n".join(f"def f{i}():\n    pass\n" for i in range(10))
    write(root, "crowded.py", f"class Small:\n    def a(self):\n        pass\n    def b(self):\n        pass\n    def c(self):\n        pass\n\n{funcs}")
    write(root, "fits.py", "class Small:\n    def a(self):\n        pass\n    def b(self):\n        pass\n")
    system = SolarSystem(name="thr")
    build_code_map(root, system)
    assert [s.title for s in planet(system, "big_class.py").satellites] == ["class Big"]
    assert "Methods are listed inside the class sub-components" in planet(system, "big_class.py").summary
    # 11 definitions + 3 methods = 14 > 12 sub-components -> not expanded
    assert "Small.a()" not in {s.title for s in planet(system, "crowded.py").satellites}
    assert [s.title for s in planet(system, "fits.py").satellites] == ["class Small", "Small.a()", "Small.b()"]
    assert "Methods shown as sub-components: Small (2)" in planet(system, "fits.py").summary


def test_methods_not_satellites_in_directory_maps(layered_project, store):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    assert all(s.kind in ("file", "document") for p in system.planets if p.kind == "dir" for s in p.satellites)


def test_directory_map_aggregates_calls_on_file_satellites(layered_project, store):
    system = CodebaseImporter.analyze_directory(layered_project, store)
    s = sats(system)
    window = s["ui::window.py"]
    assert window.calls == []  # Window has no calls; the import alone is not a call
    assert s["connectors::importer.py"].calls == ["core::util.py"]  # from core.util import helper; helper()
    assert s["core::util.py"].called_by == ["connectors::importer.py"]
    assert "Calls into: core/util.py (helper())" in s["connectors::importer.py"].content
    root_sat = s["Project Root::main.py"]
    assert root_sat.calls == ["ui::window.py"] and "Calls into: ui/window.py (Window())" in root_sat.content
    assert planet(system, "Project Root").is_entry is False  # main.py has no __main__ block


def test_syntax_error_file_survives(tmp_path):
    root = tmp_path / "broken"
    write(root, "good.py", "import bad\n\ndef ok():\n    bad.f()\n")
    write(root, "bad.py", "def f(:\n    pass\nclass K:\n")
    (root / "latin.py").write_bytes(b"# caf\xe9\ndef g():\n    return 1\n")
    system = SolarSystem(name="broken")
    stats = build_code_map(root, system)
    bad = planet(system, "bad.py")
    assert "Python AST parsing failed (SyntaxError)" in bad.summary and "no call analysis" in bad.summary
    assert {"f()", "class K"} <= {s.title for s in bad.satellites}
    assert sats(system)["good.py::ok()"].calls == []  # target unparsed -> nothing resolved
    assert [s.title for s in planet(system, "latin.py").satellites] == ["g()"]
    assert stats.planets == 3


def test_rescan_idempotent_with_new_fields(tmp_path, store):
    root = write_assistant_calls(tmp_path / "NOVA")

    def shape(system):
        return sorted(
            (p.title, p.kind, p.is_entry, s.title, s.kind, s.signature, s.line, tuple(s.calls), tuple(s.called_by), s.content)
            for p in system.planets for s in p.satellites
        ), sorted(l.label for l in system.links)

    system = CodebaseImporter.analyze_directory(root, store)
    planet(system, "islemler.py").ai_summary = "LLM prose"
    first = shape(system)
    again = CodebaseImporter.analyze_directory(root, store)
    assert again is system and shape(again) == first
    assert planet(again, "islemler.py").ai_summary == "LLM prose"
    reloaded = GalaxyStore(storage_path=store.storage_path)
    assert shape(next(s for s in reloaded.systems if s.source == "codebase")) == first


def test_call_analysis_guards(tmp_path, monkeypatch):
    root = write_assistant_calls(tmp_path / "NOVA")
    monkeypatch.setattr(ci, "CALL_ANALYSIS_MAX_FILE_BYTES", 200)
    system = SolarSystem(name="g")
    stats = build_code_map(root, system)
    assert sats(system)["asistan.py::baslat()"].calls == []
    assert "Call analysis skipped for 1 file larger than 0 KB" in stats.core_plan
    assert "Call analysis skipped: file is larger than 0 KB." in planet(system, "asistan.py").summary
    assert sats(system)["mikrofon.py::dinle_ve_yaz()"].calls == ["mikrofon.py::ses_kaydet()"]  # small file still analysed
    monkeypatch.setattr(ci, "CALL_ANALYSIS_MAX_FILE_BYTES", 300_000)
    monkeypatch.setattr(ci, "CALL_ANALYSIS_MAX_CODE_FILES", 3)
    system = SolarSystem(name="g2")
    stats = build_code_map(root, system)
    assert stats.calls == 0 and not any(s.calls for s in sats(system).values())
    assert "Call analysis skipped: the project has more than 3 code files" in stats.core_plan
    assert stats.links == 4  # imports are still resolved


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------
def write_php_site(root: Path) -> Path:
    write(root, "composer.json", '{"require": {"php": ">=8.0", "ext-pdo": "*", "monolog/monolog": "^3.0"}}')
    write(root, "index.php", """\
        <?php
        require_once 'includes/db.php';
        require __DIR__ . '/config/app.php';
        include $_SERVER['DOCUMENT_ROOT'] . '/includes/helpers.php';
        include $page . '.php';
        use App\\Models\\User;
        use Monolog\\Logger;
        ?>
        <html><head>
        <link rel="stylesheet" href="css/site.css">
        <script src="./js/app.js"></script>
        <script src="https://cdn.example.com/x.js"></script>
        </head>
        <body>It's <?php echo render_header(db_connect()); ?></body></html>
        """)
    write(root, "includes/db.php", """\
        <?php
        function db_connect() { return new PDO('sqlite::memory:'); }
        function db_query($sql) { return db_connect()->query($sql); }
        """)
    write(root, "includes/helpers.php", """\
        <?php
        require_once(__DIR__.'/db.php');
        // function fake_in_comment() {}
        $s = "function fake_in_string() {}";
        function render_header($conn) { return "<h1>{$conn}</h1>"; }
        """)
    write(root, "config/app.php", "<?php\ndefine('APP', 1);\n")
    write(root, "book.php", """\
        <?php
        require_once 'includes/db.php';
        function save_booking($x) { return db_query("INSERT INTO b VALUES ($x)"); }
        echo save_booking(1);
        """)
    write(root, "api/rooms.php", "<?php\ninclude dirname(__FILE__) . '/../includes/db.php';\n$rows = db_query('SELECT 1');\n")
    write(root, "src/Models/User.php", """\
        <?php
        namespace App\\Models;

        interface HasName { public function name(); }
        abstract class Model {}
        class User extends Model implements HasName, \\JsonSerializable
        {
            public function name() { return 'x'; }
            private function secret() {}
            public static function find($id) { return null; }
            public function jsonSerialize(): mixed { return []; }
        }
        trait Greets {}
        """)
    write(root, "css/site.css", "body { color: red; }\n")
    write(root, "js/app.js", "function boot() {}\n")
    return root


def test_php_includes_uses_calls_and_entry(tmp_path, store):
    root = write_php_site(tmp_path / "site")
    system = CodebaseImporter.analyze_directory(root, store)
    edges = links_by_title(system)
    assert set(edges) == {
        ("index.php", "db.php"), ("index.php", "app.php"), ("index.php", "helpers.php"), ("index.php", "User.php"),
        ("index.php", "site.css"), ("index.php", "app.js"),
        ("helpers.php", "db.php"), ("book.php", "db.php"), ("rooms.php", "db.php"),
    }
    assert edges[("book.php", "db.php")].label == "includes includes/db.php — calls db_query()"
    assert edges[("index.php", "User.php")].label == "uses App\\Models\\User"
    assert edges[("index.php", "site.css")].label == "references css/site.css"
    assert edges[("index.php", "helpers.php")].label == "includes includes/helpers.php — calls render_header()"
    index = planet(system, "index.php")
    assert index.is_entry and "is `index.php` at the project root" in index.summary
    assert "Unresolved includes: 1" in index.summary  # include $page . '.php'
    s = sats(system)
    assert s["index.php::(module code)"].calls == ["db.php::db_connect()", "helpers.php::render_header()"]
    assert s["book.php::save_booking()"].calls == ["db.php::db_query()"]
    assert s["rooms.php::(module code)"].calls == ["db.php::db_query()"]
    assert s["db.php::db_query()"].called_by == ["book.php::save_booking()", "rooms.php::(module code)"]
    assert s["db.php::db_query()"].calls == []  # same-file PHP calls are not resolved (documented)
    assert [x.title for x in planet(system, "helpers.php").satellites] == ["render_header()"]  # comment/string ignored
    user = planet(system, "User.php")
    assert [x.title for x in user.satellites] == ["interface HasName", "class Model", "class User", "trait Greets"]
    uc = s["User.php::class User"].content
    assert "Extends: Model" in uc and "Implements: HasName, \\JsonSerializable" in uc
    assert "Methods (4, 3 public):" in uc and "private function secret() {}" in uc and "static public static function find" in uc
    ext = planet(system, ci.EXTERNAL_PLANET_TITLE)
    mono = next(x for x in ext.satellites if x.title == "monolog/monolog")
    assert "Declared in: composer.json (^3.0)" in mono.content
    assert {x.title for x in ext.satellites} == {"monolog/monolog", "Monolog\\"}  # no autoload_psr4.php -> namespace kept apart


def test_php_vendor_autoload_maps_namespace_to_package(tmp_path):
    root = write_php_site(tmp_path / "site")
    write(root, "vendor/composer/autoload_psr4.php", "<?php\nreturn array(\n    'Monolog\\\\' => array($vendorDir . '/monolog/monolog/src/Monolog'),\n);\n")
    system = SolarSystem(name="site")
    build_code_map(root, system)
    ext = planet(system, ci.EXTERNAL_PLANET_TITLE)
    mono = next(x for x in ext.satellites if x.title == "monolog/monolog")
    assert [x.title for x in ext.satellites] == ["monolog/monolog"]
    assert "Imported by (1): index.php" in mono.content


def test_php_case_insensitive_include_fallback(tmp_path):
    root = tmp_path / "case"
    write(root, "index.php", "<?php\nrequire_once 'Includes/DB.php';\n")
    write(root, "includes/db.php", "<?php\nfunction x() {}\n")
    system = SolarSystem(name="case")
    stats = build_code_map(root, system)
    assert set(links_by_title(system)) == {("index.php", "db.php")}
    assert "Case-insensitive matches: 1 path" in stats.core_plan


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------
def write_node_project(root: Path) -> Path:
    write(root, "package.json", '{"name": "demo", "main": "src/index.mjs", "bin": {"demo": "scripts/cli.cjs"},'
          ' "dependencies": {"chalk": "^5.0.0", "@scope/pkg": "1.0.0"}}')
    write(root, "src/index.mjs", """\
        import fs from 'node:fs';
        import path from 'path';
        import chalk from 'chalk';
        import { helper } from '@scope/pkg/sub';
        import { formatName, parseArgs as parse } from './util';
        import * as math from './math';
        import Greeter from './greeter.mjs';

        export function main(argv) {
          const a = parse(argv);
          console.log(chalk.green(formatName(a.name)));
          return math.add(1, 2);
        }

        export function unused() {
          // formatName() in a comment is ignored
          return new Greeter('x');
        }
        """)
    write(root, "src/util.mjs", """\
        export function formatName(n) { return n.toUpperCase(); }
        export const parseArgs = (argv) => ({ name: argv[0] });
        function internal() { return formatName('y'); }
        """)
    write(root, "src/math/index.mjs", "export function add(a, b) {\n  return a + b;\n}\n")
    write(root, "src/greeter.mjs", """\
        export default class Greeter extends Base {
          constructor(name) { this.name = name; }
          greet() { if (this.name) { return 'hi'; } }
          static create() { return new Greeter('a'); }
          #secret() {}
        }
        """)
    write(root, "scripts/cli.cjs", "const { main } = require('../src/index.mjs');\nconst _ = require('lodash');\nmain(process.argv);\n")
    write(root, "test/util.test.mjs", "import { formatName } from '../src/util.mjs';\nimport assert from 'node:assert';\nassert.ok(formatName('a'));\n")
    write(root, "examples/demo.mjs", "import { main } from '../src/index.mjs';\nmain([]);\n")
    return root


def test_es_module_project(tmp_path, store):
    root = write_node_project(tmp_path / "node")
    system = CodebaseImporter.analyze_directory(root, store)
    edges = links_by_title(system)
    assert set(edges) == {
        ("src/index.mjs", "util.mjs"), ("src/index.mjs", "src/math/index.mjs"), ("src/index.mjs", "greeter.mjs"),
        ("cli.cjs", "src/index.mjs"), ("util.test.mjs", "util.mjs"), ("demo.mjs", "src/index.mjs"),
    }
    assert edges[("src/index.mjs", "util.mjs")].label == "imports ./util — calls formatName(), parseArgs()"
    assert edges[("src/index.mjs", "src/math/index.mjs")].label == "imports ./math — calls add()"
    s = sats(system)
    assert s["src/index.mjs::main()"].calls == ["src/math/index.mjs::add()", "util.mjs::formatName()", "util.mjs::parseArgs()"]
    assert s["src/index.mjs::unused()"].calls == ["greeter.mjs::class Greeter"]  # default import + new
    assert s["src/index.mjs::main()"].called_by == ["cli.cjs::(module code)", "demo.mjs::(module code)"]
    assert s["util.mjs::formatName()"].called_by == ["src/index.mjs::main()", "util.test.mjs::(module code)"]
    assert "internal()" in {x.title for x in planet(system, "util.mjs").satellites}
    assert s["util.mjs::internal()"].calls == []  # same-file JS calls are not resolved (documented)
    assert "Exported: yes" in s["util.mjs::formatName()"].content and "Exported: no" in s["util.mjs::internal()"].content
    g = s["greeter.mjs::class Greeter"].content
    assert "Extends: Base" in g and "Exported: yes (default export)" in g
    assert "Methods (4, 3 public):" in g and "static create()" in g and "#secret() {} [private]" in g
    # entry points from package.json
    entries = {p.title for p in system.planets if p.is_entry}
    assert entries == {"src/index.mjs", "cli.cjs"}
    assert "is the `main` of package.json" in planet(system, "src/index.mjs").summary
    assert "is the `bin` `demo` of package.json" in planet(system, "cli.cjs").summary
    # externals: bare specifiers -> package names, built-ins excluded
    ext = planet(system, ci.EXTERNAL_PLANET_TITLE)
    titles = [x.title for x in ext.satellites]
    assert sorted(titles) == ["@scope/pkg", "chalk", "lodash"]
    content = {x.title: x.content for x in ext.satellites}
    assert "Declared in: package.json (1.0.0)" in content["@scope/pkg"] and "Declared: no" in content["lodash"]
    assert "Built-in modules: fs, path" in planet(system, "src/index.mjs").summary


def test_tsconfig_paths_vite_alias_and_html(tmp_path):
    root = tmp_path / "web"
    write(root, "frontend/package.json", '{"dependencies": {"react": "18.2.0"}}')
    write(root, "frontend/tsconfig.json", """\
        {
          // comments and trailing commas are allowed
          "compilerOptions": {
            "baseUrl": ".",
            "paths": {"~lib/*": ["src/lib/*"], "@missing/*": ["src/nope/*"]},
          },
        }
        """)
    write(root, "frontend/index.html", '<html><script type="module" src="/src/main.tsx"></script><link href="./missing.css"></html>\n')
    write(root, "frontend/src/main.tsx", """\
        import React from 'react';
        import App from '@/App';
        import { util } from '~lib/util';
        import ghost from '@missing/ghost';
        import gone from '@/Gone';
        util();
        """)
    write(root, "frontend/src/App.tsx", "export default function App() { return null }\n")
    write(root, "frontend/src/lib/util.ts", "export function util() {}\n")
    system = SolarSystem(name="web")
    stats = build_code_map(root, system)
    edges = links_by_title(system)
    assert set(edges) == {("main.tsx", "App.tsx"), ("main.tsx", "util.ts"), ("index.html", "main.tsx")}
    assert edges[("main.tsx", "App.tsx")].label == "imports @/App (via Vite `@/` alias)"
    assert edges[("main.tsx", "util.ts")].label.startswith("imports ~lib/util (via `frontend/tsconfig.json` paths) — calls util()")
    assert edges[("index.html", "main.tsx")].label == "references /src/main.tsx"
    assert sats(system)["main.tsx::(module code)"].calls == ["util.ts::util()"]
    assert [x.title for x in planet(system, ci.EXTERNAL_PLANET_TITLE).satellites] == ["react"]  # not @missing/ghost
    assert "Path aliases: 2 imports resolved" in stats.core_plan
    assert planet(system, "index.html").is_entry


def test_js_extensions_are_code_and_resolve(tmp_path):
    root = tmp_path / "exts"
    for ext in (".mjs", ".cjs", ".mts", ".cts", ".jsx", ".tsx"):
        write(root, f"lib/m{ext[1:]}{ext}", "export function f() {}\n")
    write(root, "lib/comp.vue", "<template><div/></template>\n<script>\nimport { f } from './mmjs';\nexport default {}\n</script>\n")
    write(root, "lib/s.svelte", "<script>\nimport x from './mcts';\n</script>\n<p>import y from './mtsx'</p>\n")
    write(root, "lib/ts.ts", "import a from './mmts.mjs';\nimport b from './mjsx.js';\n")
    system = SolarSystem(name="exts")
    build_code_map(root, system)
    edges = set(links_by_title(system))
    assert {("comp.vue", "mmjs.mjs"), ("s.svelte", "mcts.cts"), ("ts.ts", "mmts.mts"), ("ts.ts", "mjsx.jsx")} == edges


# ---------------------------------------------------------------------------
# Purpose evidence / purpose / core plan "Purpose:" section
# ---------------------------------------------------------------------------
ASSISTANT_SETTINGS = '''\
import json

# ==========================================
# NOVA SHARED CONFIGURATION FILE
# Speech, microphone and model options are all kept here.
# ==========================================

ASSISTANT_NAME = "NOVA"
MODEL_NAME = "qwen2.5:3b"
WHISPER_MODEL = "small"
SYSTEM_MESSAGE = (
    f"Hello, I am {ASSISTANT_NAME}. I am a calm AI helper that works offline on this desktop. "
    "Keep every reply short and plain. "
    "ACTIONS AVAILABLE: [ACTION: open_browser] [ACTION: show_time] [ACTION: focus_mode(on)] "
    + "Never remove any file. " * 30
)
'''


def write_assistant_purpose(root: Path) -> Path:
    write(root, "degerler.py", ASSISTANT_SETTINGS)
    write(root, "islemler.py", "import psutil\nimport keyboard\nimport degerler\n\ndef tarayici_ac():\n    pass\n\ndef saati_goster():\n    pass\n\ndef odak_modu():\n    pass\n")
    write(root, "mikrofon.py", "import sounddevice\nfrom faster_whisper import WhisperModel\nimport degerler\n\ndef ses_kaydet():\n    pass\n\ndef metne_cevir():\n    pass\n")
    write(root, "asistan.py", "import ollama\nimport degerler, islemler, mikrofon\n\ndef main():\n    pass\n\nif __name__ == '__main__':\n    main()\n")
    return root


def test_purpose_evidence_from_code_without_readme(tmp_path, store):
    root = write_assistant_purpose(tmp_path / "NOVA")
    system = CodebaseImporter.analyze_directory(root, store)
    ev = system.purpose_evidence
    assert "Header comment (degerler.py): NOVA SHARED CONFIGURATION FILE Speech, microphone and model options" in ev
    assert "====" not in ev
    line = next(ln for ln in ev.splitlines() if "SYSTEM_MESSAGE" in ln)
    assert line.startswith('Text in code (degerler.py, SYSTEM_MESSAGE): "Hello, I am NOVA. I am a calm AI helper')
    assert line.endswith('…"') and len(line) < ci.PURPOSE_STRING_CAP + 60
    caps = next(ln for ln in ev.splitlines() if ln.startswith("Capabilities implied by libraries:"))
    assert "speech-to-text (faster_whisper)" in caps and "LLM / AI model (ollama)" in caps
    assert "microphone/audio input (sounddevice)" in caps and "keyboard/mouse automation (keyboard)" in caps
    assert "system/process monitoring (psutil)" in caps
    names = next(ln for ln in ev.splitlines() if ln.startswith("Names in code:"))
    for n in ("tarayici_ac()", "saati_goster()", "odak_modu()", "ses_kaydet()", "metne_cevir()"):
        assert n in names
    assert "main()" not in names  # generic names are left out
    assert "Entry point: asistan.py" in ev
    assert len(ev) <= ci.PURPOSE_EVIDENCE_BUDGET
    assert system.purpose == "" and system.purpose_source == ""
    first = system.core_plan.splitlines()[0]
    assert first.startswith("Purpose: Not written yet — based on the code this project involves: speech-to-text (faster_whisper)")
    assert "LLM / AI model (ollama)" in first
    assert system.core_plan.splitlines()[2].startswith("Entry point: asistan.py")


def test_no_signals_line(tmp_path):
    root = tmp_path / "plain"
    write(root, "a.py", "def f():\n    pass\n")
    system = SolarSystem(name="plain")
    stats = build_code_map(root, system)
    assert stats.core_plan.splitlines()[0] == "Purpose: Not written yet — based on the code this project involves: no recognisable signals"


README_WITH_USAGE = """\
# Shopper

[![build](https://img.shields.io/badge/build-passing-green.svg)](https://ci)

Shopper is a small web shop for a local bakery. Customers order cakes online and pick them up in the store.

## Installation

Run `npm install` and then `npm start`.

## Usage

Open http://localhost:3000 in your browser and log in as admin.
"""


def test_readme_intro_under_h1_becomes_purpose(tmp_path, store):
    root = tmp_path / "shopper"
    write(root, "README.md", README_WITH_USAGE)
    write(root, "server.js", "const express = require('express');\n")
    system = CodebaseImporter.analyze_directory(root, store)
    assert system.purpose == "Shopper is a small web shop for a local bakery. Customers order cakes online and pick them up in the store."
    assert system.purpose_source == "readme"
    for text in (system.purpose, system.purpose_evidence, system.core_plan):
        assert "npm install" not in text and "localhost" not in text and "log in as admin" not in text
        assert "img.shields" not in text
    assert "README intro (README.md): Shopper is a small web shop" in system.purpose_evidence
    assert system.core_plan.startswith("Purpose: Shopper is a small web shop for a local bakery.")
    assert "(from the README intro in README.md)" in system.core_plan.splitlines()[0]
    assert "README sections" not in system.core_plan


def test_readme_usage_first_then_hakkinda(tmp_path):
    root = tmp_path / "GuildKeeper"
    write(root, "README.md", """\
        # GuildKeeper

        GuildKeeper.cmd dosyasına çift tıklayın, sonra açılan pencerede bekleyin.

        Önce `pip install -r requirements.txt` komutunu çalıştırın.

        ## Kurulum

        Python 3.11 kurun.

        ## Hakkında

        GuildKeeper, Discord sunucularında moderasyon yapan ve üyelere seviye veren bir bottur.
        """)
    write(root, "bot.py", "import discord\n")
    system = SolarSystem(name="GuildKeeper", project_name="GuildKeeper")
    stats = build_code_map(root, system)
    assert system.purpose == "GuildKeeper, Discord sunucularında moderasyon yapan ve üyelere seviye veren bir bottur."
    assert "çift tıklayın" not in system.purpose_evidence and "pip install" not in system.purpose_evidence
    assert "çift tıklayın" not in stats.core_plan


def test_readme_double_click_english_is_skipped():
    intro, _title, tool = ci.readme_intro("# Tool\n\nDouble-click Tool.cmd to start.\n\n## About\n\nTool renames photos by date.\n")
    assert tool == "" and intro == "Tool renames photos by date."


def test_create_next_app_readme_is_discarded(tmp_path, store):
    root = tmp_path / "site"
    write(root, "README.md", """\
        This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://github.com/vercel/next.js/tree/canary/packages/create-next-app).

        ## Getting Started

        First, run the development server:

        ```bash
        npm run dev
        ```
        """)
    write(root, "package.json", '{"name": "site", "dependencies": {"next": "14", "react": "18"}}')
    write(root, "app/page.tsx", "export default function Page() { return null }\n")
    system = CodebaseImporter.analyze_directory(root, store)
    assert system.purpose == "" and system.purpose_source == ""
    assert "README (README.md) is the unmodified create-next-app template (ignored)" in system.purpose_evidence
    assert "README (README.md) is the unmodified create-next-app template (ignored)" in system.core_plan
    assert "bootstrapped" not in system.purpose_evidence and "development server" not in system.core_plan
    assert "web frontend (react, next)" in system.purpose_evidence


def test_package_description_wins_over_readme(tmp_path, store):
    root = tmp_path / "pkg"
    write(root, "package.json", '{"name": "pkg", "description": "Turns Markdown notes into a static blog."}')
    write(root, "README.md", "# pkg\n\nA long readme introduction that says something else entirely here.\n")
    write(root, "index.js", "module.exports = 1;\n")
    system = CodebaseImporter.analyze_directory(root, store)
    assert system.purpose == "Turns Markdown notes into a static blog." and system.purpose_source == "readme"
    ev = system.purpose_evidence.splitlines()
    assert ev[0] == "Own description (package.json): Turns Markdown notes into a static blog."
    assert ev[1].startswith("README intro (README.md):")
    assert "(from the project's own description in package.json)" in system.core_plan.splitlines()[0]


def test_pyproject_and_composer_descriptions(tmp_path):
    root = tmp_path / "py"
    write(root, "pyproject.toml", '[project]\nname = "py"\ndescription = "Backs up photos to a NAS."\n')
    write(root, "a.py", "x = 1\n")
    system = SolarSystem(name="py")
    build_code_map(root, system)
    assert system.purpose == "Backs up photos to a NAS."
    root2 = tmp_path / "php"
    write(root2, "composer.json", '{"description": "Booking system for a small hotel."}')
    write(root2, "index.php", "<?php echo 1;\n")
    system2 = SolarSystem(name="php")
    build_code_map(root2, system2)
    assert "Own description (composer.json): Booking system for a small hotel." in system2.purpose_evidence


def test_secrets_never_appear_in_evidence(tmp_path):
    root = tmp_path / "sec"
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PyFR"
    write(root, ".env", "OPENAI_API_KEY=sk-envsecretvalue1234567890\n")
    write(root, "config.py", f'''\
API_KEY = "this is a very secret helper message that must never be quoted anywhere at all"
WELCOME_MESSAGE = "Welcome to the tool. It converts your invoices into tidy spreadsheets for you."
HELP_TEXT = "Connect with postgres://admin:hunter2@db.example.com/prod to load everything quickly."
TITLE_TOKEN = "{jwt}"
ABOUT_TEXT = "Upload key a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4 when the tool asks for it, then continue."
SYSTEM_PROMPT = "You are a helper. Use token sk-abcdefghijklmnop1234 to call the service when needed."
''')
    write(root, "main.js", 'const password = "correct horse battery staple is my long password string";\n'
                           'const welcomeMessage = "Hello and welcome to the invoice converter, enjoy your stay!";\n')
    system = SolarSystem(name="sec")
    build_code_map(root, system)
    ev = system.purpose_evidence
    assert "Welcome to the tool. It converts your invoices" in ev
    assert "Hello and welcome to the invoice converter" in ev
    for bad in ("very secret", "sk-envsecret", "hunter2", "eyJhbGci", "a1b2c3d4e5f6", "sk-abcdef", "horse battery", ".env"):
        assert bad not in ev, bad


def test_looks_secret_helper():
    assert ci._looks_secret("token eyJabc12345.eyJdef67890.sig_abcdef")
    assert ci._looks_secret("https://user:pass@example.com/x")
    assert ci._looks_secret("ghp_ABCDEFGHIJKLMNOPQRST1234")
    assert ci._looks_secret("Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0")
    assert not ci._looks_secret("A local voice assistant that listens through the microphone.")
    assert not ci._looks_secret("connectors/codebase_importer.py and node_modules/react")


def test_purpose_source_rules_on_rescan(tmp_path, store):
    root = tmp_path / "rules"
    write(root, "README.md", "# rules\n\nRules is a tiny habit tracker for the terminal.\n")
    write(root, "main.py", "print(1)\n")
    system = CodebaseImporter.analyze_directory(root, store)
    assert system.purpose_source == "readme"
    # README changes -> readme purpose follows
    write(root, "README.md", "# rules\n\nRules tracks daily habits and shows streaks.\n")
    system = CodebaseImporter.analyze_directory(root, store)
    assert system.purpose == "Rules tracks daily habits and shows streaks."
    # user purpose survives untouched
    system.purpose, system.purpose_source = "My own words.", "user"
    ci.update_core_plan_purpose(system)
    system = CodebaseImporter.analyze_directory(root, store)
    assert (system.purpose, system.purpose_source) == ("My own words.", "user")
    assert system.core_plan.splitlines()[0] == "Purpose: My own words. (entered by the user)"
    # ai purpose survives when the project has no own words
    (root / "README.md").unlink()
    system.purpose, system.purpose_source = "A habit tracker.", "ai"
    system = CodebaseImporter.analyze_directory(root, store)
    assert (system.purpose, system.purpose_source) == ("A habit tracker.", "ai")
    assert system.core_plan.startswith("Purpose: A habit tracker. (suggested by the local AI model")
    assert len(ci.find_systems_for_folder(store, str(root))) == 1


def test_apply_purpose_text_never_overwrites_user_and_refuses_ai(tmp_path):
    # Changed: AI text is never a purpose any more (it is a suggestion, see
    # connectors.purpose_suggestion), so apply_purpose_text refuses source "ai".
    system = SolarSystem(name="x", core_plan="Purpose: Not written yet — based on the code this project involves: x\n\nMap: y")
    assert not ci.apply_purpose_text(system, "A drawing app.", "ai")
    assert system.purpose == "" and system.core_plan.startswith("Purpose: Not written yet")
    assert ci.apply_purpose_text(system, "A drawing app.", "user")
    assert system.purpose_source == "user" and system.core_plan.startswith("Purpose: A drawing app. (entered by the user)")
    assert system.core_plan.endswith("\n\nMap: y")
    assert not ci.apply_purpose_text(system, "Other.", "readme")
    assert system.purpose == "A drawing app."


def test_evidence_is_deterministic(tmp_path, store):
    root = write_assistant_purpose(tmp_path / "det")
    write(root, "README.md", "# det\n\nDet is a voice assistant for the desktop.\n")
    a = CodebaseImporter.analyze_directory(root, store).purpose_evidence
    b = CodebaseImporter.analyze_directory(root, store).purpose_evidence
    s2 = SolarSystem(name="det", project_name="det")
    build_code_map(root, s2)
    assert a == b == s2.purpose_evidence


def test_php_site_titles_and_database(tmp_path):
    root = tmp_path / "otel"
    write(root, "index.php", """\
        <?php require 'inc/db.php'; ?>
        <html><head><title>Sunset Hotel – Book a room</title>
        <meta name="description" content="Online room booking for the Sunset Hotel in Bodrum.">
        </head><body><h1>Welcome to <?= $name ?> Sunset Hotel</h1></body></html>
        """)
    write(root, "rooms.php", "<?php echo 'rooms';\n")
    write(root, "contact.php", "<?php echo 'contact';\n")
    write(root, "config.php", "<?php $x = 1;\n")
    write(root, "inc/db.php", "<?php function db() { return new PDO('mysql:host=x'); }\n")
    system = SolarSystem(name="otel")
    build_code_map(root, system)
    ev = system.purpose_evidence
    assert 'page title "Sunset Hotel – Book a room" (index.php)' in ev
    assert 'meta description "Online room booking for the Sunset Hotel in Bodrum." (index.php)' in ev
    assert 'heading "Welcome to Sunset Hotel" (index.php)' in ev
    assert "PHP website: index.php plus 2 other pages at the root (contact, rooms)" in ev
    assert "database (PDO in PHP)" in ev


def test_backup_directories_are_skipped(tmp_path):
    root = tmp_path / "site"
    write(root, "app.py", "import util\n")
    write(root, "util.py", "def f():\n    pass\n")
    for d in ("backup", "Backups", "site-backup", "old_backup", "v1.bak"):
        write(root, f"{d}/app.py", "import util\n")
        write(root, f"{d}/package.json", '{"dependencies": {"express": "4"}}')
    write(root, "backupper/tool.py", "x = 1\n")  # not a backup name
    system = SolarSystem(name="site")
    stats = build_code_map(root, system)
    assert stats.code_files == 3
    assert sorted(stats.backup_dirs) == ["Backups", "backup", "old_backup", "site-backup", "v1.bak"]
    assert "Skipped 5 backup folders" in stats.core_plan
    assert "express" not in system.purpose_evidence
    assert ci.count_code_files(root) == 3


def test_ignored_folders_setting(tmp_path, store):
    parent = tmp_path / "projects"
    write(parent, "keep/main.py", "print(1)\n")
    write(parent, "skip/main.py", "print(2)\n")
    write(parent, "skip/sub/x.py", "print(3)\n")
    store.set_setting("ignored_folders", [str(parent / "skip")])
    assert ci.is_ignored_folder(store, parent / "skip")
    assert ci.is_ignored_folder(store, str(parent / "skip" / "sub"))
    assert not ci.is_ignored_folder(store, parent / "skipper")
    assert not ci.is_ignored_folder(store, parent / "keep")
    results = CodebaseImporter.scan_parent_projects_directory(parent, store)
    assert [s.project_name for s in results] == ["keep"]
    st = ci.LAST_SCAN_STATS["codebase"]
    assert st["skipped_ignored"] == 1 and st["skipped_ignored_paths"] == [str(parent / "skip")]
    assert CodebaseImporter.analyze_directory(parent / "skip" / "sub", store) is None
    assert ci.LAST_SCAN_STATS["codebase"]["skipped_ignored"] == 2


def test_ignored_folder_windows_paths_case_insensitive():
    keys = [ci.workspace_key(r"C:\Users\x\Documents\Codex")]
    assert ci.is_ignored_folder(None, r"c:\users\X\documents\codex\2026-09-22\tablo", keys)
    assert not ci.is_ignored_folder(None, r"C:\Users\x\Documents\CodexOther", keys)


def test_has_user_made_content_ignores_importer_only_kinds():
    s = SolarSystem(name="s", source="codex")
    ext = s.add_planet(title=ci.EXTERNAL_PLANET_TITLE)
    ext.kind = "external"
    ext.is_user_modified = True
    pkg = ext.add_satellite(title="ollama")
    pkg.kind, pkg.is_user_modified = "package", True
    assert ci.has_user_content(s) and not ci.has_user_made_content(s)
    pkg2 = ext.add_satellite(title="my note")
    pkg2.is_user_modified = True  # added by the user (no kind)
    assert ci.has_user_made_content(s)
    s2 = SolarSystem(name="s2", source="codex")
    s2.purpose_source = "user"
    assert ci.has_user_made_content(s2)
    assert ci.has_user_made_content(SolarSystem(name="m", source="manual"))
