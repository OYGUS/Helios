"""AI purpose suggestions: the rules for `SolarSystem.purpose_suggestion`.

The local model's sentence about what a project is for is never stored as the purpose.
It is a *suggestion* (`purpose_suggestion`, made from the evidence whose
`evidence_key` is `purpose_suggestion_key`) that the user accepts (purpose source
"ai"), edits (source "user") or dismisses. Every reader and writer of these fields
goes through the pure functions here (the UI, the Ollama worker and the galaxy.json
migration); none of them talk to Ollama or the store.

Rules:

* A suggestion is stored only while the purpose is empty or was an accepted AI
  suggestion (source "ai"); never when the user wrote the purpose ("user") or it comes
  from the project's own words ("readme"), nor when the evidence is weak
  (`assess_purpose_evidence`, also for manual requests). The text must pass
  `purpose_quality.check_purpose_suggestion` (checked again here, whatever the caller
  did before).
* Automatic generation (`manual=False`) is skipped when the user dismissed a
  suggestion for the same evidence (`purpose_dismissed_key`); a manual request
  ("Suggest with AI") ignores the dismissal.
* `needs_auto_suggestion` decides which projects an automatic run asks about: an
  empty purpose, strong evidence, no suggestion yet for the current evidence and no
  dismissal for it. An accepted "ai" purpose is therefore never re-suggested
  automatically.
* The core plan's `Purpose:` line only ever shows `purpose`; with only a suggestion it
  keeps saying "Not written yet".
* `migrate_ai_purposes_to_suggestions` converts galaxy.json files written before
  format version 2, where an Ollama answer was stored directly as an "ai" purpose.
"""

from __future__ import annotations

import re
from typing import Iterable

from connectors.codebase_importer import update_core_plan_purpose
from connectors.purpose_evidence import assess_purpose_evidence, evidence_key
from connectors.purpose_quality import REASONS, QualityResult, check_purpose_suggestion

__all__ = (
    "evidence_key", "current_evidence_key", "suggestion_allowed", "pending_suggestion",
    "check_suggestion_text", "set_purpose_suggestion", "accept_purpose_suggestion",
    "save_edited_purpose", "dismiss_purpose_suggestion", "needs_auto_suggestion",
    "manual_suggestion_allowed", "migrate_ai_purposes_to_suggestions", "classify_purpose_failure",
    "purpose_failure_detail",
)

# Purpose sources a suggestion may never replace or sit next to.
PROTECTED_SOURCES = ("user", "readme")


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _name(system: object) -> str:
    return str(getattr(system, "project_name", "") or getattr(system, "name", "") or "")


def current_evidence_key(system: object) -> str:
    return evidence_key(str(getattr(system, "purpose_evidence", "") or ""))


def suggestion_allowed(system: object) -> bool:
    """True if a suggestion may be stored / shown for this project: the purpose is empty
    or an accepted AI suggestion, and never written by the user or taken from a README."""
    source = str(getattr(system, "purpose_source", "") or "")
    if source in PROTECTED_SOURCES:
        return False
    return source == "ai" or not _text(getattr(system, "purpose", ""))


def pending_suggestion(system: object) -> str:
    """The suggestion to show (one line), or "" when there is none or it may not be shown."""
    text = _text(getattr(system, "purpose_suggestion", ""))
    if not text or not suggestion_allowed(system):
        return ""
    if text == _text(getattr(system, "purpose", "")):
        return ""
    return text


def evidence_is_strong(system: object) -> bool:
    evidence = str(getattr(system, "purpose_evidence", "") or "")
    return bool(evidence.strip()) and assess_purpose_evidence(evidence, _name(system)).strong


def check_suggestion_text(system: object, text: str) -> QualityResult:
    return check_purpose_suggestion(_text(text), str(getattr(system, "purpose_evidence", "") or ""), _name(system))


def set_purpose_suggestion(system, text: str, *, manual: bool = False) -> bool:
    """Stores `text` as the project's suggestion (never as its purpose). Returns True if
    stored. `manual` (the user pressed "Suggest with AI") overrides a dismissal and
    clears it. Not a user action: `is_user_modified` is never touched."""
    clean = _text(text)
    if not clean or not suggestion_allowed(system):
        return False
    key = current_evidence_key(system)
    if not manual and system.purpose_dismissed_key and system.purpose_dismissed_key == key:
        return False
    if clean == _text(system.purpose):
        return False  # already the accepted purpose
    if not evidence_is_strong(system):
        return False  # the rule that keeps Ollama from being asked also keeps its answers out
    if not check_suggestion_text(system, clean).ok:
        return False
    system.purpose_suggestion = clean
    system.purpose_suggestion_key = key
    if manual:
        system.purpose_dismissed_key = ""
    return True


def _clear_suggestion(system) -> None:
    system.purpose_suggestion = ""
    system.purpose_suggestion_key = ""


def accept_purpose_suggestion(system) -> bool:
    """Accept: the suggestion becomes the purpose (source "ai"); the suggestion is cleared
    and the core plan's `Purpose:` line updated. Returns True if the purpose changed."""
    text = pending_suggestion(system)
    if not text:
        return False
    system.purpose = text
    system.purpose_source = "ai"
    _clear_suggestion(system)
    system.purpose_dismissed_key = ""
    update_core_plan_purpose(system)
    return True


def save_edited_purpose(system, text: str) -> bool:
    """The inline editor's Save (also after "Edit" on a suggestion): a non-empty text
    becomes a "user" purpose and the suggestion is cleared; an empty text clears the
    purpose (scans may fill it again) and keeps a pending suggestion. Returns True if
    anything changed."""
    clean = re.sub(r"\s+", " ", text or "").strip()
    new_source = "user" if clean else ""
    had_suggestion = bool(_text(system.purpose_suggestion)) and bool(clean)
    if clean == _text(system.purpose) and new_source == (system.purpose_source or "") and not had_suggestion:
        return False
    system.purpose = clean
    system.purpose_source = new_source
    if clean:
        _clear_suggestion(system)
    update_core_plan_purpose(system)
    return True


def dismiss_purpose_suggestion(system) -> bool:
    """Dismiss: the suggestion is cleared and no automatic one is made again for the
    current evidence. Returns True if there was a suggestion."""
    had = bool(_text(system.purpose_suggestion))
    _clear_suggestion(system)
    system.purpose_dismissed_key = current_evidence_key(system)
    return had


def needs_auto_suggestion(system: object) -> bool:
    """True if an automatic run should ask the local model for a suggestion (see the
    module docstring). Works on `SolarSystem` and on the Ollama worker's snapshots."""
    if _text(getattr(system, "purpose", "")):
        return False
    if str(getattr(system, "purpose_source", "") or "") in PROTECTED_SOURCES:
        return False
    evidence = str(getattr(system, "purpose_evidence", "") or "")
    if not evidence.strip():
        return False
    key = evidence_key(evidence)
    if _text(getattr(system, "purpose_suggestion", "")) and getattr(system, "purpose_suggestion_key", "") == key:
        return False
    if getattr(system, "purpose_dismissed_key", "") == key:
        return False
    return assess_purpose_evidence(evidence, _name(system)).strong


def manual_suggestion_allowed(system: object) -> bool:
    """A manual "Suggest with AI" request: any project whose purpose is not the user's or
    the README's, with strong evidence (a dismissal or an existing suggestion does not
    matter)."""
    if str(getattr(system, "purpose_source", "") or "") in PROTECTED_SOURCES:
        return False
    evidence = str(getattr(system, "purpose_evidence", "") or "")
    return bool(evidence.strip()) and assess_purpose_evidence(evidence, _name(system)).strong


def migrate_ai_purposes_to_suggestions(systems: Iterable) -> int:
    """galaxy.json before format version 2 stored Ollama's answer as the purpose
    (source "ai") without the user ever accepting it. Each such purpose becomes a
    suggestion for the current evidence when that evidence is strong and the text passes the quality check, otherwise it
    is dropped; the purpose and its source are cleared and the core plan's `Purpose:`
    line updated. "user" and "readme" purposes are untouched. Returns how many projects
    changed."""
    changed = 0
    for system in systems:
        if (system.purpose_source or "") != "ai" or not _text(system.purpose):
            continue
        old = _text(system.purpose)
        system.purpose, system.purpose_source = "", ""
        # Kept only when Ollama would be asked today (strong evidence) and the text passes.
        if evidence_is_strong(system) and check_suggestion_text(system, old).ok:
            system.purpose_suggestion = old
            system.purpose_suggestion_key = current_evidence_key(system)
        else:
            _clear_suggestion(system)
        update_core_plan_purpose(system)
        changed += 1
    return changed


def classify_purpose_failure(message: str) -> str:
    """Kind of a failed purpose request from the worker's message: "unknown" (the model
    answered UNKNOWN), "rejected" (an answer that failed a check: the message carries a
    reason code, see `ollama_enhancer.purpose_rejection_reason`) or "error" (no answer:
    connection, timeout, not asked)."""
    from connectors.ollama_enhancer import purpose_rejection_reason  # imports this module

    reason = purpose_rejection_reason(message)
    if reason == "unknown":
        return "unknown"
    if reason:
        return "rejected"
    return "unknown" if "UNKNOWN" in str(message or "") else "error"


def purpose_failure_detail(message: str) -> str:
    """The quality check's short explanation for a rejected answer ("" when the message has
    no reason code), e.g. "the answer did not match the evidence"."""
    from connectors.ollama_enhancer import purpose_rejection_reason

    reason = purpose_rejection_reason(message)
    if reason == "echo":
        return "the model repeated the prompt instead of answering"
    return REASONS.get(reason, "")
