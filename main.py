"""Helios — Interactive Architecture & AI Project Graph.

To launch the application:
    python main.py

`python main.py --smoke-test` (or `Helios.exe --smoke-test`) opens the main window,
runs the event loop for a few seconds and quits with exit code 0, or 1 if the bundled
icon is missing. The build checks use it to prove a frozen build starts.
"""

from __future__ import annotations

import sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from core import paths as app_paths
from ui.main_window import HeliosMainWindow


SMOKE_TEST_FLAG = "--smoke-test"
SMOKE_TEST_MS = 3000


def _smoke_report(text: str) -> None:
    # A windowed exe on Windows has no console (sys.stdout is None).
    stream = sys.stdout or sys.stderr
    if stream is not None:
        print(text, file=stream, flush=True)


def main() -> None:
    smoke_test = SMOKE_TEST_FLAG in sys.argv[1:]
    app = QApplication([a for a in sys.argv if a != SMOKE_TEST_FLAG])
    app.setApplicationName("Helios")

    icon_path = app_paths.resource_path("assets", "helios.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = HeliosMainWindow()
    window.show()
    window.setWindowState(
        (window.windowState() & ~Qt.WindowState.WindowMinimized)
        | Qt.WindowState.WindowActive
    )
    window.raise_()
    window.activateWindow()
    if smoke_test:
        icon_ok = icon_path.exists()
        _smoke_report(
            f"Helios smoke test: frozen={app_paths.is_frozen()} data={window.store.storage_path} "
            f"projects={app_paths.projects_dir()} icon={'found' if icon_ok else 'MISSING'}"
        )
        QTimer.singleShot(SMOKE_TEST_MS, lambda: app.exit(0 if icon_ok else 1))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
