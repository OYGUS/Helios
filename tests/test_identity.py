"""Tests for the identity line (`SolarSystem.identity`, `codebase_importer.identity_line`)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from connectors import codebase_importer as ci
from connectors.agent_importer import AgentHistoryImporter
from connectors.codebase_importer import (
    IDENTITY_MAX_CHARS,
    CodebaseImporter,
    identity_kind,
    identity_languages,
    identity_line,
    stored_identity,
)
from connectors.live_bridge import main as bridge_main
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


# ---------------------------------------------------------------------------
# The pure function
# ---------------------------------------------------------------------------
def test_full_line():
    line = identity_line(
        languages=[("Python", 20)],
        libraries=["PySide6", "pytest"],
        entry="main.py",
        signals=["pyside6"],
    )
    assert line == "Python desktop app · PySide6 · entry: main.py"


def test_no_kind_signal_gives_only_languages():
    line = identity_line(languages=[("Python", 4)], libraries=["ollama", "faster_whisper", "sounddevice"], entry="asistan.py")
    assert line == "Python · ollama, faster_whisper, sounddevice · entry: asistan.py"


def test_partial_lines():
    assert identity_line(languages=[("Python", 3)]) == "Python"
    assert identity_line(languages=[("Python", 3)], entry="app.py") == "Python · entry: app.py"
    assert identity_line(languages=[("JavaScript", 3)], libraries=["express"]) == "JavaScript web server · express"
    assert identity_line(libraries=["requests"]) == "requests"
    assert identity_line() == ""
    # Kind without a known language: the kind alone, capitalised.
    assert identity_line(signals=[ci.MARKER_GODOT]) == "Game"


def test_no_code_fallback():
    assert identity_line(code_files=0) == "No code files"
    assert identity_line(code_files=0, file_count=3) == "3 files, no code"
    assert identity_line(code_files=0, file_count=1) == "1 file, no code"


@pytest.mark.parametrize(
    "signals, kind",
    [
        (["PySide6"], "desktop app"), (["PyQt5"], "desktop app"), (["PyQt6"], "desktop app"), (["tkinter"], "desktop app"),
        (["wx"], "desktop app"), (["kivy"], "desktop app"), (["customtkinter"], "desktop app"),
        (["electron", "react-dom"], "desktop app"),  # Electron wins over the web frontend it renders
        (["@tauri-apps/api"], "desktop app"),
        (["express"], "web server"), (["fastify"], "web server"), (["koa"], "web server"), (["flask"], "web server"),
        (["fastapi"], "web server"), ([ci.MARKER_AIOHTTP_WEB], "web server"), (["@nestjs/core"], "web server"),
        (["django"], "web app"), (["next"], "web app"), (["react-dom"], "web app"), (["vue"], "web app"),
        (["svelte"], "web app"), (["@sveltejs/kit"], "web app"), (["@angular/core"], "web app"), (["vite"], "web app"),
        (["react-dom", "express"], "web app"),  # frontend + its server = one web app
        ([ci.MARKER_ROOT_INDEX_PHP], "website"), ([ci.MARKER_ROOT_INDEX_PHP, "jquery", "vue"], "website"),
        ([ci.MARKER_CLI], "command-line tool"),
        (["discord"], "bot"), (["discord.py"], "bot"), (["discord.js"], "bot"), (["aiogram"], "bot"),
        (["telegram"], "bot"), (["python-telegram-bot"], "bot"), (["telegraf"], "bot"), (["whatsapp-web.js"], "bot"),
        (["pygame"], "game"), (["phaser"], "game"), ([ci.MARKER_UNITY], "game"), ([ci.MARKER_GODOT], "game"),
        (["react-native"], "mobile app"),
        (["PySide6", ci.MARKER_CLI], "desktop app"),  # a CLI helper never beats a real kind
    ],
)
def test_kind_table(signals, kind):
    assert identity_kind(signals)[0] == kind


@pytest.mark.parametrize(
    "signals",
    [
        [],
        ["requests", "numpy", "aiohttp"],  # aiohttp alone may be a client: no kind
        ["react"],  # react alone could be react-native or a library
        ["PySide6", "discord"],  # desktop + bot: ambiguous
        ["flask", "discord"],  # bot + web server: ambiguous
        ["argparse"],  # argparse without a __main__ entry is not a CLI signal
    ],
)
def test_ambiguous_or_missing_signals_give_no_kind(signals):
    assert identity_kind(signals) == ("", [])
    line = identity_line(languages=[("Python", 3)], signals=signals)
    assert line == "Python"


def test_dev_tools_and_stdlib_are_not_listed():
    libs = ["pytest", "eslint", "eslint-config-next", "typescript", "@types/node", "prettier", "@typescript-eslint/parser",
            "jest", "vite", "@testing-library/react", "os", "zustand"]
    assert identity_line(languages=[("TypeScript", 9)], libraries=libs) == "TypeScript web app · zustand"
    # tkinter gives the kind but is standard library, so it is not listed as a library.
    assert identity_line(languages=[("Python", 2)], libraries=["requests"], signals=["tkinter"]) == "Python desktop app · requests"


def test_kind_library_comes_first_and_declared_name_used_when_not_imported():
    line = identity_line(languages=[("JavaScript", 5)], libraries=["cors", "dotenv", "express", "mysql2"])
    assert line.startswith("JavaScript web server · express, cors, dotenv")
    line = identity_line(languages=[("JavaScript", 5)], libraries=["react", "lodash"], signals=["electron", "react-dom"])
    assert line == "JavaScript desktop app · electron, react, lodash"


def test_languages_normalised_markup_ignored_and_second_language_threshold():
    assert identity_languages([("TypeScript (React)", 65), ("TypeScript", 56), ("JavaScript (ESM)", 3), ("CSS", 1)]) == ["TypeScript"]
    assert identity_languages([("PHP", 69), ("CSS", 2), ("JavaScript", 1), ("SQL", 1)]) == ["PHP"]
    assert identity_languages([("Python", 40), ("HTML", 6)]) == ["Python"]
    assert identity_languages([("HTML", 3), ("CSS", 2)]) == ["HTML", "CSS"]  # markup only when it is all there is
    assert identity_languages([("Python", 10), ("JavaScript", 4)]) == ["Python", "JavaScript"]
    assert identity_languages([("Python", 30), ("JavaScript", 2)]) == ["Python"]
    assert identity_line(languages=[("Python", 10), ("JavaScript", 4)]) == "Python + JavaScript"


def test_length_cap_drops_libraries_first_then_entry():
    libs = ["a-very-long-library-name-one", "a-very-long-library-name-two", "a-very-long-library-name-three"]
    line = identity_line(languages=[("Python", 3)], libraries=libs, entry="src/some/deep/folder/entry_point.py",
                         signals=["flask"])
    assert line == "Python web server · flask · entry: src/some/deep/folder/entry_point.py"
    line = identity_line(languages=[("Python", 3)], libraries=libs[:2], entry="main.py")
    assert line == "Python · a-very-long-library-name-one, a-very-long-library-name-two · entry: main.py"
    line = identity_line(languages=[("Python", 3)], libraries=libs, entry="main.py")
    assert len(line) <= IDENTITY_MAX_CHARS and line.endswith("entry: main.py") and "three" not in line
    huge_entry = "x/" * 60 + "main.py"
    line = identity_line(languages=[("Python", 3)], libraries=["flask"], entry=huge_entry)
    assert line == "Python web server · flask"  # an entry that can never fit is dropped after the libraries
    assert len(line) <= IDENTITY_MAX_CHARS


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------
def make_desktop(root: Path) -> Path:
    write(root, "main.py", "from PySide6 import QtWidgets\nimport ui\nif __name__ == '__main__':\n    ui.run()\n")
    write(root, "ui.py", "from PySide6.QtWidgets import QWidget\nimport requests\n\ndef run():\n    pass\n")
    write(root, "tools/cli.py", "import argparse\nif __name__ == '__main__':\n    argparse.ArgumentParser()\n")
    write(root, "tests/test_ui.py", "import pytest\nimport ui\n")
    write(root, "requirements.txt", "PySide6>=6.5\nrequests\npytest\n")
    return root


def test_analyze_directory_writes_identity(store, tmp_path):
    root = make_desktop(tmp_path / "app")
    system = CodebaseImporter.analyze_directory(root, store)
    assert system.identity == "Python desktop app · PySide6, requests · entry: main.py"
    assert stored_identity(system) == system.identity
    saved = json.loads(store.storage_path.read_text(encoding="utf-8"))
    assert any(s.get("identity") == system.identity for s in saved["systems"])


def test_rescan_replaces_old_value(store, tmp_path):
    root = make_desktop(tmp_path / "app")
    system = CodebaseImporter.analyze_directory(root, store)
    system.identity = "stale value from an older scan"
    (root / "main.py").write_text("import flask\nif __name__ == '__main__':\n    pass\n", encoding="utf-8")
    (root / "ui.py").write_text("import requests\n", encoding="utf-8")
    (root / "requirements.txt").write_text("flask\nrequests\n", encoding="utf-8")
    again = CodebaseImporter.analyze_directory(root, store)
    assert again is system
    assert system.identity == "Python web server · flask, requests · entry: main.py"


def test_scan_kinds_from_markers(store, tmp_path):
    php = tmp_path / "site"
    write(php, "index.php", "<?php include 'header.php'; echo 'hi';\n")
    write(php, "header.php", "<?php echo 'h';\n")
    assert CodebaseImporter.analyze_directory(php, store).identity == "PHP website · entry: index.php"

    cli = tmp_path / "tool"
    write(cli, "tool.py", "import argparse\n\ndef main():\n    argparse.ArgumentParser()\n\nif __name__ == '__main__':\n    main()\n")
    assert CodebaseImporter.analyze_directory(cli, store).identity == "Python command-line tool · entry: tool.py"

    web = tmp_path / "aioweb"
    write(web, "server.py", "from aiohttp import web\nif __name__ == '__main__':\n    web.run_app(web.Application())\n")
    assert CodebaseImporter.analyze_directory(web, store).identity == "Python web server · aiohttp · entry: server.py"

    node = tmp_path / "next-shop"
    write(node, "package.json", json.dumps({"dependencies": {"next": "14", "react": "18", "react-dom": "18"},
                                            "devDependencies": {"typescript": "5", "eslint": "8", "@types/react": "18"}}))
    write(node, "app/page.tsx", "import Link from 'next/link';\nimport React from 'react';\nexport default function P() { return null; }\n")
    write(node, "app/layout.tsx", "import React from 'react';\nexport default function L() { return null; }\n")
    assert CodebaseImporter.analyze_directory(node, store).identity == "TypeScript web app · next, react"

    godot = tmp_path / "godot-game"
    write(godot, "project.godot", "config_version=5\n")
    write(godot, "tools/export.py", "print('x')\n")
    assert CodebaseImporter.analyze_directory(godot, store).identity.startswith("Python game")


def test_no_code_scan(tmp_path):
    root = tmp_path / "notes"
    write(root, "README.md", "# Notes\n")
    write(root, "todo.md", "# Todo\n")
    system = SolarSystem(name="notes", source="codebase")
    stats = ci.build_code_map(root, system)
    assert stats.identity == system.identity == "2 files, no code"


def test_agent_importer_path_sets_identity_and_keeps_it_when_folder_is_gone(tmp_path, store, _fake_home):
    root = make_desktop(tmp_path / "Desktop" / "Studio")
    session = [{"type": "user", "cwd": str(root), "isSidechain": False, "message": {"role": "user", "content": "Build the studio window"}},
               {"type": "assistant", "cwd": str(root), "isSidechain": False,
                "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t0", "name": "Edit", "input": {"file_path": str(root / "ui.py")}}]}}]
    path = _fake_home / ".claude" / "projects" / "studio" / "s1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(e) for e in session) + "\n", encoding="utf-8")
    AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
    (proj,) = [s for s in store.systems if s.source == "claude_code"]
    assert proj.identity == "Python desktop app · PySide6, requests · entry: main.py"
    # Re-scan after the code changed: replaced. PySide6 is still used by ui.py and flask
    # now by main.py: desktop app vs web server is ambiguous, so no kind word.
    (root / "main.py").write_text("import flask\nimport ui\nif __name__ == '__main__':\n    pass\n", encoding="utf-8")
    AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
    assert proj.identity == "Python · flask, PySide6, requests · entry: main.py"
    # The folder disappears: the project keeps its last identity.
    import shutil
    shutil.rmtree(root)
    AgentHistoryImporter.scan_claude_code(store, max_sessions=10)
    kept = [s for s in store.systems if s.id == proj.id]
    assert not kept or kept[0].identity == "Python · flask, PySide6, requests · entry: main.py"


def test_live_bridge_analyze_folder_writes_identity(tmp_path):
    data = tmp_path / "galaxy.json"
    GalaxyStore(data)
    root = make_desktop(tmp_path / "proj")
    assert bridge_main(["--data", str(data), "analyze-folder", "--path", str(root)]) == 0
    saved = json.loads(data.read_text(encoding="utf-8"))
    (entry,) = [s for s in saved["systems"] if s.get("source") == "codebase"]
    assert entry["identity"] == "Python desktop app · PySide6, requests · entry: main.py"


# ---------------------------------------------------------------------------
# Old galaxy.json files (no identity field)
# ---------------------------------------------------------------------------
OLD_EVIDENCE = (
    "Capabilities implied by libraries: web server / API (express), real-time communication (socket.io), database (mysql2)\n"
    "Project shape: JavaScript, 2 code files; web server / API (express)\n"
    "Entry point: server.js (is run by `scripts.start` (`node server.js`))"
)


def _old_system(**extra):
    base = {
        "name": "dice-server", "project_name": "dice-server", "source": "antigravity",
        "core_plan": "Purpose: x\n\nEntry point: server.js — is run by `scripts.start`\nLanguages: JavaScript (2), Markdown / Wiki (1)",
        "purpose_evidence": OLD_EVIDENCE,
        "planets": [{"title": "External Libraries", "kind": "external", "satellites": [
            {"title": "cors", "kind": "package", "content": "cors — npm package\nImported by (1): server.js"},
            {"title": "express", "kind": "package", "content": "express — npm package\nImported by (2): server.js, x.js"},
            {"title": "socket.io", "kind": "package", "content": "socket.io — npm package\nImported by (2): server.js, x.js"},
        ]}],
    }
    base.update(extra)
    return base


def test_old_galaxy_json_loads_with_default_and_derived_identity(tmp_path):
    path = tmp_path / "galaxy.json"
    path.write_text(json.dumps({"systems": [_old_system(), {"name": "Drawn", "source": "manual", "core_plan": "my notes"}]}),
                    encoding="utf-8")
    store = GalaxyStore(storage_path=path)
    assert store.load_error is None
    old, manual = store.systems
    assert old.identity == ""  # the field defaults; nothing is written on load
    assert stored_identity(old) == "JavaScript web server · express, socket.io, cors · entry: server.js"
    assert stored_identity(manual) == ""  # drawn by hand, never scanned
    assert "identity" not in json.loads(path.read_text(encoding="utf-8"))["systems"][0]


def test_stored_derivation_partial_and_empty():
    s = SolarSystem(name="x", source="codex", core_plan="", purpose_evidence="Project shape: Python + HTML, 46 code files; command-line tool (argparse in cli.py)")
    assert stored_identity(s) == "Python command-line tool"
    s = SolarSystem(name="y", source="codex", core_plan="", purpose_evidence="Project shape: no code, 0 code files")
    assert stored_identity(s) == "No code files"
    s = SolarSystem(name="z", source="codex", core_plan="", purpose_evidence="")
    assert stored_identity(s) == ""
    s = SolarSystem(name="w", source="codex", core_plan="", purpose_evidence=(
        "Project shape: PHP + JavaScript, 73 code files; PHP website: index.php plus 2 other pages at the root (a, b)\n"
        "Capabilities implied by libraries: database (PDO in PHP)\n"
        "Entry point: index.php (is `index.php` at the project root)"))
    assert stored_identity(s) == "PHP + JavaScript website · entry: index.php"
    # A user-edited core plan is not parsed; a scanned value always wins.
    s.identity = "Python · flask"
    assert stored_identity(s) == "Python · flask"


def test_cross_source_merge_keeps_an_identity():
    store_like = GalaxyStore.__new__(GalaxyStore)
    store_like.systems = []
    primary = SolarSystem(name="A", source="codebase")
    other = SolarSystem(name="A", source="codex", identity="Python · flask")
    store_like.systems = [primary, other]
    GalaxyStore.merge_systems(store_like, primary, [other])
    assert primary.identity == "Python · flask"
