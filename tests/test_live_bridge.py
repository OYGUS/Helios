"""Tests for the connectors.live_bridge CLI (idempotency, exact matching, validation, exit codes)."""

import json
from pathlib import Path

import pytest

from connectors.live_bridge import main
from core.models import GalaxyStore


@pytest.fixture
def data(tmp_path) -> Path:
    path = tmp_path / "galaxy.json"
    GalaxyStore(path)  # creates the starter file
    return path


def run(data: Path, *args: str) -> int:
    return main(["--data", str(data), *args])


def project(data: Path, name: str = "Proj", source: str = "claude_code"):
    store = GalaxyStore(data)
    assert store.load_error is None
    return store.get_by_project_name(source, name)


BASE = ("--project", "Proj", "--source", "claude_code")


def test_set_sun_creates_project(data, capsys):
    assert run(data, "set-sun", *BASE, "--title", "Proj Core", "--plan", "The plan") == 0
    system = project(data)
    assert system.sun_title == "Proj Core" and system.core_plan == "The plan"
    # Alias works and is idempotent (still one project).
    assert run(data, "set-core", *BASE, "--title", "Proj Core 2", "--plan", "Plan 2") == 0
    store = GalaxyStore(data)
    assert len([s for s in store.systems if s.project_name == "Proj"]) == 1
    assert project(data).core_plan == "Plan 2"


def test_add_planet_idempotent(data, capsys):
    assert run(data, "add-planet", *BASE, "--title", "UI Layer", "--layer", "ui", "--summary", "v1") == 0
    assert "added" in capsys.readouterr().out
    assert run(data, "add-module", *BASE, "--title", "  ui layer ", "--summary", "v2") == 0
    assert "updated" in capsys.readouterr().out
    system = project(data)
    titles = [p.title for p in system.planets]
    assert titles == ["UI Layer"]
    assert system.planets[0].summary == "v2"
    assert system.planets[0].layer == "ui"  # not reset when --layer omitted


def test_add_moon_idempotent(data, capsys):
    assert run(data, "add-planet", *BASE, "--title", "UI Layer", "--layer", "ui") == 0
    assert run(data, "add-moon", *BASE, "--planet", "UI Layer", "--title", "Canvas", "--content", "a", "--tag", "code") == 0
    assert "added" in capsys.readouterr().out
    assert run(data, "add-component", *BASE, "--planet", "ui layer", "--title", "canvas", "--content", "b") == 0
    assert "updated" in capsys.readouterr().out
    sats = project(data).planets[0].satellites
    assert len(sats) == 1
    assert sats[0].content == "b" and sats[0].visual_tag == "code"


def test_add_link_idempotent(data):
    run(data, "add-planet", *BASE, "--title", "UI Layer", "--layer", "ui")
    run(data, "add-planet", *BASE, "--title", "Data Store", "--layer", "research")
    before = len(project(data).links)
    assert run(data, "add-link", *BASE, "--from-planet", "UI Layer", "--to-planet", "Data Store", "--label", "reads") == 0
    after_first = project(data).links
    assert run(data, "add-link", *BASE, "--from-planet", "ui layer", "--to-planet", "data store", "--label", "reads") == 0
    after_second = project(data).links
    assert len(after_second) == len(after_first)
    system = project(data)
    ids = {p.title: p.id for p in system.planets}
    matching = [l for l in after_second if l.source_planet_id == ids["UI Layer"] and l.target_planet_id == ids["Data Store"]]
    assert len(matching) == 1 and matching[0].label == "reads"
    assert len(after_first) >= before


def test_exact_matching_does_not_hit_substring(data, capsys):
    run(data, "add-planet", *BASE, "--title", "Build Guide", "--layer", "docs")
    text_before = data.read_text(encoding="utf-8")
    capsys.readouterr()
    rc = run(data, "add-moon", *BASE, "--planet", "UI", "--title", "Canvas")
    assert rc == 1
    err = capsys.readouterr().err
    assert "Build Guide" in err  # lists available modules
    assert data.read_text(encoding="utf-8") == text_before
    assert project(data).planets[0].satellites == []


def test_unique_word_prefix_is_accepted(data):
    run(data, "add-planet", *BASE, "--title", "UI Layer", "--layer", "ui")
    assert run(data, "add-moon", *BASE, "--planet", "UI", "--title", "Canvas") == 0
    assert [s.title for s in project(data).planets[0].satellites] == ["Canvas"]


def test_ambiguous_prefix_is_rejected(data):
    run(data, "add-planet", *BASE, "--title", "UI Layer", "--layer", "ui")
    run(data, "add-planet", *BASE, "--title", "UI Widgets", "--layer", "ui")
    assert run(data, "add-moon", *BASE, "--planet", "UI", "--title", "Canvas") == 1


def test_add_moon_unknown_module_fails_unless_create_flag(data):
    run(data, "set-sun", *BASE, "--title", "Proj", "--plan", "p")
    text_before = data.read_text(encoding="utf-8")
    assert run(data, "add-moon", *BASE, "--planet", "Ghost", "--title", "X") == 1
    assert data.read_text(encoding="utf-8") == text_before
    assert run(data, "add-moon", *BASE, "--planet", "Ghost", "--title", "X", "--create-planet") == 0
    system = project(data)
    assert [p.title for p in system.planets] == ["Ghost"]
    assert [s.title for s in system.planets[0].satellites] == ["X"]


def test_add_link_unknown_module_fails_and_writes_nothing(data, capsys):
    run(data, "add-planet", *BASE, "--title", "UI Layer", "--layer", "ui")
    text_before = data.read_text(encoding="utf-8")
    capsys.readouterr()
    rc = run(data, "add-link", *BASE, "--from-planet", "UI Layer", "--to-planet", "Nope")
    assert rc == 1
    assert "Nope" in capsys.readouterr().err
    assert data.read_text(encoding="utf-8") == text_before


def test_add_link_unknown_project_fails(data):
    text_before = data.read_text(encoding="utf-8")
    assert run(data, "add-link", "--project", "Missing", "--from-planet", "A", "--to-planet", "B") == 1
    assert data.read_text(encoding="utf-8") == text_before


@pytest.mark.parametrize(
    "args",
    [
        ("add-planet", "--project", "Proj", "--source", "bogus", "--title", "X"),
        ("add-planet", "--project", "Proj", "--layer", "nonsense", "--title", "X"),
        ("add-moon", "--project", "Proj", "--planet", "X", "--title", "Y", "--tag", "weird"),
        ("add-planet", "--title", "X"),  # missing --project
        ("set-sun", "--title", "X", "--plan", "Y"),  # missing --project
        ("add-moon", "--planet", "X", "--title", "Y"),  # missing --project
        ("add-link", "--from-planet", "A", "--to-planet", "B"),  # missing --project
        ("add-planet", "--project", "   ", "--title", "X"),  # empty --project
    ],
)
def test_invalid_arguments_rejected(data, args, capsys):
    text_before = data.read_text(encoding="utf-8")
    assert run(data, *args) == 2
    assert capsys.readouterr().err
    assert data.read_text(encoding="utf-8") == text_before


def test_project_match_is_exact_not_substring(data):
    run(data, "set-sun", "--project", "Helios Extra", "--source", "claude_code", "--title", "T", "--plan", "P")
    run(data, "add-planet", "--project", "Extra", "--source", "claude_code", "--title", "Mod")
    store = GalaxyStore(data)
    names = sorted(s.name for s in store.systems if s.source == "claude_code")
    assert names == ["Extra", "Helios Extra"]
    assert store.get_by_project_name("claude_code", "Helios Extra").planets == []


def test_corrupt_store_makes_bridge_fail_without_writing(tmp_path, capsys):
    path = tmp_path / "galaxy.json"
    path.write_bytes(b'{"systems": [')
    assert main(["--data", str(path), "add-planet", *BASE, "--title", "X"]) == 1
    assert path.read_bytes() == b'{"systems": ['
    assert "backup" in capsys.readouterr().err.lower()
    assert list(tmp_path.glob("galaxy.corrupt-*.json"))


def test_concurrent_change_is_reapplied_not_overwritten(data, monkeypatch):
    """If another process writes between our load and save, the bridge reloads and re-applies."""
    import connectors.live_bridge as bridge

    real_handler = bridge.COMMANDS["add-planet"]
    calls = {"n": 0}

    def racing_handler(store, args):
        calls["n"] += 1
        if calls["n"] == 1:
            other = GalaxyStore(data)  # simulate the GUI saving a change meanwhile
            from core.models import SolarSystem

            other.add_system(SolarSystem(name="FromGUI", project_name="FromGUI", source="manual"))
        return real_handler(store, args)

    monkeypatch.setitem(bridge.COMMANDS, "add-planet", racing_handler)
    assert run(data, "add-planet", *BASE, "--title", "UI Layer") == 0
    assert calls["n"] == 2
    store = GalaxyStore(data)
    assert store.get_by_project_name("manual", "FromGUI") is not None
    assert [p.title for p in store.get_by_project_name("claude_code", "Proj").planets] == ["UI Layer"]


def test_output_is_valid_json_and_no_temp_files(data):
    run(data, "add-planet", *BASE, "--title", "UI Layer", "--layer", "ui")
    json.loads(data.read_text(encoding="utf-8"))
    assert sorted(p.name for p in data.parent.iterdir()) == ["galaxy.json"]
