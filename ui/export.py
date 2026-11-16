"""Export the opened project's map as a PNG image or a Markdown document.

* :func:`render_map_image` draws the whole map (all of the scene's visible content, not
  just the part on screen) into an image at 2x, on the theme background, with a margin.
  Selection outlines, hover states and search dimming are switched off while it draws
  and restored afterwards; the active map filter is respected (hidden items stay hidden);
  widgets over the canvas (tool strip, banners) are not part of the scene, so they never
  appear. Very large maps are scaled down so neither side exceeds `EXPORT_MAX_SIDE` px.
* The Markdown text comes from the pure :func:`core.markdown_export.build_markdown`.
* :func:`export_map` asks where to save (standard dialog, default name from the project
  name), writes the file atomically and reports "Saved to …" in the status bar, or shows
  a clear message when the file could not be written.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from core.markdown_export import build_markdown, file_stem
from core.models import atomic_write_bytes, atomic_write_text
from ui import theme

EXPORT_SCALE = 2.0
EXPORT_MAX_SIDE = 8000  # px, per side
LAST_DIR_SETTING = "last_export_dir"
FORMATS = {
    "png": ("PNG image (*.png)", ".png", "-map"),
    "md": ("Markdown document (*.md)", ".md", "-architecture"),
}


def map_scene_rect(canvas) -> QRectF:
    """Scene area of everything drawn for the opened project (hidden items excluded)."""
    rect = QRectF()
    if canvas.sun_item is not None:
        rect = canvas.sun_item.sceneBoundingRect()
    for it in [*canvas.planet_items, *canvas.note_items]:
        if it.isVisible():
            rect = rect.united(it.sceneBoundingRect())
            for s_item in getattr(it, "satellite_items", []):
                if s_item.isVisible():
                    rect = rect.united(s_item.sceneBoundingRect())
            chip = getattr(it, "more_chip", None)
            if chip is not None and chip.isVisible():
                rect = rect.united(chip.sceneBoundingRect())
    m = float(theme.EXPORT_MARGIN)
    return rect.adjusted(-m, -m, m, m) if not rect.isEmpty() else rect


def export_scale(rect: QRectF, scale: float = EXPORT_SCALE, max_side: int = EXPORT_MAX_SIDE) -> float:
    """`scale`, reduced so the image fits `max_side` on both sides."""
    if rect.isEmpty():
        return scale
    return min(scale, max_side / max(1.0, rect.width()), max_side / max(1.0, rect.height()))


@contextmanager
def clean_view_state(canvas) -> Iterator[None]:
    """No selection, hover, tour highlight or search dimming while the map is drawn."""
    saved_sel = canvas.selected_node
    saved_hover = canvas.hovered_planet_item
    saved_query = canvas.search_query
    flags: List[Tuple[object, str, bool]] = []
    for it in canvas.scene_obj.items():
        for attr in ("_hovered", "is_tour_active"):
            if hasattr(it, attr) and getattr(it, attr):
                flags.append((it, attr, True))
                setattr(it, attr, False)
    try:
        canvas.selected_node = None
        canvas.hovered_planet_item = None
        if saved_query:
            canvas.search_query = ""
            canvas._apply_search_filter(center=False)
        canvas._update_focus_state()
        yield
    finally:
        for it, attr, val in flags:
            setattr(it, attr, val)
        canvas.selected_node = saved_sel
        canvas.hovered_planet_item = saved_hover
        if saved_query:
            canvas.search_query = saved_query
            canvas._apply_search_filter(center=False)
        canvas._update_focus_state()
        canvas.scene_obj.update()


def render_map_image(canvas, scale: float = EXPORT_SCALE, max_side: int = EXPORT_MAX_SIDE) -> QImage:
    """The whole opened map as an image (empty QImage when no project map is open)."""
    if canvas.view_mode != "system" or canvas.active_system is None:
        return QImage()
    with clean_view_state(canvas):
        rect = map_scene_rect(canvas)
        if rect.isEmpty():
            return QImage()
        s = export_scale(rect, scale, max_side)
        w = max(1, min(max_side, int(round(rect.width() * s))))
        h = max(1, min(max_side, int(round(rect.height() * s))))
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(theme.qcolor(theme.CANVAS_BG))
        painter = QPainter(img)
        try:
            painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                                   | QPainter.RenderHint.SmoothPixmapTransform)
            painter.scale(w / rect.width(), h / rect.height())
            painter.translate(-rect.left(), -rect.top())
            canvas.drawBackground(painter, rect)
            canvas.scene_obj.render(painter, rect, rect)
        finally:
            painter.end()
    return img


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = image.save(buf, "PNG")
    buf.close()
    if not ok:
        raise OSError("the image could not be encoded as PNG")
    return bytes(data.data())


def write_png(canvas, path: Path) -> QImage:
    image = render_map_image(canvas)
    if image.isNull():
        raise ValueError("there is no map to draw (open a project first)")
    atomic_write_bytes(Path(path), png_bytes(image))
    return image


def write_markdown(system, path: Path) -> str:
    from connectors.codebase_importer import stored_identity

    text = build_markdown(system, stored_identity)
    atomic_write_text(Path(path), text)
    return text


def default_path(store, system, fmt: str) -> Path:
    name = str(getattr(system, "project_name", "") or getattr(system, "name", "") or "")
    _flt, ext, suffix = FORMATS[fmt]
    last = str(store.get_setting(LAST_DIR_SETTING, "") or "") if store is not None else ""
    folder = Path(last) if last and Path(last).is_dir() else Path.home()
    return folder / f"{file_stem(name)}{suffix}{ext}"


def export_map(window: QWidget, system, fmt: Optional[str] = None) -> Optional[Path]:
    """Asks for a file name and writes the map; returns the path written (None = cancelled
    or failed). `fmt` "png" / "md" limits the dialog to one format."""
    canvas = window.canvas
    store = window.store
    if system is None:
        return None
    first = fmt or "png"
    filters = [FORMATS[first][0]] if fmt else [FORMATS["png"][0], FORMATS["md"][0]]
    path_str, chosen = QFileDialog.getSaveFileName(
        window, "Export map", str(default_path(store, system, first)), ";;".join(filters)
    )
    if not path_str:
        return None
    path = Path(path_str)
    kind = fmt
    if kind is None:
        kind = "md" if (path.suffix.lower() == ".md" or (path.suffix.lower() != ".png" and "Markdown" in (chosen or ""))) else "png"
    if path.suffix.lower() != FORMATS[kind][1]:
        path = path.with_name(path.name + FORMATS[kind][1])
    try:
        if kind == "png":
            if canvas.view_mode != "system" or canvas.active_system is not system:
                canvas.enter_solar_system(system)
            write_png(canvas, path)
        else:
            write_markdown(system, path)
    except Exception as exc:
        QMessageBox.warning(window, "Could not save the file",
                            f"Helios could not save the map to:\n{path}\n\n{exc}")
        window.status_label.setText(f"Export failed: {exc}")
        return None
    if store is not None:
        store.set_setting(LAST_DIR_SETTING, str(path.parent))
    window.status_label.setText(f"Saved to {path}")
    window.status_label.setToolTip(str(path))
    return path
