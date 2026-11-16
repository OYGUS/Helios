"""`PurposeMixin` of `HeliosMainWindow` (see ui/window/window.py for the full map).

The project purpose in the header: its states (purpose / suggestion / empty), the
inline editor, Accept / Edit / Dismiss of a suggestion, the evidence viewer and a
manual “Suggest with AI” request. All rules go through connectors.purpose_suggestion.

A plain mixin: no Qt base and no ``__init__``; the methods were moved verbatim from
ui/main_window.py and run on the `HeliosMainWindow` instance.

Window attributes it relies on (created in `HeliosMainWindow.__init__` or
`LayoutMixin._build_ui` and its builders unless assigned here):
    _detected_ollama_model, _evidence_viewer, _purpose_edit_target, _selected_node,
    _selected_type, _store_busy, _updating_ui, btn_suggest_ai, canvas, edit_purpose,
    lbl_purpose, lbl_purpose_source, lbl_suggestion, purpose_edit_box, purpose_empty,
    purpose_view, status_label, store, suggestion_box
Of those, assigned by this mixin:
    _evidence_viewer, _purpose_edit_target, _updating_ui
"""

from __future__ import annotations

import copy
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMenu

from connectors.purpose_suggestion import (
    PROTECTED_SOURCES as PROTECTED_PURPOSE_SOURCES,
    accept_purpose_suggestion,
    dismiss_purpose_suggestion,
    pending_suggestion,
    save_edited_purpose,
)
from core.models import SolarSystem
from ui.inspector import EvidenceViewer
from ui.window.project_text import _weak_purpose_evidence_tip, purpose_source_tag


class PurposeMixin:
    """The project purpose block, its editor and suggestion actions."""

    def _refresh_purpose_block(self, sys_obj: Optional[SolarSystem]) -> None:
        """Header states: the purpose (+ source tag), an AI suggestion waiting for the user
        (a separate "Suggested" box with Accept / Edit / Dismiss), or one muted
        "Add what this project is for" line. A suggestion is never shown as the purpose."""
        if not hasattr(self, "lbl_purpose"):
            return
        editing = self.purpose_edit_box.isVisible() and self._purpose_edit_target is not None
        if editing and self._purpose_edit_target is not sys_obj:
            self._cancel_purpose_edit(refresh=False)
            editing = False
        if editing:
            return
        self.purpose_edit_box.setVisible(False)
        if sys_obj is None:
            self.purpose_view.setVisible(False)
            self.purpose_empty.setVisible(False)
            self.suggestion_box.setVisible(False)
            return
        purpose = str(getattr(sys_obj, "purpose", "") or "").strip()
        suggestion = pending_suggestion(sys_obj, ai_available=self._ai_available())
        if purpose:
            self.lbl_purpose.setText(purpose)
            tag = purpose_source_tag(sys_obj)
            self.lbl_purpose_source.setText(tag)
            self.lbl_purpose_source.setVisible(bool(tag))
        else:
            self.lbl_purpose.setText("")
        self.purpose_view.setVisible(bool(purpose))
        self.lbl_suggestion.setText(suggestion)
        self.suggestion_box.setVisible(bool(suggestion))
        self.purpose_empty.setVisible(not purpose and not suggestion)
        # "Suggest with AI" sits on the empty line; with a purpose or a pending suggestion
        # it is in the "…" menu only.
        visible, enabled, tip = self._suggest_ai_state(sys_obj)
        self.btn_suggest_ai.setVisible(visible and not purpose and not suggestion)
        self.btn_suggest_ai.setEnabled(enabled)
        self.btn_suggest_ai.setToolTip(tip)

    def _ai_available(self) -> bool:
        """Ollama is enabled and a model was found (the app's availability state; never
        probed here). Without it no AI control is shown anywhere."""
        return not self.store.get_setting("disable_ollama", False) and bool(self._detected_ollama_model)

    def _suggest_ai_state(self, system: Optional[SolarSystem]) -> "tuple[bool, bool, str]":
        """(visible, enabled, tooltip) of "Suggest with AI" for `system`. Hidden without
        Ollama and for purposes written by the user or taken from the README; disabled
        with the reason when the evidence is weak or Ollama is busy."""
        if system is None or not self._ai_available():
            return False, False, ""
        if str(getattr(system, "purpose_source", "") or "") in PROTECTED_PURPOSE_SOURCES:
            return False, False, ""
        if not str(getattr(system, "purpose_evidence", "") or "").strip():
            return True, False, ("Nothing has been collected about this project yet. Re-scan it "
                                 "(Add project › Analyze a folder, or Sync) so the AI has something to go on.")
        weak = _weak_purpose_evidence_tip(system)
        if weak:
            return True, False, weak
        if self._is_ollama_running():
            return True, False, "Ollama is busy with another task. Try again when it has finished."
        return True, True, (f"Ask the local model ({self._detected_ollama_model}) for a one-sentence "
                            "suggestion from what was collected about this project. You decide whether to keep it.")

    def _begin_purpose_edit(self, prefill: Optional[str] = None) -> None:
        """Opens the inline editor with the purpose, or with ``prefill`` ("Edit" on a
        suggestion: saving makes it the user's purpose, Cancel keeps the suggestion)."""
        system = self.canvas.active_system
        if system is None or self._store_busy:
            return
        self._purpose_edit_target = system
        text = prefill if isinstance(prefill, str) else str(getattr(system, "purpose", "") or "")
        self.edit_purpose.setPlainText(text)
        self.purpose_view.setVisible(False)
        self.purpose_empty.setVisible(False)
        self.suggestion_box.setVisible(False)
        self.purpose_edit_box.setVisible(True)
        self.edit_purpose.setFocus(Qt.FocusReason.OtherFocusReason)
        cursor = self.edit_purpose.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.edit_purpose.setTextCursor(cursor)

    def _cancel_purpose_edit(self, refresh: bool = True) -> None:
        self._purpose_edit_target = None
        self.purpose_edit_box.setVisible(False)
        if refresh:
            self._refresh_purpose_block(self.canvas.active_system)

    def _save_purpose_edit(self) -> None:
        target = self._purpose_edit_target
        if target is None or not any(s is target for s in self.store.systems) or self._store_busy:
            self._cancel_purpose_edit()
            return
        text = self.edit_purpose.toPlainText().strip()
        probe = copy.copy(target)
        if not save_edited_purpose(probe, text):
            self._cancel_purpose_edit()
            return
        self.store.push_undo()
        save_edited_purpose(target, text)
        self._purpose_edit_target = None
        self.purpose_edit_box.setVisible(False)
        self._save_store_quietly()
        self._after_purpose_changed([target.id])
        self.status_label.setText(
            "Description saved. Scans and Ollama will never overwrite it · Ctrl+Z to undo."
            if text else "Description cleared · Ctrl+Z to undo."
        )

    def _accept_purpose_suggestion(self) -> bool:
        system = self.canvas.active_system
        if system is None or self._store_busy or not pending_suggestion(system, self._ai_available()):
            return False
        self.store.push_undo()
        accept_purpose_suggestion(system)
        self._save_store_quietly()
        self._after_purpose_changed([system.id])
        self.status_label.setText("Suggestion accepted as the project's purpose · Ctrl+Z to undo.")
        return True

    def _edit_purpose_suggestion(self) -> None:
        system = self.canvas.active_system
        if system is None:
            return
        self._begin_purpose_edit(prefill=pending_suggestion(system, self._ai_available()))

    def _dismiss_purpose_suggestion(self) -> bool:
        system = self.canvas.active_system
        if system is None or self._store_busy or not pending_suggestion(system, self._ai_available()):
            return False
        self.store.push_undo()
        dismiss_purpose_suggestion(system)
        self._save_store_quietly()
        self._after_purpose_changed([system.id])
        self.status_label.setText(
            "Suggestion dismissed. It is not suggested again unless the project changes · Ctrl+Z to undo."
        )
        return True

    def _save_store_quietly(self) -> None:
        self._updating_ui = True
        try:
            self.store.save()
        except Exception as exc:
            self.status_label.setText(f"Saving failed: {exc}")
        finally:
            self._updating_ui = False

    def _after_purpose_changed(self, system_ids: List[str]) -> None:
        """Refreshes every view that shows a project's purpose (header, body, tree, cards, sun)."""
        ids = set(system_ids)
        self._refresh_purpose_block(self.canvas.active_system)
        self._refresh_project_tooltips()
        if hasattr(self.canvas, "refresh_project_texts"):
            self.canvas.refresh_project_texts(ids)
        active = self.canvas.active_system
        if (active is not None and active.id in ids and self._selected_type == "sun"
                and self._selected_node is active):
            self._load_node_into_inspector(active, "sun")

    def _project_menu_actions(self, menu: QMenu, system: SolarSystem) -> None:
        """Purpose entries of the header's "…" menu (AI entries only when Ollama is available)."""
        act_edit = menu.addAction("Edit purpose")
        act_edit.triggered.connect(lambda: self._begin_purpose_edit())
        visible, enabled, tip = self._suggest_ai_state(system)
        if visible:
            act_ask = menu.addAction("Suggest with AI")
            act_ask.setEnabled(enabled)
            act_ask.setToolTip(tip)
            act_ask.triggered.connect(lambda: self._suggest_purpose_with_ai(system))
        act_ev = menu.addAction("Show what this is based on")
        act_ev.triggered.connect(lambda: self._show_purpose_evidence(system))

    def _show_purpose_evidence(self, system: Optional[SolarSystem]) -> Optional[QDialog]:
        if system is None:
            return None
        if self._evidence_viewer is not None:
            self._evidence_viewer.close()
            self._evidence_viewer.deleteLater()
        viewer = EvidenceViewer(system.project_name or system.name,
                                str(getattr(system, "purpose_evidence", "") or ""), self)
        self._evidence_viewer = viewer
        viewer.show()
        return viewer

    def _suggest_purpose_with_ai(self, system: Optional[SolarSystem]) -> bool:
        """"Suggest with AI": asks the local model for a purpose suggestion for one project
        (a manual request: a dismissal is ignored). The answer is only a suggestion."""
        if system is None or not any(s is system for s in self.store.systems):
            return False
        visible, enabled, tip = self._suggest_ai_state(system)
        if not visible or not enabled:
            if tip:
                self.status_label.setText(tip)
            return False
        if self._store_busy:
            self.status_label.setText("A scan is running. Try again once it has finished.")
            return False
        return self._start_ollama_worker(
            [system], str(self._detected_ollama_model), force=False, summarize_project=False,
            write_purpose=True, purpose_only=True, purpose_manual=True,
        )
