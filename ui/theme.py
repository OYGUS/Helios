"""Helios visual theme: palette, spacing, radii, type scale and the application stylesheet.

Everything that decides how Helios looks lives here, so the window, dialogs and the
canvas share one calm, dark-but-soft look:

* neutral dark greys for surfaces (no saturated navy), few borders, generous spacing;
* one warm "sun" accent (amber), used sparingly: the sun, the primary button, selection;
* stored model colours (layer / source / relation hex values) are never changed — the UI
  maps them to softened display colours with :func:`soft_color`;
* body text keeps a contrast ratio of at least 4.5:1 against every surface it sits on.

Widgets opt into styles through object names (``PrimaryBtn``, ``QuietBtn``, ``Card`` …)
and the dynamic ``role`` property on labels (``title``, ``section``, ``secondary`` …)
instead of inline ``setStyleSheet`` calls with colour literals.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Dict, Optional

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QStyleFactory, QWidget

# ----------------------------------------------------------------------------
# Palette
# ----------------------------------------------------------------------------
BG = "#15171B"  # window / canvas background
PANEL = "#1B1E24"  # side panels, top bar, status bar
RAISED = "#23272F"  # cards, menus, quiet buttons
HOVER = "#2A2F38"  # hover state of rows and quiet buttons
PRESSED = "#31363F"
INPUT = "#202329"  # text fields
HAIRLINE = "#2C313A"  # the few borders that remain
BORDER_STRONG = "#3A404B"

TEXT = "#E6E8EC"  # primary text (13.6:1 on PANEL)
TEXT_2 = "#9AA3B2"  # secondary text (6.6:1 on PANEL)
MUTED = "#828B9A"  # captions / hints (4.8:1 on PANEL)
DISABLED = "#5A626F"

ACCENT = "#E2A956"  # the sun: warm amber, used sparingly
ACCENT_HOVER = "#EBB86E"
ACCENT_PRESSED = "#D39A47"
ON_ACCENT = "#1C1408"  # text on the accent (10:1)
ACCENT_SOFT = "rgba(226, 169, 86, 0.16)"  # selection background
ACCENT_TEXT = "#F0C27F"  # accent-coloured text on dark surfaces

DANGER = "#E58C8C"
SUCCESS = "#86C79A"
FOCUS = ACCENT

CANVAS_BG = "#15171B"

# ----------------------------------------------------------------------------
# Spacing, radii, type scale (px)
# ----------------------------------------------------------------------------
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24

RADIUS_S = 4
RADIUS_M = 6
RADIUS_L = 10

FS_CAPTION = 11
FS_SMALL = 12
FS_BODY = 13
FS_SECTION = 13
FS_HEADING = 15
FS_TITLE = 17

CONTROL_H = 28  # buttons, fields, segmented controls
ROW_H = 30  # tree / list rows

# Project purpose (what the project is for) in the pinned project header.
PURPOSE_MAX_LINES = 3
# Identity line (what the project IS, derived from the code analysis) under the purpose,
# in the header and on project cards: smaller and muted so it never reads as the purpose.
IDENTITY_FS = FS_CAPTION
IDENTITY_COLOR = MUTED
IDENTITY_CARD_LINE_H = 16.0  # height of its line on a project card
# AI purpose suggestion (not the purpose until the user accepts it): a separate, subtly
# tinted and bordered box with a small "Suggested" tag and the sentence in muted italics.
SUGGESTION_BG = "rgba(154, 163, 178, 0.07)"
SUGGESTION_BORDER = "#343A45"
SUGGESTION_TEXT = TEXT_2  # muted italic, still readable (6.6:1)
SUGGESTION_TAG_BG = "rgba(154, 163, 178, 0.16)"
SUGGESTION_TAG_TEXT = TEXT_2
SUGGESTION_CARD_COLOR = MUTED  # "Suggested: …" line on project cards
# Fit-to-view never zooms out so far that module titles render smaller than this
# (the map can be panned instead).
MAP_MIN_TITLE_PX = 10.5
# Margin (scene px) around the map when fitting it to the view.
MAP_FIT_MARGIN = 22.0

MONO_FAMILIES = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo", "monospace")

# Plain words for the stored kind values (one vocabulary: Project / Module / Component / Link).
PLANET_KIND_LABELS: Dict[str, str] = {
    "file": "File",
    "dir": "Folder",
    "layer": "Layer group",
    "history": "Session history",
    "external": "External libraries",
}
SATELLITE_KIND_LABELS: Dict[str, str] = {
    "function": "Function",
    "class": "Class",
    "method": "Method",
    "constants": "Constants",
    "file": "File",
    "package": "Package",
    "session": "Session",
    "document": "Document",
}
# Small text glyphs used as kind icons in lists (exist in Segoe UI / Segoe UI Symbol and
# degrade to plain letters elsewhere).
SATELLITE_KIND_GLYPHS: Dict[str, str] = {
    "function": "ƒ",
    "method": "ƒ",
    "class": "C",
    "constants": "=",
    "file": "▤",
    "package": "◫",
    "session": "◷",
    "document": "▤",
}


# ----------------------------------------------------------------------------
# Colour helpers
# ----------------------------------------------------------------------------
def qcolor(value: str, alpha: Optional[int] = None) -> QColor:
    c = QColor(value)
    if not c.isValid():
        c = QColor(TEXT_2)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


@lru_cache(maxsize=256)
def soft_color_hex(hex_value: str) -> str:
    """Maps a stored (often saturated) colour to a calm display colour of the same hue."""
    c = QColor(hex_value or "")
    if not c.isValid():
        return TEXT_2
    h, s, l, _ = c.getHslF()
    if h < 0:  # achromatic
        return QColor.fromHslF(0.0, 0.0, 0.62).name()
    s = min(0.42, s * 0.55 + 0.05)
    l = 0.64
    return QColor.fromHslF(h, s, l).name()


def soft_color(hex_value: str, alpha: Optional[int] = None) -> QColor:
    return qcolor(soft_color_hex(hex_value), alpha)


def rgba(hex_value: str, alpha: float) -> str:
    c = QColor(hex_value)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"


def contrast_ratio(fg: str, bg: str) -> float:
    def lum(hex_value: str) -> float:
        c = QColor(hex_value)
        vals = []
        for v in (c.redF(), c.greenF(), c.blueF()):
            vals.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]

    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ----------------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------------
def ui_font_family() -> str:
    if sys.platform.startswith("win"):
        return "Segoe UI"
    try:
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    except Exception:  # pragma: no cover - no QGuiApplication yet
        return "Sans Serif"


def mono_font_family() -> str:
    try:
        families = set(QFontDatabase.families())
    except Exception:  # pragma: no cover
        families = set()
    for fam in MONO_FAMILIES:
        if fam in families:
            return fam
    try:
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    except Exception:  # pragma: no cover
        return "monospace"


def font(px: int = FS_BODY, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False) -> QFont:
    """A UI font with a pixel size (used by the canvas painters)."""
    f = QFont(mono_font_family() if mono else ui_font_family())
    f.setPixelSize(int(px))
    f.setWeight(weight)
    return f


# ----------------------------------------------------------------------------
# Widget helpers
# ----------------------------------------------------------------------------
def set_role(widget: QWidget, role: str) -> QWidget:
    """Tags a widget with a stylesheet role (``QLabel[role="section"]`` …) and repolishes it."""
    widget.setProperty("role", role)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    return widget


def label(text: str = "", role: str = "", wrap: bool = False, parent: Optional[QWidget] = None) -> QLabel:
    lbl = QLabel(text, parent)
    if role:
        lbl.setProperty("role", role)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def repolish(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


# ----------------------------------------------------------------------------
# Stylesheet
# ----------------------------------------------------------------------------
def build_stylesheet() -> str:
    mono = ", ".join(f'"{f}"' if " " in f else f for f in MONO_FAMILIES)
    return f"""
* {{
    font-size: {FS_BODY}px;
}}
QMainWindow, QDialog {{
    background-color: {BG};
    color: {TEXT};
}}
QWidget#CentralRoot {{
    background-color: {BG};
}}
QLabel {{
    color: {TEXT};
    background: transparent;
}}
QLabel[role="title"] {{ font-size: {FS_TITLE}px; font-weight: 600; color: {TEXT}; }}
QLabel[role="heading"] {{ font-size: {FS_HEADING}px; font-weight: 600; color: {TEXT}; }}
QLabel[role="section"] {{ font-size: {FS_SECTION}px; font-weight: 600; color: {TEXT_2}; }}
QLabel[role="secondary"] {{ color: {TEXT_2}; }}
QLabel[role="caption"] {{ font-size: {FS_SMALL}px; color: {TEXT_2}; }}
QLabel[role="muted"] {{ font-size: {FS_SMALL}px; color: {MUTED}; }}
QLabel[role="mono"] {{ font-family: {mono}; font-size: {FS_SMALL}px; color: {TEXT_2}; }}
QLabel[role="brand"] {{ font-size: 15px; font-weight: 600; color: {TEXT}; }}
QLabel[role="ok"] {{ font-size: {FS_SMALL}px; color: {SUCCESS}; }}
QLabel[role="warn"] {{ font-size: {FS_SMALL}px; color: {ACCENT_TEXT}; }}
QLabel[role="crumb-sep"] {{ color: {MUTED}; }}
QLabel[role="crumb"] {{ color: {TEXT}; font-weight: 600; }}
QLabel[role="body"] {{ color: {TEXT}; }}
QLabel[role="lead"] {{ font-size: {FS_HEADING}px; color: {TEXT}; }}
QLabel#PurposeText {{
    color: {TEXT};
    border-radius: {RADIUS_S}px;
    padding: 1px 0px;
}}
QLabel#PurposeText:hover {{ background-color: {HOVER}; }}
QLabel#PurposePlaceholder {{ color: {MUTED}; font-style: italic; }}
QFrame#SuggestionBox {{
    background-color: {SUGGESTION_BG};
    border: 1px dashed {SUGGESTION_BORDER};
    border-radius: {RADIUS_M}px;
}}
QLabel#SuggestionTag {{
    background-color: {SUGGESTION_TAG_BG};
    color: {SUGGESTION_TAG_TEXT};
    border-radius: 8px;
    padding: 0px 7px;
    font-size: {FS_CAPTION}px;
    font-weight: 600;
}}
QLabel#SuggestionText {{ color: {SUGGESTION_TEXT}; font-style: italic; background: transparent; }}
QPushButton#SuggestionBtn {{
    background-color: transparent;
    color: {TEXT_2};
    border: 1px solid {HAIRLINE};
    border-radius: {RADIUS_S}px;
    padding: 0px 10px;
    min-height: 22px;
    max-height: 22px;
    font-size: {FS_SMALL}px;
}}
QPushButton#SuggestionBtn:hover {{ background-color: {HOVER}; color: {TEXT}; }}
QPushButton#PurposeAddBtn {{
    background: transparent;
    color: {MUTED};
    font-style: italic;
    padding: 0px;
    min-height: 20px;
    text-align: left;
}}
QPushButton#PurposeAddBtn:hover {{ color: {TEXT_2}; text-decoration: underline; background: transparent; }}
QLabel#IdentityLine {{ font-size: {IDENTITY_FS}px; color: {IDENTITY_COLOR}; padding: 0px; }}
QLabel#Chip {{
    background-color: {HOVER};
    color: {TEXT_2};
    border-radius: 9px;
    padding: 1px 8px;
    font-size: {FS_CAPTION}px;
}}
QLabel#Chip[tone="accent"] {{
    background-color: {ACCENT_SOFT};
    color: {ACCENT_TEXT};
}}
QLabel a {{ color: {ACCENT_TEXT}; }}

/* ---------- surfaces ---------- */
QFrame#TopBar {{
    background-color: {PANEL};
    border-bottom: 1px solid {HAIRLINE};
}}
QFrame#LeftPanel, QFrame#Inspector {{
    background-color: {PANEL};
}}
QFrame#StatusBar {{
    background-color: {PANEL};
    border-top: 1px solid {HAIRLINE};
}}
QFrame#ProjectHeader {{
    background-color: {PANEL};
    border-bottom: 1px solid {HAIRLINE};
}}
QFrame#Card {{
    background-color: {RAISED};
    border-radius: {RADIUS_L}px;
}}
QFrame#AiSummaryBox {{
    background-color: {RAISED};
    border: 1px dashed {BORDER_STRONG};
    border-radius: {RADIUS_M}px;
}}
QFrame#TourCard {{
    background-color: {ACCENT_SOFT};
    border-radius: {RADIUS_M}px;
}}
QFrame#EditSection {{
    background-color: {RAISED};
    border-radius: {RADIUS_L}px;
}}
QFrame#Hairline {{
    background-color: {HAIRLINE};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}
QFrame#EmptyState {{
    background-color: {PANEL};
    border: 1px solid {HAIRLINE};
    border-radius: 12px;
}}
QWidget#Transparent, QWidget#InspectorBody {{
    background: transparent;
}}

/* ---------- buttons ---------- */
QPushButton, QToolButton {{
    background-color: {RAISED};
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: {RADIUS_M}px;
    padding: 0px 12px;
    min-height: {CONTROL_H - 2}px;
}}
QPushButton:hover, QToolButton:hover {{ background-color: {HOVER}; }}
QPushButton:pressed, QToolButton:pressed {{ background-color: {PRESSED}; }}
QPushButton:focus, QToolButton:focus {{ border: 1px solid {FOCUS}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {DISABLED}; background-color: {PANEL}; }}
QPushButton:checked {{ background-color: {ACCENT_SOFT}; color: {ACCENT_TEXT}; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; }}
QPushButton#PrimaryBtn {{
    background-color: {ACCENT};
    color: {ON_ACCENT};
    font-weight: 600;
}}
QPushButton#PrimaryBtn:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton#PrimaryBtn:pressed {{ background-color: {ACCENT_PRESSED}; }}
QPushButton#PrimaryBtn:focus {{ border: 1px solid {TEXT}; }}
QPushButton#PrimaryBtn:disabled {{ background-color: {RAISED}; color: {DISABLED}; }}
QPushButton#PrimaryBtn[menu="true"] {{ padding-right: 24px; }}
QPushButton#QuietBtn, QPushButton#SubtleBtn, QPushButton#OllamaBtn {{
    background-color: transparent;
    color: {TEXT_2};
    padding: 0px 8px;
}}
QPushButton#QuietBtn:hover, QPushButton#SubtleBtn:hover, QPushButton#OllamaBtn:hover {{
    background-color: {HOVER};
    color: {TEXT};
}}
QPushButton#QuietBtn:checked, QPushButton#SubtleBtn:checked {{
    background-color: {ACCENT_SOFT};
    color: {ACCENT_TEXT};
}}
QPushButton#QuietBtn:disabled, QPushButton#SubtleBtn:disabled, QPushButton#OllamaBtn:disabled {{
    background-color: transparent;
    color: {DISABLED};
}}
QPushButton#SecondaryBtn {{
    background-color: {RAISED};
    color: {TEXT};
}}
QPushButton#DangerBtn {{
    background-color: transparent;
    color: {DANGER};
}}
QPushButton#DangerBtn:hover {{ background-color: {rgba(DANGER, 0.12)}; }}
QPushButton#DangerBtn:disabled {{ color: {DISABLED}; }}
QPushButton#ChoiceBtn {{
    text-align: left;
    padding: 9px 14px;
    background-color: {RAISED};
    border: 1px solid {HAIRLINE};
    border-radius: {RADIUS_L}px;
    min-height: 40px;
}}
QPushButton#ChoiceBtn:hover {{ background-color: {HOVER}; border: 1px solid {BORDER_STRONG}; }}
QPushButton#ChoiceBtn:focus {{ border: 1px solid {FOCUS}; }}
QPushButton#ChoiceBtn[tone="danger"] {{ color: {DANGER}; }}
QFrame#IgnoredRow {{
    background-color: {INPUT};
    border-radius: {RADIUS_M}px;
}}
QPushButton#LinkBtn {{
    background: transparent;
    color: {ACCENT_TEXT};
    padding: 0px 2px;
    min-height: 20px;
    text-align: left;
}}
QPushButton#LinkBtn:hover {{ text-decoration: underline; background: transparent; }}
QPushButton#LinkBtn:disabled {{ color: {DISABLED}; background: transparent; }}
QPushButton#CrumbBtn {{
    background: transparent;
    color: {TEXT_2};
    padding: 0px 6px;
}}
QPushButton#CrumbBtn:hover {{ color: {TEXT}; background-color: {HOVER}; }}
QPushButton#CrumbBtn[current="true"] {{ color: {TEXT}; font-weight: 600; }}
QPushButton#RowBtn {{
    background-color: transparent;
    border-radius: {RADIUS_M}px;
    padding: 0px 8px;
    min-height: {ROW_H - 2}px;
    text-align: left;
}}
QPushButton#RowBtn:hover {{ background-color: {HOVER}; }}
QPushButton#ChipBtn {{
    background-color: {HOVER};
    color: {TEXT_2};
    border-radius: 10px;
    padding: 0px 10px;
    min-height: 20px;
    font-size: {FS_CAPTION}px;
}}
QPushButton#ChipBtn:hover {{ color: {TEXT}; background-color: {PRESSED}; }}

/* segmented control */
QFrame#Segmented {{
    background-color: {BG};
    border-radius: 7px;
}}
QPushButton#SegBtn {{
    background: transparent;
    color: {TEXT_2};
    border-radius: 5px;
    min-height: 22px;
    padding: 0px 10px;
    font-size: {FS_SMALL}px;
}}
QPushButton#SegBtn:hover {{ color: {TEXT}; }}
QPushButton#SegBtn:checked {{ background-color: {RAISED}; color: {TEXT}; }}

/* canvas tool strip */
QFrame#ToolStrip, QFrame#DrawingSubBar {{
    background-color: {rgba(PANEL, 0.94)};
    border: 1px solid {HAIRLINE};
    border-radius: {RADIUS_L}px;
}}
QToolButton#ToolBtn {{
    background: transparent;
    border-radius: {RADIUS_M}px;
    padding: 0px;
    min-height: 32px;
    min-width: 32px;
}}
QToolButton#ToolBtn:hover {{ background-color: {HOVER}; }}
QToolButton#ToolBtn:checked {{ background-color: {ACCENT_SOFT}; }}
QPushButton#SubOptBtn {{
    background: transparent;
    color: {TEXT_2};
    min-height: 24px;
    padding: 0px 8px;
    font-size: {FS_SMALL}px;
}}
QPushButton#SubOptBtn:hover {{ background-color: {HOVER}; color: {TEXT}; }}
QPushButton#SubOptBtn:checked {{ background-color: {ACCENT_SOFT}; color: {ACCENT_TEXT}; }}

/* ---------- inputs ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
    background-color: {INPUT};
    color: {TEXT};
    border: 1px solid {HAIRLINE};
    border-radius: {RADIUS_M}px;
    padding: 4px 8px;
    selection-background-color: {rgba(ACCENT, 0.35)};
    selection-color: {TEXT};
}}
QLineEdit, QComboBox {{ min-height: {CONTROL_H - 10}px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid {FOCUS};
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {DISABLED}; }}
QLineEdit#SearchField {{
    background-color: {BG};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {RAISED};
    color: {TEXT};
    border: 1px solid {HAIRLINE};
    selection-background-color: {HOVER};
    outline: 0;
}}
QCheckBox {{ color: {TEXT}; spacing: 8px; }}
QCheckBox:focus {{ color: {ACCENT_TEXT}; }}

/* ---------- tree ---------- */
QTreeWidget {{
    background: transparent;
    border: none;
    outline: 0;
    color: {TEXT};
}}
QTreeWidget::item {{
    min-height: {ROW_H - 4}px;
    padding: 2px 4px;
    border-radius: {RADIUS_M}px;
}}
QTreeWidget::item:hover {{ background-color: {HOVER}; }}
QTreeWidget::item:selected {{ background-color: {ACCENT_SOFT}; color: {TEXT}; }}
QTreeWidget:focus {{ border: none; }}

/* ---------- scrolling ---------- */
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: rgba(255, 255, 255, 0.10);
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{ min-height: 28px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; }}
QScrollBar::handle:hover {{ background-color: rgba(255, 255, 255, 0.20); }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QSplitter::handle {{ background-color: {BG}; }}
QSplitter::handle:horizontal {{ width: 3px; }}
QSplitter::handle:hover {{ background-color: {HAIRLINE}; }}

/* ---------- popups ---------- */
QToolTip {{
    background-color: {RAISED};
    color: {TEXT};
    border: 1px solid {HAIRLINE};
    padding: 6px 8px;
    border-radius: {RADIUS_M}px;
}}
QMenu {{
    background-color: {RAISED};
    color: {TEXT};
    border: 1px solid {HAIRLINE};
    padding: 6px;
    border-radius: 8px;
}}
QMenu::item {{ padding: 6px 16px; border-radius: {RADIUS_M}px; }}
QMenu::item:selected {{ background-color: {HOVER}; }}
QMenu::item:disabled {{ color: {DISABLED}; }}
QMenu::separator {{ height: 1px; background-color: {HAIRLINE}; margin: 6px 4px; }}

QProgressBar#BusyBar {{
    background-color: {HAIRLINE};
    border: none;
    border-radius: 2px;
    max-height: 4px;
    min-height: 4px;
}}
QProgressBar#BusyBar::chunk {{ background-color: {ACCENT}; border-radius: 2px; }}

/* ---------- integrations page ---------- */
QFrame#IntegrationCard {{
    background-color: {RAISED};
    border: 1px solid transparent;
    border-radius: {RADIUS_L}px;
}}
QFrame#IntegrationCard:hover {{ border: 1px solid {HAIRLINE}; }}
QFrame#IntegrationCard[state="locked"] {{ background-color: {PANEL}; }}
QLabel#Monogram {{
    background-color: {BG};
    border-radius: 8px;
    font-size: {FS_CAPTION}px;
    font-weight: 600;
}}
QPushButton#ToggleBtn {{ min-width: 92px; background-color: {HOVER}; }}
QPushButton#ToggleBtn:hover {{ background-color: {PRESSED}; }}
QPushButton#ToggleBtn[state="on"] {{ background-color: {ACCENT_SOFT}; color: {ACCENT_TEXT}; }}
QPushButton#ToggleBtn[state="locked"] {{ background-color: transparent; color: {TEXT_2}; }}
"""


@lru_cache(maxsize=1)
def stylesheet() -> str:
    return build_stylesheet()


def build_palette() -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: INPUT,
        QPalette.ColorRole.AlternateBase: RAISED,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: RAISED,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.BrightText: TEXT,
        QPalette.ColorRole.Highlight: HOVER,
        QPalette.ColorRole.HighlightedText: TEXT,
        QPalette.ColorRole.ToolTipBase: RAISED,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.Link: ACCENT_TEXT,
        QPalette.ColorRole.Light: HOVER,
        QPalette.ColorRole.Midlight: RAISED,
        QPalette.ColorRole.Mid: HAIRLINE,
        QPalette.ColorRole.Dark: PANEL,
        QPalette.ColorRole.Shadow: "#0B0C0E",
    }
    for role, value in roles.items():
        pal.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED))
    return pal


def apply_app_theme(app: Optional[QApplication] = None) -> None:
    """Applies the Helios style, palette, font and stylesheet to the application (idempotent)."""
    app = app or QApplication.instance()
    if app is None:
        return
    if app.property("heliosThemeApplied"):
        return
    fusion = QStyleFactory.create("Fusion")
    if fusion is not None:
        app.setStyle(fusion)
    app.setPalette(build_palette())
    base = QFont(ui_font_family())
    base.setPixelSize(FS_BODY)
    app.setFont(base)
    app.setStyleSheet(stylesheet())
    app.setProperty("heliosThemeApplied", True)
