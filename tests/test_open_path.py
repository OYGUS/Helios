"""Opening a file or folder in the system file manager: one helper for every OS.

`ui.window.open_path.open_in_file_manager` goes through `QDesktopServices.openUrl` (no
`os.startfile` / `xdg-open` / `open` subprocess); the window and the settings dialog keep
their status text on success and their warning when the system could not open the path.
"""

from __future__ import annotations

import pytest

from tests.test_ui_smoke import env, hard_timeout, qapp, spin  # noqa: F401

from core.models import SolarSystem  # noqa: E402
from ui.window import open_path  # noqa: E402


class FakeDesktop:
    def __init__(self, result: bool = True):
        self.result = result
        self.opened = []

    def openUrl(self, url):  # noqa: N802 (Qt name)
        self.opened.append(url)
        return self.result


@pytest.fixture
def desktop(monkeypatch):
    fake = FakeDesktop()
    monkeypatch.setattr(open_path, "QDesktopServices", fake)
    return fake


def _opened_paths(fake):
    return [u.toLocalFile() for u in fake.opened]


def test_helper_opens_a_local_file_url(desktop, tmp_path):
    open_path.open_in_file_manager(tmp_path)
    assert len(desktop.opened) == 1
    url = desktop.opened[0]
    assert url.isLocalFile()
    assert url.toLocalFile() == tmp_path.as_posix()

    desktop.opened.clear()
    open_path.open_in_file_manager(str(tmp_path / "with space"))
    assert desktop.opened[0].toLocalFile() == (tmp_path / "with space").as_posix()


def test_helper_raises_when_the_system_cannot_open(desktop, tmp_path):
    desktop.result = False
    with pytest.raises(OSError, match="could not open"):
        open_path.open_in_file_manager(tmp_path)


def test_window_projects_folder_success_and_failure(env, desktop):
    projects = env["tmp"] / "projects"
    projects.mkdir()
    win = env["make_window"](onboarded=True)
    win.show()
    spin(0.1)

    win._open_helios_projects_folder()
    assert _opened_paths(desktop) == [projects.as_posix()]
    assert "Opened 'projects/' directory" in win.status_label.text()
    assert not env["boxes"]["warning"].calls

    desktop.result = False
    win._open_helios_projects_folder()
    calls = env["boxes"]["warning"].calls
    assert len(calls) == 1 and calls[0][1] == "Could not open folder"


def test_window_open_node_path_uses_the_helper(env, desktop):
    folder = env["tmp"] / "workspace"
    folder.mkdir()
    win = env["make_window"](onboarded=True)
    win.show()
    spin(0.1)
    system = SolarSystem(name="P", project_name="P", source="codebase", workspace_path=str(folder))

    win._open_node_path(system)
    assert _opened_paths(desktop) == [folder.as_posix()]
    assert win.status_label.text() == f"Opened: {folder}"

    # A missing file opens its existing parent folder.
    desktop.opened.clear()
    system.workspace_path = str(folder / "gone.py")
    win._open_node_path(system)
    assert _opened_paths(desktop) == [folder.as_posix()]

    desktop.result = False
    win._open_node_path(system)
    calls = env["boxes"]["warning"].calls
    assert len(calls) == 1 and calls[0][1] == "Could not open"


def test_settings_dialog_projects_folder_uses_the_helper(env, desktop):
    from ui.main_window import HeliosSettingsDialog

    projects = env["tmp"] / "projects"
    projects.mkdir()
    win = env["make_window"](onboarded=True)
    dlg = HeliosSettingsDialog(win.store, win)
    try:
        dlg._open_helios_projects_folder()
        assert _opened_paths(desktop) == [projects.as_posix()]
        assert not env["boxes"]["warning"].calls

        desktop.result = False
        dlg._open_helios_projects_folder()
        calls = env["boxes"]["warning"].calls
        assert len(calls) == 1 and calls[0][1] == "Could not open folder"
    finally:
        dlg.deleteLater()
