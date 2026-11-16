"""`NewProjectStudioDialog`: name, starting template, purpose and stack of a designed project."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui import theme


class NewProjectStudioDialog(QDialog):
    """Wizard for designing a new project from scratch or from a built-in template."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New project — Helios")
        self.setMinimumSize(520, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(6)

        layout.addWidget(theme.label("Design a new project", "title"))
        sub = theme.label(
            "Give it a name and pick a starting layout. You can add modules, components and links "
            "with the drawing tools afterwards.",
            "secondary",
            wrap=True,
        )
        layout.addWidget(sub)
        layout.addSpacing(10)

        layout.addWidget(theme.label("Name", "caption"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("e.g. Analytics engine")
        layout.addWidget(self.edit_name)
        layout.addSpacing(6)

        layout.addWidget(theme.label("Starting layout", "caption"))
        self.combo_template = QComboBox()
        self.combo_template.addItem("Blank canvas", "blank")
        self.combo_template.addItem("Full-stack app (UI → API → database)", "fullstack")
        self.combo_template.addItem("AI agent (input → LLM → tools → memory)", "ai_agent")
        self.combo_template.addItem("Data pipeline (ingest → process → output)", "data_pipeline")
        layout.addWidget(self.combo_template)
        layout.addSpacing(6)

        layout.addWidget(theme.label("What it does", "caption"))
        self.edit_purpose = QTextEdit()
        self.edit_purpose.setPlaceholderText("The problem it solves and how it is put together…")
        self.edit_purpose.setMinimumHeight(90)
        layout.addWidget(self.edit_purpose)
        layout.addSpacing(6)

        layout.addWidget(theme.label("Tech stack (optional)", "caption"))
        self.edit_stack = QLineEdit()
        self.edit_stack.setPlaceholderText("e.g. Python, FastAPI, PostgreSQL")
        layout.addWidget(self.edit_stack)

        layout.addStretch(1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_create = QPushButton("Create project")
        btn_create.setObjectName("PrimaryBtn")
        btn_create.setDefault(True)
        btn_create.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_create)
        layout.addLayout(btn_row)
