"""`FlowLayout`: lays widgets out left to right and wraps onto a new line when the row
is too narrow.

Used for short rows of buttons in the narrow right panel. With normal fonts every row
fits on one line and looks exactly like a ``QHBoxLayout`` with a trailing stretch; with
wide fonts (large Windows text scaling, a fallback font) the buttons wrap instead of
demanding more width than the panel has. A row wider than its panel would otherwise
break the height-for-width layout of the whole header (wrapped labels above it are then
given less height than their text needs and get cut off).

Hidden widgets take no space. The layout reports height-for-width, so a parent
``QVBoxLayout`` gives it the height of all its lines.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Left-aligned, wrapping row of widgets (no stretch items)."""

    def __init__(self, parent: Optional[QWidget] = None, h_spacing: int = 4, v_spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # ------------------------------------------------------------------ QLayout API
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 (Qt API)
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802 (Qt API)
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:  # noqa: N802 (Qt API)
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 (Qt API)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt API)
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """One line with every shown item."""
        items = self._shown()
        m = self.contentsMargins()
        if not items:
            return QSize(m.left() + m.right(), m.top() + m.bottom())
        w = sum(i.sizeHint().width() for i in items) + self._h * (len(items) - 1)
        h = max(i.sizeHint().height() for i in items)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt API)
        """The widest single item: everything else can wrap."""
        size = QSize()
        for item in self._shown():
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # ------------------------------------------------------------------ layout
    def _shown(self) -> List[QLayoutItem]:
        return [i for i in self._items if not i.isEmpty()]

    def _do_layout(self, rect: QRect, apply: bool) -> int:
        m: QMargins = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        items = self._shown()
        # Each line is laid out first so its items can be centred vertically.
        line: List[tuple] = []

        def flush() -> None:
            if apply:
                for it, px, hint in line:
                    dy = (line_h - hint.height()) // 2
                    it.setGeometry(QRect(QPoint(px, y + dy), hint))
            line.clear()

        for item in items:
            hint = item.sizeHint().boundedTo(QSize(max(area.width(), item.minimumSize().width()), 1 << 20))
            if line and x + hint.width() > area.right() + 1:
                flush()
                y += line_h + self._v
                x, line_h = area.x(), 0
            line.append((item, x, hint))
            x += hint.width() + self._h
            line_h = max(line_h, hint.height())
        flush()
        return y + line_h - rect.y() + m.bottom()


def flow_row(spacing: int = 4, margins: tuple = (0, 0, 0, 0)) -> FlowLayout:
    """A detached `FlowLayout` for ``parent_layout.addLayout(...)``."""
    lay = FlowLayout(h_spacing=spacing, v_spacing=spacing)
    lay.setContentsMargins(*margins)
    return lay

