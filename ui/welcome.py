"""The welcome panel shown on the canvas while Helios has no projects at all.

It says in one sentence what Helios does and offers the concrete ways to add a first
project, each with a button that runs the existing action through the canvas's
``contextActionRequested`` signal (the main window handles it):

* ``analyze_folder`` — "Analyze a folder…" (pick any folder),
* ``scan_helios_projects`` / ``open_projects_folder`` — the ``projects/`` folder
  (its real location comes from ``core.paths.projects_dir``),
* ``open_integrations`` — the Integrations page, to import from AI coding tools,
* ``create_new_project`` — design a project by hand.

The canvas shows it when the store is empty and hides it as soon as a project exists.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core import paths as app_paths
from ui import theme
from ui.inspector import ElidedLabel

WELCOME_TITLE = "Welcome to Helios"
WELCOME_TEXT = ("Helios reads the code in a folder and draws a map of its parts "
                "and how they connect.")
# Below this panel width each step stacks its button under the text.
NARROW_WIDTH = 480


class _Step(QFrame):
    """One way to add a project: number, title, explanation, one button."""

    def __init__(self, number: int, title: str, text: str, button: QPushButton,
                 extra: Optional[QWidget] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomeStep")
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(theme.SPACE_M, theme.SPACE_M - 2, theme.SPACE_M, theme.SPACE_M - 2)
        self.box.setSpacing(theme.SPACE_M)
        num = QLabel(str(number))
        num.setObjectName("WelcomeNumber")
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setFixedSize(22, 22)
        self.num = num
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(theme.SPACE_S)
        head.addWidget(num, 0, Qt.AlignmentFlag.AlignTop)
        self.lbl_title = theme.label(title, "body")
        self.lbl_title.setObjectName("WelcomeStepTitle")
        head.addWidget(self.lbl_title, 1)
        col.addLayout(head)
        self.lbl_text = theme.label(text, "caption", wrap=True)
        self.lbl_text.setContentsMargins(22 + theme.SPACE_S, 0, 0, 0)
        col.addWidget(self.lbl_text)
        if extra is not None:
            extra.setContentsMargins(22 + theme.SPACE_S, 0, 0, 0)
            col.addWidget(extra)
        self.box.addLayout(col, 1)
        self.button = button
        # The button sits right of the text, or under it (indented like the text) when narrow.
        self.button_holder = QWidget()
        self.button_holder.setObjectName("Transparent")
        hl = QHBoxLayout(self.button_holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(button)
        hl.addStretch(1)
        self._button_layout = hl
        self.box.addWidget(self.button_holder, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_narrow(self, narrow: bool) -> None:
        self.box.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.box.setSpacing(theme.SPACE_S if narrow else theme.SPACE_M)
        self._button_layout.setContentsMargins(22 + theme.SPACE_S if narrow else 0, 0, 0, 0)


class WelcomePanel(QFrame):
    """Friendly first-run panel with the ways to add a first project."""

    def __init__(self, run_action: Callable[[str], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WelcomePanel")
        self._run_action = run_action
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE_XL, theme.SPACE_XL - 4, theme.SPACE_XL, theme.SPACE_L)
        lay.setSpacing(theme.SPACE_S)

        self.lbl_title = theme.label(WELCOME_TITLE, "title")
        lay.addWidget(self.lbl_title)
        self.lbl_text = theme.label(WELCOME_TEXT, "secondary", wrap=True)
        lay.addWidget(self.lbl_text)
        lay.addSpacing(theme.SPACE_S)
        lay.addWidget(theme.label("Add your first project", "section"))

        self.btn_analyze = self._button("Analyze a folder…", "analyze_folder", primary=True)
        self.btn_scan = self._button("Scan the folder", "scan_helios_projects")
        self.btn_ai = self._button("Choose AI tools…", "open_integrations")

        path_row = QWidget()
        path_row.setObjectName("Transparent")
        pr = QHBoxLayout(path_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(theme.SPACE_S)
        self.lbl_projects_dir = ElidedLabel("", mode=Qt.TextElideMode.ElideMiddle)
        theme.set_role(self.lbl_projects_dir, "mono")
        self.lbl_projects_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pr.addWidget(self.lbl_projects_dir, 1)
        self.btn_open_dir = QPushButton("Open")
        self.btn_open_dir.setObjectName("LinkBtn")
        self.btn_open_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_dir.setToolTip("Open this folder in your file manager")
        self.btn_open_dir.clicked.connect(lambda: self._run_action("open_projects_folder"))
        pr.addWidget(self.btn_open_dir, 0)

        self.steps: List[_Step] = [
            _Step(1, "Map one folder", "Pick any folder with code on this computer.", self.btn_analyze),
            _Step(2, "Use the projects folder",
                  "Put your project folders here, then scan them all:", self.btn_scan, path_row),
            _Step(3, "Import from AI coding tools",
                  "Maps the folders you worked in with Antigravity, Codex or Claude Code.", self.btn_ai),
        ]
        for step in self.steps:
            lay.addWidget(step)

        lay.addSpacing(2)
        self.btn_design = QPushButton("Or design a project by hand…")
        self.btn_design.setObjectName("LinkBtn")
        self.btn_design.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_design.clicked.connect(lambda: self._run_action("create_new_project"))
        lay.addWidget(self.btn_design, 0, Qt.AlignmentFlag.AlignLeft)
        self._narrow: Optional[bool] = None
        self.refresh_paths()
        self.hide()

    def _button(self, text: str, action: str, primary: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("PrimaryBtn" if primary else "SecondaryBtn")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(140)
        btn.clicked.connect(lambda: self._run_action(action))
        return btn

    def projects_dir_text(self) -> str:
        return str(app_paths.projects_dir())

    def refresh_paths(self) -> None:
        self.lbl_projects_dir.setText(self.projects_dir_text())
        self.lbl_projects_dir.setToolTip(self.projects_dir_text())

    def set_narrow(self, narrow: bool) -> None:
        if narrow == self._narrow:
            return
        self._narrow = narrow
        for step in self.steps:
            step.set_narrow(narrow)

    def place(self, area_w: int, area_h: int, left_reserved: int = 0) -> None:
        """Sizes the panel for the canvas viewport and centres it."""
        avail = max(260, area_w - left_reserved - 2 * theme.SPACE_L)
        width = min(560, avail)
        self.set_narrow(width < NARROW_WIDTH)
        self.setFixedWidth(width)
        self.setMinimumHeight(0)
        self.adjustSize()
        h = self.heightForWidth(width) if self.hasHeightForWidth() else self.sizeHint().height()
        h = max(h, self.minimumSizeHint().height())
        self.resize(width, h)
        x = left_reserved + max(theme.SPACE_L, (area_w - left_reserved - width) // 2)
        y = max(theme.SPACE_L, (area_h - h) // 2)
        self.move(x, y)
