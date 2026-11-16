"""The window stays responsive: probes and long store work run off the UI thread, caches
follow the data, and the edits that used to skip the undo history are undoable.

Uses the isolated `env` fixture of tests/test_ui_smoke.py (all probes faked)."""

from __future__ import annotations

import threading
import time

import pytest

from tests.test_ui_smoke import env, hard_timeout, qapp, spin, wait_until  # noqa: F401

from PySide6 import QtWidgets  # noqa: E402

from connectors import ollama_enhancer as oe  # noqa: E402
from connectors.agent_importer import AgentDetector  # noqa: E402
from core.models import PlanetLink, SolarSystem  # noqa: E402
from tests.test_ui_smoke import FAKE_DETECTED  # noqa: E402


def _slow_detect(threads, delay=0.4):
    def detect():
        threads.append(threading.get_ident())
        time.sleep(delay)
        return {k: dict(v) for k, v in FAKE_DETECTED.items()}

    return staticmethod(detect)


def test_integrations_detection_never_blocks_the_ui_thread(env, monkeypatch):
    threads = []
    monkeypatch.setattr(AgentDetector, "detect_all", _slow_detect(threads))
    t0 = time.monotonic()
    win = env["make_window"](onboarded=True)
    assert time.monotonic() - t0 < 0.35, "window construction waited for the tool probes"
    view = win.connections_view
    assert view.cards == {} and view.is_detecting()
    # Saving before the tools are known writes nothing.
    sources_before = win.store.get_setting("connected_sources")
    view._on_connect()
    assert win.store.get_setting("connected_sources") == sources_before
    assert not win.is_store_busy()
    wait_until(lambda: bool(view.cards))
    assert set(view.cards) == set(FAKE_DETECTED)
    assert view.lbl_stats.text() == "2 sources detected"
    assert threads and all(t != threading.get_ident() for t in threads)
    # Opening the page re-detects in the background, then updates the cards.
    t0 = time.monotonic()
    win._switch_to_page("connections")
    assert time.monotonic() - t0 < 0.3
    wait_until(lambda: len(threads) == 2 and not view.is_detecting())
    assert win.central_stack.currentIndex() == 1


def test_integrations_cards_added_while_busy_are_disabled(env, monkeypatch):
    gate = threading.Event()

    def detect():
        gate.wait(5)
        return {k: dict(v) for k, v in FAKE_DETECTED.items()}

    monkeypatch.setattr(AgentDetector, "detect_all", staticmethod(detect))
    win = env["make_window"](onboarded=True)
    view = win.connections_view
    view.set_busy(True, "busy")
    gate.set()
    wait_until(lambda: bool(view.cards))
    assert not any(c.isEnabled() for c in view.cards.values())
    view.set_busy(False)
    assert all(c.isEnabled() for c in view.cards.values())


def test_settings_dialog_lists_models_off_the_ui_thread(env, monkeypatch):
    from ui.main_window import HeliosSettingsDialog

    threads = []

    def models():
        threads.append(threading.get_ident())
        time.sleep(0.3)
        return ["llama3:8b", "qwen2.5-coder:3b"]

    monkeypatch.setattr(oe.OllamaEnhancer, "detect_installed_models", staticmethod(models))
    monkeypatch.setattr(oe.OllamaEnhancer, "get_preferred_model",
                        staticmethod(lambda chosen_model=None: "qwen2.5-coder:3b"))
    win = env["make_window"](onboarded=True)
    win.store.set_setting("selected_ollama_model", "old:1")
    t0 = time.monotonic()
    dlg = HeliosSettingsDialog(win.store, win)
    assert time.monotonic() - t0 < 0.25
    # Saving while the list is loading keeps the stored model.
    dlg._save_settings_and_close()
    assert win.store.get_setting("selected_ollama_model") == "old:1"
    dlg2 = HeliosSettingsDialog(win.store, win)
    wait_until(lambda: dlg2.combo_model.count() == 2)
    assert dlg2.combo_model.currentText() == "qwen2.5-coder:3b"  # the preferred one
    assert threads and all(t != threading.get_ident() for t in threads)
    dlg2._save_settings_and_close()
    assert win.store.get_setting("selected_ollama_model") == "qwen2.5-coder:3b"


def test_merge_duplicates_runs_on_a_store_worker_and_is_undoable(env, monkeypatch):
    from ui.main_window import HeliosSettingsDialog
    from core.models import GalaxyStore

    threads = []
    real = GalaxyStore.merge_all_duplicates

    def merge(self):
        threads.append(threading.get_ident())
        return real(self)

    monkeypatch.setattr(GalaxyStore, "merge_all_duplicates", merge)
    win = env["make_window"](onboarded=True)
    win.show()
    spin(0.2)
    a = SolarSystem(name="Twin", project_name="Twin", source="codebase")
    a.add_planet(title="core")
    b = SolarSystem(name="Twin", project_name="Twin", source="antigravity")
    b.add_planet(title="core")
    win.store.systems.extend([a, b])
    win.store.save()
    win._populate_tree()
    n_before = len(win.store.systems)
    dlg = HeliosSettingsDialog(win.store, win)
    dlg._run_deduplication()
    assert win.is_store_busy() and not dlg.isEnabled()
    wait_until(lambda: not win.is_store_busy())
    spin(0.05)
    assert threads and threads[0] != threading.get_ident()
    assert len(win.store.systems) == n_before - 1
    assert env["boxes"]["information"].calls[-1][1] == "Merge complete"
    assert dlg.isEnabled()
    win._on_undo()
    assert len(win.store.systems) == n_before


def test_ollama_run_without_a_detected_model_probes_in_the_background(env, monkeypatch):
    threads = []

    def pref(chosen_model=None):
        threads.append(threading.get_ident())
        time.sleep(0.2)
        return None

    monkeypatch.setattr(oe.OllamaEnhancer, "get_preferred_model", staticmethod(pref))
    win = env["make_window"](onboarded=True)
    win._detected_ollama_model = None
    t0 = time.monotonic()
    win._run_ollama_for_systems([win.store.systems[0]])
    assert time.monotonic() - t0 < 0.15
    wait_until(lambda: bool(env["boxes"]["warning"].calls))
    assert env["boxes"]["warning"].calls[-1][1] == "No Ollama model found"
    assert threads and threads[0] != threading.get_ident()


# ----------------------------------------------------------------------------
# Edits that now push an undo snapshot first
# ----------------------------------------------------------------------------
def _two_module_window(env):
    win = env["make_window"](onboarded=True)
    win.show()
    spin(0.1)
    s = win.store.systems[0]
    while len(s.planets) < 2:
        s.add_planet(title=f"extra{len(s.planets)}")
    win.store.save()
    win.canvas.enter_solar_system(s)
    spin(0.05)
    return win, s


def test_clear_links_is_undoable(env):
    win, s = _two_module_window(env)
    p0, p1 = s.planets[0], s.planets[1]
    s.add_link(p0.id, p1.id, label="uses")
    win.store.save()
    n_links = len(s.links)
    win._on_canvas_context_action("clear_links", p0)
    assert all(p0.id not in (l.source_planet_id, l.target_planet_id) for l in win.canvas.active_system.links)
    win._on_undo()
    assert len(win.canvas.active_system.links) == n_links


def test_add_link_from_selected_is_undoable(env, monkeypatch):
    win, s = _two_module_window(env)
    p0, p1 = s.add_planet(title="link_src"), s.add_planet(title="link_dst")
    win.store.save()
    win.canvas.rebuild_scene()
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: (p1.title, True)))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("calls", True)))
    n_links = len(s.links)
    win.canvas.select_object(p0, "planet")
    win._prompt_add_link_from_selected()
    assert len(win.canvas.active_system.links) == n_links + 1
    win._on_undo()
    assert len(win.canvas.active_system.links) == n_links


def test_add_planet_and_satellite_are_undoable(env, monkeypatch):
    win, s = _two_module_window(env)
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Fresh", True)))
    n_planets = len(s.planets)
    win._add_planet_to_active()
    assert len(win.canvas.active_system.planets) == n_planets + 1
    win._on_undo()
    assert len(win.canvas.active_system.planets) == n_planets
    target = win.canvas.active_system.planets[0]
    n_sats = len(target.satellites)
    win.canvas.select_object(target, "planet")
    win._add_satellite_to_selected()
    assert len(win.canvas.active_system.planets[0].satellites) == n_sats + 1
    win._on_undo()
    assert len(win.canvas.active_system.planets[0].satellites) == n_sats


# ----------------------------------------------------------------------------
# Tree filter and search index
# ----------------------------------------------------------------------------
def _visible_titles(win):
    out = []

    def walk(item):
        for i in range(item.childCount()):
            ch = item.child(i)
            if not ch.isHidden():
                out.append(ch.text(0))
            walk(ch)

    walk(win.hierarchy_tree.invisibleRootItem())
    return out


def test_tree_filter_waits_for_a_pause_and_clears_at_once(env):
    win = env["make_window"](onboarded=True)
    s = win.store.systems[0]
    s.add_planet(title="zebra_module")
    win.store.save()
    win._populate_tree()
    all_rows = _visible_titles(win)
    win.tree_filter.setText("zebra")
    assert _visible_titles(win) == all_rows  # not yet: the filter waits for a pause
    wait_until(lambda: "zebra_module" in _visible_titles(win) and len(_visible_titles(win)) < len(all_rows))
    win.tree_filter.setText("")
    assert _visible_titles(win) == all_rows  # cleared at once
    # A rebuild keeps the active filter.
    win.tree_filter.setText("zebra")
    win._apply_tree_filter_now()
    win._populate_tree()
    assert "zebra_module" in _visible_titles(win) and len(_visible_titles(win)) < len(all_rows)


def test_selecting_projects_keeps_the_search_index_until_the_data_changes(env):
    win = env["make_window"](onboarded=True)
    a = win.store.systems[0]
    b = SolarSystem(name="Second", project_name="Second")
    b.add_planet(title="needle_module")
    win.store.add_system(b)
    win._populate_tree()
    ctl = win.search_ctl
    ctl.index()
    builds = ctl.index_builds
    win.canvas.enter_solar_system(b)
    win.canvas.enter_solar_system(a)
    ctl.index()
    assert ctl.index_builds == builds  # pure selection: same index
    b.planets[0].title = "renamed_needle"
    win.store.save()
    win.canvas.enter_solar_system(b)  # the data changed: rebuilt
    entries, total = ctl.index().search("renamed_needle", 5)
    assert total == 1 and ctl.index_builds == builds + 1


def test_auto_align_is_undoable(env):
    win, s = _two_module_window(env)
    for p in s.planets:
        p.angle, p.is_user_modified = 12.0, True
    win.store.save()
    before = [(p.angle, p.orbit_radius) for p in s.planets]
    win._rebalance_active_system_layout()
    assert [(p.angle, p.orbit_radius) for p in win.canvas.active_system.planets] != before
    win._on_undo()
    assert [(p.angle, p.orbit_radius) for p in win.canvas.active_system.planets] == before


def test_new_project_is_undoable_and_a_cancelled_dialog_adds_no_step(env, monkeypatch):
    from ui.window.new_project_dialog import NewProjectStudioDialog

    win = env["make_window"](onboarded=True)
    win.show()
    spin(0.1)
    depth = len(win.store._undo_stack)
    monkeypatch.setattr(NewProjectStudioDialog, "exec", lambda self: QtWidgets.QDialog.DialogCode.Rejected)
    win._create_new_system()
    assert len(win.store._undo_stack) == depth
    n = len(win.store.systems)

    def accept(self):
        self.edit_name.setText("Brand New")
        return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(NewProjectStudioDialog, "exec", accept)
    win._create_new_system()
    assert len(win.store.systems) == n + 1 and len(win.store._undo_stack) == depth + 1
    win._on_undo()
    assert len(win.store.systems) == n
    assert all(s.name != "Brand New" for s in win.store.systems)


def test_undo_resets_the_view_as_if_every_object_were_new(env):
    """Unchanged projects keep their objects through undo, but the view still behaves as
    before: the selection goes back to the open project and the inspector shows it."""
    win, s = _two_module_window(env)
    other = SolarSystem(name="Untouched", project_name="Untouched")
    other.add_planet(title="keep.py")
    win.store.add_system(other)
    win.canvas.enter_solar_system(s)
    win.store.push_undo()
    s.planets[0].summary = "edited"
    win.store.save()
    p1 = s.planets[1]
    win.canvas.select_object(p1, "planet")
    assert win._selected_node is p1
    win._on_undo()
    active = win.canvas.active_system
    assert active.id == s.id
    assert win.canvas.selected_node is active
    assert win._selected_node is active and win._selected_type == "sun"
    assert any(x is other for x in win.store.systems)  # kept object
