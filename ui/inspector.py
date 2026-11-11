"""Building blocks of the Helios inspector panel (right side) and the status-bar helpers.

* :func:`render_text_html` turns stored generated text into calm, read-only HTML
  (old ALL-CAPS heading lines become small sentence-case labels, "• " lines become list
  rows, markdown asterisks / backticks are never shown raw). Display only: stored text is
  never rewritten.
* :class:`RowButton` is a clickable, keyboard-focusable list row (Uses / Used by /
  Contains / Calls …).
* :class:`ExpandableText` shows rendered text with a "Show more" expander.
* :class:`ClampedLabel` / :class:`PurposeEditor` / :class:`EvidenceViewer` show and edit a
  project's purpose (what the project is for) in the pinned project header.
* :func:`skipped_folder_summary` and :class:`SkippedFoldersDialog` explain folders that the
  last sync skipped (``connectors.agent_importer.LAST_SCAN_STATS``), read defensively.
"""

from __future__ import annotations

import html
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QKeyEvent, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from connectors.purpose_evidence import assess_purpose_evidence
from ui import theme

# Acronyms kept upper-case when a heading is turned into sentence case.
_ACRONYMS = {
    "AI", "API", "AST", "CLI", "CPU", "CSS", "GPU", "HTML", "HTTP", "ID", "IO", "JS", "JSON", "LLM",
    "MD", "OS", "PR", "SQL", "TS", "TTS", "UI", "URL", "UX", "README", "TODO", "MCP", "STT",
}
_HEADING_RE = re.compile(r"^([A-Z0-9][A-Z0-9 &/()'\-,.+]{2,}?)\s*:\s*(.*)$")


def _upper_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(ch.isupper() for ch in letters) / len(letters)


def sentence_case(text: str) -> str:
    """"PROJECT PURPOSE & OVERVIEW (WHAT IT DOES)" -> "Project purpose & overview (what it does)"."""
    words = text.split(" ")
    out: List[str] = []
    for i, w in enumerate(words):
        core = re.sub(r"[^A-Za-z0-9]", "", w)
        if core.upper() in _ACRONYMS and core.isupper():
            out.append(w)
            continue
        lw = w.lower()
        if not out:
            # capitalise the first letter of the first word
            for j, ch in enumerate(lw):
                if ch.isalpha():
                    lw = lw[:j] + ch.upper() + lw[j + 1:]
                    break
        out.append(lw)
    return " ".join(out)


def _inline(text: str) -> str:
    """Escapes text and turns **bold**, __bold__, `code` into HTML; stray asterisks vanish."""
    esc = html.escape(text, quote=False)
    esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
    esc = re.sub(r"__(.+?)__", r"<b>\1</b>", esc)
    esc = re.sub(
        r"`([^`]+)`",
        lambda m: f"<span style=\"font-family:'{theme.mono_font_family()}'; color:{theme.TEXT_2};\">{m.group(1)}</span>",
        esc,
    )
    esc = re.sub(r"(?<!\w)\*(\S[^*]*?)\*(?!\w)", r"<i>\1</i>", esc)
    esc = esc.replace("**", "").replace("*", "") if "*" in esc else esc
    return esc


_NUMBERED_RE = re.compile(r"^(\d{1,3})[.)]\s+(.+)$")


def strip_emphasis(text: str) -> str:
    """Removes markdown emphasis markers (**bold**, __bold__, *em*, _em_) and keeps the words."""
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    out = re.sub(r"__(.+?)__", r"\1", out)
    out = re.sub(r"(?<!\w)\*(\S[^*]*?)\*(?!\w)", r"\1", out)
    out = re.sub(r"(?<!\w)_(\S[^_]*?)_(?!\w)", r"\1", out)
    return out.replace("**", "").replace("__", "")


def _clean_list_item(text: str) -> str:
    """A README list item as a clean row: no emphasis markers, no dangling label colon."""
    body = strip_emphasis(text).strip()
    if body.endswith(":") and len(body) > 1:
        body = body[:-1].rstrip()
    return body


def split_blocks(text: str) -> List[Tuple[str, str]]:
    """Splits stored text into display blocks.

    Kinds: ("heading", t), ("bullet", t), ("numbered", "n\0t"), ("para", t), ("kv", "k\0v"),
    ("gap", ""). List items (``- x``, ``• x``, ``1. x``) lose their emphasis markers.
    """
    blocks: List[Tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if blocks and blocks[-1][0] != "gap":
                blocks.append(("gap", ""))
            continue
        if line.startswith("#"):
            blocks.append(("heading", line.lstrip("#").strip()))
            continue
        if re.match(r"^[•\-\*·]\s+", line):
            body = re.sub(r"^([•\-\*·])\s+", "", line)
            blocks.append(("bullet", _clean_list_item(body)))
            continue
        m_num = _NUMBERED_RE.match(line)
        if m_num:
            blocks.append(("numbered", f"{m_num.group(1)}\0{_clean_list_item(m_num.group(2))}"))
            continue
        m = _HEADING_RE.match(line)
        if m and _upper_ratio(m.group(1)) > 0.85 and len(m.group(1)) <= 70:
            head, rest = m.group(1).strip(), m.group(2).strip()
            if rest:
                blocks.append(("kv", f"{sentence_case(head)}\0{rest}"))
            else:
                blocks.append(("heading", sentence_case(head)))
            continue
        if line.endswith(":") and _upper_ratio(line) > 0.85 and len(line) <= 72:
            blocks.append(("heading", sentence_case(line[:-1])))
            continue
        blocks.append(("para", line))
    while blocks and blocks[-1][0] == "gap":
        blocks.pop()
    while blocks and blocks[0][0] == "gap":
        blocks.pop(0)
    return blocks


def blocks_to_html(blocks: Sequence[Tuple[str, str]]) -> str:
    parts: List[str] = []
    for kind, value in blocks:
        if kind == "gap":
            parts.append("<div style='font-size:6px;'>&nbsp;</div>")
        elif kind == "heading":
            parts.append(
                f"<div style='color:{theme.TEXT_2}; font-weight:600; font-size:{theme.FS_SMALL}px;"
                f" margin-top:6px; margin-bottom:2px;'>{_inline(value)}</div>"
            )
        elif kind == "bullet":
            parts.append(
                "<table cellspacing='0' cellpadding='0' style='margin-top:1px; margin-bottom:1px;'><tr>"
                f"<td style='color:{theme.MUTED}; padding-right:7px;'>•</td>"
                f"<td>{_inline(value)}</td></tr></table>"
            )
        elif kind == "numbered":
            n, _, body = value.partition("\0")
            parts.append(
                "<table cellspacing='0' cellpadding='0' style='margin-top:1px; margin-bottom:1px;'><tr>"
                f"<td style='color:{theme.MUTED}; padding-right:7px;'>{html.escape(n)}.</td>"
                f"<td>{_inline(body)}</td></tr></table>"
            )
        elif kind == "kv":
            k, _, v = value.partition("\0")
            parts.append(
                f"<div style='margin-top:2px;'><span style='color:{theme.TEXT_2};'>{_inline(k)}:</span> {_inline(v)}</div>"
            )
        else:
            parts.append(f"<div style='margin-top:1px; margin-bottom:1px;'>{_inline(value)}</div>")
    return f"<div style='color:{theme.TEXT}; line-height:135%;'>" + "".join(parts) + "</div>"


def render_text_html(text: str, max_lines: int = 0) -> Tuple[str, bool]:
    """Returns (html, truncated). With ``max_lines`` only the first lines are rendered."""
    blocks = split_blocks(text)
    truncated = False
    if max_lines and len([b for b in blocks if b[0] != "gap"]) > max_lines:
        kept: List[Tuple[str, str]] = []
        count = 0
        for b in blocks:
            if b[0] != "gap":
                count += 1
            if count > max_lines:
                break
            kept.append(b)
        while kept and kept[-1][0] in ("gap", "heading"):
            kept.pop()
        blocks = kept
        truncated = True
    return blocks_to_html(blocks), truncated


def plain_text_view(text: str) -> str:
    """Rendered text without markup (used to check that no raw markdown leaks)."""
    out = []
    for kind, value in split_blocks(text):
        if kind == "kv":
            k, _, v = value.partition("\0")
            value = f"{k}: {v}"
        elif kind == "numbered":
            n, _, body = value.partition("\0")
            value = f"{n}. {body}"
        value = re.sub(r"\*\*(.+?)\*\*", r"\1", value).replace("**", "").replace("`", "")
        out.append(value)
    return "\n".join(out)


def _norm_words(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", strip_emphasis(text).lower()).split())


def strip_repeated_text(text: str, purpose: str) -> str:
    """Removes the lines of ``text`` that only repeat ``purpose`` (display only).

    Used so the project's "About" section does not show the purpose a second time. A
    heading left without content by the removal is dropped too.
    """
    target = _norm_words(purpose)
    if not text or not target:
        return text or ""
    kept: List[str] = []
    for raw in text.splitlines():
        if re.match(r"^\s*(project\s+)?purpose\s*:\s*\S", strip_emphasis(raw), re.IGNORECASE):
            continue  # an older generated purpose line; the real purpose is shown above
        norm = _norm_words(raw.lstrip("•-*#· "))
        if len(target) < 12:
            kept.append(raw)
            continue
        if norm and len(norm) >= 12 and (norm == target or norm in target or target in norm and
                                         len(norm) - len(target) < 8):
            continue
        kept.append(raw)
    # Drop headings that now introduce nothing (followed by a blank line, another heading or the end).
    out: List[str] = []
    for i, raw in enumerate(kept):
        line = raw.strip()
        is_heading = bool(line) and (
            line.startswith("#")
            or (line.endswith(":") and _upper_ratio(line) > 0.85 and len(line) <= 72)
        )
        if is_heading:
            nxt = next((k.strip() for k in kept[i + 1:] if k.strip()), "")
            gap_follows = i + 1 >= len(kept) or not kept[i + 1].strip()
            nxt_is_heading = nxt.startswith("#") or (nxt.endswith(":") and _upper_ratio(nxt) > 0.85)
            if not nxt or gap_follows or nxt_is_heading:
                continue
        out.append(raw)
    return "\n".join(out).strip()


def wrap_lines(text: str, font: QFont, width: float, max_lines: int) -> List[str]:
    """Word-wraps ``text`` into at most ``max_lines`` lines of ``width`` px; the last is elided."""
    flat = " ".join((text or "").split())
    if not flat or width <= 4 or max_lines <= 0:
        return [flat] if flat else []
    layout = QTextLayout(flat, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(opt)
    layout.beginLayout()
    spans: List[Tuple[int, int]] = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        spans.append((line.textStart(), line.textLength()))
    layout.endLayout()
    lines = [flat[a:a + n].strip() for a, n in spans]
    if len(lines) <= max_lines:
        return [ln for ln in lines if ln]
    head = lines[: max_lines - 1]
    rest = flat[spans[max_lines - 1][0]:].strip()
    fm = QFontMetricsF(font)
    last = fm.elidedText(rest, Qt.TextElideMode.ElideRight, width)
    if last == rest:  # elidedText may keep text that fits only because of rounding
        last = rest.rsplit(" ", 1)[0] + "…"
    return head + [last]


# ----------------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------------
def section_label(text: str) -> QLabel:
    lbl = theme.label(text, "section")
    lbl.setContentsMargins(0, 6, 0, 2)
    return lbl


def chip(text: str, tone: str = "", tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Chip")
    if tone:
        lbl.setProperty("tone", tone)
    if tooltip:
        lbl.setToolTip(tooltip)
    lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return lbl


def hairline() -> QFrame:
    line = QFrame()
    line.setObjectName("Hairline")
    return line


class RowButton(QPushButton):
    """A flat, clickable list row: [glyph] title ...... secondary."""

    def __init__(
        self,
        title: str,
        secondary: str = "",
        glyph: str = "",
        on_click: Optional[Callable[[], None]] = None,
        tooltip: str = "",
        mono_secondary: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RowBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.row_title = title
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)
        if glyph:
            g = theme.label(glyph, "muted")
            g.setFixedWidth(14)
            g.setAlignment(Qt.AlignmentFlag.AlignCenter)
            g.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(g)
        t = theme.label("", "body")
        t.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        t.setTextFormat(Qt.TextFormat.PlainText)
        self._title_label = t
        lay.addWidget(t, 1)
        self._sec_label: Optional[QLabel] = None
        if secondary:
            s = theme.label("", "mono" if mono_secondary else "muted")
            s.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            s.setTextFormat(Qt.TextFormat.PlainText)
            self._sec_label = s
            lay.addWidget(s, 0)
        self._secondary = secondary
        self.setMinimumHeight(theme.ROW_H)
        self.setMaximumHeight(theme.ROW_H)
        self.setToolTip(tooltip or (f"{title}\n{secondary}" if secondary else title))
        self.setAccessibleName(title)
        if on_click is not None:
            self.clicked.connect(on_click)
        self._apply_elision()

    def _apply_elision(self) -> None:
        avail = max(60, self.width() - 40)
        fm = self._title_label.fontMetrics()
        if self._sec_label is not None:
            sec_w = min(int(avail * 0.5), self._sec_label.fontMetrics().horizontalAdvance(self._secondary) + 4)
            self._sec_label.setText(
                self._sec_label.fontMetrics().elidedText(self._secondary, Qt.TextElideMode.ElideRight, sec_w)
            )
            avail -= sec_w + 8
        self._title_label.setText(fm.elidedText(self.row_title, Qt.TextElideMode.ElideRight, max(40, avail - 20)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    def text(self) -> str:  # tests / accessibility read the row text
        return self.row_title


class ElidedLabel(QLabel):
    """One-line label that elides long text instead of widening the window.

    ``text()`` returns the full text (the elided form is only what is painted); the full
    text is also the tooltip when it does not fit.
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight) -> None:
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(20)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = str(text or "")
        self._refresh()

    def text(self) -> str:  # noqa: D401
        return self._full

    def _refresh(self) -> None:
        fm = self.fontMetrics()
        width = max(10, self.width() - 2)
        line = self._full.replace("\n", "  ")
        shown = fm.elidedText(line, self._mode, width)
        super().setText(shown)
        self.setToolTip(self._full if shown != line else "")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (event.Type.FontChange, event.Type.StyleChange):
            self._refresh()


class PlainRow(QWidget):
    """A non-clickable row (e.g. an unresolvable reference)."""

    def __init__(self, title: str, secondary: str = "", glyph: str = "", tooltip: str = "") -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)
        if glyph:
            g = theme.label(glyph, "muted")
            g.setFixedWidth(14)
            lay.addWidget(g)
        self.title_label = theme.label(title, "secondary")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.title_label, 1)
        if secondary:
            lay.addWidget(theme.label(secondary, "muted"))
        self.setFixedHeight(theme.ROW_H)
        if tooltip:
            self.setToolTip(tooltip)


class ExpandableText(QWidget):
    """Rendered, read-only text with a "Show more" expander past ``max_lines`` lines."""

    def __init__(self, text: str, max_lines: int = 8, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.source_text = text or ""
        self.max_lines = max_lines
        self.expanded = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.label = QLabel()
        self.label.setObjectName("RenderedText")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.label)
        self.btn_more = QPushButton("Show more")
        self.btn_more.setObjectName("LinkBtn")
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_more.clicked.connect(self.toggle)
        lay.addWidget(self.btn_more, 0, Qt.AlignmentFlag.AlignLeft)
        self._render()

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self._render()

    def _render(self) -> None:
        if self.expanded:
            html_text, _ = render_text_html(self.source_text)
            truncated = True
        else:
            html_text, truncated = render_text_html(self.source_text, self.max_lines)
        self.label.setText(html_text)
        self.btn_more.setVisible(truncated)
        self.btn_more.setText("Show less" if self.expanded else "Show more")


class ClampedLabel(QLabel):
    """Word-wrapped plain text clamped to ``max_lines`` lines (the last one ends with "…").

    ``text()`` returns the full text; the full text is the tooltip when it was clamped.
    Emits ``clicked`` on a left click (used to start editing the project purpose).
    """

    clicked = Signal()

    def __init__(self, text: str = "", max_lines: int = 3, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.max_lines = max(1, int(max_lines))
        self.clamped = False
        self.idle_tooltip = ""
        self.setWordWrap(True)
        self.setIndent(0)
        self.setMargin(0)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = str(text or "")
        self._refresh()

    def text(self) -> str:  # noqa: D401
        return self._full

    def shown_text(self) -> str:
        return QLabel.text(self)

    def _refresh(self) -> None:
        self.ensurePolished()
        width = self.contentsRect().width() if self.width() > 40 else 320
        lines = wrap_lines(self._full, self.font(), max(40.0, float(width) - 2.0), self.max_lines)
        shown = "\n".join(lines)
        self.clamped = " ".join(shown.split()) != " ".join(self._full.split())
        QLabel.setText(self, shown)
        if self.clamped:
            self.setToolTip(self._full + (f"\n\n{self.idle_tooltip}" if self.idle_tooltip else ""))
        else:
            self.setToolTip(self.idle_tooltip)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._refresh()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()


class PurposeEditor(QTextEdit):
    """Small multi-line plain-text editor: Ctrl+Enter saves, Esc cancels."""

    saveRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setObjectName("PurposeEditor")
        self.setPlaceholderText("In one to three sentences: what is this project, and what is it for?")
        fm = self.fontMetrics()
        self.setFixedHeight(int(fm.lineSpacing() * 4 + 18))

    @staticmethod
    def _is_save_key(event: QKeyEvent) -> bool:
        return event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and bool(
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )

    def event(self, event) -> bool:  # noqa: D401 (Qt API)
        # Take Esc / Ctrl+Enter before the window-wide shortcuts see them.
        if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Escape or self._is_save_key(event):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Escape:
            self.cancelRequested.emit()
            event.accept()
            return
        if self._is_save_key(event):
            self.saveRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class EvidenceViewer(QDialog):
    """Non-modal, read-only view of what a project's purpose is based on (``purpose_evidence``)."""

    EMPTY_NOTE = (
        "Nothing has been collected for this project yet. "
        "Re-scan the project to collect evidence (README, entry point, file and package names)."
    )

    def __init__(self, project_name: str, evidence: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"What the description of '{project_name}' is based on")
        self.setModal(False)
        self.setMinimumSize(460, 300)
        self.resize(600, 440)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(10)
        outer.addWidget(theme.label("What this is based on", "heading"))
        outer.addWidget(theme.label(
            "Helios collected this from the project's files. It is what the AI summary may use; "
            "nothing here comes from chat history.", "secondary", wrap=True))
        self.evidence_text = str(evidence or "").strip()
        #: How strong the evidence is (Ollama only writes a purpose from strong evidence).
        self.strength_label: Optional[QLabel] = None
        if self.evidence_text:
            strength = assess_purpose_evidence(self.evidence_text, project_name)
            self.strength_label = theme.label(strength.explanation + ".", "muted", wrap=True)
            self.strength_label.setTextFormat(Qt.TextFormat.PlainText)
            outer.addWidget(self.strength_label)
        if self.evidence_text:
            self.text_view = QTextEdit()
            self.text_view.setObjectName("EvidenceText")
            self.text_view.setReadOnly(True)
            self.text_view.setAcceptRichText(False)
            self.text_view.setPlainText(self.evidence_text)
            self.text_view.setFont(theme.font(theme.FS_SMALL, mono=True))
            outer.addWidget(self.text_view, 1)
            self.empty_label: Optional[QLabel] = None
        else:
            self.text_view = None
            self.empty_label = theme.label(self.EMPTY_NOTE, "muted", wrap=True)
            outer.addWidget(self.empty_label)
            outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("Close")
        btn.clicked.connect(self.close)
        btn.setDefault(True)
        row.addWidget(btn)
        outer.addLayout(row)
        btn.setFocus()


class KeyValueGrid(QWidget):
    """Label / value rows ("Folder  C:\\...")."""

    def __init__(self, rows: Sequence[Tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for key, value in rows:
            row = QHBoxLayout()
            row.setSpacing(10)
            k = theme.label(key, "caption")
            k.setFixedWidth(92)
            k.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            v = theme.label(value, "body", wrap=True)
            v.setTextFormat(Qt.TextFormat.PlainText)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row.addWidget(k)
            row.addWidget(v, 1)
            lay.addLayout(row)


def clear_layout(layout) -> None:
    while layout.count():
        child = layout.takeAt(0)
        w = child.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif child.layout() is not None:
            clear_layout(child.layout())


# ----------------------------------------------------------------------------
# Skipped folders (status bar link + explanation dialog)
# ----------------------------------------------------------------------------
SKIP_REASONS: Dict[str, str] = {
    "skipped_scratch": "Temporary work folders an AI tool created for a session (no project file such as package.json or .git at their root).",
    "skipped_ignored": "Folders you chose never to import (see Settings \u203a Ignored folders).",
    "skipped_missing_workspace": "Folders that no longer exist on this computer.",
    "skipped_no_code": "Folders without any source code Helios can read.",
}


def skipped_folder_summary(stats: object) -> Tuple[int, List[Tuple[str, str, int, List[str]]]]:
    """Reads ``LAST_SCAN_STATS`` defensively.

    Returns ``(total, [(source, reason_key, count, paths)])``; anything malformed is ignored.
    """
    total = 0
    details: List[Tuple[str, str, int, List[str]]] = []
    if not isinstance(stats, dict):
        return 0, []
    for source, per_source in stats.items():
        if not isinstance(per_source, dict):
            continue
        for key, value in per_source.items():
            if not isinstance(key, str) or not key.startswith("skipped_") or key.endswith("_paths"):
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            raw_paths = per_source.get(f"{key}_paths", [])
            paths = [str(p) for p in raw_paths] if isinstance(raw_paths, (list, tuple)) else []
            total += count
            details.append((str(source), key, count, paths))
    return total, details


def reason_text(key: str) -> str:
    if key in SKIP_REASONS:
        return SKIP_REASONS[key]
    words = key.replace("skipped_", "").replace("_", " ").strip()
    return f"Skipped: {words}." if words else "Skipped."


class SkippedFoldersDialog(QDialog):
    """Small non-modal list that explains which folders the last sync skipped and why."""

    def __init__(self, details: Sequence[Tuple[str, str, int, List[str]]], source_titles: Dict[str, str],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Skipped folders")
        self.setModal(False)
        self.setMinimumSize(460, 320)
        self.resize(560, 420)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(10)
        outer.addWidget(theme.label("Skipped folders", "heading"))
        outer.addWidget(theme.label(
            "These folders appeared in your AI tools' history but did not become projects. "
            "Nothing was deleted.", "secondary", wrap=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("Transparent")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(6)
        grouped: Dict[str, List[Tuple[str, int, List[str]]]] = {}
        for source, key, count, paths in details:
            grouped.setdefault(key, []).append((source, count, paths))
        for key, entries in grouped.items():
            n = sum(c for _, c, _ in entries)
            lay.addWidget(section_label(f"{n} folder{'s' if n != 1 else ''}"))
            lay.addWidget(theme.label(reason_text(key), "body", wrap=True))
            for source, count, paths in entries:
                src = source_titles.get(source, source)
                for p in paths:
                    row = theme.label(p, "mono", wrap=True)
                    row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    row.setToolTip(f"From {src}")
                    lay.addWidget(row)
                if count > len(paths):
                    lay.addWidget(theme.label(f"…and {count - len(paths)} more from {src}", "muted"))
        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("Close")
        btn.clicked.connect(self.close)
        row.addWidget(btn)
        outer.addLayout(row)
