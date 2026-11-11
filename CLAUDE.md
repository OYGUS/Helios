# Helios — Guide for coding agents working on this repo

Helios is a Python/PySide6 desktop app that maps local codebases and AI-coding-agent session histories
(Antigravity, Codex, Claude Code) into an interactive architecture graph.
Data lives in `data/galaxy.json` (git-ignored); the app also watches that file and live-reloads it.
Windows is the primary target; Linux runs headless in CI.

## Layout
- `main.py` — app entry point (`python main.py`); `run_helios.bat` — Windows launcher.
- `core/models.py` — dataclasses (`SolarSystem` = project, `PlanetNode` = module, `SatelliteNode` = sub-component, links), `SOURCE_CATEGORIES`, `LAYER_DEFINITIONS`, `GalaxyStore` (load/save/undo), `atomic_write_text`.
- `connectors/codebase_importer.py` — analyzes a directory; modules = top-level dirs, or one module per file for small projects; links only from resolved imports/includes (Python, JS/TS, PHP, HTML references); also call relations, external libraries, entry points.
- `connectors/agent_importer.py` — finds projects from Antigravity/Codex/Claude Code session logs; the map is built from the real code in the session's folder, sessions only fill one `Work History` module; one project per folder across sources.
- `connectors/ollama_enhancer.py` — optional local Ollama prose: a purpose *suggestion* (from `purpose_evidence` only), module descriptions, a project summary block.
- `connectors/purpose_suggestion.py` — the only rules for `SolarSystem.purpose_suggestion` (store / accept / edit / dismiss, `needs_auto_suggestion`, the galaxy.json v2 migration); `connectors/purpose_quality.py` — `check_purpose_suggestion`, the quality check every suggestion must pass.
- `connectors/purpose_evidence.py` — pure helpers for `purpose_evidence`: the comment-noise table (`NOISE_PATTERNS`, `clean_header_text`), the session line (always the first evidence line) and `assess_purpose_evidence` (points score; Ollama is asked for a purpose only when it is strong, `PURPOSE_STRONG_SCORE`).
- `connectors/live_bridge.py` — CLI that edits `galaxy.json` (see the optional section below).
- `ui/main_window.py`, `ui/space_canvas.py` — main window/inspector and the QGraphicsScene canvas (both very large).
- `ui/theme.py` — the only place for colours, spacing, fonts and the stylesheet (no inline colour literals elsewhere). `ui/inspector.py` — right-panel rendering helpers.
- `ui/workers.py` — `QThread` workers for long scans and the canvas paint freezer.
- `tests/` — pytest suite. `projects/` — user repos to scan (git-ignored).

## Run
```bash
pip install -r requirements-dev.txt
python main.py
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
```
On Windows PowerShell set `$env:QT_QPA_PLATFORM = "offscreen"` first. CI runs the same pytest command.

## Conventions
- Standard library + PySide6 only. Do not add runtime dependencies without asking.
- Windows is the primary target: use `pathlib`, no POSIX-only calls, handle Windows paths as strings where needed.
- Tests must never write to the repo's `data/`. Use `GalaxyStore(storage_path=tmp_path / "galaxy.json")`, and pass `--data` / a temp path to the bridge.
- The UI must not block. Long work (scans, imports, Ollama) goes in `ui/workers.py`; a store-mutating worker owns the store while it runs (see that module's docstring).
- Imported projects must only show links that were found in source. Never invent dependencies; generated text must say how a result was derived (AST vs. pattern matching, session history vs. architecture). Nothing in a map may be derived from chat text. The local LLM only writes prose (`PlanetNode.ai_summary`); it never creates nodes or links.
- Every project has a purpose line (what it is for). `purpose_source == "user"` is never overwritten by scans or the LLM; without a README description or the LLM the purpose stays empty — never fabricate one from code. The LLM is asked only when `assess_purpose_evidence` says the evidence is strong (code facts such as libraries, shape and names score 0); weak evidence leaves the purpose empty. The LLM's answer is never stored as the purpose: it is a suggestion (`purpose_suggestion`, shown as "Suggested" with Accept / Edit / Dismiss) until the user accepts it (source "ai") or edits it (source "user"); a dismissal blocks automatic suggestions until the evidence changes (`purpose_dismissed_key`). Go through `connectors.purpose_suggestion`; the core plan's `Purpose:` line never shows a suggestion.
- `galaxy.json` has a top-level `format_version` (`GALAXY_FORMAT_VERSION`, now 2). `GalaxyStore.load` migrates older files in memory (v2: old "ai" purposes become suggestions); `save` always writes the current version.
- Without Ollama (turned off or no model found) no AI control, hint or warning is shown anywhere.
- Every scanned project also has an identity line (`SolarSystem.identity`, "what it is"): built only by `codebase_importer.identity_line` from analysis facts (languages, the `IDENTITY_KINDS` table, libraries, entry point), never by the LLM, not user-editable, replaced on every scan; old data without it is derived for display by `stored_identity`. Unknown parts are left out — never guess a kind.
- A tool's dated scratch workspace is a project only with a root marker at its root; folders in the `ignored_folders` setting are never imported.
- `is_user_modified` is set only by a real user action (a drag that moved the item, a saved edit, a drawn node/link).
- Keep existing `galaxy.json` files loadable: do not rename or remove dataclass fields; add new fields with defaults.
- Saves go through `atomic_write_text`; an unreadable `galaxy.json` must never be overwritten (a `galaxy.corrupt-*.json` backup is made).
- Visible UI text uses one vocabulary: Project, Module, Component, Link (the solar system is drawn, not named).
- Code, comments, docstrings and UI strings are in English.
- Keep README.md, this file and `.agent/skills/helios/SKILL.md` in sync when behaviour changes.

---

## Optional: reflecting work into a running Helios app

Do this only when the user asks (for example "import this project into Helios" or "add this to the map"),
not after every file change. Run from the Helios repo root (or pass `--data PATH/to/galaxy.json` before the
command). The open app picks up changes through its `data/galaxy.json` file watcher.

```powershell
python -m connectors.live_bridge analyze-folder --path "./ProjectDirectory"
python -m connectors.live_bridge set-sun --project "ProjectName" --source claude_code --title "ProjectName" --plan "Goal, architecture summary, key decisions"
python -m connectors.live_bridge add-planet --project "ProjectName" --source claude_code --title "UI Layer" --layer ui --summary "Visual components"
python -m connectors.live_bridge add-moon --project "ProjectName" --source claude_code --planet "UI Layer" --title "Space Canvas" --tag code --content "What it does and how"
python -m connectors.live_bridge add-link --project "ProjectName" --source claude_code --from-planet "UI Layer" --to-planet "Core Layer" --label "data model flow"
```

- Aliases: `set-core`, `add-module`, `add-component`; flags `--module`, `--from-module`, `--to-module`, `--create-module`.
- `--project` is required (use the real project name, not a chat sentence). `--source` is one of `antigravity` (the default), `claude_code`, `codebase`, `codex`, `manual` — pass `claude_code` explicitly.
- `--layer`: `architecture`, `ui`, `core`, `integration`, `testing`, `research`, `docs` (default `core`). `--tag`: `thought` (default), `code`, `diagram`, `idea`.
- Commands are idempotent: the same title (exact, case-insensitive, trimmed) updates the existing node instead of duplicating it.
- `add-moon` needs the module to exist (exact title; a unique whole-word prefix of a title is also accepted); add `--create-planet` to create it. `add-link` needs both modules.
- Exit codes: 0 success, 1 runtime problem (unknown module, unreadable `galaxy.json`, directory not found, write failure), 2 invalid arguments.
- `analyze-folder` builds the map with the codebase importer; re-running it on the same directory refreshes the existing `codebase` project (matched by path) instead of adding a second one, and keeps links drawn by hand.
