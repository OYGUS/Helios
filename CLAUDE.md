# Helios — Guide for coding agents working on this repo

Helios is a Python/PySide6 desktop app that maps local codebases and AI-coding-agent session histories
(Antigravity, Codex, Claude Code) into an interactive architecture graph.
Data lives in `data/galaxy.json` (git-ignored) when run from source, or in `%APPDATA%\Helios\data\galaxy.json`
for the `Helios.exe` build (see `core/paths.py`); the app also watches that file and live-reloads it.
Windows is the primary target; Linux runs headless in CI.

## Layout
- `main.py` — app entry point (`python main.py`; `--smoke-test` opens the window, waits ~3 s and exits 0, used by the build checks); `run_helios.bat` — Windows launcher.
- `core/paths.py` — the only place that decides paths: `is_frozen`, `resource_path` (bundled `assets/`), `source_dir` (None when frozen), `user_root` / `data_dir` / `galaxy_path` / `projects_dir`. From source: the repo checkout, as always. Frozen: a per-user folder (`%APPDATA%\Helios`). `HELIOS_DATA_DIR` overrides the data root in both modes.
- `helios.spec` — PyInstaller recipe (windowed, one-folder `dist/Helios/`); `build_exe.bat` — one-step Windows build; `requirements-build.txt` — build-only deps (PyInstaller); `.github/workflows/release.yml` — on a `v*` tag: tests, build, smoke test, `Helios-windows.zip` on the GitHub Release.
- `core/models.py` — dataclasses (`SolarSystem` = project, `PlanetNode` = module, `SatelliteNode` = sub-component, links), `SOURCE_CATEGORIES`, `LAYER_DEFINITIONS`, `GalaxyStore` (load/save/undo), `atomic_write_text`.
- `connectors/codebase_importer.py` — facade (import from here; it re-exports every name) over the package `connectors/codebase/`, which analyzes a directory; modules = top-level dirs, or one module per file for small projects; links only from resolved imports/includes (Python, JS/TS, PHP, HTML references); also call relations, external libraries, entry points. One module per step, lower never imports higher: `constants`, `fileinfo`, `lang_common` / `lang_python` / `lang_patterns`, `purpose_text`, `grouping`, `walk`, `resolve`, `calls`, `libraries`, `content`, `purpose`, `identity`, `mapbuild` (`build_code_map`), `project` (refresh / ignored folders), `importer` (`CodebaseImporter`); the map is in the facade's docstring. Monkeypatch a tunable in the submodule that reads it.
- `connectors/agent_importer.py` — finds projects from Antigravity/Codex/Claude Code session logs; the map is built from the real code in the session's folder, sessions only fill one `Work History` module; one project per folder across sources.
- `connectors/ollama_enhancer.py` — optional local Ollama prose: a purpose *suggestion* (from `purpose_evidence` only), module descriptions, a project summary block.
- `connectors/purpose_suggestion.py` — the only rules for `SolarSystem.purpose_suggestion` (store / accept / edit / dismiss, `needs_auto_suggestion`, the galaxy.json v2 migration); `connectors/purpose_quality.py` — `check_purpose_suggestion`, the quality check every suggestion must pass.
- `connectors/purpose_evidence.py` — pure helpers for `purpose_evidence`: the comment-noise table (`NOISE_PATTERNS`, `clean_header_text`), the session line (always the first evidence line) and `assess_purpose_evidence` (points score; Ollama is asked for a purpose only when it is strong, `PURPOSE_STRONG_SCORE`).
- `connectors/live_bridge.py` — CLI that edits `galaxy.json` (see the optional section below).
- `ui/main_window.py` — facade (import from here; it re-exports every name) over the package `ui/window/`: `HeliosMainWindow` (`window.py`: `__init__` + the mixin map) is assembled from plain mixins, one module per area (`startup`, `store_worker`, `history`, `layout`, `header`, `purpose`, `tree`, `navigation`, `inspector_panel`, `node_edits`, `projects`, `ollama_runs`); dialogs and helpers have their own modules (`settings_dialog`, `integrations`, `new_project_dialog`, `remove_project_dialog`, `ignored_folders`, `project_text`, `constants`). `ui/window/` never imports the facade. Monkeypatch a module attribute in the submodule that reads it (e.g. `ui.window.ollama_runs.OllamaEnhanceWorker`).
- `ui/space_canvas.py` — facade of the QGraphicsScene canvas; the code is in `ui/canvas/` (one module per graphics item, `view.py` = `SpaceCanvasView`, its behaviour in `view_*` mixins; module map in the facade docstring).
- `ui/theme.py` — the only place for colours, spacing, fonts and the stylesheet (no inline colour literals elsewhere). `ui/inspector.py` — right-panel rendering helpers.
- `ui/workers.py` — `QThread` workers for long scans, read-only probe threads (`start_probe_worker`) and the canvas paint freezer.
- `ui/welcome.py` — `WelcomePanel`, shown on the canvas while the store has no projects (the ways to add a first project; actions go through `contextActionRequested`).
- `ui/search.py` — `SearchController` (debounced typing, results list under the search field, Up/Down/Enter/Esc); `core/search_index.py` — the pure `SearchIndex` over stored data only (names, the analysis-owned `defined_names` lists — old data falls back to the clipped `Defines:` line —, `Files (N):` lists, packages).
- `ui/filters.py` — `MapFilter` (view state only: link kinds, external libraries, Work History, layers), `FilterPopover`, `FilterBanner`; hidden items are `setVisible(False)`, layout unchanged.
- `ui/export.py` — "Export map" (PNG of the whole scene at 2x, capped at `EXPORT_MAX_SIDE`; Markdown); `core/markdown_export.py` — pure `build_markdown`; `core/map_facts.py` — pure readers of stored facts (`link_kind`, `defined_names`, `relative_path`).
- `tests/` — pytest suite. `projects/` — user repos to scan (git-ignored).

## Run
```bash
pip install -r requirements-dev.txt
python main.py
QT_QPA_PLATFORM=offscreen python -m pytest tests -q
```
On Windows PowerShell set `$env:QT_QPA_PLATFORM = "offscreen"` first. CI runs the same pytest command.

Build the exe on Windows with `build_exe.bat` (or `pip install -r requirements-build.txt` then `pyinstaller --noconfirm helios.spec`).
Release: the version lives only in `pyproject.toml`; push a matching tag (`git tag v0.1.0 && git push --tags`).

## Conventions
- Standard library + PySide6 only. Do not add runtime dependencies without asking (build-only tools go in `requirements-build.txt`).
- Paths come from `core.paths`, never from `Path(__file__)`: bundled files via `resource_path`, user data via `data_dir` / `projects_dir`, Helios's own source via `source_dir` (None in a frozen build, so code that maps Helios itself must skip). A frozen first run starts with no projects (no starter project).
- Windows is the primary target: use `pathlib`, no POSIX-only calls, handle Windows paths as strings where needed.
- Tests must never write to the repo's `data/`. Use `GalaxyStore(storage_path=tmp_path / "galaxy.json")`, and pass `--data` / a temp path to the bridge.
- The UI must not block. Long work (scans, imports, Ollama, "Merge duplicates") goes in `ui/workers.py`; a store-mutating worker owns the store while it runs (see that module's docstring). Nothing probes the disk or the network on the UI thread: tool detection, model lists and similar read-only probes run on a probe thread started with `start_probe_worker` (the window waits for them on close).
- Edits push `store.push_undo()` before they change the data, then call `store.save()` (synchronous and atomic). Saving is incremental only in how it builds the text: each project's indented block is reused while its compact JSON is unchanged, so keep `save()` / `_snapshot()` going through `dumps_compact` / `dumps_indented` (byte-identical to `json.dumps(asdict(...))`); `save` skips the write only when the text equals the file this store wrote, and `is_own_write` trusts the file identity (inode, device, size, mtime_ns) before comparing text.
- Undo / redo: snapshots are tuples of per-project compact JSON; a restore keeps the `SolarSystem` object of every project whose text is unchanged and parses only the others (same result and bytes as parsing everything). UI code must still treat every object as replaced after `store.undo()` / `redo()` (`_drop_stale_view_state(all_replaced=True)`, `_refresh_inspector_for_store(all_replaced=True)`); never keep a model reference across an undo. Every user edit pushes an undo step (only after its dialog was accepted); background results (scans, Ollama text) do not.
- The project tree is always rebuilt in full by `_populate_tree` after a data change (rows built detached, inserted with `addTopLevelItems`). Do not update rows in place, reset the view instead of `clear()`, keep Python references to rows across a rebuild, clone rows, or replace the depth-first `_highlight_node_in_tree` walk: each of these was measured to change the tree's scroll position or current row (see tests/test_tree_equivalence.py, which compares against the previous tree code). The canvas is rebuilt with `rebuild_scene()` after an edit as well.
- Settings are cached in memory: always go through `GalaxyStore.get_setting` / `set_setting` (never read or write `settings.json` directly in the app); the cache is keyed by the file's identity, so another process's change is seen on the next read. `get_setting` returns copies of lists / dicts.
- Caches follow the data they were made from and are checked on use, never invalidated by hand: the search index reuses a project's entries only for the same compact JSON; canvas items re-measure a label when its text or width changes, a link's curve when its end points, radii or reverse flag change; the tree rows are rebuilt whenever the data changed (`store.data_version` counts saves and loads; a pure selection keeps the search index). When adding a cache, key it on every input it reads and add a test that changes each input.
- Imported projects must only show links that were found in source. Never invent dependencies; generated text must say how a result was derived (AST vs. pattern matching, session history vs. architecture). Nothing in a map may be derived from chat text. The local LLM only writes prose (`PlanetNode.ai_summary`); it never creates nodes or links.
- Every project has a purpose line (what it is for). `purpose_source == "user"` is never overwritten by scans or the LLM; without a README description or the LLM the purpose stays empty — never fabricate one from code. The LLM is asked only when `assess_purpose_evidence` says the evidence is strong (code facts such as libraries, shape and names score 0); weak evidence leaves the purpose empty. The LLM's answer is never stored as the purpose: it is a suggestion (`purpose_suggestion`, shown as "Suggested" with Accept / Edit / Dismiss) until the user accepts it (source "ai") or edits it (source "user"); a dismissal blocks automatic suggestions until the evidence changes (`purpose_dismissed_key`). A suggestion is shown only while it matches the current evidence (`purpose_suggestion_key`; a stale one stays stored but hidden) and only while Ollama is available (UI callers pass `ai_available` to `pending_suggestion`). Go through `connectors.purpose_suggestion`; the core plan's `Purpose:` line never shows a suggestion.
- `galaxy.json` has a top-level `format_version` (`GALAXY_FORMAT_VERSION`, now 2). `GalaxyStore.load` migrates older files in memory (v2: old "ai" purposes become suggestions); `save` always writes the current version.
- Without Ollama (turned off or no model found) no AI control, hint or warning is shown anywhere.
- Every scanned project also has an identity line (`SolarSystem.identity`, "what it is"): built only by `codebase_importer.identity_line` from analysis facts (languages, the `IDENTITY_KINDS` table, libraries, entry point), never by the LLM, not user-editable, replaced on every scan; old data without it is derived for display by `stored_identity`. Unknown parts are left out — never guess a kind.
- A tool's dated scratch workspace is a project only with a root marker at its root; folders in the `ignored_folders` setting are never imported.
- `SatelliteNode.defined_names` / `PlanetNode.defined_names`: every definition of the file found at scan time (unclipped, at most `DEFINED_NAMES_CAP`, dunder methods left out), for search only; analysis-owned (replaced on every scan, never shown raw, never user data).
- `is_user_modified` is set only by a real user action (a drag that moved the item, a saved edit, a drawn node/link).
- Keep existing `galaxy.json` files loadable: do not rename or remove dataclass fields; add new fields with defaults.
- Saves go through `atomic_write_text` (`atomic_write_bytes` for binary files such as exported PNGs); an unreadable `galaxy.json` must never be overwritten (a `galaxy.corrupt-*.json` backup is made; the fallback is the starter project from source, no projects when frozen).
- Search, the map filter and export only read stored data: no re-scan, no change to `galaxy.json`, the undo history or `is_user_modified`. Link kinds are read back from the stored label/type by `core.map_facts.link_kind` (`PlanetLink` has no kind field). Exported Markdown never contains a pending purpose suggestion, Work History session text or the absolute project folder.
- `ui/main_window.py`, `ui/space_canvas.py` and `connectors/codebase_importer.py` are facades that only re-export names: never add code there. Put changes in the implementing modules (`ui/window/`, `ui/canvas/`, `connectors/codebase/`) and new features in their own modules.
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
