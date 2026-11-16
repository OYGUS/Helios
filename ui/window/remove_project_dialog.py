"""`RemoveProjectDialog`: remove a project from Helios only, or also ignore its folder
(`_ChoiceButton` is its large two-line choice button)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from core.models import SolarSystem
from ui import theme


class _ChoiceButton(QPushButton):
    """A large, left-aligned choice: a title line and an explanation line."""

    def __init__(self, title: str, explanation: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChoiceBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choice_title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(1)
        t = theme.label(title, "body")
        t.setStyleSheet("font-weight: 600;")
        e = theme.label(explanation, "caption", wrap=True)
        for w in (t, e):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        self.setAccessibleName(title)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(58)

    def text(self) -> str:  # tests / accessibility read the choice title
        return self.choice_title


class RemoveProjectDialog(QDialog):
    """Asks how to remove a project: from Helios only, or also never import its folder again."""

    REMOVE = "remove"
    IGNORE = "ignore"

    def __init__(self, system: SolarSystem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.choice = ""
        self.setWindowTitle("Remove project")
        self.setMinimumWidth(460)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(10)
        name = system.project_name or system.name
        outer.addWidget(theme.label(f"Remove '{name}'?", "heading"))
        folder = (system.workspace_path or "").strip()
        if folder:
            lbl = theme.label(folder, "mono", wrap=True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        else:
            lbl = theme.label("No folder is linked to this project.", "muted", wrap=True)
        outer.addWidget(lbl)
        outer.addSpacing(4)
        self.btn_remove = _ChoiceButton(
            "Remove from Helios",
            "It may come back on the next sync if its folder is found again.",
        )
        self.btn_remove.clicked.connect(lambda: self._choose(self.REMOVE))
        outer.addWidget(self.btn_remove)
        self.btn_ignore: Optional[_ChoiceButton] = None
        if folder:
            self.btn_ignore = _ChoiceButton(
                "Remove and never import this folder again",
                "Scans skip this folder from now on. You can undo this in Settings › Ignored folders.",
            )
            self.btn_ignore.clicked.connect(lambda: self._choose(self.IGNORE))
            outer.addWidget(self.btn_ignore)
        outer.addWidget(theme.label("Ctrl+Z brings the project back.", "muted"))
        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_cancel.setDefault(True)
        row.addWidget(btn_cancel)
        outer.addLayout(row)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()

    @classmethod
    def ask(cls, system: SolarSystem, parent: Optional[QWidget] = None) -> str:
        """Shows the dialog modally; returns REMOVE, IGNORE or "" (cancelled)."""
        dlg = cls(system, parent)
        dlg.exec()
        choice = dlg.choice
        dlg.deleteLater()
        return choice
