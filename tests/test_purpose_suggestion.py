"""AI purpose suggestions (connectors.purpose_suggestion) and the galaxy.json v2 migration."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from connectors import codebase_importer as ci
from connectors import purpose_suggestion as ps
from connectors.codebase_importer import CodebaseImporter
from connectors.purpose_evidence import evidence_key
from connectors.purpose_quality import QualityResult
from core.models import GALAXY_FORMAT_VERSION, GalaxyStore, SolarSystem

STRONG = "Own description (package.json): A small tool that renames holiday photos by date.\nNames in code: x()"
STRONG2 = "README intro (README.md): Sorts holiday photos into folders by the date they were taken."
WEAK = "Names in code: a()\nEntry point: run.py"
PLAN = "Purpose: Not written yet — based on the code this project involves: x\n\nMap: y"


def _system(purpose="", source="", evidence=STRONG, name="Photos"):
    s = SolarSystem(name=name, project_name=name, core_plan=PLAN)
    s.purpose, s.purpose_source, s.purpose_evidence = purpose, source, evidence
    return s


@pytest.fixture
def reject_all(monkeypatch):
    monkeypatch.setattr(ps, "check_purpose_suggestion",
                        lambda text, evidence, name: QualityResult(False, "no_overlap", "x"))


# --- evidence_key ------------------------------------------------------------
def test_evidence_key_is_short_stable_and_whitespace_insensitive():
    k = evidence_key(STRONG)
    assert len(k) == 12 and all(c in "0123456789abcdef" for c in k)
    assert evidence_key("  " + STRONG.replace(" ", "   ") + "\n") == k
    assert evidence_key(STRONG2) != k and evidence_key("") == "" and evidence_key("   ") == ""


# --- storage rules -------------------------------------------------------------
def test_suggestion_never_overwrites_user_or_readme():
    for source in ("user", "readme"):
        s = _system("Own text.", source)
        assert not ps.set_purpose_suggestion(s, "A photo renamer.")
        assert not ps.set_purpose_suggestion(s, "A photo renamer.", manual=True)
        assert s.purpose == "Own text." and s.purpose_source == source and s.purpose_suggestion == ""


def test_suggestion_is_stored_beside_empty_or_ai_purpose_and_ai_replaced_only_on_accept():
    s = _system()
    assert ps.set_purpose_suggestion(s, "  A photo\nrenamer.  ")
    assert s.purpose == "" and s.purpose_suggestion == "A photo renamer."
    assert s.purpose_suggestion_key == evidence_key(STRONG)
    assert s.core_plan.startswith("Purpose: Not written yet"), "the core plan never shows a suggestion"
    s2 = _system("Old accepted.", "ai")
    assert ps.set_purpose_suggestion(s2, "Renames holiday photos by their date.", manual=True)
    assert s2.purpose == "Old accepted." and s2.purpose_source == "ai"
    assert ps.pending_suggestion(s2) == "Renames holiday photos by their date."
    assert ps.accept_purpose_suggestion(s2)
    assert s2.purpose == "Renames holiday photos by their date." and s2.purpose_source == "ai" and s2.purpose_suggestion == ""


def test_weak_evidence_refuses_suggestions_also_manual():
    s = _system(evidence=WEAK)
    assert not ps.set_purpose_suggestion(s, "Runs a()", manual=True)
    assert not ps.set_purpose_suggestion(s, "Runs a()")
    assert s.purpose_suggestion == ""
    assert not ps.set_purpose_suggestion(_system(evidence=""), "A photo renamer.", manual=True)


def test_migration_drops_old_ai_text_on_weak_evidence(tmp_path):
    path = tmp_path / "galaxy.json"
    # The text overlaps the evidence ("quiz"), but the evidence is weak: Ollama would not
    # be asked today, so its old answer is not shown either.
    weak = 'Names in code: quizHandler()\nEntry point: run.py'
    _write_old_file(path, [("Quiz", "A quiz system likely related to gardening.", "ai", weak)] + ROWS)
    store = GalaxyStore(storage_path=path)
    by = {s.project_name: s for s in store.systems}
    assert (by["Quiz"].purpose, by["Quiz"].purpose_source, by["Quiz"].purpose_suggestion) == ("", "", "")
    assert by["Quiz"].core_plan.startswith("Purpose: Not written yet")
    assert by["AiProj"].purpose_suggestion == "A photo renamer."
    assert store.migrated_purposes == 2


def test_stored_text_is_quality_checked_again(reject_all):
    s = _system()
    assert not ps.set_purpose_suggestion(s, "A photo renamer.", manual=True)
    assert s.purpose_suggestion == ""
    assert not ps.set_purpose_suggestion(_system(), "   ")


def test_accept_sets_ai_and_updates_core_plan_and_not_user_modified():
    s = _system()
    ps.set_purpose_suggestion(s, "A photo renamer.")
    assert ps.accept_purpose_suggestion(s)
    assert (s.purpose, s.purpose_source, s.purpose_suggestion, s.purpose_suggestion_key) == (
        "A photo renamer.", "ai", "", "")
    assert s.core_plan.startswith("Purpose: A photo renamer. (suggested by the local AI model")
    assert s.is_user_modified is False
    assert not ps.accept_purpose_suggestion(s), "nothing left to accept"


def test_edit_and_save_makes_user_purpose_and_clears_suggestion():
    s = _system()
    ps.set_purpose_suggestion(s, "A photo renamer.")
    assert ps.save_edited_purpose(s, "Renames my holiday photos.")
    assert (s.purpose, s.purpose_source, s.purpose_suggestion) == ("Renames my holiday photos.", "user", "")
    assert s.core_plan.startswith("Purpose: Renames my holiday photos. (entered by the user)")
    assert not ps.save_edited_purpose(s, "Renames my holiday photos.")
    # Clearing an empty purpose with a pending suggestion keeps the suggestion.
    s2 = _system()
    ps.set_purpose_suggestion(s2, "A photo renamer.")
    assert not ps.save_edited_purpose(s2, "  ")
    assert s2.purpose_suggestion == "A photo renamer."


def test_dismiss_blocks_automatic_until_evidence_changes_and_manual_overrides():
    s = _system()
    ps.set_purpose_suggestion(s, "A photo renamer.")
    assert ps.dismiss_purpose_suggestion(s)
    assert s.purpose_suggestion == "" and s.purpose_dismissed_key == evidence_key(STRONG)
    assert not ps.needs_auto_suggestion(s)
    assert not ps.set_purpose_suggestion(s, "A photo renamer again.")  # automatic: refused
    # Manual request overrides the dismissal.
    assert ps.set_purpose_suggestion(s, "A photo renamer again.", manual=True)
    assert s.purpose_dismissed_key == ""
    ps.dismiss_purpose_suggestion(s)
    # Evidence changes (re-scan): automatic suggestions are possible again.
    s.purpose_evidence = STRONG2
    assert ps.needs_auto_suggestion(s)
    assert ps.set_purpose_suggestion(s, "Sorts holiday photos.")
    assert s.purpose_suggestion_key == evidence_key(STRONG2)


def test_needs_auto_suggestion():
    assert ps.needs_auto_suggestion(_system())
    assert not ps.needs_auto_suggestion(_system(evidence=WEAK)), "weak evidence"
    assert not ps.needs_auto_suggestion(_system(evidence=""))
    assert not ps.needs_auto_suggestion(_system("Mine.", "user"))
    assert not ps.needs_auto_suggestion(_system("Own.", "readme"))
    assert not ps.needs_auto_suggestion(_system("Accepted.", "ai")), "an accepted ai purpose is not re-suggested"
    s = _system()
    ps.set_purpose_suggestion(s, "A photo renamer.")
    assert not ps.needs_auto_suggestion(s), "a suggestion for the current evidence exists"
    s.purpose_evidence = STRONG2
    assert ps.needs_auto_suggestion(s), "the suggestion was made from older evidence"


def test_manual_allowed_and_failure_classification():
    assert ps.manual_suggestion_allowed(_system("Accepted.", "ai"))
    assert not ps.manual_suggestion_allowed(_system("Mine.", "user"))
    assert not ps.manual_suggestion_allowed(_system(evidence=WEAK))
    assert ps.classify_purpose_failure("Ollama could not tell ... (answered UNKNOWN).") == "unknown"
    assert ps.classify_purpose_failure("Ollama returned no response.") == "error"
    generic = "No suggestion for 'X': the answer was too generic to say what the project is for (reason: generic)."
    assert ps.classify_purpose_failure(generic) == "rejected"
    assert ps.purpose_failure_detail(generic) == "the answer was too generic to say what the project is for"
    assert ps.classify_purpose_failure("Ollama could not tell (answered UNKNOWN) (reason: unknown).") == "unknown"
    assert ps.classify_purpose_failure("Ollama echoed the prompt instead of writing the purpose (reason: echo).") == "rejected"
    assert ps.classify_purpose_failure("The answer is too generic.") == "error", "no reason code: not a check failure"
    assert ps.purpose_failure_detail("Ollama returned no response.") == ""


def test_apply_purpose_text_is_not_reachable_with_ai():
    s = _system()
    assert not ci.apply_purpose_text(s, "A photo renamer.", "ai")
    assert s.purpose == ""


def test_readme_rescan_clears_pending_suggestion_and_user_purpose_survives(tmp_path):
    store = GalaxyStore(storage_path=tmp_path / "galaxy.json")
    root = tmp_path / "photos"
    root.mkdir()
    (root / "main.py").write_text('"""Renames holiday photos by the date they were taken."""\nprint(1)\n', encoding="utf-8")
    system = CodebaseImporter.analyze_directory(root, store)
    system.purpose, system.purpose_source = "", ""
    system.purpose_suggestion, system.purpose_suggestion_key = "A photo renamer.", "abc"
    (root / "README.md").write_text("# photos\n\nPhotos renames holiday pictures by date.\n", encoding="utf-8")
    system = CodebaseImporter.analyze_directory(root, store)
    assert system.purpose_source == "readme" and system.purpose_suggestion == ""
    # A user purpose is unchanged across rescan + an Ollama answer + migration.
    system.purpose, system.purpose_source = "My words.", "user"
    system = CodebaseImporter.analyze_directory(root, store)
    assert not ps.set_purpose_suggestion(system, "AI words.", manual=True)
    assert ps.migrate_ai_purposes_to_suggestions([system]) == 0
    assert (system.purpose, system.purpose_source, system.purpose_suggestion) == ("My words.", "user", "")


# --- migration -----------------------------------------------------------------
def _write_old_file(path: Path, rows):
    systems = []
    for name, purpose, source, evidence in rows:
        s = SolarSystem(name=name, project_name=name, core_plan=f"Purpose: {purpose} (x)\n\nMap: y" if purpose else PLAN)
        s.purpose, s.purpose_source, s.purpose_evidence = purpose, source, evidence
        d = asdict(s)
        for k in ("purpose_suggestion", "purpose_suggestion_key", "purpose_dismissed_key"):
            d.pop(k)  # written by an older Helios
        systems.append(d)
    path.write_text(json.dumps({"updated_at": "x", "systems": systems}), encoding="utf-8")


ROWS = [
    ("AiProj", "A photo renamer.", "ai", STRONG),
    ("UserProj", "Mine.", "user", STRONG),
    ("ReadmeProj", "Own words.", "readme", STRONG2),
    ("Empty", "", "", STRONG),
]


def test_migration_turns_ai_into_suggestion_and_keeps_user_readme(tmp_path):
    path = tmp_path / "galaxy.json"
    _write_old_file(path, ROWS)
    store = GalaxyStore(storage_path=path)
    by = {s.project_name: s for s in store.systems}
    assert store.loaded_format_version == 0 and store.migrated_purposes == 1
    ai = by["AiProj"]
    assert (ai.purpose, ai.purpose_source) == ("", "")
    assert ai.purpose_suggestion == "A photo renamer." and ai.purpose_suggestion_key == evidence_key(STRONG)
    assert ai.core_plan.startswith("Purpose: Not written yet") and ai.core_plan.endswith("Map: y")
    assert (by["UserProj"].purpose, by["UserProj"].purpose_source) == ("Mine.", "user")
    assert (by["ReadmeProj"].purpose, by["ReadmeProj"].purpose_source) == ("Own words.", "readme")
    assert by["UserProj"].purpose_suggestion == by["ReadmeProj"].purpose_suggestion == ""
    # In memory only: the file is untouched until the next normal save.
    assert "format_version" not in json.loads(path.read_text(encoding="utf-8"))
    store.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["format_version"] == GALAXY_FORMAT_VERSION == 2


def test_no_second_migration_so_accepted_ai_purposes_stay(tmp_path):
    path = tmp_path / "galaxy.json"
    _write_old_file(path, ROWS)
    store = GalaxyStore(storage_path=path)
    ai = next(s for s in store.systems if s.project_name == "AiProj")
    assert ps.accept_purpose_suggestion(ai)
    store.save()
    again = GalaxyStore(storage_path=path)
    assert again.loaded_format_version == 2 and again.migrated_purposes == 0
    ai2 = next(s for s in again.systems if s.project_name == "AiProj")
    assert (ai2.purpose, ai2.purpose_source, ai2.purpose_suggestion) == ("A photo renamer.", "ai", "")
    # A live reload (file watcher) of a v2 file does not migrate either.
    assert again.load() and again.migrated_purposes == 0


def test_migration_drops_old_ai_text_that_fails_the_check(tmp_path, reject_all):
    path = tmp_path / "galaxy.json"
    _write_old_file(path, ROWS)
    store = GalaxyStore(storage_path=path)
    ai = next(s for s in store.systems if s.project_name == "AiProj")
    assert ai.purpose == "" and ai.purpose_source == "" and ai.purpose_suggestion == ""
    assert store.migrated_purposes == 1


def test_every_writer_writes_the_version(tmp_path):
    # Fresh store (starter project), corrupt-file fallback and the CLI bridge.
    store = GalaxyStore(storage_path=tmp_path / "a" / "galaxy.json")
    assert json.loads((tmp_path / "a" / "galaxy.json").read_text(encoding="utf-8"))["format_version"] == 2
    bad = tmp_path / "b" / "galaxy.json"
    bad.parent.mkdir()
    bad.write_text("{not json", encoding="utf-8")
    store = GalaxyStore(storage_path=bad)
    assert store.load_error and store.backup_path is not None
    store.save()
    assert json.loads(bad.read_text(encoding="utf-8"))["format_version"] == 2
    from connectors import live_bridge

    old = tmp_path / "c" / "galaxy.json"
    old.parent.mkdir()
    _write_old_file(old, ROWS)
    code = live_bridge.main(["--data", str(old), "add-planet", "--project", "AiProj", "--source", "manual",
                             "--title", "UI Layer"])
    assert code == 0
    saved = json.loads(old.read_text(encoding="utf-8"))
    assert saved["format_version"] == 2
    row = next(s for s in saved["systems"] if s["project_name"] == "AiProj")
    assert row["purpose"] == "" and row["purpose_suggestion"] == "A photo renamer."
