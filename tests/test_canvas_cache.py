"""Canvas caches (text metrics, link curves, reverse-link flags, parent lookup) always give
what a fresh computation gives, after every kind of change."""

from __future__ import annotations

import copy

from tests.test_ui_smoke import env, hard_timeout, qapp, spin  # noqa: F401

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QFont, QFontMetricsF  # noqa: E402

from core.models import PlanetLink, SatelliteNode, SolarSystem  # noqa: E402
from ui.canvas.helpers import elide  # noqa: E402
from ui.canvas.link_item import ConstellationLinkItem  # noqa: E402
from ui.canvas.module_item import PLANET_LABEL_W, _widest_chip_width  # noqa: E402
from ui import theme  # noqa: E402


def _window(env):
    win = env["make_window"](onboarded=True)
    s = SolarSystem(name="Cache", project_name="Cache")
    for i in range(3):
        p = s.add_planet(title=f"module_{i}.py")
        p.satellites.clear()
        for c in range(3):
            p.add_satellite(title=f"comp_{i}_{c}()", content="x", visual_tag="code")
    s.add_link(s.planets[0].id, s.planets[1].id, label="calls")
    win.store.add_system(s)
    win.canvas.enter_solar_system(s)
    spin(0.05)
    return win, s


def _fresh_sat_rect_width(item):
    text = elide(item.satellite.title, item._font, item.label_width)
    return text, min(item.label_width, QFontMetricsF(item._font).horizontalAdvance(text) + 2)


def test_component_label_follows_title_and_width(env):
    win, s = _window(env)
    item = win.canvas.planet_item_map[s.planets[0].id].satellite_items[0]
    assert (item.display_title(), item._label_rect().width()) == _fresh_sat_rect_width(item)
    item.satellite.title = "a_much_longer_component_name_that_needs_eliding_now()"
    assert (item.display_title(), item._label_rect().width()) == _fresh_sat_rect_width(item)
    assert item.display_title().endswith("…")
    item.label_width = 60.0
    assert (item.display_title(), item._label_rect().width()) == _fresh_sat_rect_width(item)
    item.satellite.title = "x()"
    assert item.display_title() == "x()"
    assert item._label_rect().width() == _fresh_sat_rect_width(item)[1]


def test_module_title_and_caption_follow_renames(env):
    win, s = _window(env)
    p_item = win.canvas.planet_item_map[s.planets[1].id]
    w0 = p_item.title_rect().width()
    p_item.planet.title = "renamed_module_with_a_really_long_name_for_eliding.py"
    text = elide(p_item.planet.title, p_item._title_font, PLANET_LABEL_W)
    assert p_item.display_title() == text
    assert p_item.title_rect().width() == QFontMetricsF(p_item._title_font).horizontalAdvance(text) + 4 != w0
    p_item._caption = "99 functions"
    assert p_item.caption_rect().width() == QFontMetricsF(p_item._caption_font).horizontalAdvance("99 functions") + 4


def test_more_chip_width_follows_count(env):
    win, s = _window(env)
    chip = win.canvas.planet_item_map[s.planets[0].id].more_chip
    font = theme.font(theme.FS_CAPTION, QFont.Weight.Medium)
    for count in (3, 12, 1234, 3):
        chip.count = count
        assert chip._rect().width() == QFontMetricsF(font).horizontalAdvance(f"+{count} more") + 14
    assert _widest_chip_width() == QFontMetricsF(font).horizontalAdvance("+999 more") + 18


def _fresh_curve(l_item):
    return ConstellationLinkItem._curve_from(l_item, l_item.source_item.pos(), l_item.target_item.pos())


def _same_curve(a, b):
    if a is None or b is None:
        return a is b
    return all(a[i] == b[i] for i in range(3)) and a[3] == b[3]


def test_link_curve_follows_moves_and_reverse_flag(env):
    win, s = _window(env)
    c = win.canvas
    l_item = c.link_items[0]
    assert _same_curve(l_item._compute_curve(), _fresh_curve(l_item))
    l_item.source_item.setPos(QPointF(300.0, -40.0))
    assert _same_curve(l_item._compute_curve(), _fresh_curve(l_item))
    l_item.source_item.radius += 5.0
    assert _same_curve(l_item._compute_curve(), _fresh_curve(l_item))
    # A reverse link added: the rebuilt items curve further apart.
    s.add_link(s.planets[1].id, s.planets[0].id, label="answers")
    c.rebuild_scene()
    assert all(it._has_reverse for it in c.link_items)
    assert all(_same_curve(it._compute_curve(), _fresh_curve(it)) for it in c.link_items)
    # Removed again / reversed: the flag follows the data after the rebuild.
    s.links.pop()
    c.rebuild_scene()
    assert not any(it._has_reverse for it in c.link_items)
    link = s.links[0]
    link.source_planet_id, link.target_planet_id = link.target_planet_id, link.source_planet_id
    s.links.append(PlanetLink(source_planet_id=s.planets[0].id, target_planet_id=s.planets[1].id))
    c.rebuild_scene()
    assert all(it._has_reverse for it in c.link_items)
    # Without the rebuild's pair set the item scans the links itself (same answer).
    it = c.link_items[0]
    it.refresh_reverse_flag()
    assert it._has_reverse


def test_parent_lookup_follows_moves_and_keeps_equality_semantics(env):
    win, s = _window(env)
    c = win.canvas
    sat = s.planets[0].satellites[1]
    c.select_object(sat, "satellite")
    assert c.selected_planet_id() == s.planets[0].id
    s.planets[2].satellites.append(s.planets[0].satellites.pop(1))  # moved
    assert s.find_parent_planet(sat) is s.planets[2]
    assert c.selected_planet_id() == s.planets[2].id
    twin = copy.deepcopy(sat)  # equal, not identical: `in` semantics find it too
    assert s.find_parent_planet(twin) is s.planets[2]
    s.planets[1].satellites.insert(0, copy.deepcopy(sat))  # an equal one in an earlier module
    assert s.find_parent_planet(sat) is s.planets[1]
    assert s.find_parent_planet(SatelliteNode(title="nowhere")) is None
    assert s.find_parent_planet("not a component") is None
