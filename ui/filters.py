"""Map filter: what the opened project's map DRAWS (view state only, never the data).

* :class:`MapFilter` — the choices: hidden link kinds (see ``core.map_facts.LINK_KINDS``),
  hide the external-libraries module, hide the Work History module, hidden layers.
  Nothing here touches ``galaxy.json``, the undo history or ``is_user_modified``; the
  choices are not saved (every start shows the whole map).
* :func:`apply_to_canvas` — hides the filtered modules and links of a canvas (hidden
  graphics items can't be hovered, clicked or selected) and keeps the layout as it is,
  so nothing jumps around when a filter is toggled.
* :class:`FilterPopover` — the small panel under the top bar's "Filter" button: one
  check box per link kind / module group / layer that occurs in the opened project,
  each with its count, plus "Show everything".
* :class:`FilterBanner` — a pill on the canvas while a filter is active, with a one-click
  "Show all".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from core.map_facts import LINK_KINDS, link_kind
from core.models import LAYER_DEFINITIONS
from ui import theme

QUIET_PLANET_KINDS = ("external", "history")


@dataclass
class MapFilter:
    hidden_link_kinds: Set[str] = field(default_factory=set)
    hide_external: bool = False
    hide_history: bool = False
    hidden_layers: Set[str] = field(default_factory=set)

    def is_active(self) -> bool:
        return bool(self.hidden_link_kinds or self.hide_external or self.hide_history or self.hidden_layers)

    def active_count(self) -> int:
        return len(self.hidden_link_kinds) + int(self.hide_external) + int(self.hide_history) + len(self.hidden_layers)

    def reset(self) -> None:
        self.hidden_link_kinds.clear()
        self.hidden_layers.clear()
        self.hide_external = False
        self.hide_history = False

    def hides_planet(self, planet: object) -> bool:
        kind = str(getattr(planet, "kind", "") or "")
        if kind == "external":
            return self.hide_external
        if kind == "history":
            return self.hide_history
        return str(getattr(planet, "layer", "") or "") in self.hidden_layers

    def hides_link(self, link: object, hidden_planet_ids: Set[str]) -> bool:
        if getattr(link, "source_planet_id", "") in hidden_planet_ids:
            return True
        if getattr(link, "target_planet_id", "") in hidden_planet_ids:
            return True
        return link_kind(link) in self.hidden_link_kinds

    def hidden_planet_ids(self, system: object) -> Set[str]:
        return {p.id for p in list(getattr(system, "planets", []) or []) if self.hides_planet(p)}

    def hides_node(self, system: object, node: object) -> bool:
        """True when `node` (module, component or link of `system`) is not drawn."""
        if system is None or not self.is_active():
            return False
        hidden = self.hidden_planet_ids(system)
        if hasattr(node, "satellites"):
            return getattr(node, "id", None) in hidden
        if hasattr(node, "source_planet_id"):
            return self.hides_link(node, hidden)
        for p in getattr(system, "planets", []) or []:
            if any(s is node for s in p.satellites):
                return p.id in hidden
        return False


def filter_options(system: object) -> Dict[str, Dict[str, int]]:
    """What the opened project offers: {"links": kind -> n, "modules": kind -> n, "layers": key -> n}."""
    links: Dict[str, int] = {}
    for link in list(getattr(system, "links", []) or []):
        k = link_kind(link)
        links[k] = links.get(k, 0) + 1
    order = list(LINK_KINDS)
    links = {k: links[k] for k in sorted(links, key=lambda k: order.index(k) if k in order else 99)}
    modules: Dict[str, int] = {}
    layers: Dict[str, int] = {}
    for p in list(getattr(system, "planets", []) or []):
        kind = str(getattr(p, "kind", "") or "")
        if kind in QUIET_PLANET_KINDS:
            modules[kind] = len(p.satellites)
            continue
        layer = str(getattr(p, "layer", "") or "")
        if layer:
            layers[layer] = layers.get(layer, 0) + 1
    lorder = list(LAYER_DEFINITIONS)
    layers = {k: layers[k] for k in sorted(layers, key=lambda k: lorder.index(k) if k in lorder else 99)}
    return {"links": links, "modules": modules, "layers": layers}


def apply_to_canvas(canvas: object) -> None:
    """Shows / hides the canvas's module and link items for its `map_filter`."""
    flt: MapFilter = getattr(canvas, "map_filter", None) or MapFilter()
    system = getattr(canvas, "active_system", None)
    hidden = flt.hidden_planet_ids(system) if (system is not None and flt.is_active()) else set()
    for p_item in getattr(canvas, "planet_items", []):
        p_item.setVisible(p_item.planet.id not in hidden)
    for l_item in getattr(canvas, "link_items", []):
        l_item.setVisible(not (flt.is_active() and flt.hides_link(l_item.link, hidden)))


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------
MODULE_GROUP_LABELS = {"external": "External libraries", "history": "Work History"}


def _count_text(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


class FilterPopover(QFrame):
    """The panel that opens under the "Filter" button (a popup window)."""

    changed = Signal()

    def __init__(self, get_system: Callable[[], object], get_filter: Callable[[], MapFilter],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("FilterPopover")
        self._get_system = get_system
        self._get_filter = get_filter
        self.boxes: Dict[str, QCheckBox] = {}
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(theme.SPACE_L, theme.SPACE_M, theme.SPACE_L, theme.SPACE_M)
        self._lay.setSpacing(theme.SPACE_XS + 2)

    def _clear(self) -> None:
        self.boxes.clear()
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                lay = item.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()

    def rebuild(self) -> None:
        self._clear()
        system = self._get_system()
        flt = self._get_filter()
        opts = filter_options(system) if system is not None else {"links": {}, "modules": {}, "layers": {}}
        head = QHBoxLayout()
        head.setSpacing(theme.SPACE_S)
        head.addWidget(theme.label("Show on the map", "heading"), 1)
        self.btn_reset = QPushButton("Show everything")
        self.btn_reset.setObjectName("LinkBtn")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setEnabled(flt.is_active())
        self.btn_reset.clicked.connect(self._reset)
        head.addWidget(self.btn_reset, 0)
        self._lay.addLayout(head)
        self._lay.addWidget(theme.label("Only changes what is drawn; your project is not changed.", "muted", wrap=True))

        if opts["links"]:
            self._section("Links")
            for kind, n in opts["links"].items():
                label, tip = LINK_KINDS.get(kind, (kind, ""))
                self._box(f"link:{kind}", f"{label}  ·  {n}", tip, kind not in flt.hidden_link_kinds)
        if opts["modules"]:
            self._section("Extra modules")
            for kind, n in opts["modules"].items():
                checked = not (flt.hide_external if kind == "external" else flt.hide_history)
                unit = ("package", "packages") if kind == "external" else ("item", "items")
                tip = ("Third-party libraries the code imports" if kind == "external"
                       else "Sessions from your AI coding tools (not part of the code)")
                self._box(f"module:{kind}", f"{MODULE_GROUP_LABELS[kind]}  ·  {_count_text(n, *unit)}", tip, checked)
        if opts["layers"]:
            self._section("Module layers")
            for layer, n in opts["layers"].items():
                title = LAYER_DEFINITIONS.get(layer, (layer, ""))[0]
                self._box(f"layer:{layer}", f"{title}  ·  {n}", "", layer not in flt.hidden_layers)
        if not any(opts.values()):
            self._lay.addWidget(theme.label("This project has nothing to filter yet.", "secondary", wrap=True))
        self.setFixedWidth(300)
        self.adjustSize()

    def _section(self, title: str) -> None:
        self._lay.addSpacing(theme.SPACE_XS)
        self._lay.addWidget(theme.label(title, "section"))

    def _box(self, key: str, text: str, tip: str, checked: bool) -> None:
        box = QCheckBox(text.replace("&", "&&"))  # "&" would become a keyboard mnemonic
        box.setChecked(checked)
        if tip:
            box.setToolTip(tip)
        box.toggled.connect(lambda on, k=key: self._toggled(k, on))
        self.boxes[key] = box
        self._lay.addWidget(box)

    def _toggled(self, key: str, shown: bool) -> None:
        flt = self._get_filter()
        group, _, value = key.partition(":")
        if group == "link":
            (flt.hidden_link_kinds.discard if shown else flt.hidden_link_kinds.add)(value)
        elif group == "layer":
            (flt.hidden_layers.discard if shown else flt.hidden_layers.add)(value)
        elif value == "external":
            flt.hide_external = not shown
        elif value == "history":
            flt.hide_history = not shown
        self.btn_reset.setEnabled(flt.is_active())
        self.changed.emit()

    def _reset(self) -> None:
        self._get_filter().reset()
        for box in self.boxes.values():
            box.blockSignals(True)
            box.setChecked(True)
            box.blockSignals(False)
        self.btn_reset.setEnabled(False)
        self.changed.emit()

    def open_below(self, anchor: QWidget) -> None:
        self.rebuild()
        pos = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 4))
        screen = anchor.screen().availableGeometry() if anchor.screen() is not None else None
        if screen is not None:
            pos.setX(max(screen.left() + 4, min(pos.x(), screen.right() - self.width() - 4)))
        self.move(pos)
        self.show()


class FilterBanner(QFrame):
    """Pill at the bottom of the canvas while a filter hides something."""

    resetRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBanner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.SPACE_M, 3, theme.SPACE_S, 3)
        lay.setSpacing(theme.SPACE_S)
        self.lbl = theme.label("", "caption")
        lay.addWidget(self.lbl)
        self.btn = QPushButton("Show all")
        self.btn.setObjectName("LinkBtn")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.resetRequested.emit)
        lay.addWidget(self.btn)
        self.hide()

    def set_counts(self, modules: int, links: int) -> None:
        parts: List[str] = []
        if modules:
            parts.append(_count_text(modules, "module", "modules"))
        if links:
            parts.append(_count_text(links, "link", "links"))
        self.setMaximumWidth(16777215)
        self.lbl.setText(("Filter on · hidden: " + ", ".join(parts)) if parts else "Filter on · nothing hidden here")
        self.btn.setToolTip("Turn the filter off and show the whole map")
        self.adjustSize()
