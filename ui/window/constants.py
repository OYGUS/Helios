"""Main-window constants: the legacy stylesheet alias and the inspector's colour / tag presets."""

from __future__ import annotations

from ui import theme


# Kept for compatibility: the stylesheet now lives in ui/theme.py.
DARK_STYLESHEET = theme.stylesheet()

PLANET_COLOR_PRESETS = [
    ("Blue (Architecture / UI)", "#3B82F6"),
    ("Cyan (Research)", "#06B6D4"),
    ("Emerald (Core / Code)", "#10B981"),
    ("Purple (Integration / API)", "#8B5CF6"),
    ("Amber (Docs / Plan)", "#F59E0B"),
    ("Rose (Security / Testing)", "#F43F5E"),
    ("Bronze (Configuration)", "#D4A574"),
]

SATELLITE_TAG_PRESETS = [
    ("Idea / Decision", "idea"),
    ("Code / Module", "code"),
    ("Architecture / Diagram", "diagram"),
    ("Note / Research", "thought"),
]
