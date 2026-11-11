"""Tests for GalaxyStore persistence (atomic save, corrupt-file handling, settings path)."""

import json
from pathlib import Path

from core.models import GalaxyStore, SolarSystem


def _make_store(tmp_path: Path) -> GalaxyStore:
    return GalaxyStore(tmp_path / "galaxy.json")


def _add_project(store: GalaxyStore, name: str) -> SolarSystem:
    system = SolarSystem(name=name, project_name=name, source="claude_code", source_id=f"claude_code:{name.lower()}")
    return store.add_system(system)


def test_first_load_missing_file_creates_starter(tmp_path):
    store = _make_store(tmp_path)
    assert store.load_error is None
    assert len(store.systems) == 1
    assert (tmp_path / "galaxy.json").exists()


def test_atomic_save_leaves_no_temp_files_and_valid_json(tmp_path):
    store = _make_store(tmp_path)
    _add_project(store, "Alpha")
    store.save()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["galaxy.json"]
    data = json.loads((tmp_path / "galaxy.json").read_text(encoding="utf-8"))
    assert {s["name"] for s in data["systems"]} >= {"Alpha"}
    assert store.is_own_write()


def test_is_own_write_detects_external_change(tmp_path):
    store = _make_store(tmp_path)
    assert store.is_own_write()
    other = _make_store(tmp_path)
    _add_project(other, "Beta")
    assert not store.is_own_write()
    assert store.reload_if_changed() is True
    assert any(s.name == "Beta" for s in store.systems)
    assert store.reload_if_changed() is False


def test_corrupt_file_on_first_load_is_backed_up(tmp_path):
    store = _make_store(tmp_path)
    _add_project(store, "Alpha")
    _add_project(store, "Beta")
    path = tmp_path / "galaxy.json"
    full = path.read_bytes()
    truncated = full[: len(full) // 2]
    path.write_bytes(truncated)

    store2 = _make_store(tmp_path)
    assert store2.load_error
    assert store2.backup_path is not None and store2.backup_path.exists()
    assert store2.backup_path.parent == tmp_path
    assert store2.backup_path.name.startswith("galaxy.corrupt-")
    assert store2.backup_path.read_bytes() == truncated
    # Constructing the store must not rewrite the corrupt file.
    assert path.read_bytes() == truncated
    # A second load() on the same store is a reload: still failing, nothing touched.
    systems_before = list(store2.systems)
    assert store2.load() is False
    assert store2.systems == systems_before
    assert path.read_bytes() == truncated


def test_save_after_failed_first_load_keeps_backup(tmp_path):
    path = tmp_path / "galaxy.json"
    original = b'{"systems": [{"name": "Precious", "planets": [ '
    path.write_bytes(original)
    store = _make_store(tmp_path)
    backup = store.backup_path
    assert backup is not None
    store.save()
    assert backup.exists()
    assert backup.read_bytes() == original
    json.loads(path.read_text(encoding="utf-8"))  # the main file is valid again


def test_corrupt_file_on_reload_keeps_memory_and_file(tmp_path):
    store = _make_store(tmp_path)
    _add_project(store, "Alpha")
    names_before = [s.name for s in store.systems]
    path = tmp_path / "galaxy.json"
    garbage = b'{"systems": [ {"name": '
    path.write_bytes(garbage)

    assert store.load() is False
    assert store.load_error
    assert [s.name for s in store.systems] == names_before
    assert path.read_bytes() == garbage
    assert sorted(p.name for p in tmp_path.iterdir()) == ["galaxy.json"]

    # A later successful load clears the error.
    store.save()
    assert store.load() is True
    assert store.load_error is None


def test_invalid_structure_is_treated_as_error(tmp_path):
    path = tmp_path / "galaxy.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = _make_store(tmp_path)
    assert store.load_error
    assert store.backup_path is not None and store.backup_path.exists()


def test_empty_valid_file_is_not_an_error(tmp_path):
    path = tmp_path / "galaxy.json"
    path.write_text('{"systems": []}', encoding="utf-8")
    store = _make_store(tmp_path)
    assert store.load_error is None
    assert store.backup_path is None
    assert len(store.systems) == 1  # starter on first load
    assert sorted(p.name for p in tmp_path.iterdir()) == ["galaxy.json"]


def test_settings_path_follows_storage_path(tmp_path):
    store = _make_store(tmp_path)
    assert store.settings_path == tmp_path / "settings.json"
    store.set_setting("theme", "dark")
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"theme": "dark"}
    assert store.get_setting("theme") == "dark"
    assert store.get_setting("missing", 5) == 5
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_round_trip_planets_satellites_links_notes(tmp_path):
    store = _make_store(tmp_path)
    system = _add_project(store, "RoundTrip")
    ui = system.add_planet(title="UI Layer", summary="Widgets", layer="ui")
    core = system.add_planet(title="Core Layer", summary="Models", color="#123456", layer="core")
    ui.add_satellite(title="Canvas", content="draws", visual_tag="code", file_path="ui/canvas.py")
    system.links = []
    system.add_link(ui.id, core.id, label="reads", relation_type="depends_on")
    system.add_note(10.0, 20.0, title="Note A", content="remember")
    store.save()

    store2 = _make_store(tmp_path)
    assert store2.load_error is None
    loaded = store2.get_by_project_name("claude_code", "roundtrip")
    assert loaded is not None
    planets = {p.title: p for p in loaded.planets}
    assert planets["UI Layer"].summary == "Widgets"
    assert planets["UI Layer"].layer == "ui"
    assert planets["Core Layer"].color == "#123456"
    sat = planets["UI Layer"].satellites[0]
    assert (sat.title, sat.content, sat.visual_tag, sat.file_path) == ("Canvas", "draws", "code", "ui/canvas.py")
    assert len(loaded.links) == 1
    link = loaded.links[0]
    assert (link.source_planet_id, link.target_planet_id, link.label, link.relation_type) == (
        ui.id, core.id, "reads", "depends_on",
    )
    assert [(n.title, n.content, n.x, n.y) for n in loaded.notes] == [("Note A", "remember", 10.0, 20.0)]


def test_imported_projects_get_no_invented_links(tmp_path):
    """Only manual projects receive default layer links; imported ones never do."""
    from core.models import GalaxyStore, SolarSystem

    store = GalaxyStore(storage_path=tmp_path / "galaxy.json")
    for source in ("codebase", "claude_code", "codex", "antigravity", "manual"):
        system = SolarSystem(name=f"p-{source}", project_name=f"p-{source}", source=source)
        system.add_planet(title="A", layer="architecture")
        system.add_planet(title="B", layer="core")
        system.add_planet(title="C", layer="ui")
        store.add_system(system)

    reloaded = GalaxyStore(storage_path=tmp_path / "galaxy.json")
    links = {s.source: len(s.links) for s in reloaded.systems if s.name.startswith("p-")}
    assert links["manual"] > 0
    assert all(links[src] == 0 for src in ("codebase", "claude_code", "codex", "antigravity"))
