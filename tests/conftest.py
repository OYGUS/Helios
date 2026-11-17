"""Session-wide test setup.

Windows: the default temp folder (`%TEMP%`, normally `C:\\Users\\<name>\\AppData\\Local\\Temp`)
lies below `AppData`, and the agent importer rightly never treats a folder below an
agent / system data location (`agent_importer._EXCLUDED_SEGMENTS`) as a project. Every
`tmp_path` project would be skipped there, so the tests could not exercise the importer.
When the default temp root is such a location, pytest's temp root (`PYTEST_DEBUG_TEMPROOT`,
read when the first `tmp_path` is made) is moved to `~/.helios-pytest` (outside AppData,
a dot folder so it is never mapped); pytest still makes `pytest-of-<user>/pytest-<N>` below
it and keeps only the last few runs. An explicitly set `PYTEST_DEBUG_TEMPROOT` or
`--basetemp` wins.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _move_temp_root_out_of_excluded_locations() -> None:
    if os.name != "nt" or os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return
    from connectors.agent_importer import _EXCLUDED_SEGMENTS

    default_root = Path(tempfile.gettempdir())
    if not any(part.lower() in _EXCLUDED_SEGMENTS for part in default_root.parts):
        return
    target = Path.home() / ".helios-pytest"
    if any(part.lower() in _EXCLUDED_SEGMENTS for part in target.parts):
        return
    try:
        target.mkdir(exist_ok=True)
    except OSError:
        return  # keep pytest's default; the importer tests will say why they fail
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(target)


_move_temp_root_out_of_excluded_locations()
