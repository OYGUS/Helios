"""`OverflowRow`: a row of toolbar buttons that never forces the window wider.

The top bar holds a fixed set of text buttons. With wide fonts (a large Windows text-scale
setting, a 125 % / 150 % display, a fallback font) their natural widths alone can exceed a
1366 px screen, and a plain ``QHBoxLayout`` would then raise the window's minimum width.
This row asks only for the width of its "More" button as its minimum: buttons that do not
fit are hidden (the least important first) and offered in the "More" menu instead, with
the same text, check state, enabled state and tooltip. At normal sizes every button is
shown and "More" is hidden, so the bar looks exactly as before.

Hidden buttons stay alive and keep their signals: a menu entry simply calls the button's
``click()``. `anchor_for` gives the widget to open a popup under (the button itself while
it is shown, otherwise "More").
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLayout, QMenu, QPushButton, QSizePolicy, QWidget


class _Item:
    __slots__ = ("widget", "priority", "separator")

    def __init__(self, widget: QWidget, priority: int, separator: bool) -> None:
        self.widget = widget
        self.priority = priority
        self.separator = separator


class OverflowRow(QWidget):
    """Buttons in a row; those that do not fit move into a "More" menu."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 4,
                 more_text: str = "More", more_object_name: str = "QuietBtn") -> None:
        super().__init__(parent)
        self._items: List[_Item] = []
        self._spacing = spacing
        self._reflowing = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        # The row decides its own minimum (see minimumSizeHint); the layout must not pin it
        # to the width of whatever is currently shown.
        lay.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.btn_more = QPushButton(more_text)
        self.btn_more.setObjectName(more_object_name)
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.setToolTip("More actions (the window is too narrow to show them all)")
        self.btn_more.clicked.connect(self._show_menu)
        self.btn_more.setVisible(False)
        lay.addWidget(self.btn_more)
        self.menu = QMenu(self)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------------ building
    def add_button(self, button: QPushButton, priority: int = 0) -> QPushButton:
        """Adds ``button``; when space runs out, lower ``priority`` values are hidden first."""
        self._insert(_Item(button, priority, separator=False))
        return button

    def add_separator(self, widget: QWidget) -> QWidget:
        """Adds a divider, shown only while a button is shown on both sides of it."""
        self._insert(_Item(widget, 0, separator=True))
        return widget

    def _insert(self, item: _Item) -> None:
        lay = self.layout()
        lay.insertWidget(lay.count() - 1, item.widget)
        self._items.append(item)
        item.widget.installEventFilter(self)
        self.updateGeometry()
        self._reflow()

    # ------------------------------------------------------------------ queries
    def hidden_buttons(self) -> List[QPushButton]:
        """Buttons currently moved into the "More" menu, in row order."""
        return [it.widget for it in self._items if not it.separator and it.widget.isHidden()]

    def anchor_for(self, button: QWidget) -> QWidget:
        """The widget a popup for ``button`` should open under."""
        return self.btn_more if button in self.hidden_buttons() else button

    # ------------------------------------------------------------------ sizing
    def _natural_width(self, widgets: List[QWidget]) -> int:
        if not widgets:
            return 0
        return sum(w.sizeHint().width() for w in widgets) + self._spacing * (len(widgets) - 1)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        widgets = [it.widget for it in self._items]
        h = max([w.sizeHint().height() for w in widgets + [self.btn_more]] or [0])
        return QSize(self._natural_width(widgets), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(self.btn_more.sizeHint().width(), self.btn_more.sizeHint().height())

    def _reflow(self) -> None:
        if self._reflowing:
            return
        self._reflowing = True
        try:
            avail = self.width()
            shown = {id(it.widget) for it in self._items}
            if self._natural_width([it.widget for it in self._items]) > avail:
                budget = avail - self.btn_more.sizeHint().width() - self._spacing
                buttons = [it for it in self._items if not it.separator]
                for it in sorted(buttons, key=lambda i: i.priority):
                    kept = [i.widget for i in self._items if id(i.widget) in shown and not i.separator]
                    if self._natural_width(kept) <= budget:
                        break
                    shown.discard(id(it.widget))
            # A divider needs a shown button on each side.
            for idx, it in enumerate(self._items):
                if not it.separator:
                    continue
                before = any(id(i.widget) in shown and not i.separator for i in self._items[:idx])
                after = any(id(i.widget) in shown and not i.separator for i in self._items[idx + 1:])
                if not (before and after):
                    shown.discard(id(it.widget))
            for it in self._items:
                want = id(it.widget) in shown
                if it.widget.isHidden() == want:
                    it.widget.setVisible(want)
            any_hidden = any(not it.separator and id(it.widget) not in shown for it in self._items)
            if self.btn_more.isHidden() == any_hidden:
                self.btn_more.setVisible(any_hidden)
        finally:
            self._reflowing = False

    # ------------------------------------------------------------------ events
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._reflow()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        # A button's text or font changed: its natural width may have changed.
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange,
                            QEvent.Type.LayoutRequest, QEvent.Type.DynamicPropertyChange):
            if not self._reflowing:
                self.updateGeometry()
                self._reflow()
        return super().eventFilter(obj, event)

    def event(self, event) -> bool:  # noqa: D401
        if event.type() == QEvent.Type.LayoutRequest and not self._reflowing:
            self._reflow()
        return super().event(event)

    # ------------------------------------------------------------------ menu
    def _show_menu(self) -> None:
        self.menu.clear()
        for btn in self.hidden_buttons():
            act = self.menu.addAction(btn.text())
            act.setToolTip(btn.toolTip())
            act.setEnabled(btn.isEnabled())
            if btn.isCheckable():
                act.setCheckable(True)
                act.setChecked(btn.isChecked())
            act.triggered.connect(lambda _checked=False, b=btn: b.click())
        self.menu.setToolTipsVisible(True)
        self.menu.popup(self.btn_more.mapToGlobal(QPoint(0, self.btn_more.height() + 2)))
