"""Tests for core.paths: source mode unchanged, frozen (PyInstaller) mode, HELIOS_DATA_DIR."""

import json
import sys
from pathlib import Path

import pytest

from core import paths
from core.models import GalaxyStore
from connectors.codebase_importer import CodebaseImporter
from ui import workers

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def source_mode(monkeypatch):
    monkeypatch.delenv(paths.DATA_DIR_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Simulates a PyInstaller build whose user data goes to tmp_path/"user"."""
    bundle = tmp_path / "bundle"
    (bundle / "assets").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "user"))
    return bundle


def test_source_mode_paths_are_the_repo_checkout(source_mode):
    assert not paths.is_frozen()
    assert paths.resource_dir() == REPO_ROOT
    assert paths.source_dir() == REPO_ROOT
    assert paths.user_root() == REPO_ROOT
    assert paths.data_dir() == REPO_ROOT / "data"
    assert paths.galaxy_path() == REPO_ROOT / "data" / "galaxy.json"
    assert paths.projects_dir() == REPO_ROOT / "projects"
    assert paths.resource_path("assets", "helios.ico").is_file()


def test_source_mode_workers_map_the_repo(source_mode):
    # Module-level constant was computed at import time in source mode.
    assert workers.HELIOS_ROOT == REPO_ROOT


def test_data_dir_override_in_source_mode(source_mode, monkeypatch, tmp_path):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom" / "data"
    assert paths.projects_dir() == tmp_path / "custom" / "projects"
    # Bundled files and the source checkout do not move.
    assert paths.resource_dir() == REPO_ROOT
    assert paths.source_dir() == REPO_ROOT


def test_blank_override_is_ignored(source_mode, monkeypatch):
    monkeypatch.setenv(paths.DATA_DIR_ENV, "   ")
    assert paths.data_dir() == REPO_ROOT / "data"


def test_frozen_mode_resources_and_data(frozen, tmp_path):
    assert paths.is_frozen()
    assert paths.resource_dir() == frozen
    assert paths.resource_path("assets", "helios.ico") == frozen / "assets" / "helios.ico"
    assert paths.source_dir() is None
    assert paths.data_dir() == tmp_path / "user" / "data"
    assert paths.projects_dir() == tmp_path / "user" / "projects"


def test_frozen_default_user_root_without_override(frozen, monkeypatch, tmp_path):
    monkeypatch.delenv(paths.DATA_DIR_ENV)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    root = paths.user_root()
    assert root == paths.default_user_root()
    assert root.name == "Helios"
    assert REPO_ROOT not in root.parents
    assert paths.data_dir() == root / "data"


@pytest.mark.parametrize(
    "platform, env, expected",
    [
        ("win32", {"APPDATA": "APPDATA_DIR"}, ("APPDATA_DIR", "Helios")),
        ("win32", {}, ("HOME", "AppData", "Roaming", "Helios")),
        ("darwin", {}, ("HOME", "Library", "Application Support", "Helios")),
        ("linux", {"XDG_DATA_HOME": "XDG_DIR"}, ("XDG_DIR", "Helios")),
        ("linux", {}, ("HOME", ".local", "share", "Helios")),
        ("linux", {"XDG_DATA_HOME": "relative/dir"}, ("HOME", ".local", "share", "Helios")),
    ],
)
def test_default_user_root_per_platform(monkeypatch, tmp_path, platform, env, expected):
    home = tmp_path / "home"
    names = {"HOME": home, "APPDATA_DIR": tmp_path / "appdata", "XDG_DIR": tmp_path / "xdg"}
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for key in ("APPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(names.get(value, value)))
    first, *rest = expected
    assert paths.default_user_root() == names[first].joinpath(*rest)


def test_frozen_first_run_starts_with_an_empty_store(frozen, tmp_path):
    store = GalaxyStore()
    assert store.file_path == tmp_path / "user" / "data" / "galaxy.json"
    assert store.settings_path == tmp_path / "user" / "data" / "settings.json"
    assert store.systems == []
    assert store.load_error is None
    data = json.loads(store.file_path.read_text(encoding="utf-8"))
    assert data["systems"] == []


def test_source_first_run_still_creates_the_starter(source_mode, tmp_path):
    store = GalaxyStore(tmp_path / "galaxy.json")
    assert len(store.systems) == 1
    assert store.systems[0].workspace_path == str(REPO_ROOT)


def test_frozen_projects_dir_is_in_user_data(frozen, tmp_path):
    proj = CodebaseImporter.get_helios_projects_dir()
    assert proj == tmp_path / "user" / "projects"
    assert proj.is_dir()


def test_frozen_sync_worker_skips_helios_self_analysis(frozen, monkeypatch, tmp_path):
    store = GalaxyStore(tmp_path / "g.json")
    called = []
    monkeypatch.setattr(CodebaseImporter, "analyze_directory", staticmethod(lambda *a, **k: called.append(a)))
    worker = workers.AgentSyncWorker(store, [], analyze_helios=True)
    worker.helios_dir = None  # HELIOS_ROOT is None in a frozen build (computed at import here)
    ok, _msg, payload = worker.work()
    assert ok and payload["helios_system"] is None
    assert called == []
