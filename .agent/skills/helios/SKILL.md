---
name: helios
description: Helios Architecture & Knowledge Graph integration. Lets an agent add or update a project's Core plan, modules, sub-components, and links in a running Helios app through the live bridge CLI, when the user asks for it.
---

# Helios Live Architecture & Knowledge Graph Skill

Use this skill when the user asks you to reflect a project into the **Helios** desktop application. Do not run it after every file change.

## Core Rules
1. **Where to run:** from the Helios repository root, or add `--data PATH/to/galaxy.json` before the command. The bridge writes `data/galaxy.json`; the open app reloads it automatically.
2. **Project Name (`--project`, required):** use the real project name (`Helios`, `MyProject`), never a chat sentence. Alias: `--system`.
3. **Source (`--source`):** one of `antigravity` (default), `claude_code`, `codebase`, `codex`, `manual`. Pass it explicitly; an invalid value exits with code 2.
4. **Layer (`--layer`):** one of `architecture`, `ui`, `core` (default), `integration`, `testing`, `research`, `docs`.
5. **Tag (`--tag`):** one of `thought` (default), `code`, `diagram`, `idea`.
6. **Idempotent:** running a command again with the same title (exact, case-insensitive, trimmed) updates the existing node; it never duplicates it.
7. **Exact titles:** `--planet` / `--from-planet` / `--to-planet` must match an existing module title (a unique whole-word prefix is also accepted, but prefer the exact title). If not found, the command fails (exit 1) and lists the available modules. For `add-moon`, add `--create-planet` to create the module.
8. **Exit codes:** 0 success, 1 runtime problem (unknown module, unreadable `galaxy.json`, missing directory, write failure), 2 invalid arguments.
9. **Only record true links:** add links you know exist in the code, not guesses.

## Commands (`connectors.live_bridge`)

Aliases matching the UI terms: `set-core` = `set-sun`, `add-module` = `add-planet`, `add-component` = `add-moon`; options `--module`, `--from-module`, `--to-module`, `--create-module`.

### 1. Update the Project Core Plan
```powershell
python -m connectors.live_bridge set-sun --project "ProjectName" --source claude_code --title "ProjectName" --plan "Primary goal and architectural summary..."
```

### 2. Add or update an Architectural Module
```powershell
python -m connectors.live_bridge add-planet --project "ProjectName" --source claude_code --title "UI Layer" --layer ui --summary "User interface components and interactions"
```

### 3. Add or update a Sub-Component of a Module
```powershell
python -m connectors.live_bridge add-moon --project "ProjectName" --source claude_code --planet "UI Layer" --title "Space Canvas" --tag code --content "Details..." --create-planet
```

### 4. Add a Data Flow Link Between Modules
```powershell
python -m connectors.live_bridge add-link --project "ProjectName" --source claude_code --from-planet "UI Layer" --to-planet "Core Layer" --label "data model"
```

### 5. Analyze a Project Directory Directly
```powershell
python -m connectors.live_bridge analyze-folder --path "./my-project"
```
Modules are the project's top-level directories (one module per file for small projects); links come only from resolved imports/includes in Python, JS/TS, PHP and HTML references. Other languages get no links. Re-running it on the same directory refreshes the existing `codebase` project instead of duplicating it. The project purpose is only written from the project's own words (with Ollama, strong evidence only produces a "Suggested" sentence the user accepts, edits or dismisses); a descriptive README or a short header comment that says what the project is for (not license / encoding / lint lines) helps. It also writes the project's identity line (languages, kind, main libraries, entry point — derived from the code only), shown under the purpose.
