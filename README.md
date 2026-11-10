# Helios

**Helios** is a desktop application that visualizes project plans, session histories from AI coding tools (Antigravity, OpenAI Codex, Claude Code), and local codebases as an interactive architectural knowledge graph.

---

## Core Architecture Model

1. **Project Core (Core Plan):**
   The central node of each project holds its primary goal, domain summary, and core architectural rules. It remains pinned at the top of the right-hand inspector panel so the big picture is never lost.
2. **Architectural Modules (Layers):**
   Layer nodes orbiting the Project Core (`Architecture & Planning`, `UI & Visualization`, `Core & Data Model`, `Integration & Bridges`, `Testing & Validation`, `Research & Analysis`, `Documentation`).
3. **Sub-Components:**
   Source files, classes, functions, and technical design decisions orbiting each module.
4. **Data Flow & Dependency Links:**
   Directed connections illustrating import relationships, API calls, and execution flow between modules.
5. **Project & Source Overview:**
   A top-level board organizing projects either unified across tools or grouped by source (`Antigravity`, `OpenAI Codex`, `Claude Code`, `Codebase Analysis`, `Personal Projects`).

---

## Key Features

- **AI Agent & Data Source Integrations:**
  Automatically detects installed AI coding assistants on your machine, filters out terminal noise from session logs, and transforms project history into clean architectural maps.
- **Deterministic Local Codebase (AST) Analysis:**
  Scans Python (`ast`), TypeScript/JavaScript, Go, Rust, C#, and Markdown files to automatically extract architectural layers, classes, functions, and cross-module dependencies.
- **Optional Local LLM (Ollama) Verification:**
  Operates completely standalone with its deterministic analysis engine. Optionally connects to local Ollama models to verify and enrich project summaries.
- **Interactive Drawing & Diagramming Tools:**
  Draw new modules, sub-components, directed data flow arrows, and freeform architecture notes directly on the canvas with full undo/redo (`Ctrl+Z` / `Ctrl+Y`) history.
- **One-Click AI Context (Prompt) Export:**
  Compiles the Project Core plan, modules, sub-components, and data flow rules into a structured Markdown prompt ready to paste into any coding agent.
- **Live CLI Bridge:**
  Coding agents can update the live map in real time via `python -m connectors.live_bridge` commands.

---

## Installation & Usage

### Requirements
- Python 3.10+
- `PySide6-Essentials`

```bash
pip install -r requirements.txt
python main.py
```

On Windows, you can also launch the application directly with `run_helios.bat`.

---

## Analyzing Local Projects (`projects/`)

Place any local repositories you want to inspect inside the `projects/` directory and use **+ Add Project -> Scan 'projects/' Directory** inside the app to map them in one click.

You can also analyze any directory from the command line:

```bash
python -m connectors.live_bridge analyze-folder --path "./my-project"
```

---

## Repository Structure

```text
Helios/
├── main.py                     # Application entry point
├── requirements.txt            # Python dependencies
├── run_helios.bat              # Windows launcher script
├── LICENSE                     # MIT License
├── CLAUDE.md                   # Claude Code live integration guide
├── core/
│   └── models.py               # Data models, layer definitions, and GalaxyStore
├── connectors/
│   ├── agent_importer.py       # Antigravity, Codex, and Claude Code session analyzer
│   ├── codebase_importer.py    # AST-based local codebase and directory analyzer
│   ├── ollama_enhancer.py      # Optional local Ollama verification enhancer
│   └── live_bridge.py          # Live CLI / AI agent bridge
├── ui/
│   ├── main_window.py          # Main window, integrations view, and inspector panel
│   └── space_canvas.py         # QGraphicsScene-based interactive architecture canvas
└── projects/                   # Directory for local projects to analyze (.gitkeep)
```

---

## License

Distributed under the [MIT License](LICENSE).
