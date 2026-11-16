"""Where Helios finds its bundled files and where it keeps user data.

Every path Helios needs comes from here; nothing else derives data paths from
``__file__``. Two modes:

* **From source** (``python main.py``): bundled files and user data both live in
  the repository checkout, exactly as before (``<repo>/data``, ``<repo>/projects``).
* **Frozen** (the PyInstaller ``Helios.exe``): bundled files (``assets/``) are read
  from the bundle (``sys._MEIPASS``); user data lives in a per-user folder,
  ``%APPDATA%\\Helios`` on Windows (``~/Library/Application Support/Helios`` on
  macOS, ``$XDG_DATA_HOME/Helios`` or ``~/.local/share/Helios`` on Linux).

In both modes the environment variable ``HELIOS_DATA_DIR`` moves the user data
root (it then holds ``data/`` and ``projects/``). Every function reads the current
state on each call, so tests can monkeypatch ``sys.frozen`` and the environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "Helios"
DATA_DIR_ENV = "HELIOS_DATA_DIR"

# The repository checkout (this file is <repo>/core/paths.py). Only meaningful from source.
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """True when running as a PyInstaller build (``Helios.exe``)."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Folder with the read-only files shipped with Helios (``assets/`` etc.)."""
    if is_frozen():
        bundle = getattr(sys, "_MEIPASS", None)
        return Path(bundle) if bundle else Path(sys.executable).resolve().parent
    return _SOURCE_ROOT


def resource_path(*parts: str) -> Path:
    """A bundled read-only file, e.g. ``resource_path("assets", "helios.ico")``."""
    return resource_dir().joinpath(*parts)


def source_dir() -> Optional[Path]:
    """Helios's own source checkout, or None in a frozen build (there is no source)."""
    return None if is_frozen() else _SOURCE_ROOT


def default_user_root() -> Path:
    """The per-user data folder a frozen build uses when ``HELIOS_DATA_DIR`` is not set."""
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "")
        base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "share"
    return base / APP_NAME


def user_root() -> Path:
    """Root of the writable user data: holds ``data/`` and ``projects/``."""
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if is_frozen():
        return default_user_root()
    return _SOURCE_ROOT


def data_dir() -> Path:
    """Folder with ``galaxy.json``, ``settings.json`` and backups (not created here)."""
    return user_root() / "data"


def galaxy_path() -> Path:
    """The default project file, ``data/galaxy.json``."""
    return data_dir() / "galaxy.json"


def projects_dir() -> Path:
    """The ``projects/`` folder users copy repositories into (not created here)."""
    return user_root() / "projects"
