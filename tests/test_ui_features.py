"""Offscreen UI tests: welcome panel, search results, map filter, map export, first run.

Reuses the isolated `env` fixture of test_ui_smoke (temp store, fake importers, recorded
message boxes). Project names and texts are invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.test_ui_smoke import env, hard_timeout, qapp, spin  # noqa: F401 (fixtures)
from tests.test_map_features import make_system

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from core import paths  # noqa: E402
from ui import export as map_export  # noqa: E402


def _window(env, systems=None, onboarded=True):
    win = env["make_window"](onboarded=onboarded)
    win.store.set_setting("connected_sources", [])  # no startup sync adding projects
    win.store.systems = list(systems if systems is not None else [make_system()])
    win.store.save()
    win.canvas.active_system = win.store.systems[0] if win.store.systems else None
    win._populate_tree()
    win.canvas.rebuild_scene()
    win.resize(1400, 900)
    win.show()
    spin(0.2)
    return win


def _key(widget, key):
    QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, key, QtCore.Qt.KeyboardModifier.NoModifier))


# ----------------------------------------------------------------------------
# Welcome panel
# ----------------------------------------------------------------------------
def test_welcome_panel_shows_without_projects_and_runs_actions(env, monkeypatch):
    win = _window(env, systems=[])
    panel = win.canvas.welcome_panel
    assert panel.isVisible() and not win.canvas.empty_overlay.isVisible()
    assert panel.lbl_projects_dir.text() == str(paths.projects_dir())
    # Fully inside the canvas viewport.
    vp = win.canvas.viewport().rect()
    assert vp.contains(QtCore.QRect(panel.pos(), panel.size())), (panel.geometry(), vp)

    calls = []
    for name in ("_analyze_codebase_folder", "_scan_helios_projects_directly",
                 "_open_helios_projects_folder", "_create_new_system"):
        monkeypatch.setattr(win, name, lambda n=name: calls.append(n))
    panel.btn_analyze.click()
    panel.btn_scan.click()
    panel.btn_open_dir.click()
    panel.btn_design.click()
    assert calls == ["_analyze_codebase_folder", "_scan_helios_projects_directly",
                     "_open_helios_projects_folder", "_create_new_system"]
    panel.btn_ai.click()
    assert win.central_stack.currentIndex() == 1
    win._switch_to_page("studio")

    # A project appears: the panel goes away; removing it brings the panel back.
    system = make_system()
    win.store.add_system(system)
    win._populate_tree()
    win.canvas.rebuild_scene()
    assert not panel.isVisible()
    win.canvas.enter_solar_system(system)
    assert not panel.isVisible()
    win._do_remove_project(system, ignore=False)
    spin(0.05)
    assert panel.isVisible()


def test_welcome_panel_fits_a_small_window(env):
    win = _window(env, systems=[])
    win.resize(1000, 650)
    spin(0.2)
    panel = win.canvas.welcome_panel
    assert win.width() <= 1000, win.width()
    vp = win.canvas.viewport().rect()
    assert vp.contains(QtCore.QRect(panel.pos(), panel.size())), (panel.geometry(), vp)
    assert panel._narrow is True


def test_project_without_modules_keeps_the_short_hint(env):
    empty = make_system("Blank")
    empty.planets, empty.links = [], []
    win = _window(env, systems=[empty])
    win.canvas.enter_solar_system(empty)
    spin(0.05)
    assert win.canvas.empty_overlay.isVisible() and win.canvas.empty_overlay.mode == "no_modules"
    assert not win.canvas.welcome_panel.isVisible()


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)


def test_frozen_first_run_skip_analyzes_nothing_and_says_nothing_about_it(env, frozen, monkeypatch):
    win = env["make_window"](onboarded=False)
    win.store.systems = []
    win.show()
    spin(0.3)
    assert win.central_stack.currentIndex() == 1  # the Integrations page comes first
    texts = []
    view = win.connections_view
    monkeypatch.setattr(view.lbl_status, "setText", lambda t: texts.append(t))
    view.btn_back.click()
    spin(0.2)
    assert not any("Helios codebase" in t for t in texts), texts
    assert win._store_worker is None or not win._store_worker.isRunning()
    assert win.store.get_setting("onboarding_completed") is True
    assert win.central_stack.currentIndex() == 0
    assert win.canvas.welcome_panel.isVisible()


# ----------------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------------
def test_search_results_list_keyboard_and_navigation(env):
    a, b = make_system("Alpha Rocket"), make_system("Beta Probe")
    win = _window(env, systems=[a, b])
    win.canvas.switch_to_galaxy_view()
    ctl = win.search_ctl
    field = win.search_input
    field.setFocus()
    builds = ctl.index_builds
    for ch in "read_g":
        field.insert(ch)
    assert not ctl.popup.isVisible()  # debounced: nothing yet
    ctl.flush()
    assert ctl.index_builds == builds + 1  # one index for all keystrokes
    assert ctl.popup.isVisible()
    entries = ctl.popup.entries
    assert [e.kind_label for e in entries] == ["Function", "Function"]
    assert entries[0].where == "Alpha Rocket › tools › gauge.py"
    _key(field, QtCore.Qt.Key.Key_Down)
    assert ctl.popup.current_entry() is entries[1]
    _key(field, QtCore.Qt.Key.Key_Up)
    assert ctl.popup.current_entry() is entries[0]
    _key(field, QtCore.Qt.Key.Key_Return)
    spin(0.05)
    # The project opened, the containing component is selected and shown.
    assert not ctl.popup.isVisible()
    assert win.canvas.view_mode == "system" and win.canvas.active_system is a
    gauge = a.planets[2].satellites[0]
    assert win.canvas.selected_node is gauge
    assert win._selected_node is gauge
    tools_item = win.canvas.planet_item_map[a.planets[2].id]
    assert all(s.isVisible() for s in tools_item.satellite_items)


def test_search_more_line_escape_and_no_match(env):
    s = make_system()
    for i in range(15):
        s.planets[0].add_satellite(title=f"stage_{i}()").kind = "function"
    win = _window(env, systems=[s])
    ctl = win.search_ctl
    win.search_input.setFocus()
    win.search_input.setText("stage_")
    ctl.flush()
    assert len(ctl.popup.entries) == 8 and ctl.popup.info_text().startswith("+7 more")
    win._on_escape_pressed()
    assert not ctl.popup.isVisible() and win.search_input.text() == "stage_"
    win._on_escape_pressed()
    assert win.search_input.text() == ""
    win.search_input.setText("qqqzzz")
    ctl.flush()
    assert ctl.popup.entries == [] and "Nothing on the map matches" in ctl.popup.info_text()


def test_search_index_rebuilds_after_the_map_changes(env):
    s = make_system()
    win = _window(env, systems=[s])
    ctl = win.search_ctl
    win.search_input.setText("brandnew")
    ctl.flush()
    assert ctl.popup.entries == []
    s.planets[0].add_satellite(title="brandnew_feature()").kind = "function"
    win._populate_tree()  # every data change refreshes the tree
    ctl.flush()
    assert [e.name for e in ctl.popup.entries] == ["brandnew_feature()"]


# ----------------------------------------------------------------------------
# Filter
# ----------------------------------------------------------------------------
def _flags(system):
    return ([p.is_user_modified for p in system.planets], [l.is_user_modified for l in system.links],
            [s.is_user_modified for p in system.planets for s in p.satellites])


def test_filter_changes_only_what_is_drawn(env):
    s = make_system()
    win = _window(env, systems=[s])
    win.canvas.enter_solar_system(s)
    spin(0.05)
    disk_before = win.store.file_path.read_bytes()
    undo_before = len(win.store._undo_stack)
    flags_before = _flags(s)

    win._open_filter_popover()
    pop = win.filter_popover
    assert pop.isVisible()
    assert set(pop.boxes) == {"link:imports", "link:manual", "module:external", "module:history",
                              "layer:architecture", "layer:core", "layer:testing"}
    assert "Imports  ·  2" in pop.boxes["link:imports"].text()
    pop.boxes["link:manual"].setChecked(False)
    pop.boxes["module:history"].setChecked(False)
    pop.boxes["layer:testing"].setChecked(False)  # hides "tools" and its links
    canvas = win.canvas
    hidden_mods = {it.planet.title for it in canvas.planet_items if not it.isVisible()}
    assert hidden_mods == {"Work History", "tools"}
    visible_links = [it.link.label for it in canvas.link_items if it.isVisible()]
    assert visible_links == ["imports core.engine — calls fire()"]
    assert win.btn_filter.property("active") == "true"
    assert canvas.filter_banner.isVisible() and "2 modules, 2 links" in canvas.filter_banner.lbl.text()
    # Hidden items cannot be hit.
    tools_item = next(it for it in canvas.planet_items if it.planet.title == "tools")
    assert canvas._find_planet_item_at(tools_item.pos()) is None
    assert tools_item not in canvas.scene_obj.items(tools_item.pos()) or not tools_item.isVisible()
    # Data untouched.
    assert win.store.file_path.read_bytes() == disk_before
    assert len(win.store._undo_stack) == undo_before
    assert _flags(s) == flags_before
    # Survives a scene rebuild; one click resets.
    canvas.rebuild_scene()
    assert not next(it for it in canvas.planet_items if it.planet.title == "tools").isVisible()
    pop.hide()
    canvas.filter_banner.btn.click()
    assert all(it.isVisible() for it in canvas.planet_items + canvas.link_items)
    assert not canvas.filter_banner.isVisible() and win.btn_filter.property("active") == "false"
    assert win.store.file_path.read_bytes() == disk_before


def test_selecting_a_hidden_item_turns_the_filter_off(env):
    s = make_system()
    win = _window(env, systems=[s])
    win.canvas.enter_solar_system(s)
    win.canvas.map_filter.hide_external = True
    win.canvas.apply_map_filter()
    ext = next(p for p in s.planets if p.kind == "external")
    assert not win.canvas.planet_item_map[ext.id].isVisible()
    win.canvas.select_object(ext, "planet")
    assert not win.canvas.map_filter.is_active()
    assert win.canvas.planet_item_map[ext.id].isVisible()
    assert "filter was turned off" in win.status_label.text()


def test_filter_button_disabled_outside_a_project(env):
    win = _window(env)
    win.canvas.switch_to_galaxy_view()
    assert not win.btn_filter.isEnabled()
    win.canvas.enter_solar_system(win.store.systems[0])
    assert win.btn_filter.isEnabled()


# ----------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------
def test_png_export_whole_map_capped_and_clean(env, monkeypatch, tmp_path):
    s = make_system()
    win = _window(env, systems=[s])
    canvas = win.canvas
    canvas.enter_solar_system(s)
    sel = s.planets[1]
    canvas.select_object(sel, "planet")
    out = tmp_path / "out" / "orbit-demo-map.png"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "PNG image (*.png)")))
    path = map_export.export_map(win, s, "png")
    assert path == out and out.stat().st_size > 1000
    img = QtGui.QImage(str(out))
    with map_export.clean_view_state(canvas):  # the image is drawn without the selection
        rect = map_export.map_scene_rect(canvas)
    assert abs(img.width() - round(rect.width() * 2)) <= 1 and abs(img.height() - round(rect.height() * 2)) <= 1
    assert win.status_label.text() == f"Saved to {out}"
    # Selection and view untouched afterwards; nothing written to the project file.
    assert canvas.selected_node is sel
    # The background is the theme colour (a corner pixel).
    from ui import theme
    assert img.pixelColor(1, 1).name() == theme.qcolor(theme.CANVAS_BG).name()
    # Capped: a tiny max side scales the whole map down.
    small = map_export.render_map_image(canvas, max_side=300)
    assert max(small.width(), small.height()) <= 300 and not small.isNull()


def test_png_export_respects_the_filter_and_clears_selection_rings(env):
    s = make_system()
    win = _window(env, systems=[s])
    canvas = win.canvas
    canvas.enter_solar_system(s)
    full = map_export.map_scene_rect(canvas)
    canvas.map_filter.hide_external = True
    canvas.map_filter.hide_history = True
    canvas.apply_map_filter()
    filtered = map_export.map_scene_rect(canvas)
    assert filtered.width() * filtered.height() < full.width() * full.height()
    seen = []
    orig = canvas.scene_obj.render

    def spy(painter, target, source, *a):
        seen.append((canvas.selected_node, canvas.hovered_planet_item))
        return orig(painter, target, source, *a)

    canvas.scene_obj.render = spy
    canvas.select_object(s.planets[0], "planet")
    map_export.render_map_image(canvas)
    assert seen == [(None, None)]
    assert canvas.selected_node is s.planets[0]


def test_markdown_export_and_write_error(env, monkeypatch, tmp_path):
    s = make_system()
    win = _window(env, systems=[s])
    out = tmp_path / "doc"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "Markdown document (*.md)")))
    path = map_export.export_map(win, s, None)
    assert path == tmp_path / "doc.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Orbit Demo\n") and "## Links (3)" in text
    # Default name and folder for the next export.
    assert map_export.default_path(win.store, s, "md") == tmp_path / "orbit-demo-architecture.md"

    def boom(*a, **k):
        raise PermissionError("disk is read-only")

    monkeypatch.setattr(map_export, "atomic_write_text", boom)
    assert map_export.export_map(win, s, "md") is None
    assert env["boxes"]["warning"].calls and "disk is read-only" in env["boxes"]["warning"].calls[-1][2]
    assert win.status_label.text().startswith("Export failed")


def test_shortcuts_do_not_collide(env):
    win = _window(env)
    keys = [sc.key().toString() for sc in win._shortcuts]
    assert len(keys) == len(set(keys)), keys
    assert "Ctrl+E" in keys


# ----------------------------------------------------------------------------
# Zero projects: no stale project in the inspector, breadcrumb, tree or selection
# ----------------------------------------------------------------------------
import json  # noqa: E402

from ui import main_window as mw  # noqa: E402


def _assert_neutral_empty(win):
    c = win.canvas
    assert win.store.systems == []
    assert c.active_system is None and c.selected_node is None and win._selected_node is None
    assert not win.btn_bc_project.isVisible() and "Orbit" not in win.lbl_breadcrumb.text()
    assert win.sun_title_display.text() == "No project yet"
    assert not win.sel_title.isVisible()
    texts = [l.text() for l in win.sections_widget.findChildren(QtWidgets.QLabel) if not l.isHidden() and l.text()]
    assert texts == ["There are no projects yet. Use Add project to analyze a folder, or Sync to read your AI tools."]
    assert win.hierarchy_tree.topLevelItemCount() == 0
    assert c.welcome_panel.isVisible()
    assert not win.btn_filter.isEnabled()


def _open_with_selection(win):
    s = win.store.systems[0]
    win.canvas.enter_solar_system(s)
    win.canvas.select_object(s.planets[1], "planet")
    spin(0.05)
    assert win.btn_bc_project.isVisible()
    return s


def test_empty_after_remove_project(env, monkeypatch):
    win = _window(env)
    s = _open_with_selection(win)
    monkeypatch.setattr(mw.RemoveProjectDialog, "ask", classmethod(lambda cls, sys_, p=None: cls.REMOVE))
    win._remove_project(s)
    spin(0.05)
    _assert_neutral_empty(win)


def test_empty_after_undo_and_after_redo(env, monkeypatch):
    win = _window(env, systems=[])
    win.store.push_undo()
    win.store.add_system(make_system())
    win._populate_tree()
    win.canvas.rebuild_scene()
    _open_with_selection(win)
    win._on_undo()
    spin(0.05)
    _assert_neutral_empty(win)
    # Redo brings it back; removing + redo also ends in a clean empty state.
    win._on_redo()
    s = _open_with_selection(win)
    monkeypatch.setattr(mw.RemoveProjectDialog, "ask", classmethod(lambda cls, sys_, p=None: cls.REMOVE))
    win._remove_project(s)
    win._on_undo()
    _open_with_selection(win)
    win._on_redo()
    spin(0.05)
    _assert_neutral_empty(win)


def test_empty_after_external_change_to_the_file(env):
    win = _window(env)
    _open_with_selection(win)
    win.store.file_path.write_text(json.dumps({"format_version": 2, "systems": []}), encoding="utf-8")
    win._on_external_store_changed(str(win.store.file_path))
    spin(0.05)
    _assert_neutral_empty(win)


def test_empty_at_startup(env):
    path = env["tmp"] / "galaxy.json"
    path.write_text(json.dumps({"format_version": 2, "systems": []}), encoding="utf-8")
    from core.models import GalaxyStore

    win = env["make_window"](store=GalaxyStore(storage_path=path))
    win.store.set_setting("connected_sources", [])
    win.show()
    spin(0.3)
    _assert_neutral_empty(win)
