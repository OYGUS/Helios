"""`SpaceCanvasView`: the zoomable, pannable QGraphicsView that draws the map.

The class itself holds the Qt signals, the tick intervals and `__init__`; its behaviour
lives in plain mixins (listed before `QGraphicsView` in the bases so that Qt finds the
event handler overrides and their `super()` calls reach `QGraphicsView`):

   * `SceneBuildMixin`   (`view_scene`)      — scene building and display layout.
   * `NavigationMixin`   (`view_navigation`) — fit, zoom, pan, view-mode transitions.
   * `FocusMixin`        (`view_focus`)      — selection, hover, moon reveal, focus dimming.
   * `SearchFilterMixin` (`view_filters`)    — search dimming, map filter and its banner.
   * `DrawingToolsMixin` (`view_tools`)      — tool strip, drawing preview, add actions.
   * `InputMixin`        (`view_input`)      — mouse, keyboard and context-menu events.
   * `TourMotionMixin`   (`view_tour`)       — guided tour and orbit motion tick.
   * `OverlayMixin`      (`view_overlays`)   — overlay placement, resize, background.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from PySide6.QtCore import QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QPainter
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QPushButton, QToolButton, QWidget

from core.models import GalaxyStore, SolarSystem
from ui import theme
from ui import filters as map_filters
from ui.welcome import WelcomePanel
from ui.canvas.helpers import SHOW_ALL_DETAILS_KEY
from ui.canvas.link_item import ConstellationLinkItem
from ui.canvas.module_item import PlanetGraphicsItem
from ui.canvas.note_item import CanvasNoteGraphicsItem
from ui.canvas.overlays import EmptyStateOverlay
from ui.canvas.overview_items import GalaxyClusterItem, GalaxySectorBannerItem
from ui.canvas.sun_item import SunGraphicsItem
from ui.canvas.view_filters import SearchFilterMixin
from ui.canvas.view_focus import FocusMixin
from ui.canvas.view_input import InputMixin
from ui.canvas.view_navigation import NavigationMixin
from ui.canvas.view_overlays import OverlayMixin
from ui.canvas.view_scene import SceneBuildMixin
from ui.canvas.view_tools import DrawingToolsMixin
from ui.canvas.view_tour import TourMotionMixin


class SpaceCanvasView(
    SceneBuildMixin,
    NavigationMixin,
    FocusMixin,
    SearchFilterMixin,
    DrawingToolsMixin,
    InputMixin,
    TourMotionMixin,
    OverlayMixin,
    QGraphicsView,
):
    """Zoomable, pannable interactive architecture canvas.

    Behaviour by mixin: scene building and layout (`SceneBuildMixin`), fit / zoom / pan and
    view-mode transitions (`NavigationMixin`), selection / hover / focus (`FocusMixin`),
    search and map filter (`SearchFilterMixin`), drawing tools (`DrawingToolsMixin`),
    mouse / keyboard / context menu (`InputMixin`), guided tour and orbit motion
    (`TourMotionMixin`), overlays and background (`OverlayMixin`).
    """

    nodeSelected = Signal(object, str)  # (node_obj, "sun" | "planet" | "satellite" | "link" | "note")
    viewModeChanged = Signal(str)  # "system" | "galaxy"
    tourStepChanged = Signal(int, int, str)  # (current_idx, total, step_title)
    contextActionRequested = Signal(str, object)  # (action_name, target_obj)
    drawingToolChanged = Signal(str, str)  # (tool_key, status_hint)
    showAllDetailsChanged = Signal(bool)
    mapFilterChanged = Signal(str)  # status text ("" = none); see ui/filters.py

    IDLE_TICK_MS = 1000
    MOTION_TICK_MS = 33

    def __init__(self, store: GalaxyStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.store = store
        self.view_mode: str = "system"
        self.active_system: Optional[SolarSystem] = self.store.systems[0] if self.store.systems else None
        self.selected_node: Optional[object] = self.active_system

        self.scene_obj = QGraphicsScene(self)
        self.scene_obj.setSceneRect(-3200, -3200, 6400, 6400)
        self.setScene(self.scene_obj)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setBackgroundBrush(QBrush(theme.qcolor(theme.CANVAS_BG)))
        # Set by the main window: Ollama is enabled and a model was found. Without it the
        # context menus show no AI entries.
        self.ai_available = False

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        # Only exposed regions are repainted (no full-viewport repaint per update).
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

        self.orbit_motion_enabled = False
        self.show_all_details = bool(self.store.get_setting(SHOW_ALL_DETAILS_KEY, False))
        self._auto_fit = False
        self._press_on_empty = None
        self._current_scale = 1.0
        self.search_query: str = ""
        self.tour_index: int = -1

        self.drawing_tool: str = "select"
        self.drawing_layer: str = "core"
        self.drawing_sat_tag: str = "code"
        self.drawing_relation_type: str = "flows_to"
        self.drawing_note_color: str = "#F59E0B"

        self.is_drawing_link: bool = False
        self._link_source_item: Optional[PlanetGraphicsItem] = None
        self._draft_cursor_pos: Optional[QPointF] = None
        self._tool_buttons: Dict[str, QToolButton] = {}
        self._suboption_buttons: List[QPushButton] = []

        self.sun_item: Optional[SunGraphicsItem] = None
        self.planet_items: List[PlanetGraphicsItem] = []
        self.planet_item_map: Dict[str, PlanetGraphicsItem] = {}
        self.link_items: List[ConstellationLinkItem] = []
        self.note_items: List[CanvasNoteGraphicsItem] = []
        self.galaxy_items: List[GalaxyClusterItem] = []
        self.sector_banners: List[GalaxySectorBannerItem] = []
        self.hovered_galaxy_system: Optional[SolarSystem] = None
        self.hovered_planet_item: Optional[PlanetGraphicsItem] = None
        self._search_revealed: Set[str] = set()

        self._hover_hide_timer = QTimer(self)
        self._hover_hide_timer.setSingleShot(True)
        self._hover_hide_timer.setInterval(260)
        self._hover_hide_timer.timeout.connect(self._on_hover_hide_timeout)

        # What the map draws (view state only, never saved): see ui/filters.py.
        self.map_filter = map_filters.MapFilter()

        self._setup_drawing_toolbar()
        self.empty_overlay = EmptyStateOverlay(self)
        self.welcome_panel = WelcomePanel(lambda action: self.contextActionRequested.emit(action, None), self)
        self.filter_banner = map_filters.FilterBanner(self)
        self.filter_banner.resetRequested.connect(self.reset_map_filter)

        # Stays active for the busy-state freezer; idles at 1 Hz while motion is off.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_animation_tick)
        self.timer.start(self.IDLE_TICK_MS)

        self.rebuild_scene()
