"""Project text helpers of the main window: the purpose source labels, the weak-evidence
tip of "Suggest with AI", the text the project filter matches and the core-plan sync helper.
Also `AUTO_PURPOSE_SETTING_KEY` (the "suggest descriptions automatically" setting)."""

from __future__ import annotations

from typing import Dict

from connectors import codebase_importer as codebase_importer_module
from connectors.purpose_evidence import assess_purpose_evidence


def _weak_purpose_evidence_tip(system) -> str:
    """Why the AI is not asked for a purpose suggestion ("" when the evidence is strong)."""
    evidence = str(getattr(system, "purpose_evidence", "") or "")
    name = str(getattr(system, "project_name", "") or getattr(system, "name", "") or "")
    strength = assess_purpose_evidence(evidence, name)
    if strength.strong:
        return ""
    return (f"Too little is known about what this project is for, so the AI is not asked "
            f"(it would guess). {strength.explanation}. Write the purpose yourself, or add a README.")


def _project_search_text(system) -> str:
    """Lower-cased purpose + identity line of a project (what the search box matches)."""
    purpose = str(getattr(system, "purpose", "") or "")
    try:
        identity = codebase_importer_module.stored_identity(system)
    except Exception:
        identity = ""
    return f"{purpose}\n{identity}".lower()


def _sync_core_plan_purpose(system) -> None:
    """Keeps the `Purpose:` line of the generated overview text in step with
    `system.purpose` (the importer owns that text's format)."""
    try:
        from connectors.codebase_importer import update_core_plan_purpose
        update_core_plan_purpose(system)
    except Exception:  # an older overview text without the line: nothing to update
        pass


PURPOSE_SOURCE_TAGS: Dict[str, str] = {
    "user": "Written by you",
    # Neutral and always true: a "readme" purpose comes from the README intro or a
    # package description; `PURPOSE_ORIGIN_TAGS` names which one when it is known.
    "readme": "From the project's description",
    "ai": "AI suggestion you accepted",
}
PURPOSE_ORIGIN_TAGS: Dict[str, str] = {
    "readme": "From the project's README",
    "package": "From the project's package description",
}


def purpose_source_tag(system: object) -> str:
    """The label under a purpose: who wrote it or where it was taken from."""
    source = str(getattr(system, "purpose_source", "") or "")
    if source == "readme":
        origin = str(getattr(system, "purpose_origin", "") or "")
        return PURPOSE_ORIGIN_TAGS.get(origin, PURPOSE_SOURCE_TAGS["readme"])
    return PURPOSE_SOURCE_TAGS.get(source, "")


AUTO_PURPOSE_SETTING_KEY = "ollama_auto_purpose"
