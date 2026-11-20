"""Open a file or folder in the system's file manager / default app, on every OS.

`QDesktopServices.openUrl` hands a ``file://`` URL to the desktop (ShellExecute on Windows,
``open`` on macOS, the portal / xdg-open on Linux): no subprocess, nothing platform-specific
here. It returns False when the desktop could not open it; that is reported as an `OSError`
so callers keep one ``except`` path for every failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def open_in_file_manager(path: Union[str, Path]) -> None:
    """Opens ``path``; raises `OSError` when the system could not open it."""
    target = str(path)
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(target)):
        raise OSError(f"The system could not open {target}")
