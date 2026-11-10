"""Helios — Interactive Architecture & AI Project Graph.

To launch the application:
    python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from ui.main_window import HeliosMainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Helios")

    icon_path = Path(__file__).resolve().parent / "assets" / "helios.ico"
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
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
