---
name: helios
description: Helios Architecture & Knowledge Graph integration. Live-updates projects by application source (antigravity, codex, claude_code, codebase) and Project Name as Project Core (Main Plan), Modules (Architectural Layers), Sub-Components (Code / Details), and Data Flow Links.
---

# Helios Live Architecture & Knowledge Graph Skill

This skill lets you reflect the projects you are working on into the **Helios** desktop application in real time.

## Core Rules
1. **Project Name (`--project`):** System names should never be raw chat sentences; always use the actual **Project Name** (`Helios`, `MyProject`, etc.).
2. **Application Source (`--source`):** Each tool is categorized under its own source identifier:
   - `antigravity` (Antigravity Projects)
   - `codex` (OpenAI Codex Projects)
   - `claude_code` (Claude Code Projects)
   - `codebase` (Codebase & Architecture Analyses)
   - `manual` (Personal & Manual Projects)
3. **Architectural Layers (`--layer`):** Modules should be assigned to one of `architecture`, `ui`, `core`, `integration`, `testing`, `research`, or `docs`.

## Commands (`connectors.live_bridge`)

Run commands from the Helios project root directory:

### 1. Update the Project Core Plan
```powershell
python -m connectors.live_bridge set-sun --project "ProjectName" --source antigravity --title "ProjectName" --plan "Primary goal and architectural summary..."
```

### 2. Add an Architectural Layer / Module
```powershell
python -m connectors.live_bridge add-planet --project "ProjectName" --source antigravity --title "UI Layer" --layer ui --summary "User interface components and interactions"
```

### 3. Add a Sub-Component to a Module
```powershell
python -m connectors.live_bridge add-moon --project "ProjectName" --source antigravity --planet "UI Layer" --title "Space Canvas" --tag code --content "Details..."
```

### 4. Add a Data Flow Link Between Modules
```powershell
python -m connectors.live_bridge add-link --project "ProjectName" --source antigravity --from-planet "UI Layer" --to-planet "Core Layer" --label "data model"
```

### 5. Analyze a Project Directory Directly
```powershell
python -m connectors.live_bridge analyze-folder --path "./my-project"
```
