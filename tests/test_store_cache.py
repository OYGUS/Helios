"""The store's caches and fast paths give exactly the old results (no Qt).

* galaxy.json text and undo snapshots are byte-identical to `json.dumps(asdict(...))`,
  also after edits (the per-project indented blocks are reused only for unchanged data);
* settings.json is cached in memory but re-read when another process changes it;
* `is_own_write` / skipped identical writes never hide another writer's change;
* the search index reuses entries only for projects whose data did not change.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict

import pytest

from core.models import (
    GALAXY_FORMAT_VERSION,
    CanvasNote,
    GalaxyStore,
    PlanetLink,
    SolarSystem,
    current_timestamp,
)
from core.search_index import SearchIndex


def _store(tmp_path) -> GalaxyStore:
    store = GalaxyStore(storage_path=tmp_path / "galaxy.json")
    store.systems = []
    for i in range(3):
        s = SolarSystem(name=f"Proj {i} ü \"q\"", project_name=f"Proj {i}", purpose="line\nnext\tx  ")
        for m in range(3):
            p = s.add_planet(title=f"mod_{m}.py", summary="Defines: run(), class Motor\r\nmore")
            for c in range(2):
                sat = p.add_satellite(title=f"comp_{m}_{c}()", content="x", visual_tag="code")
                sat.defined_names = ["helper()", "class Engine", "Engine.spin()"] if c else []
        s.links.append(PlanetLink(source_planet_id=s.planets[0].id, target_planet_id=s.planets[1].id, label="imports"))
        s.notes.append(CanvasNote(title="n", x=1.5, y=-2e10))
        s.rebuild_links_and_tour()
        store.systems.append(s)
    return store


def _reference_text(store: GalaxyStore) -> str:
    return json.dumps(
        {
            "format_version": GALAXY_FORMAT_VERSION,
            "updated_at": current_timestamp(),
            "systems": [asdict(s) for s in store.systems],
        },
        ensure_ascii=False,
        indent=2,
    )


def _reference_snapshot(store: GalaxyStore) -> str:
    return json.dumps([asdict(s) for s in store.systems], ensure_ascii=False)


def test_saved_text_and_snapshot_match_asdict_json_after_every_kind_of_change(tmp_path):
    store = _store(tmp_path)
    steps = [
        lambda st: None,
        lambda st: setattr(st.systems[1].planets[0].satellites[0], "title", "renamed()"),
        lambda st: st.systems[0].planets[1].satellites.append(st.systems[0].planets[0].satellites.pop()),
        lambda st: setattr(st.systems[2].links[0], "source_planet_id", st.systems[2].links[0].target_planet_id),
        lambda st: st.systems[2].notes.clear(),
        lambda st: st.systems.append(SolarSystem(name="Zeta")),
        lambda st: st.systems.pop(0),
        lambda st: st.systems.append(st.systems[0]),  # the same object twice
        lambda st: setattr(st, "systems", []),
    ]
    for step in steps:
        step(store)
        store.save()
        on_disk = (tmp_path / "galaxy.json").read_text(encoding="utf-8")
        assert on_disk == _reference_text(store)
        assert store.last_written_text == on_disk
        assert store._snapshot() == _reference_snapshot(store)


def test_unchanged_projects_reuse_their_block_changed_ones_do_not(tmp_path):
    store = _store(tmp_path)
    store.save()
    blocks_before = dict(store._indented_blocks)
    store.systems[0].planets[0].title = "changed_title.py"
    store.save()
    assert "changed_title.py" in (tmp_path / "galaxy.json").read_text(encoding="utf-8")
    reused = [b for b in store._indented_blocks.values() if b in blocks_before.values()]
    assert len(reused) == len(store.systems) - 1


def test_undo_redo_restore_exact_state(tmp_path):
    store = _store(tmp_path)
    store.save()
    before = (tmp_path / "galaxy.json").read_text(encoding="utf-8")
    store.push_undo()
    store.systems[0].planets[0].satellites.clear()
    store.save()
    assert store.undo()
    assert (tmp_path / "galaxy.json").read_text(encoding="utf-8") == before
    assert store.redo()
    assert store.systems[0].planets[0].satellites == []


def test_settings_cache_sees_changes_made_by_another_process(tmp_path):
    store = GalaxyStore(storage_path=tmp_path / "galaxy.json")
    store.set_setting("theme", "dark")
    assert store.get_setting("theme") == "dark"
    # Another store (another Helios) replaces the file.
    other = GalaxyStore(storage_path=tmp_path / "galaxy.json")
    other.set_setting("theme", "light")
    assert store.get_setting("theme") == "light"
    # An editor writes the file in place (same inode), same length.
    path = tmp_path / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["theme"] = "blue!"
    time.sleep(0.01)
    with open(path, "r+", encoding="utf-8") as fh:
        fh.seek(0)
        fh.write(json.dumps(data, ensure_ascii=False, indent=2))
        fh.truncate()
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert store.get_setting("theme") == "blue!"
    # Unreadable, then removed: the default, as before.
    path.write_text("{not json", encoding="utf-8")
    assert store.get_setting("theme", "dflt") == "dflt"
    path.unlink()
    assert store.get_setting("theme", "dflt") == "dflt"
    store.set_setting("a", 1)
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_settings_values_are_copies_and_round_trip_like_json(tmp_path):
    store = GalaxyStore(storage_path=tmp_path / "galaxy.json")
    store.set_setting("ignored_folders", ["a"])
    got = store.get_setting("ignored_folders")
    got.append("b")
    assert store.get_setting("ignored_folders") == ["a"]
    store.set_setting("pair", ("x", 1))  # stored as JSON: read back as a list
    assert store.get_setting("pair") == ["x", 1]
    assert store.get_setting("missing", 5) == 5


def test_is_own_write_and_skipped_identical_save_never_hide_other_writers(tmp_path):
    store = _store(tmp_path)
    store.save()
    path = tmp_path / "galaxy.json"
    assert store.is_own_write()
    ino = os.stat(path).st_ino
    store.save()  # same text: the write is skipped, the file is untouched
    assert os.stat(path).st_ino == ino and store.is_own_write()
    # In-place rewrite by someone else with the same length (mtime changes).
    text = path.read_text(encoding="utf-8")
    changed = text.replace('"Proj 0"', '"Proj X"', 1)
    assert len(changed) == len(text) and changed != text
    time.sleep(0.01)
    with open(path, "r+", encoding="utf-8", newline="") as fh:
        fh.write(changed)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert not store.is_own_write()
    # A save now really writes (the file is not the one this store wrote).
    store.save()
    assert path.read_text(encoding="utf-8") == store.last_written_text
    # Another store replaces the file.
    other = GalaxyStore(storage_path=path)
    other.systems[0].name = "From other"
    other.save()
    assert not store.is_own_write()
    assert store.reload_if_changed() is True
    assert any(s.name == "From other" for s in store.systems)
    assert store.is_own_write()


def test_crlf_file_is_still_compared_as_text(tmp_path):
    path = tmp_path / "galaxy.json"
    payload = json.dumps({"format_version": GALAXY_FORMAT_VERSION, "systems": []}, indent=2)
    path.write_bytes(payload.replace("\n", "\r\n").encode("utf-8"))
    store = GalaxyStore(storage_path=path)
    assert store._own_file_identity is None  # bytes differ from the normalized text
    assert store.is_own_write()


def test_data_version_moves_on_save_and_load(tmp_path):
    store = _store(tmp_path)
    v0 = store.data_version
    store.save()
    assert store.data_version > v0
    v1 = store.data_version
    store.load()
    assert store.data_version > v1


def _entries(ix):
    return [(e.name, e.kind, e.system_id, e.planet_id, e.satellite_id, e.where, e.extra) for e in ix.entries]


@pytest.mark.parametrize("change", ["rename", "move", "purpose", "remove_planet", "add_system"])
def test_search_cache_reuses_only_unchanged_projects(tmp_path, change):
    store = _store(tmp_path)
    cache: dict = {}
    SearchIndex(store.systems, None, cache)
    s0, s1 = store.systems[0], store.systems[1]
    if change == "rename":
        s1.planets[2].satellites[0].title = "fresh_name()"
    elif change == "move":
        s1.planets[1].satellites.append(s1.planets[0].satellites.pop(0))
    elif change == "purpose":
        s0.purpose = "now about rockets"
    elif change == "remove_planet":
        s1.remove_planet(s1.planets[0])
    else:
        store.systems.append(SolarSystem(name="Brand new"))
    cached = SearchIndex(store.systems, None, cache)
    fresh = SearchIndex(store.systems)
    assert _entries(cached) == _entries(fresh)
    for q in ("fresh_name", "rockets", "comp_0", "brand", "engine", "proj"):
        assert [e.name for e in cached.search(q, 8)[0]] == [e.name for e in fresh.search(q, 8)[0]]
        assert cached.search(q, 8)[1] == fresh.search(q, 8)[1]


def test_search_narrowing_and_ranking_match_a_full_scan(tmp_path):
    store = _store(tmp_path)
    ix = SearchIndex(store.systems)
    for q in ("c", "co", "com", "comp_1", "comp_1_1", "comp_1", "e", "eng", "engine.spin", "x"):
        got = ix.search(q, 5, store.systems[1].id)
        fresh = SearchIndex(store.systems).search(q, 5, store.systems[1].id)
        assert [(e.name, e.system_id) for e in got[0]] == [(e.name, e.system_id) for e in fresh[0]]
        assert got[1] == fresh[1]


def test_search_with_duplicate_project_ids_falls_back_to_one_pass(tmp_path):
    store = _store(tmp_path)
    store.systems[1].id = store.systems[0].id
    cache: dict = {}
    assert _entries(SearchIndex(store.systems, None, cache)) == _entries(SearchIndex(store.systems))


# ----------------------------------------------------------------------------
# Undo / redo: per-project snapshots, only changed projects parsed again
# ----------------------------------------------------------------------------
def _clone(tmp_path, store, name):
    other = GalaxyStore(storage_path=tmp_path / name / "galaxy.json")
    other.systems = []
    other._restore_snapshot(store._snapshot())  # the old whole-text path
    return other


def test_partial_restore_equals_parsing_the_whole_snapshot(tmp_path):
    store = _store(tmp_path)
    store.save()
    edits = [
        lambda st: setattr(st.systems[0].planets[0], "title", "edited.py"),
        lambda st: st.systems[1].planets[1].satellites.pop(),
        lambda st: st.systems.append(SolarSystem(name="Fresh", source="manual")),
        lambda st: st.systems.pop(0),
        lambda st: setattr(st.systems[0], "purpose", "changed purpose"),
    ]
    for edit in edits:
        store.push_undo()
        edit(store)
        store.save()
    for _ in range(len(edits) + 1):
        target = store._undo_stack[-1] if store._undo_stack else None
        if target is None:
            break
        reference = _clone(tmp_path, store, f"ref{len(store._undo_stack)}")
        reference._restore_snapshot("[" + ", ".join(target) + "]")
        assert store.undo()
        assert store.last_written_text == reference.last_written_text  # same bytes
        assert store._snapshot() == reference._snapshot()
    while store.can_redo():
        store.redo()
    assert store.systems[0].purpose == "changed purpose"


def test_snapshots_share_the_text_of_unchanged_projects(tmp_path):
    store = _store(tmp_path)
    store.push_undo()
    store.systems[0].name = "first changed"
    store.push_undo()
    a, b = store._undo_stack[-2], store._undo_stack[-1]
    assert a[0] != b[0]
    assert a[1] is b[1] and a[2] is b[2]


def test_restore_drops_internal_projects_like_parsing_does(tmp_path):
    store = _store(tmp_path)
    junk = SolarSystem(name="[Codex] leftovers", source="codex", source_id=".codex-x")
    store.systems.append(junk)
    store.push_undo()
    store.systems[0].name = "edit"
    store.undo()  # the junk project is in both snapshots: dropped like a parse drops it
    assert all(s is not junk for s in store.systems)
    assert not GalaxyStore._dropped_on_load("codebase", None, "x")
    assert GalaxyStore._dropped_on_load("codex", "plugin", "x")


def test_undo_of_a_one_project_edit_parses_only_that_project(tmp_path):
    store = _store(tmp_path)
    store.save()
    objects = list(store.systems)
    store.push_undo()
    store.systems[1].planets[0].title = "edited.py"
    store.save()
    assert store.undo()
    assert store.systems[0] is objects[0] and store.systems[2] is objects[2]
    assert store.systems[1] is not objects[1]
    assert store.systems[1].planets[0].title == "mod_0.py"
    assert store.redo()
    assert store.systems[1].planets[0].title == "edited.py"
    assert store.systems[0] is objects[0]
