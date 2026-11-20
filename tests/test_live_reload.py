"""Live reload of galaxy.json through the window's file watcher.

Every save replaces galaxy.json with a rename (`atomic_write_text`). On Linux (inotify)
QFileSystemWatcher drops a path that was replaced that way, so the window must re-add it
after every change notification, including the ones it ignores (its own writes); otherwise
external edits (connectors/live_bridge.py, another window) stop being picked up after the
first save.
"""

from __future__ import annotations

from tests.test_ui_smoke import env, hard_timeout, qapp, spin, wait_until  # noqa: F401

from core.models import GalaxyStore, SolarSystem  # noqa: E402


def _window_with_project(env):
    path = env["tmp"] / "live" / "galaxy.json"
    store = GalaxyStore(storage_path=path)
    store.systems.append(SolarSystem(name="Alpha", project_name="Alpha", source="manual"))
    store.save()
    win = env["make_window"](store=GalaxyStore(storage_path=path), onboarded=True)
    win.store.set_setting("connected_sources", [])
    win.show()
    spin(0.2)
    return win, path, store.systems[0].id


def _name(win, project_id: str) -> str:
    return next(s.name for s in win.store.systems if s.id == project_id)


def _watched(win) -> bool:
    return str(win.store.storage_path) in win.file_watcher.files()


def _external_rename(path, project_id: str, name: str) -> None:
    other = GalaxyStore(storage_path=path)
    project = next(s for s in other.systems if s.id == project_id)
    project.name = name
    project.project_name = name
    other.save()


def test_external_edit_after_own_save_is_picked_up(env):
    win, path, pid = _window_with_project(env)
    assert _watched(win)

    # A save by Helios itself: ignored as a change, but the watcher must stay armed.
    win.store.push_undo()
    next(s for s in win.store.systems if s.id == pid).sun_title = "Edited in Helios"
    win.store.save()
    spin(0.5)
    wait_until(lambda: _watched(win), timeout=5)

    _external_rename(path, pid, "Beta")
    wait_until(lambda: _name(win, pid) == "Beta", timeout=10)
    assert "Live update received" in win.status_label.text()
    assert next(s for s in win.store.systems if s.id == pid).sun_title == "Edited in Helios"

    # A second external edit still arrives (the watcher was re-armed after the reload too).
    spin(0.2)
    _external_rename(path, pid, "Gamma")
    wait_until(lambda: _name(win, pid) == "Gamma", timeout=10)
    assert _watched(win)


def test_every_ignored_change_rearms_the_watcher(env):
    win, path, _ = _window_with_project(env)
    key = str(path)

    # Own write: nothing is reloaded, the path is watched again.
    win.file_watcher.removePath(key)
    win.store.is_own_write = lambda: True
    win._on_external_store_changed(key)
    assert _watched(win)

    # Busy (a worker owns the store) and updating-UI branches as well.
    for attr in ("_store_busy", "_updating_ui"):
        win.file_watcher.removePath(key)
        setattr(win, attr, True)
        try:
            win._on_external_store_changed(key)
        finally:
            setattr(win, attr, False)
        assert _watched(win), attr

    # A reload that raises still re-arms it.
    win.file_watcher.removePath(key)
    win.store.is_own_write = lambda: False

    def broken_load():
        raise OSError("sharing violation")

    win.store.load = broken_load
    win._on_external_store_changed(key)
    assert "sharing violation" in win.status_label.text()
    assert _watched(win)
