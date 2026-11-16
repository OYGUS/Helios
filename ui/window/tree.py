"""`ProjectTreeMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

The left project tree: building it (by project / by source), tooltips, highlighting
the selection, the filter field, clicks and the context menu.

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _updating_ui, btn_group_by_project, btn_group_by_source, canvas, hierarchy_tree,
    search_ctl, store, tree_filter
Of those, assigned by this mixin:
    _updating_ui
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QBrush, QFont
from PySide6.QtWidgets import QMenu, QTreeWidgetItem

from connectors import codebase_importer as codebase_importer_module
from core.models import CanvasNote, PlanetLink, PlanetNode, SatelliteNode, SolarSystem, get_source_meta
from ui import theme
from ui.window.project_text import _project_search_text

# The filter field waits this long after the last keystroke (like the search box).
TREE_FILTER_DEBOUNCE_MS = 140


class _TreeNode:
    """Python mirror of one tree row (made by the first filtering after the rows were
    built, see `_mirror_tree_rows`), so typing in the filter does not walk the
    QTreeWidget item by item on every keystroke."""

    __slots__ = ("item", "text", "kind", "obj", "children", "hidden")

    def __init__(self, item: QTreeWidgetItem, text: str, kind: str, obj: object) -> None:
        self.item = item
        self.text = text.lower()
        self.kind = kind
        self.obj = obj
        self.children: List["_TreeNode"] = []
        self.hidden = False


class ProjectTreeMixin:
    """The left project tree."""

    def _update_group_toggle_styles(self) -> None:
        mode = getattr(self, "tree_group_mode", "project")
        self.btn_group_by_project.setChecked(mode == "project")
        self.btn_group_by_source.setChecked(mode != "project")

    def _populate_system_nodes_in_tree(self, sys_item: QTreeWidgetItem, sys_obj: SolarSystem) -> None:
        """Adds modules, components, links and notes under a project item."""
        secondary = QBrush(theme.qcolor(theme.TEXT_2))
        for planet in sys_obj.planets:
            p_item = QTreeWidgetItem(sys_item, [planet.title])
            p_item.setData(0, Qt.ItemDataRole.UserRole, ("planet", planet))
            p_item.setIcon(0, self._dot_icon(planet.color, hollow=planet.kind in ("history", "external")))
            p_item.setToolTip(0, f"{planet.title}\n{theme.PLANET_KIND_LABELS.get(planet.kind, 'Module')}")
            for sat in planet.satellites:
                s_item = QTreeWidgetItem(p_item, [sat.title])
                s_item.setData(0, Qt.ItemDataRole.UserRole, ("satellite", sat))
                s_item.setForeground(0, secondary)
                s_item.setToolTip(0, sat.title)

        if sys_obj.links:
            links_root = QTreeWidgetItem(sys_item, [f"Links ({len(sys_obj.links)})"])
            links_root.setData(0, Qt.ItemDataRole.UserRole, ("links_group", sys_obj))
            links_root.setForeground(0, secondary)
            p_map = {p.id: p.title for p in sys_obj.planets}
            for link in sys_obj.links:
                src_t = p_map.get(link.source_planet_id, "?")
                dst_t = p_map.get(link.target_planet_id, "?")
                l_item = QTreeWidgetItem(links_root, [f"{src_t} → {dst_t}"])
                l_item.setData(0, Qt.ItemDataRole.UserRole, ("link", link))
                l_item.setForeground(0, secondary)
                l_item.setToolTip(0, f"{src_t} → {dst_t}\n{link.label}")

        if sys_obj.notes:
            notes_root = QTreeWidgetItem(sys_item, [f"Notes ({len(sys_obj.notes)})"])
            notes_root.setData(0, Qt.ItemDataRole.UserRole, ("notes_group", sys_obj))
            notes_root.setForeground(0, secondary)
            for note in sys_obj.notes:
                n_item = QTreeWidgetItem(notes_root, [note.title])
                n_item.setData(0, Qt.ItemDataRole.UserRole, ("note", note))
                n_item.setForeground(0, secondary)

    def _project_tooltip(self, sys_obj: SolarSystem) -> str:
        purpose = " ".join(str(getattr(sys_obj, "purpose", "") or "").split())
        identity = codebase_importer_module.stored_identity(sys_obj)
        identity_txt = f"\n{identity} ({codebase_importer_module.IDENTITY_TOOLTIP.lower()})" if identity else ""
        return (
            f"{sys_obj.project_name or sys_obj.name}\n"
            f"{purpose or 'No description yet.'}{identity_txt}\n\n"
            f"Sources: {sys_obj.get_source_labels()} · "
            f"{len(sys_obj.planets)} modules · {len(sys_obj.links)} links"
        )

    def _refresh_project_tooltips(self) -> None:
        """Updates the project rows' tooltips (purpose) without rebuilding the tree."""
        def walk(item: QTreeWidgetItem) -> None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "sun" and isinstance(data[1], SolarSystem):
                item.setToolTip(0, self._project_tooltip(data[1]))
            for i in range(item.childCount()):
                walk(item.child(i))

        walk(self.hierarchy_tree.invisibleRootItem())

    def _tree_data_key(self) -> tuple:
        """What the tree rows were built from: the store's saved/loaded data version and
        the project list. Equal keys: rebuilding would create the very same rows."""
        systems = self.store.systems
        return (
            getattr(self.store, "data_version", None),
            id(systems),
            tuple(id(s) for s in systems),
            getattr(self, "tree_group_mode", "project"),
        )

    def _populate_tree(self, invalidate_search: bool = True) -> None:
        if invalidate_search and getattr(self, "search_ctl", None) is not None:
            self.search_ctl.invalidate()  # the tree is rebuilt whenever the map data changed
        self._updating_ui = True
        # The filter's mirror of the rows is rebuilt from the new rows when it is next
        # needed; its references to the old rows go before `clear()` (rows kept alive by
        # a Python wrapper make the view keep its scroll range, so the tree would scroll
        # differently than it always did).
        self._tree_roots: Optional[List[_TreeNode]] = None
        self.hierarchy_tree.clear()
        # Rows are built detached (no model notification per row) and inserted at once;
        # the view's layout is pending from clear() until then either way.
        tops: List[QTreeWidgetItem] = []
        expand: List[QTreeWidgetItem] = []
        active_sys = self.canvas.active_system
        mode = getattr(self, "tree_group_mode", "project")
        bold = QFont(theme.ui_font_family())
        bold.setPixelSize(theme.FS_BODY)
        bold.setWeight(QFont.Weight.DemiBold)

        if mode == "project":
            grouped_proj = self.store.grouped_by_project()
            for proj_name, sys_list in grouped_proj.items():
                if not sys_list:
                    continue
                if len(sys_list) == 1:
                    sys_obj = sys_list[0]
                    sys_item = QTreeWidgetItem([proj_name])
                    tops.append(sys_item)
                    sys_item.setData(0, Qt.ItemDataRole.UserRole, ("sun", sys_obj))
                    sys_item.setFont(0, bold)
                    sys_item.setIcon(0, self._dot_icon(str(get_source_meta(sys_obj.source)["color"])))
                    sys_item.setToolTip(0, self._project_tooltip(sys_obj))
                    self._populate_system_nodes_in_tree(sys_item, sys_obj)
                    if sys_obj is active_sys:
                        expand.append(sys_item)
                else:
                    proj_item = QTreeWidgetItem([proj_name])
                    tops.append(proj_item)
                    proj_item.setData(0, Qt.ItemDataRole.UserRole, ("project_group", (proj_name, sys_list)))
                    proj_item.setFont(0, bold)
                    proj_item.setIcon(0, self._dot_icon(str(get_source_meta(sys_list[0].source)["color"])))
                    proj_item.setToolTip(0, f"{proj_name}\nFrom {len(sys_list)} sources: "
                                            + ", ".join(s.get_source_labels() for s in sys_list))
                    for sys_obj in sys_list:
                        cat_meta = get_source_meta(sys_obj.source)
                        src_item = QTreeWidgetItem(
                            proj_item, [f"{cat_meta['short']} · {len(sys_obj.planets)} modules"]
                        )
                        src_item.setData(0, Qt.ItemDataRole.UserRole, ("sun", sys_obj))
                        src_item.setIcon(0, self._dot_icon(str(cat_meta["color"])))
                        src_item.setToolTip(0, self._project_tooltip(sys_obj))
                        self._populate_system_nodes_in_tree(src_item, sys_obj)
                        if sys_obj is active_sys:
                            expand.append(src_item)
                    expand.append(proj_item)
        else:
            grouped_src = self.store.grouped_by_source()
            for source_key, sys_list in grouped_src.items():
                if not sys_list:
                    continue
                cat_meta = get_source_meta(source_key)
                cat_item = QTreeWidgetItem([f"{cat_meta['title']}  ·  {len(sys_list)}"])
                tops.append(cat_item)
                cat_item.setData(0, Qt.ItemDataRole.UserRole, ("category", source_key))
                cat_item.setForeground(0, QBrush(theme.qcolor(theme.TEXT_2)))
                cat_item.setIcon(0, self._dot_icon(str(cat_meta["color"])))
                for sys_obj in sys_list:
                    display_proj = sys_obj.project_name or sys_obj.name
                    sys_item = QTreeWidgetItem(cat_item, [display_proj])
                    sys_item.setData(0, Qt.ItemDataRole.UserRole, ("sun", sys_obj))
                    sys_item.setToolTip(0, self._project_tooltip(sys_obj))
                    if sys_obj is active_sys:
                        sys_item.setFont(0, bold)
                    self._populate_system_nodes_in_tree(sys_item, sys_obj)
                    if sys_obj is active_sys:
                        expand.append(sys_item)
                expand.append(cat_item)

        self.hierarchy_tree.addTopLevelItems(tops)
        for item in expand:
            item.setExpanded(True)
        self._tree_built_key = self._tree_data_key()
        self._updating_ui = False
        if self.tree_filter.text().strip():
            self._filter_tree(self.tree_filter.text())

    def _refresh_tree_for_selection(self) -> None:
        """Rebuilds the tree after a project was selected (expansion follows the open
        project). The rows are rebuilt (not reset in place) so the view state, including
        the scroll position, is exactly what it always was; only the search index is kept
        when the rows were built from the current data (same `_tree_data_key`): a pure
        selection changes nothing searchable."""
        unchanged = getattr(self, "_tree_built_key", None) == self._tree_data_key()
        self._populate_tree(invalidate_search=not unchanged)

    def _highlight_node_in_tree(self, target_obj: object) -> None:
        # A depth-first walk of the tree on purpose: looking the row up in a map instead
        # finds the same row but leaves the view's pending layout in another state, so
        # the next rebuild scrolls the tree differently (the screenshots differ).
        self._updating_ui = True
        try:
            def search_item(item: QTreeWidgetItem) -> bool:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data and data[1] is target_obj:
                    p = item.parent()
                    while p:
                        p.setExpanded(True)
                        p = p.parent()
                    self.hierarchy_tree.setCurrentItem(item)
                    item.setExpanded(True)
                    return True
                for i in range(item.childCount()):
                    if search_item(item.child(i)):
                        return True
                return False

            root = self.hierarchy_tree.invisibleRootItem()
            for i in range(root.childCount()):
                if search_item(root.child(i)):
                    return
        finally:
            self._updating_ui = False

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        item = self.hierarchy_tree.itemAt(pos)
        menu = QMenu(self)

        if not item:
            act_analyze = menu.addAction("Analyze a folder…")
            act_analyze.triggered.connect(self._analyze_codebase_folder)
            act_new_proj = menu.addAction("Design a new project…")
            act_new_proj.triggered.connect(self._create_new_system)
            menu.addSeparator()
            act_scan_helios = menu.addAction("Scan the 'projects/' folder")
            act_scan_helios.triggered.connect(self._scan_helios_projects_directly)
            act_open_projects = menu.addAction("Open the 'projects/' folder")
            act_open_projects.triggered.connect(self._open_helios_projects_folder)
            menu.addSeparator()
            act_expand = menu.addAction("Expand all")
            act_expand.triggered.connect(self.hierarchy_tree.expandAll)
            act_collapse = menu.addAction("Collapse all")
            act_collapse.triggered.connect(self.hierarchy_tree.collapseAll)
            act_refresh = menu.addAction("Refresh list")
            act_refresh.triggered.connect(self._populate_tree)
            menu.addSeparator()
            act_sync_agents = menu.addAction("Sync AI tools")
            act_sync_agents.triggered.connect(self._sync_agent_histories)
            act_settings = menu.addAction("Settings…")
            act_settings.triggered.connect(self._open_settings_dialog)
            menu.exec(self.hierarchy_tree.viewport().mapToGlobal(pos))
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        node_type, node_obj = data

        if node_type == "project_group":
            proj_name, sys_list = node_obj
            primary_sys = sys_list[0]
            act_open = menu.addAction(f"Open '{proj_name}'")
            act_open.triggered.connect(lambda: self.canvas.enter_solar_system(primary_sys))
            menu.addSeparator()

            if len(sys_list) > 1:
                sources_str = " & ".join(s.source.capitalize() for s in sys_list)
                act_merge = menu.addAction(f"Merge into one project ({sources_str})")
                act_merge.triggered.connect(lambda: self._merge_specific_systems(sys_list))
                menu.addSeparator()

            act_add_module = menu.addAction("Add module…")
            act_add_module.triggered.connect(lambda: self._quick_add_module_to_system(primary_sys))
            act_add_note = menu.addAction("Add note…")
            act_add_note.triggered.connect(lambda: self._quick_add_note_to_system(primary_sys))
            menu.addSeparator()
            act_copy_prompt = menu.addAction("Copy as AI prompt")
            act_copy_prompt.triggered.connect(lambda: self._copy_project_as_prompt(primary_sys))
            menu.addSeparator()
            for sys_entry in sys_list:
                label = "Remove project…" if len(sys_list) == 1 else \
                    f"Remove the {get_source_meta(sys_entry.source)['short']} version…"
                act_rm = menu.addAction(label)
                act_rm.triggered.connect(lambda checked=False, t=sys_entry: self._remove_project(t))

        elif node_type == "category":
            act_galaxy = menu.addAction("Show all projects")
            act_galaxy.triggered.connect(lambda: self.canvas.switch_to_galaxy_view())
            menu.addSeparator()
            act_new_proj = menu.addAction("Design a new project…")
            act_new_proj.triggered.connect(self._create_new_system)

        elif node_type == "sun" and isinstance(node_obj, SolarSystem):
            act_open = menu.addAction("Open project")
            act_open.triggered.connect(lambda: self.canvas.enter_solar_system(node_obj))
            menu.addSeparator()
            related = self.store.find_related_systems(node_obj)
            if related:
                act_merge_rel = menu.addAction(f"Merge with its other sources ({len(related) + 1} projects)")
                act_merge_rel.triggered.connect(lambda: self._merge_specific_systems([node_obj] + related))
                menu.addSeparator()
            act_add_module = menu.addAction("Add module…")
            act_add_module.triggered.connect(lambda: self._quick_add_module_to_system(node_obj))
            act_add_note = menu.addAction("Add note…")
            act_add_note.triggered.connect(lambda: self._quick_add_note_to_system(node_obj))
            menu.addSeparator()
            act_copy_prompt = menu.addAction("Copy as AI prompt")
            act_copy_prompt.triggered.connect(lambda: self._copy_project_as_prompt(node_obj))
            if node_obj.workspace_path:
                act_open_dir = menu.addAction("Open folder")
                act_open_dir.triggered.connect(lambda: self._open_node_path(node_obj))
            act_rebalance = menu.addAction("Auto-align layout")
            act_rebalance.triggered.connect(lambda: (self.canvas.enter_solar_system(node_obj), self._rebalance_active_system_layout()))
            menu.addSeparator()
            act_del = menu.addAction("Remove project…")
            act_del.triggered.connect(lambda: self._remove_project(node_obj))

        elif node_type == "planet" and isinstance(node_obj, PlanetNode):
            act_sel = menu.addAction("Show module")
            act_sel.triggered.connect(lambda: self.canvas.select_object(node_obj, "planet"))
            menu.addSeparator()
            act_add_sat = menu.addAction("Add component…")
            act_add_sat.triggered.connect(lambda: self._quick_add_sat_to_planet(node_obj))
            act_link = menu.addAction("Link to another module…")
            act_link.triggered.connect(lambda: (self.canvas.select_object(node_obj, "planet"), self._prompt_add_link_from_selected()))
            menu.addSeparator()
            act_del = menu.addAction("Delete module…")
            act_del.triggered.connect(lambda: (self.canvas.select_object(node_obj, "planet"), self._delete_current_node()))

        elif node_type == "satellite" and isinstance(node_obj, SatelliteNode):
            act_sel = menu.addAction("Show component")
            act_sel.triggered.connect(lambda: self.canvas.select_object(node_obj, "satellite"))
            if node_obj.file_path:
                act_open_f = menu.addAction("Open file")
                act_open_f.triggered.connect(lambda: self._open_node_path(node_obj))
            menu.addSeparator()
            act_del = menu.addAction("Delete component…")
            act_del.triggered.connect(lambda: (self.canvas.select_object(node_obj, "satellite"), self._delete_current_node()))

        elif node_type == "link" and isinstance(node_obj, PlanetLink):
            act_sel = menu.addAction("Show link")
            act_sel.triggered.connect(lambda: self.canvas.select_object(node_obj, "link"))
            menu.addSeparator()
            act_del = menu.addAction("Delete link…")
            act_del.triggered.connect(lambda: (self.canvas.select_object(node_obj, "link"), self._delete_current_node()))

        elif node_type == "note" and isinstance(node_obj, CanvasNote):
            act_sel = menu.addAction("Show note")
            act_sel.triggered.connect(lambda: self.canvas.select_object(node_obj, "note"))
            menu.addSeparator()
            act_del = menu.addAction("Delete note…")
            act_del.triggered.connect(lambda: (self.canvas.select_object(node_obj, "note"), self._delete_current_node()))

        menu.exec(self.hierarchy_tree.viewport().mapToGlobal(pos))

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating_ui:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        node_type, node_obj = data

        if node_type == "category":
            self.canvas.switch_to_galaxy_view()
            return

        if node_type == "project_group":
            proj_name, sys_list = node_obj
            if sys_list:
                primary = sys_list[0]
                if self.canvas.active_system is not primary or self.canvas.view_mode != "system":
                    self.canvas.enter_solar_system(primary)
                else:
                    self.canvas.select_object(primary, "sun")
            return

        def _get_owner_system(tree_item: QTreeWidgetItem) -> Optional[SolarSystem]:
            curr = tree_item.parent()
            while curr:
                d = curr.data(0, Qt.ItemDataRole.UserRole)
                if d and d[0] == "sun" and isinstance(d[1], SolarSystem):
                    return d[1]
                curr = curr.parent()
            return None

        if node_type == "sun" and isinstance(node_obj, SolarSystem):
            if self.canvas.active_system is not node_obj or self.canvas.view_mode != "system":
                self.canvas.enter_solar_system(node_obj)
            else:
                self.canvas.select_object(node_obj, "sun")

        elif node_type == "planet" and isinstance(node_obj, PlanetNode):
            owner_sys = _get_owner_system(item)
            if owner_sys and (self.canvas.active_system is not owner_sys or self.canvas.view_mode != "system"):
                self.canvas.enter_solar_system(owner_sys)
            self.canvas.select_object(node_obj, "planet")

        elif node_type == "satellite" and isinstance(node_obj, SatelliteNode):
            owner_sys = _get_owner_system(item)
            if owner_sys and (self.canvas.active_system is not owner_sys or self.canvas.view_mode != "system"):
                self.canvas.enter_solar_system(owner_sys)
            self.canvas.select_object(node_obj, "satellite")

        elif node_type == "link" and isinstance(node_obj, PlanetLink):
            owner_sys = _get_owner_system(item)
            if owner_sys and (self.canvas.active_system is not owner_sys or self.canvas.view_mode != "system"):
                self.canvas.enter_solar_system(owner_sys)
            self.canvas.select_object(node_obj, "link")

        elif node_type == "note" and isinstance(node_obj, CanvasNote):
            owner_sys = _get_owner_system(item)
            if owner_sys and (self.canvas.active_system is not owner_sys or self.canvas.view_mode != "system"):
                self.canvas.enter_solar_system(owner_sys)
            self.canvas.select_object(node_obj, "note")

        elif node_type in ("links_group", "notes_group") and isinstance(node_obj, SolarSystem):
            if self.canvas.active_system is not node_obj or self.canvas.view_mode != "system":
                self.canvas.enter_solar_system(node_obj)

    def _mirror_tree_rows(self) -> List[_TreeNode]:
        """The Python mirror of the current rows (one walk of the tree; kept until the rows
        are rebuilt, so later keystrokes do not walk the tree again)."""
        def mirror(item: QTreeWidgetItem) -> _TreeNode:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            kind, obj = (data[0], data[1]) if data else ("", None)
            node = _TreeNode(item, item.text(0), kind, obj)
            node.hidden = item.isHidden()
            node.children = [mirror(item.child(i)) for i in range(item.childCount())]
            return node

        root = self.hierarchy_tree.invisibleRootItem()
        return [mirror(root.child(i)) for i in range(root.childCount())]

    def _on_tree_filter_text_changed(self, text: str) -> None:
        """Typing in the filter field: filters after a short pause (at once when cleared)."""
        timer = getattr(self, "_tree_filter_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(TREE_FILTER_DEBOUNCE_MS)
            timer.timeout.connect(self._apply_tree_filter_now)
            self._tree_filter_timer = timer
        if not text.strip():
            timer.stop()
            self._filter_tree(text)
            return
        timer.start()

    def _apply_tree_filter_now(self) -> None:
        timer = getattr(self, "_tree_filter_timer", None)
        if timer is not None:
            timer.stop()
        self._filter_tree(self.tree_filter.text())

    def _filter_tree(self, query: str) -> None:
        """Filters items in the left tree based on the search bar text and expands matches.

        Works on the Python mirror of the rows (`_TreeNode`): the same matching as walking
        the tree, but only rows whose hidden state changes are touched."""
        q = query.strip().lower()
        roots = getattr(self, "_tree_roots", None)
        if roots is None:
            roots = self._tree_roots = self._mirror_tree_rows()

        def filter_node(node: _TreeNode, top: bool) -> bool:
            matches = (q in node.text) if q else True
            if q and not matches:
                if node.kind == "sun" and isinstance(node.obj, SolarSystem):
                    matches = q in _project_search_text(node.obj)
                elif node.kind == "project_group":
                    matches = any(q in _project_search_text(x) for x in node.obj[1])
            child_matches = False
            for child in node.children:
                if filter_node(child, False):
                    child_matches = True

            visible = matches or child_matches
            if node.hidden == visible:
                node.item.setHidden(not visible)
                node.hidden = not visible
            if q and child_matches:
                node.item.setExpanded(True)
            elif not q:
                # Keep top-level categories expanded when search is cleared
                if top:
                    node.item.setExpanded(True)
            return visible

        for node in roots:
            filter_node(node, True)
