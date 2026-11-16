"""`EmptyStateOverlay`: the short hint with the primary action shown instead of a blank canvas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout

from ui import theme

if TYPE_CHECKING:  # annotations only: a runtime import would be an import cycle
    from ui.canvas.view import SpaceCanvasView


# ----------------------------------------------------------------------------
# Empty-state overlay
# ----------------------------------------------------------------------------
class EmptyStateOverlay(QFrame):
    """Short friendly hint with the primary action, shown instead of a blank canvas."""

    def __init__(self, canvas: "SpaceCanvasView") -> None:
        super().__init__(canvas)
        self.setObjectName("EmptyState")
        self.canvas = canvas
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(8)
        self.lbl_title = theme.label("", "heading")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_text = theme.label("", "secondary", wrap=True)
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_text)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.btn_primary = QPushButton("")
        self.btn_primary.setObjectName("PrimaryBtn")
        self.btn_primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_secondary = QPushButton("")
        self.btn_secondary.setObjectName("SecondaryBtn")
        self.btn_secondary.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(self.btn_secondary)
        row.addWidget(self.btn_primary)
        row.addStretch(1)
        lay.addSpacing(6)
        lay.addLayout(row)
        self.btn_primary.clicked.connect(lambda: self._run(self._primary_action))
        self.btn_secondary.clicked.connect(lambda: self._run(self._secondary_action))
        self._primary_action = ""
        self._secondary_action = ""
        self.mode = ""
        self.setFixedWidth(420)
        self.hide()

    def _run(self, action: str) -> None:
        if action == "add_module_tool":
            self.canvas.set_drawing_tool("add_planet")
        elif action:
            self.canvas.contextActionRequested.emit(action, None)

    def show_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "no_projects":
            self.lbl_title.setText("No projects yet")
            self.lbl_text.setText("Pick a folder with code and Helios draws its map. "
                                  "You can also sketch a project by hand.")
            self.btn_primary.setText("Analyze a folder…")
            self._primary_action = "analyze_folder"
            self.btn_secondary.setText("Design a project…")
            self._secondary_action = "create_new_project"
        else:
            self.lbl_title.setText("This project has no modules yet")
            self.lbl_text.setText("Add a module by hand, or analyze a folder to build the map from code.")
            self.btn_primary.setText("Add a module")
            self._primary_action = "add_module_tool"
            self.btn_secondary.setText("Analyze a folder…")
            self._secondary_action = "analyze_folder"
        self.adjustSize()
        self.show()
        self.raise_()
