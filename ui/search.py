"""Search box behaviour: a debounced query, a compact results list, keyboard navigation.

:class:`SearchController` sits between the top bar's search field and the window:

* typing is debounced (`DEBOUNCE_MS`); then the window's existing filter callback runs
  once (dim the canvas, filter the project tree) and the results list is refreshed;
* the results come from a lightweight :class:`core.search_index.SearchIndex` built from
  the store only when it is needed and thrown away when the map changes
  (:meth:`SearchController.invalidate`) — the scene is never rebuilt while typing;
* the list (:class:`SearchPopup`) is a child of the main window placed under the field, so
  the field keeps the keyboard: Up / Down move, Enter opens, Esc closes the list.
  At most `MAX_RESULTS` rows are shown, then a "+N more" line.

Choosing a result calls ``on_choose(entry)``; the window opens the project, centres and
selects the node (a function or file hit leads to the component or module containing it).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from core.search_index import SearchEntry, SearchIndex
from ui import theme

DEBOUNCE_MS = 140
MAX_RESULTS = 8
ROW_H = 34
KIND_COL_W = 78
ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
INFO_ROLE = Qt.ItemDataRole.UserRole + 2


class _ResultDelegate(QStyledItemDelegate):
    """Row: small type label · name · where (muted, elided)."""

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), ROW_H if index.data(ENTRY_ROLE) is not None else ROW_H - 8)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 1, -4, -1)
        entry: Optional[SearchEntry] = index.data(ENTRY_ROLE)
        if entry is None:
            painter.setFont(theme.font(theme.FS_SMALL))
            painter.setPen(theme.qcolor(theme.MUTED))
            painter.drawText(r.adjusted(10, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             str(index.data(INFO_ROLE) or ""))
            painter.restore()
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(theme.HOVER if not selected else theme.PRESSED))
            painter.drawRoundedRect(r, theme.RADIUS_M, theme.RADIUS_M)
        x = r.left() + 10
        # type label
        f_kind = theme.font(theme.FS_CAPTION, QFont.Weight.DemiBold)
        painter.setFont(f_kind)
        painter.setPen(theme.qcolor(theme.ACCENT_TEXT if entry.kind in ("project", "entry") else theme.TEXT_2))
        kind_rect = QRect(x, r.top(), KIND_COL_W - 6, r.height())
        painter.drawText(kind_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(f_kind).elidedText(entry.kind_label, Qt.TextElideMode.ElideRight, kind_rect.width()))
        x += KIND_COL_W
        avail = r.right() - 10 - x
        f_name = theme.font(theme.FS_BODY)
        fm_name = QFontMetrics(f_name)
        name = fm_name.elidedText(entry.name, Qt.TextElideMode.ElideMiddle, int(avail * 0.62) if entry.where else avail)
        painter.setFont(f_name)
        painter.setPen(theme.qcolor(theme.TEXT))
        name_w = fm_name.horizontalAdvance(name)
        painter.drawText(QRect(x, r.top(), name_w + 2, r.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        if entry.where:
            f_where = theme.font(theme.FS_SMALL)
            painter.setFont(f_where)
            painter.setPen(theme.qcolor(theme.MUTED))
            wx = x + name_w + 10
            ww = r.right() - 10 - wx
            if ww > 30:
                where = QFontMetrics(f_where).elidedText(entry.where, Qt.TextElideMode.ElideLeft, ww)
                painter.drawText(QRect(wx, r.top(), ww, r.height()),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, where)
        painter.restore()


class SearchPopup(QFrame):
    """The compact results list under the search field."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("SearchPopup")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        self.list = QListWidget()
        self.list.setObjectName("SearchResults")
        self.list.setItemDelegate(_ResultDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(False)
        self.list.viewport().setAutoFillBackground(False)
        lay.addWidget(self.list)
        self.entries: List[SearchEntry] = []
        self.hide()

    def set_results(self, entries: Sequence[SearchEntry], total: int, query: str) -> None:
        self.list.clear()
        self.entries = list(entries)
        for e in self.entries:
            item = QListWidgetItem()
            item.setData(ENTRY_ROLE, e)
            item.setToolTip(f"{e.kind_label}: {e.name}" + (f"\n{e.where}" if e.where else ""))
            item.setSizeHint(QSize(10, ROW_H))
            self.list.addItem(item)
        info = ""
        if not self.entries:
            info = f"Nothing on the map matches “{query}”"
        elif total > len(self.entries):
            info = f"+{total - len(self.entries)} more — type more to narrow it down"
        if info:
            item = QListWidgetItem()
            item.setData(INFO_ROLE, info)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(10, ROW_H - 8))
            self.list.addItem(item)
        if self.entries:
            self.list.setCurrentRow(0)
        h = sum(self.list.item(i).sizeHint().height() for i in range(self.list.count()))
        self.list.setFixedHeight(h + 2)
        self.adjustSize()

    def info_text(self) -> str:
        for i in range(self.list.count()):
            v = self.list.item(i).data(INFO_ROLE)
            if v:
                return str(v)
        return ""

    def current_entry(self) -> Optional[SearchEntry]:
        row = self.list.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def move_selection(self, step: int) -> None:
        if not self.entries:
            return
        row = self.list.currentRow()
        row = (row + step) % len(self.entries) if row >= 0 else 0
        self.list.setCurrentRow(row)


class SearchController(QObject):
    """Connects the search field, the index, the results list and the window."""

    def __init__(
        self,
        field: QLineEdit,
        host: QWidget,
        get_systems: Callable[[], Sequence[object]],
        get_active_id: Callable[[], str],
        on_query: Callable[[str], None],
        on_choose: Callable[[SearchEntry], None],
        identity_of: Optional[Callable[[object], str]] = None,
    ) -> None:
        super().__init__(host)
        self.field = field
        self.host = host
        self._get_systems = get_systems
        self._get_active_id = get_active_id
        self._on_query = on_query
        self._on_choose = on_choose
        self._identity_of = identity_of
        self._index: Optional[SearchIndex] = None
        # Entries of each project by its data fingerprint: a rebuild after an edit only
        # re-reads the projects whose stored data changed (see `core.search_index`).
        self._entry_cache: Dict[str, List[SearchEntry]] = {}
        self.index_builds = 0  # for tests: how often the index was built
        self.popup = SearchPopup(host)
        self.popup.list.itemClicked.connect(self._on_item_clicked)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.flush)
        field.textChanged.connect(self._on_text_changed)
        field.installEventFilter(self)

    # --- index -------------------------------------------------------------
    def invalidate(self) -> None:
        """The map changed: the next search builds a fresh index."""
        self._index = None

    def index(self) -> SearchIndex:
        if self._index is None:
            self._index = SearchIndex(list(self._get_systems()), self._identity_of, self._entry_cache)
            self.index_builds += 1
        return self._index

    # --- typing ------------------------------------------------------------
    def _on_text_changed(self, text: str) -> None:
        if not text.strip():
            self._timer.stop()
            self.flush()
            return
        self._timer.start()

    def flush(self) -> None:
        """Runs the pending query now (also used by tests)."""
        self._timer.stop()
        text = self.field.text()
        self._on_query(text)
        if not text.strip():
            self.hide_popup()
            return
        entries, total = self.index().search(text, MAX_RESULTS, self._get_active_id() or "")
        self.popup.set_results(entries, total, text.strip())
        if self.field.hasFocus() or not self.popup.isVisible():
            self.show_popup()

    def show_popup(self) -> None:
        self.position_popup()
        self.popup.show()
        self.popup.raise_()

    def hide_popup(self) -> None:
        self.popup.hide()

    def position_popup(self) -> None:
        f = self.field
        top_left = f.mapTo(self.host, QPoint(0, f.height() + 4))
        width = max(f.width(), 380)
        x = min(top_left.x(), max(4, self.host.width() - width - 8))
        self.popup.setFixedWidth(width)
        self.popup.adjustSize()
        self.popup.move(x, top_left.y())

    # --- choosing -----------------------------------------------------------
    def choose(self, entry: Optional[SearchEntry] = None) -> bool:
        entry = entry or self.popup.current_entry()
        if entry is None:
            return False
        self.hide_popup()
        self._on_choose(entry)
        return True

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(ENTRY_ROLE)
        if entry is not None:
            self.choose(entry)

    def handle_escape(self) -> bool:
        """Esc: closes the list first; True when it did something."""
        if self.popup.isVisible():
            self.hide_popup()
            return True
        return False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.field:
            et = event.type()
            if et == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                key = event.key()
                if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                    if not self.popup.isVisible() and self.field.text().strip():
                        self.flush()
                        self.show_popup()
                    else:
                        self.popup.move_selection(1 if key == Qt.Key.Key_Down else -1)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if self._timer.isActive():
                        self.flush()
                    return self.choose()
            elif et == QEvent.Type.FocusOut:
                # A click in the list keeps the field's focus (the list takes none); anything
                # else closes the list a moment later so a click on a row still lands.
                QTimer.singleShot(150, self._hide_if_unfocused)
            elif et == QEvent.Type.FocusIn and self.field.text().strip() and not self._timer.isActive():
                QTimer.singleShot(0, self._reshow)
        return super().eventFilter(obj, event)

    def _hide_if_unfocused(self) -> None:
        if not self.field.hasFocus():
            self.hide_popup()

    def _reshow(self) -> None:
        if self.field.hasFocus() and self.field.text().strip() and self.popup.list.count():
            self.show_popup()
