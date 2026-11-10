# Helios — Claude Code Guide & Live Architecture Bridge

This file enables **Claude Code** to communicate with the Helios desktop application in real time and map projects into the architectural graph.

---

## Live Integration Commands

Whenever Claude Code adds a new feature, modifies files in a repository, or is asked by the user to import a project into Helios, run the following commands:

### 1. Automatically Analyze a Project Directory
```powershell
python -m connectors.live_bridge analyze-folder --path "./ProjectDirectory"
```
Scans source files in the project (Python AST, TypeScript, Go, C#, Markdown, etc.) and automatically converts architectural layers, files, and classes into the Project Core, Modules, and Sub-Components.

### 2. Update the Project Core Plan
```powershell
python -m connectors.live_bridge set-sun --project "ProjectName" --source claude_code --title "ProjectName" --plan "Primary goal, architecture summary, and key technical decisions..."
```

### 3. Add a New Architectural Module (Layer)
Supported layer keys (`--layer`):
- `architecture` (Architecture & Planning)
- `ui` (UI & Visualization)
- `core` (Core & Data Model)
- `integration` (Integration & API Bridges)
- `testing` (Testing, Security & Validation)
- `research` (Research & Analysis)
- `docs` (Documentation)

```powershell
python -m connectors.live_bridge add-planet --project "ProjectName" --source claude_code --title "UI Layer" --layer ui --summary "Visual components and user interactions"
```

### 4. Add a Sub-Component / Code Detail to a Module
```powershell
python -m connectors.live_bridge add-moon --project "ProjectName" --source claude_code --planet "UI Layer" --title "Space Canvas Component" --tag code --content "WHAT DOES IT DO?\nRenders interactive canvas items.\n\nHOW IT WORKS:\nUses QGraphicsScene for vector node rendering."
```

### 5. Add a Data Flow Link Between Modules
```powershell
python -m connectors.live_bridge add-link --project "ProjectName" --source claude_code --from-planet "UI Layer" --to-planet "Core Layer" --label "data model flow"
```

---

## Live Refresh Mechanism
The Helios desktop app watches `data/galaxy.json` in the background using `QFileSystemWatcher`. Running any of the commands above updates the open desktop canvas immediately.
