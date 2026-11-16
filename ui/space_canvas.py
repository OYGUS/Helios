"""Helios interactive architecture canvas (QGraphicsView & QGraphicsScene).

A calm solar system with progressive disclosure:

- The project is the sun, modules are planets on orbits, components are moons and links
  are thin arrows between planets.
- By default only the sun, the planets (title + small caption) and low-contrast links are
  drawn. A planet shows its moons (with labels laid out on a fan facing away from the sun)
  when it is selected or hovered, or always when "Show all details" is on.
- Selecting a planet highlights its links (with labels) and dims everything unrelated;
  selecting a moon also highlights the planets that own the components it calls / is
  called by.
- All-projects overview: one calm card per project, grouped by source under quiet headers.
- Drawing tools live in a compact vertical icon strip docked at the left edge.

Colours come from :mod:`ui.theme`; stored model colours are only mapped to softened display
colours, never rewritten. Planet positions are computed for display (evenly spread, history /
external modules on the outermost orbit) unless the user moved a planet by hand
(``is_user_modified``), in which case the stored angle / radius are used.

Module map (`ui/canvas/`; item modules never import the view modules at runtime, and no
module there imports this facade):

   * `helpers`         — constants (`MAX_VISIBLE_MOONS`, label widths, dim opacities, drag
                         thresholds, `TAG_BADGE_MAP`), animations, `elide`,
                         `planet_caption`, `resolve_component_ref`, tool icons, chip / arrow
                         painters.
   * `link_item`       — `ConstellationLinkItem` (a link).
   * `note_item`       — `CanvasNoteGraphicsItem` (a note card).
   * `component_items` — `SatelliteGraphicsItem` (a component / moon), `MoreMoonsChipItem`.
   * `module_item`     — `PlanetGraphicsItem` (a module / planet), `EntryBadgeItem`.
   * `overview_items`  — all-projects view: `GalaxySectorBannerItem`, `GalaxyClusterItem` and
                         the project text snippets (`purpose_snippet`, `identity_snippet`, ...).
   * `sun_item`        — `SunGraphicsItem` (the project core / sun).
   * `overlays`        — `EmptyStateOverlay`.
   * `view`            — `SpaceCanvasView`: signals and `__init__`; behaviour in the mixins
                         `view_scene` (scene building, layout), `view_navigation` (fit, zoom,
                         pan, view-mode transitions), `view_focus` (selection, hover, focus),
                         `view_filters` (search dimming, map filter), `view_tools` (drawing
                         tools, `TOOL_SPECS`), `view_input` (mouse, keyboard, context menu),
                         `view_tour` (guided tour, orbit motion), `view_overlays` (overlay
                         placement, background).

This module is a facade: it re-exports every name that was importable from it before the
split (public and private helpers, and the Qt / model / typing names it imported), so
`from ui.space_canvas import X` keeps working. Each name is the very object defined in its
submodule. Tests that monkeypatch a module-level name must patch the submodule that reads
it (for example `ui.canvas.module_item.MAX_VISIBLE_MOONS`).
"""

from __future__ import annotations

import math  # noqa: F401
from typing import Dict, List, Optional, Sequence, Set, Tuple  # noqa: F401

from PySide6.QtCore import (  # noqa: F401
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (  # noqa: F401
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QWheelEvent,
)
from PySide6.QtWidgets import (  # noqa: F401
    QButtonGroup,
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QStyleOptionGraphicsItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.models import (  # noqa: F401
    LAYER_DEFINITIONS,
    RELATION_TYPES,
    SOURCE_CATEGORIES,
    CanvasNote,
    GalaxyStore,
    PlanetLink,
    PlanetNode,
    SatelliteNode,
    SolarSystem,
)
from connectors.codebase_importer import IDENTITY_TOOLTIP, stored_identity  # noqa: F401
from connectors.purpose_suggestion import pending_suggestion  # noqa: F401
from ui import theme  # noqa: F401
from ui import filters as map_filters  # noqa: F401
from ui.inspector import wrap_lines  # noqa: F401
from ui.welcome import WelcomePanel  # noqa: F401

# The implementation, re-exported name by name (each name is the object defined in its submodule).
from ui.canvas.helpers import (  # noqa: F401
    DIM_OPACITY,
    DRAG_COMMIT_PX,
    DRAG_START_PX,
    MAX_VISIBLE_MOONS,
    MOON_LABEL_W,
    MOON_ROW,
    PLANET_LABEL_W,
    QUIET_KINDS,
    SEARCH_DIM_OPACITY,
    SHOW_ALL_DETAILS_KEY,
    TAG_BADGE_MAP,
    _PLURALS,
    _arrow_head,
    _canvas_menu,
    _draw_chip,
    _draw_tool_glyph,
    _view_distance_px,
    animate_item_pop_in,
    animate_item_scale,
    chip_rect_for,
    elide,
    make_tool_icon,
    planet_caption,
    resolve_component_ref,
)
from ui.canvas.link_item import ConstellationLinkItem  # noqa: F401
from ui.canvas.note_item import CanvasNoteGraphicsItem  # noqa: F401
from ui.canvas.component_items import MoreMoonsChipItem, SatelliteGraphicsItem  # noqa: F401
from ui.canvas.module_item import EntryBadgeItem, PlanetGraphicsItem  # noqa: F401
from ui.canvas.sun_item import SunGraphicsItem  # noqa: F401
from ui.canvas.overview_items import (  # noqa: F401
    GalaxyClusterItem,
    GalaxySectorBannerItem,
    identity_snippet,
    purpose_snippet,
    suggestion_snippet,
)
from ui.canvas.overlays import EmptyStateOverlay  # noqa: F401
from ui.canvas.view_tools import TOOL_SPECS  # noqa: F401
from ui.canvas.view import SpaceCanvasView  # noqa: F401
