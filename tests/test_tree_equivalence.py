"""The project tree after every kind of edit is exactly what the previous tree code made.

Two windows with the same size and data run the same steps; one uses the tree code as it
was before this change (rows created in the tree one by one, the filter walking the rows),
copied below as the reference. After each step the tree's scroll position, expanded rows,
current row, hidden rows and the geometry of the visible rows must be equal.
"""

from __future__ import annotations

import types

import pytest

from tests.test_ui_smoke import env, hard_timeout, qapp, spin  # noqa: F401

from PySide6 import QtWidgets  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QBrush, QFont  # noqa: E402
from PySide6.QtWidgets import QTreeWidgetItem  # noqa: E402

from core.models import GalaxyStore, SolarSystem, get_source_meta  # noqa: E402
from ui import theme  # noqa: E402
from ui.window.project_text import _project_search_text  # noqa: E402


def reference_system_rows(self, sys_item: QTreeWidgetItem, sys_obj: SolarSystem) -> None:
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



def reference_populate_tree(self, invalidate_search: bool = True) -> None:
    """The tree rebuild as it was before (rows created in the tree one by one)."""
    if invalidate_search and getattr(self, "search_ctl", None) is not None:
        self.search_ctl.invalidate()  # the tree is rebuilt whenever the map data changed
    self._updating_ui = True
    self.hierarchy_tree.clear()
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
                sys_item = QTreeWidgetItem(self.hierarchy_tree, [proj_name])
                sys_item.setData(0, Qt.ItemDataRole.UserRole, ("sun", sys_obj))
                sys_item.setFont(0, bold)
                sys_item.setIcon(0, self._dot_icon(str(get_source_meta(sys_obj.source)["color"])))
                sys_item.setToolTip(0, self._project_tooltip(sys_obj))
                reference_system_rows(self, sys_item, sys_obj)
                if sys_obj is active_sys:
                    sys_item.setExpanded(True)
            else:
                proj_item = QTreeWidgetItem(self.hierarchy_tree, [proj_name])
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
                    reference_system_rows(self, src_item, sys_obj)
                    if sys_obj is active_sys:
                        src_item.setExpanded(True)
                proj_item.setExpanded(True)
    else:
        grouped_src = self.store.grouped_by_source()
        for source_key, sys_list in grouped_src.items():
            if not sys_list:
                continue
            cat_meta = get_source_meta(source_key)
            cat_item = QTreeWidgetItem(self.hierarchy_tree, [f"{cat_meta['title']}  ·  {len(sys_list)}"])
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
                reference_system_rows(self, sys_item, sys_obj)
                if sys_obj is active_sys:
                    sys_item.setExpanded(True)
            cat_item.setExpanded(True)

    self._updating_ui = False
    if self.tree_filter.text().strip():
        self._filter_tree(self.tree_filter.text())



def reference_filter_tree(self, query: str) -> None:
    """Filters items in the left tree in real time based on the search bar text and expands matches."""
    q = query.strip().lower()
    root = self.hierarchy_tree.invisibleRootItem()

    def filter_item(item: QTreeWidgetItem) -> bool:
        item_text = item.text(0).lower()
        matches = (q in item_text) if q else True
        if q and not matches:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "sun" and isinstance(data[1], SolarSystem):
                matches = q in _project_search_text(data[1])
            elif data and data[0] == "project_group":
                matches = any(q in _project_search_text(x) for x in data[1][1])
        child_matches = False
        for i in range(item.childCount()):
            if filter_item(item.child(i)):
                child_matches = True

        visible = matches or child_matches
        item.setHidden(not visible)
        if q and child_matches:
            item.setExpanded(True)
        elif not q:
            # Keep top-level categories expanded when search is cleared
            if item.parent() is None:
                item.setExpanded(True)
        return visible

    for i in range(root.childCount()):
        filter_item(root.child(i))



# ----------------------------------------------------------------------------
def _fill(store) -> None:
    store.systems = []
    for pi in range(12):
        s = SolarSystem(id=f"p{pi:02d}", name=f"Proj{pi:02d}", project_name=f"Proj{pi:02d}",
                        source=["codebase", "antigravity", "claude_code", "manual"][pi % 4])
        for mi in range(3 + pi % 6):
            p = s.add_planet(title=f"mod_{pi}_{mi}.py")
            p.id = f"p{pi:02d}m{mi:02d}"
            p.satellites.clear()
            for ci in range(2 + (pi + mi) % 7):
                sat = p.add_satellite(title=f"comp_{mi:02d}_{ci:02d}()", content="x", visual_tag="code")
                sat.id = f"p{pi:02d}m{mi:02d}c{ci:02d}"
        if len(s.planets) > 2:
            s.add_link(s.planets[0].id, s.planets[1].id, label="calls")
            s.add_link(s.planets[2].id, s.planets[0].id, label="uses")
        if pi % 3 == 0:
            s.add_note(1.0, 2.0, title="note", content="n")
        store.systems.append(s)
    store.save()


def _window(env, reference: bool):
    # Each window has its own galaxy.json: with one shared file, a save in one window is an
    # external change for the other, whose file watcher reloads it (it does on Windows), and
    # the two trees would then be compared after different code paths.
    folder = env["tmp"] / ("reference" if reference else "new")
    win = env["make_window"](store=GalaxyStore(storage_path=folder / "galaxy.json"), onboarded=True)
    win.store.set_setting("connected_sources", [])
    _fill(win.store)
    if reference:
        win._populate_tree = types.MethodType(reference_populate_tree, win)
        win._filter_tree = types.MethodType(reference_filter_tree, win)
    win.canvas.active_system = win.store.systems[0]
    win._populate_tree()
    win.resize(1200, 760)
    win.show()
    spin(0.2)
    return win


def _tree_state(win) -> dict:
    t = win.hierarchy_tree
    rows = []

    def walk(item, path):
        for i in range(item.childCount()):
            ch = item.child(i)
            p = path + (i,)
            rect = t.visualItemRect(ch)
            rows.append((p, ch.text(0), ch.isExpanded(), ch.isHidden(), rect.getRect(), ch.isSelected()))
            walk(ch, p)

    walk(t.invisibleRootItem(), ())
    cur = t.currentItem()
    return {
        "v": (t.verticalScrollBar().value(), t.verticalScrollBar().maximum()),
        "h": (t.horizontalScrollBar().value(), t.horizontalScrollBar().maximum()),
        "current": cur.text(0) if cur is not None else None,
        "rows": rows,
    }


def _steps(win):
    """(label, action) pairs; each action runs the same user step on its window."""
    c = win.canvas

    def big():
        return next(s for s in win.store.systems if s.id == "p05")

    def scroll(v):
        sb = win.hierarchy_tree.verticalScrollBar()
        sb.setValue(min(sb.maximum(), v))

    return [
        ("open project", lambda: c.enter_solar_system(big())),
        ("select module", lambda: c.select_object(big().planets[3], "planet")),
        ("select component", lambda: c.select_object(big().planets[4].satellites[-1], "satellite")),
        ("scroll", lambda: scroll(9)),
        ("add component", lambda: win._quick_add_sat_to_planet(big().planets[1])),
        ("inspector save", lambda: (c.select_object(big().planets[2], "planet"), win._save_current_node())),
        ("delete component", lambda: (setattr(win, "_selected_node", big().planets[0].satellites[0]),
                                      setattr(win, "_selected_type", "satellite"), win._delete_current_node())),
        ("undo", win._on_undo),
        ("redo", win._on_redo),
        ("scroll more", lambda: scroll(25)),
        ("other project", lambda: c.enter_solar_system(win.store.systems[8])),
        ("back", lambda: c.enter_solar_system(big())),
        ("filter", lambda: win._filter_tree("comp_03")),
        ("filtered edit", lambda: win._quick_add_sat_to_planet(big().planets[0])),
        ("filter cleared", lambda: win._filter_tree("")),
        ("by source", lambda: win.btn_group_by_source.click()),
        ("source edit", lambda: win._quick_add_note_to_system(big())),
        ("by project", lambda: win.btn_group_by_project.click()),
        ("add module", lambda: win._quick_add_module_to_system(big())),
        ("delete module", lambda: (setattr(win, "_selected_node", big().planets[1]),
                                   setattr(win, "_selected_type", "planet"), win._delete_current_node())),
        ("auto-align", win._rebalance_active_system_layout),
        ("undo twice", lambda: (win._on_undo(), win._on_undo())),
        ("all projects", c.switch_to_galaxy_view),
    ]


def test_tree_state_matches_the_previous_tree_code_after_every_edit(env, monkeypatch):
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Added", True)))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem", staticmethod(lambda *a, **k: (a[3][0], True)))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    ref = _window(env, reference=True)
    new = _window(env, reference=False)
    assert _tree_state(ref) == _tree_state(new)
    for (label, act_ref), (_, act_new) in zip(_steps(ref), _steps(new)):
        act_ref()
        spin(0.05)
        act_new()
        spin(0.05)
        a, b = _tree_state(ref), _tree_state(new)
        assert a["v"] == b["v"], label
        assert a["current"] == b["current"], label
        assert a == b, label
