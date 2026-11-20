"""`HistoryMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

Keyboard shortcuts (incl. Esc handling), undo / redo, and the live reload of
galaxy.json through the file watcher; the helpers that keep the view state valid when
the store's project list changes underneath it.

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _ignore_redo, _ignore_undo, _selected_node, _shortcuts, _store_busy, _updating_ui,
    canvas, central_stack, file_watcher, purpose_edit_box, search_ctl, search_input,
    status_label, store
Of those, assigned by this mixin:
    _shortcuts, _updating_ui, file_watcher
"""

from __future__ import annotations

from PySide6.QtCore import QFileSystemWatcher
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QTextEdit

from ui.window.ignored_folders import add_ignored_folder, remove_ignored_folder


class HistoryMixin:
    """Keyboard shortcuts, undo / redo and live reload of galaxy.json."""

    def _node_in_store(self, node: object) -> bool:
        for s in self.store.systems:
            if node is s:
                return True
            if any(node is x for x in s.links) or any(node is x for x in s.notes):
                return True
            for p in s.planets:
                if node is p or any(node is sat for sat in p.satellites):
                    return True
        return False

    def _drop_stale_view_state(self, all_replaced: bool = False) -> None:
        """The project list changed underneath the view (undo, redo, live reload): never keep
        a removed project open or a removed node selected. Call before rebuilding.

        ``all_replaced``: treat every node as gone (undo / redo: the store may keep the
        objects of unchanged projects, but the view behaves as when every object was new)."""
        c = self.canvas
        systems = self.store.systems
        if c.active_system is not None and not any(s is c.active_system for s in systems):
            c.active_system = systems[0] if systems else None
        if c.selected_node is not None and (all_replaced or not self._node_in_store(c.selected_node)):
            c.selected_node = c.active_system
        if not systems:
            c.selected_node = None
            c.view_mode = "galaxy"

    def _refresh_inspector_for_store(self, all_replaced: bool = False) -> None:
        """After `_drop_stale_view_state` + rebuild: neutral inspector without projects,
        the open project when the shown node is gone (always with ``all_replaced``)."""
        self._on_map_filter_changed("")  # the Filter button follows the view mode
        if not self.store.systems:
            self._clear_inspector()
        elif all_replaced or self._selected_node is None or not self._node_in_store(self._selected_node):
            if self.canvas.active_system is not None:
                self._load_node_into_inspector(self.canvas.active_system, "sun")

    def _setup_shortcuts(self) -> None:
        sc_search = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_search.activated.connect(self._focus_search_input)

        sc_esc = QShortcut(QKeySequence("Escape"), self)
        sc_esc.activated.connect(self._on_escape_pressed)

        sc_undo = QShortcut(QKeySequence("Ctrl+Z"), self)
        sc_undo.activated.connect(self._on_undo)

        sc_redo1 = QShortcut(QKeySequence("Ctrl+Y"), self)
        sc_redo1.activated.connect(self._on_redo)

        sc_redo2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        sc_redo2.activated.connect(self._on_redo)

        # Delete shortcut (with confirmation dialog)
        sc_del = QShortcut(QKeySequence.StandardKey.Delete, self)
        sc_del.activated.connect(self._handle_delete_shortcut)

        # Canvas zoom in / out and reset shortcuts
        sc_zoom_in1 = QShortcut(QKeySequence("Ctrl++"), self)
        sc_zoom_in1.activated.connect(self.canvas.zoom_in)

        sc_zoom_in2 = QShortcut(QKeySequence("Ctrl+="), self)
        sc_zoom_in2.activated.connect(self.canvas.zoom_in)

        sc_zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        sc_zoom_out.activated.connect(self.canvas.zoom_out)

        sc_zoom_reset = QShortcut(QKeySequence("Ctrl+0"), self)
        sc_zoom_reset.activated.connect(self.canvas.reset_zoom)

        # "F" fits the map (text fields keep typing "f": they take the key first).
        sc_fit = QShortcut(QKeySequence("F"), self)
        sc_fit.activated.connect(self._fit_view_shortcut)

        # Ctrl+E: export the opened project's map (PNG or Markdown).
        sc_export = QShortcut(QKeySequence("Ctrl+E"), self)
        sc_export.activated.connect(lambda: self._export_map(self.canvas.active_system))

        # Kept so the busy state can disable all of them while a worker owns the store.
        self._shortcuts = [
            sc_search, sc_esc, sc_undo, sc_redo1, sc_redo2, sc_del,
            sc_zoom_in1, sc_zoom_in2, sc_zoom_out, sc_zoom_reset, sc_fit, sc_export,
        ]

    def _fit_view_shortcut(self) -> None:
        if isinstance(QApplication.focusWidget(), (QLineEdit, QTextEdit, QComboBox)):
            return
        self.canvas.fit_to_view()

    def _handle_delete_shortcut(self) -> None:
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QTextEdit)):
            return
        if self._selected_node:
            self._delete_current_node()

    def _on_undo(self) -> None:
        depth = len(getattr(self.store, "_undo_stack", []))
        undone_ignore = self._ignore_undo.pop() if self._ignore_undo and self._ignore_undo[-1][0] == depth else None
        if self.store.undo():
            if undone_ignore is not None:
                remove_ignored_folder(self.store, undone_ignore[1])
                self._ignore_redo.append((len(getattr(self.store, "_redo_stack", [])), undone_ignore[1]))
            self._updating_ui = True
            active_id = self.canvas.active_system.id if self.canvas.active_system else None
            if active_id:
                for s in self.store.systems:
                    if s.id == active_id:
                        self.canvas.active_system = s
                        break
            if self.canvas.active_system is None or not any(
                s is self.canvas.active_system for s in self.store.systems
            ):
                self.canvas.active_system = self.store.systems[0] if self.store.systems else None
            self._drop_stale_view_state(all_replaced=True)
            self._populate_tree()
            self.canvas.rebuild_scene()
            self._refresh_sun_anchor_card()
            self._update_breadcrumb()
            self._updating_ui = False
            self._refresh_inspector_for_store(all_replaced=True)
            self.status_label.setText("Undo (Ctrl+Z) applied.")
        else:
            self.status_label.setText("Nothing left to undo.")

    def _on_redo(self) -> None:
        rdepth = len(getattr(self.store, "_redo_stack", []))
        redone_ignore = self._ignore_redo.pop() if self._ignore_redo and self._ignore_redo[-1][0] == rdepth else None
        if self.store.redo():
            if redone_ignore is not None and add_ignored_folder(self.store, redone_ignore[1]):
                self._ignore_undo.append((len(getattr(self.store, "_undo_stack", [])), redone_ignore[1]))
            self._updating_ui = True
            active_id = self.canvas.active_system.id if self.canvas.active_system else None
            if active_id:
                for s in self.store.systems:
                    if s.id == active_id:
                        self.canvas.active_system = s
                        break
            self._drop_stale_view_state(all_replaced=True)
            self._populate_tree()
            self.canvas.rebuild_scene()
            self._refresh_sun_anchor_card()
            self._update_breadcrumb()
            self._updating_ui = False
            self._refresh_inspector_for_store(all_replaced=True)
            self.status_label.setText("Redo (Ctrl+Y) applied.")
        else:
            self.status_label.setText("Nothing left to redo.")

    def _focus_search_input(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _on_escape_pressed(self) -> None:
        if getattr(self, "search_ctl", None) is not None and self.search_ctl.handle_escape():
            return
        if hasattr(self, "purpose_edit_box") and self.purpose_edit_box.isVisible():
            self._cancel_purpose_edit()
            return
        if getattr(self, "central_stack", None) and self.central_stack.currentIndex() == 1:
            self._switch_to_page("studio")
            return
        if self.search_input.hasFocus() and self.search_input.text():
            self.search_input.clear()
            return
        if self.canvas.is_drawing_link:
            self.canvas.is_drawing_link = False
            self.canvas._link_source_item = None
            self.canvas.scene_obj.update()
            return
        if self.canvas.drawing_tool != "select":
            self.canvas.set_drawing_tool("select")
            return
        if self.canvas.tour_index >= 0:
            self._stop_tour()
            return
        if self.canvas.view_mode == "system":
            self.canvas.switch_to_galaxy_view()

    def _setup_live_file_watcher(self) -> None:
        self.file_watcher = QFileSystemWatcher(self)
        if self.store.storage_path.exists():
            self.file_watcher.addPath(str(self.store.storage_path))
        self.file_watcher.fileChanged.connect(self._on_external_store_changed)

    def _on_external_store_changed(self, path: str) -> None:
        # Every branch re-arms the watcher: on Linux (inotify) QFileSystemWatcher drops a
        # path that is replaced by a rename, which is how every save writes galaxy.json
        # (`atomic_write_text`), so an early return here would lose all later external edits.
        try:
            self._reload_external_store()
        finally:
            self._rearm_file_watcher()

    def _reload_external_store(self) -> None:
        if self._updating_ui or self._store_busy:
            # A background worker owns the store; the watcher is re-armed when it finishes.
            return
        if self.store.is_own_write():
            # Ignore file changes triggered by Helios itself
            return
        try:
            active_id = self.canvas.active_system.id if self.canvas.active_system else None
            loaded = self.store.load()
            if loaded is False or getattr(self.store, "load_error", None):
                # The in-memory graph is kept untouched when the file cannot be parsed.
                err = getattr(self.store, "load_error", None) or "unknown error"
                backup = getattr(self.store, "backup_path", None)
                text = f"Live update could not be read ({err}). Nothing was overwritten."
                if backup:
                    text += f" Backup: {backup}"
                self.status_label.setText(text)
                return
            if active_id:
                for sys_obj in self.store.systems:
                    if sys_obj.id == active_id:
                        self.canvas.active_system = sys_obj
                        break
            self._drop_stale_view_state()
            self._populate_tree()
            self.canvas.rebuild_scene()
            self._refresh_sun_anchor_card()
            self._update_breadcrumb()
            self._refresh_inspector_for_store()
            self.status_label.setText("Live update received: Graph synchronized.")
        except Exception as exc:
            self.status_label.setText(f"Live update failed: {exc}")
