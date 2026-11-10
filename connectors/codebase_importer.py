"""Codebase, Understand-Anything (.ua), and Markdown Wiki Analysis Engine.

1. Imports `.ua/knowledge-graph.json` or `.understand-anything/knowledge-graph.json` directly if present.
2. Otherwise uses Python `ast`, multi-language structural parsing, and Markdown `[[wikilink]]`
   scanning to analyze the directory and convert it into a Project Core (Core Plan),
   Modules (Architectural Layers), Sub-Components (Files/Classes/Functions), and Dependency Links.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.models import LAYER_DEFINITIONS, GalaxyStore, PlanetNode, SolarSystem

IGNORED_DIRS: Set[str] = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    "target",
    "bin",
    "obj",
}

CODE_EXTENSIONS: Dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript (React)",
    ".js": "JavaScript",
    ".jsx": "JavaScript (React)",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".java": "Java",
    ".cpp": "C++",
    ".c": "C",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
    ".md": "Markdown / Wiki",
}


def _classify_file_layer(rel_path: Path) -> str:
    """Determines the architectural layer based on file path and stem."""
    parts = [p.lower() for p in rel_path.parts]
    stem = rel_path.stem.lower()
    suffix = rel_path.suffix.lower()

    if suffix == ".md":
        if stem in {"readme", "claude", "architecture", "plan", "roadmap"}:
            return "architecture"
        return "docs"

    if any(p in {"ui", "views", "components", "pages", "frontend", "widgets", "templates", "styles"} for p in parts) or any(
        k in stem for k in ("window", "canvas", "view", "widget", "dialog", "ui", "style", "theme", "dashboard")
    ):
        return "ui"

    if any(p in {"test", "tests", "__tests__", "spec", "scripts"} for p in parts) or stem.startswith("test_") or stem.endswith("_test"):
        return "testing"

    if any(
        p in {"connectors", "api", "routes", "controllers", "clients", "bridges", "adapters", "plugins", "skills", "agents"}
        for p in parts
    ) or any(k in stem for k in ("connector", "importer", "bridge", "client", "api", "webhook", "bot", "scraper", "fetch")):
        return "integration"

    if any(p in {"core", "models", "domain", "entities", "db", "database", "store", "services", "engine"} for p in parts) or any(
        k in stem for k in ("model", "schema", "store", "db", "database", "engine", "service", "manager", "analyzer")
    ):
        return "core"

    if stem in {"main", "app", "index", "config", "settings", "setup"}:
        return "architecture"

    return "core"


def _analyze_python_file(file_path: Path) -> Tuple[str, List[str], List[str], List[str]]:
    """Parses a Python file with AST and returns its docstring, classes, functions, and imports."""
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except Exception:
        return "", [], [], []

    doc = ast.get_docstring(tree) or ""
    classes: List[str] = []
    functions: List[str] = []
    imports: List[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            c_doc = ast.get_docstring(node) or ""
            short_doc = c_doc.splitlines()[0] if c_doc else ""
            methods = [
                n.name
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
            ]
            desc = f"class {node.name}"
            if methods:
                desc += f" ({', '.join(methods[:4])})"
            if short_doc:
                desc += f" — {short_doc[:70]}"
            classes.append(desc)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_") or node.name == "__init__":
                f_doc = ast.get_docstring(node) or ""
                short_doc = f_doc.splitlines()[0] if f_doc else ""
                desc = f"def {node.name}()"
                if short_doc:
                    desc += f" — {short_doc[:70]}"
                functions.append(desc)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])

    return doc.strip(), classes, functions, imports


def _analyze_generic_code_file(file_path: Path) -> Tuple[str, List[str], List[str], List[str]]:
    """Analyzes TS/JS/Go/C# files using structural regex."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", [], [], []

    classes = re.findall(r"\b(?:class|interface|struct)\s+([A-Za-z0-9_]+)", text)
    functions = re.findall(
        r"\b(?:function|def|func)\s+([A-Za-z0-9_]+)|\b(?:export\s+const|const)\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\(",
        text,
    )
    fn_list = [a or b for a, b in functions if (a or b)][:8]
    imports = re.findall(r"(?:from|import)\s+['\"]([^'\"]+)['\"]", text)
    first_comment = ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:5]
    for ln in lines:
        if ln.startswith(("//", "/*", "#", "*")):
            first_comment = ln.lstrip("/*# ").rstrip("*/ ").strip()
            if len(first_comment) > 10:
                break

    return first_comment, [f"class/type {c}" for c in classes[:6]], [f"fn {f}()" for f in fn_list], imports


def _analyze_markdown_file(file_path: Path) -> Tuple[str, List[str], List[str]]:
    """Extracts headings, wikilinks, and summary paragraphs from a Markdown file."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "", [], []

    headings = re.findall(r"^#{1,3}\s+(.+)$", text, flags=re.MULTILINE)
    wikilinks = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)
    paragraphs = [
        p.strip()
        for p in text.splitlines()
        if p.strip() and not p.strip().startswith(("#", "!", "<", "---", "```", "|"))
    ]
    summary = "\n".join(paragraphs[:3])[:450]
    return summary, headings[:8], wikilinks[:10]


TECH_FINGERPRINTS: Dict[str, Tuple[str, str]] = {
    # UI / Desktop / Frontend
    "pyside6": ("Desktop Graphics & UI Application", "PySide6 (Qt6) desktop graphical user interface"),
    "pyqt6": ("Desktop Graphics & UI Application", "PyQt6 desktop windowing system"),
    "pyqt5": ("Desktop GUI Application", "PyQt5 desktop windowing system"),
    "tkinter": ("Desktop GUI Application", "Tkinter interface library"),
    "electron": ("Cross-Platform Desktop Application", "Electron-based desktop system"),
    "tauri": ("Lightweight Desktop Application", "Rust & Webview Tauri application"),
    "react": ("Web Frontend & Component UI", "React-based web interface"),
    "next": ("Full-Stack Web Portal", "Next.js web application"),
    "vue": ("Web Frontend UI", "Vue.js single-page application"),
    "svelte": ("Reactive Web UI", "Svelte compiler-based web interface"),
    "tailwindcss": ("Styling & Design System", "TailwindCSS utility-first styling"),

    # API / Backend
    "fastapi": ("High-Performance REST API", "FastAPI asynchronous microservice / API backend"),
    "flask": ("Web Service / REST API", "Flask micro web framework service"),
    "django": ("Full-Featured Web Platform", "Django database-driven web framework"),
    "express": ("Node.js Backend & API Service", "Express.js RESTful API infrastructure"),
    "nestjs": ("Enterprise TypeScript Backend", "NestJS modular backend service"),
    "spring": ("Enterprise Java Service", "Spring Boot backend service"),

    # AI / Data / Machine Learning
    "torch": ("Deep Learning & AI", "PyTorch neural network training and inference"),
    "tensorflow": ("AI & Machine Learning Model", "TensorFlow machine learning system"),
    "pandas": ("Data Analysis & Processing Engine", "Pandas tabular data analytics"),
    "scipy": ("Scientific Computing Engine", "SciPy numerical analysis"),
    "langchain": ("LLM & Agent Workflow", "LangChain language model chains and autonomous agents"),
    "ollama": ("Local LLM Integration", "Ollama local AI inference"),

    # Bot / Automation / Scraping
    "telethon": ("Telegram Bot & Automation", "Telethon MTProto API Telegram automation"),
    "aiogram": ("Telegram Bot Service", "Aiogram asynchronous bot framework"),
    "discord": ("Discord Bot Service", "Discord API bot and community automation"),
    "selenium": ("Web Automation & Testing", "Selenium browser automation and scraping"),
    "playwright": ("End-to-End Web Automation", "Playwright browser automation"),
    "scrapy": ("Web Crawling Engine", "Scrapy data extraction framework"),

    # Database / ORM
    "sqlalchemy": ("Database ORM & Model Layer", "SQLAlchemy relational database abstraction"),
    "prisma": ("Database ORM Layer", "Prisma type-safe database client"),
    "mongoose": ("NoSQL / MongoDB Data Layer", "Mongoose document object modeling"),
    "sqlite3": ("Embedded Database Store", "SQLite local file-based storage"),
}


def _detect_project_technologies(folder: Path) -> Tuple[List[str], List[str], str]:
    """Deterministically extracts technologies, architectural types, and descriptions
    from dependency files (package.json, requirements.txt, pyproject.toml, etc.).
    """
    detected_types: List[str] = []
    tech_stack: List[str] = []
    pkg_summary = ""
    seen_keys: Set[str] = set()

    pkg_json = folder / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            desc = str(data.get("description", "")).strip()
            if desc:
                pkg_summary = desc
            deps = dict(data.get("dependencies", {}))
            deps.update(data.get("devDependencies", {}))
            for dep_name in deps.keys():
                dep_low = dep_name.lower().replace("@", "").split("/")[0]
                for key, (t_type, t_desc) in TECH_FINGERPRINTS.items():
                    if key in dep_low and key not in seen_keys:
                        seen_keys.add(key)
                        if t_type not in detected_types:
                            detected_types.append(t_type)
                        tech_stack.append(f"{t_desc} (`{dep_name}`)")
        except Exception:
            pass

    req_files = [folder / "requirements.txt", folder / "pyproject.toml", folder / "setup.py", folder / "Pipfile"]
    for rf in req_files:
        if not rf.exists():
            continue
        try:
            txt = rf.read_text(encoding="utf-8", errors="replace").lower()
            for key, (t_type, t_desc) in TECH_FINGERPRINTS.items():
                if key in txt and key not in seen_keys:
                    seen_keys.add(key)
                    if t_type not in detected_types:
                        detected_types.append(t_type)
                    tech_stack.append(f"{t_desc} (`{key}`)")
        except Exception:
            pass

    cargo_toml = folder / "Cargo.toml"
    if cargo_toml.exists():
        if "Rust Systems Application" not in detected_types:
            detected_types.append("Rust Systems & Performance Application")
        tech_stack.append("Cargo package manager and Rust compiler")

    go_mod = folder / "go.mod"
    if go_mod.exists():
        if "Go Microservice / Backend" not in detected_types:
            detected_types.append("Go Microservice & Concurrent System")
        tech_stack.append("Go runtime and module system")

    return detected_types, tech_stack, pkg_summary


class CodebaseImporter:
    """Converts local project directories or Understand-Anything (.ua) graphs into Helios systems."""

    @staticmethod
    def analyze_directory(folder_path: Path, store: GalaxyStore) -> Optional[SolarSystem]:
        """Analyzes the given directory and adds/updates it in the store (`source='codebase'`)."""
        folder = folder_path.resolve()
        if not folder.exists() or not folder.is_dir():
            return None

        for ua_dir_name in (".ua", ".understand-anything"):
            ua_graph_path = folder / ua_dir_name / "knowledge-graph.json"
            if ua_graph_path.exists():
                ua_sys = CodebaseImporter._import_from_ua_graph(folder, ua_graph_path)
                if ua_sys:
                    return CodebaseImporter._save_or_update(store, ua_sys)

        project_name = folder.name
        files: List[Path] = []
        lang_counts: Dict[str, int] = {}
        total_lines = 0

        for item in folder.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(folder)
            if any(part in IGNORED_DIRS or part.startswith(".") for part in rel.parts[:-1]):
                continue
            ext = item.suffix.lower()
            if ext in CODE_EXTENSIONS:
                files.append(item)
                lang = CODE_EXTENSIONS[ext]
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
                try:
                    total_lines += len(item.read_text(encoding="utf-8", errors="replace").splitlines())
                except Exception:
                    pass

        if not files:
            return None

        from connectors.agent_importer import PROJECT_BLUEPRINTS

        blueprint = PROJECT_BLUEPRINTS.get(project_name)
        readme_summary = str(blueprint.get("purpose", "")) if blueprint else ""
        for doc_name in ("README.md", "CLAUDE.md", "AGENTS.md", "readme.md", "index.md"):
            doc_file = folder / doc_name
            if doc_file.exists():
                summary, headings, _ = _analyze_markdown_file(doc_file)
                if not readme_summary:
                    readme_summary = summary
                if headings:
                    readme_summary += "\n\nKey Sections: " + " • ".join(headings[:6])
                break

        if not readme_summary:
            for entry_name in ("main.py", "app.py", "index.py", "server.py"):
                entry_p = folder / entry_name
                if entry_p.exists():
                    doc, _, _, _ = _analyze_python_file(entry_p)
                    if doc:
                        readme_summary = doc[:380]
                        break

        detected_types, tech_stack, pkg_summary = _detect_project_technologies(folder)

        purpose_blocks: List[str] = []
        if readme_summary:
            purpose_blocks.append(readme_summary)
        elif pkg_summary:
            purpose_blocks.append(pkg_summary)
        elif detected_types:
            purpose_blocks.append(f"{project_name} is a software project built as a {' + '.join(detected_types)}.")
        else:
            purpose_blocks.append(f"{project_name} source codebase and architectural modules.")

        if detected_types:
            purpose_blocks.append("ARCHITECTURAL TYPE & ROLE:\n• " + "\n• ".join(detected_types))

        if tech_stack:
            purpose_blocks.append("TECHNOLOGIES & LIBRARIES USED:\n• " + "\n• ".join(tech_stack[:8]))

        lang_str = ", ".join(f"{k} ({v})" for k, v in sorted(lang_counts.items(), key=lambda x: -x[1]))
        core_plan_lines = [
            "PROJECT PURPOSE & OVERVIEW (WHAT IT DOES):\n" + "\n\n".join(purpose_blocks),
            f"\nSTRUCTURE & SCOPE:\n• {len(files)} source files • ~{total_lines} lines of code/docs",
            f"• Languages: {lang_str}",
            f"\nPROJECT DIRECTORY: {folder}",
        ]

        system = SolarSystem(
            name=project_name,
            project_name=project_name,
            sun_title=project_name,
            core_plan="\n".join(core_plan_lines),
            source="codebase",
            source_id=str(folder).lower(),
            workspace_path=str(folder),
        )

        layer_files: Dict[str, List[Path]] = {}
        file_layer_map: Dict[str, str] = {}
        for f in files:
            rel = f.relative_to(folder)
            layer = _classify_file_layer(rel)
            layer_files.setdefault(layer, []).append(f)
            file_layer_map[f.stem.lower()] = layer
            if len(rel.parts) > 1:
                file_layer_map[rel.parts[0].lower()] = layer

        layer_order = ["architecture", "core", "integration", "ui", "testing", "docs", "research"]
        layer_planets: Dict[str, PlanetNode] = {}
        cross_layer_imports: List[Tuple[str, str, str]] = []

        layer_purpose_map = {
            "architecture": "Manages application entry points (`main`, `app`, `config`), lifecycle initialization, and high-level setup.",
            "core": "Executes domain data structures, class models, persistent storage (`store`/`db`), and core business logic.",
            "integration": "Contains adapters connecting to external services, local AI models (Ollama), agent session logs, and external data sources.",
            "ui": "Houses user interface windows, the interactive graphics canvas, panels, dialogs, and visual theme components.",
            "testing": "Contains unit tests, validation scripts, and security/quality verification checks.",
            "docs": "Contains README files, architectural guides, agent skills (`SKILL.md`), and technical documentation.",
            "research": "Contains technical research notes, prototypes, and comparative analyses.",
        }

        for layer_key in layer_order:
            f_list = layer_files.get(layer_key, [])
            if not f_list:
                continue

            layer_title, layer_color = LAYER_DEFINITIONS.get(
                layer_key, ("Module Layer", "#4EA8DE")
            )
            rel_names = [str(f.relative_to(folder)) for f in f_list[:10]]
            layer_purpose = layer_purpose_map.get(
                layer_key, "Groups source files and modules belonging to this layer."
            )
            planet = system.add_planet(
                title=layer_title,
                summary=(
                    f"MODULE PURPOSE & ROLE:\n"
                    f"'{layer_title}' layer of {project_name} ({len(f_list)} source files). {layer_purpose}\n\n"
                    f"HOW IT WORKS & TECHNICAL FLOW:\n"
                    f"• Each sub-component under this module represents a concrete source code or documentation file.\n"
                    f"• Classes, functions, and cross-layer import dependencies were automatically extracted via AST analysis.\n\n"
                    f"INCLUDED FILES:\n• " + "\n• ".join(rel_names)
                ),
                color=layer_color,
                layer=layer_key,
            )
            layer_planets[layer_key] = planet

            sorted_files = sorted(f_list, key=lambda p: (len(p.relative_to(folder).parts), -p.stat().st_size))
            for f_item in sorted_files[:10]:
                rel_str = str(f_item.relative_to(folder))
                ext = f_item.suffix.lower()
                try:
                    line_count = len(f_item.read_text(encoding="utf-8", errors="replace").splitlines())
                except Exception:
                    line_count = 0

                if ext == ".py":
                    doc, classes, funcs, imports = _analyze_python_file(f_item)
                    role_text = (
                        doc[:380]
                        if doc
                        else f"`{rel_str}` defines Python classes and functions in the '{layer_title}' layer of {project_name}."
                    )
                    parts = [
                        f"WHAT IT DOES:\n{role_text}",
                        f"\nHOW IT WORKS & TECHNICAL DETAILS:\n• File Path: `{rel_str}` (~{line_count} lines of Python)",
                    ]
                    if classes:
                        parts.append("\nDEFINED CLASSES:\n• " + "\n• ".join(classes[:6]))
                    if funcs:
                        parts.append("\nKEY FUNCTIONS & METHODS:\n• " + "\n• ".join(funcs[:8]))
                    if imports:
                        parts.append("\nIMPORTED PACKAGES / DEPENDENCIES:\n" + ", ".join(sorted(set(imports))[:12]))
                    parts.append(f"\nRELATED FILE: `{f_item}`")
                    planet.add_satellite(
                        title=f_item.name,
                        content="\n".join(parts),
                        visual_tag="code" if (classes or funcs) else "diagram",
                        file_path=str(f_item),
                    )
                    for imp in imports:
                        target_layer = file_layer_map.get(imp.lower())
                        if target_layer and target_layer != layer_key:
                            cross_layer_imports.append((layer_key, target_layer, f"{f_item.name} → {imp}"))

                elif ext == ".md":
                    md_sum, headings, wikilinks = _analyze_markdown_file(f_item)
                    parts = [
                        f"WHAT IT DOES:\n{md_sum or f'`{rel_str}` is a project documentation and technical guide file.'}",
                        f"\nHOW IT WORKS & TECHNICAL DETAILS:\n• Document Path: `{rel_str}` (~{line_count} lines of Markdown)",
                    ]
                    if headings:
                        parts.append("\nSECTIONS & HEADINGS:\n• " + "\n• ".join(headings))
                    if wikilinks:
                        parts.append("\nWIKI LINKS: " + ", ".join(wikilinks))
                    parts.append(f"\nRELATED FILE: `{f_item}`")
                    planet.add_satellite(
                        title=f_item.name,
                        content="\n".join(parts),
                        visual_tag="diagram",
                        file_path=str(f_item),
                    )

                else:
                    comment, classes, funcs, imports = _analyze_generic_code_file(f_item)
                    lang_label = CODE_EXTENSIONS.get(ext, "Code")
                    parts = [
                        f"WHAT IT DOES:\n{comment or f'`{rel_str}` ({lang_label}) source file operating in the {layer_title} layer.'}",
                        f"\nHOW IT WORKS & TECHNICAL DETAILS:\n• File Path: `{rel_str}` (~{line_count} lines of {lang_label})",
                    ]
                    if classes:
                        parts.append("\nSTRUCTURES & TYPES:\n• " + "\n• ".join(classes))
                    if funcs:
                        parts.append("\nFUNCTIONS:\n• " + "\n• ".join(funcs))
                    parts.append(f"\nRELATED FILE: `{f_item}`")
                    planet.add_satellite(
                        title=f_item.name,
                        content="\n".join(parts),
                        visual_tag="code",
                        file_path=str(f_item),
                    )

        added_links = set()
        for src_layer, dst_layer, reason in cross_layer_imports:
            if src_layer in layer_planets and dst_layer in layer_planets and src_layer != dst_layer:
                pair = (src_layer, dst_layer)
                if pair not in added_links:
                    added_links.add(pair)
                    system.add_link(
                        layer_planets[src_layer].id,
                        layer_planets[dst_layer].id,
                        label=reason,
                        relation_type="flows_to",
                    )

        if "architecture" in layer_planets:
            arch_p = layer_planets["architecture"]
            if "ui" in layer_planets and ("architecture", "ui") not in added_links:
                system.add_link(arch_p.id, layer_planets["ui"].id, label="Window / UI Startup", relation_type="depends_on")
                added_links.add(("architecture", "ui"))
            if "core" in layer_planets and ("architecture", "core") not in added_links:
                system.add_link(arch_p.id, layer_planets["core"].id, label="Core Engine Setup", relation_type="depends_on")
                added_links.add(("architecture", "core"))

        if "ui" in layer_planets and "core" in layer_planets and ("ui", "core") not in added_links:
            system.add_link(layer_planets["ui"].id, layer_planets["core"].id, label="User Actions & Model Calls", relation_type="flows_to")
            added_links.add(("ui", "core"))

        if "core" in layer_planets and "integration" in layer_planets and ("core", "integration") not in added_links:
            system.add_link(layer_planets["core"].id, layer_planets["integration"].id, label="External Service & Data Bridge", relation_type="api_call")
            added_links.add(("core", "integration"))

        if "testing" in layer_planets and "core" in layer_planets and ("testing", "core") not in added_links:
            system.add_link(layer_planets["testing"].id, layer_planets["core"].id, label="Unit Test & Validation", relation_type="validates")
            added_links.add(("testing", "core"))

        system.rebuild_links_and_tour()
        return CodebaseImporter._save_or_update(store, system)

    @staticmethod
    def _import_from_ua_graph(folder: Path, graph_path: Path) -> Optional[SolarSystem]:
        """Converts an Understand-Anything `.ua/knowledge-graph.json` file directly into a project system."""
        try:
            data = json.loads(graph_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None

        project_name = data.get("projectName") or data.get("name") or folder.name
        summary = data.get("summary") or data.get("description") or "Understand-Anything knowledge graph."
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        system = SolarSystem(
            name=project_name,
            project_name=project_name,
            sun_title=f"{project_name} (.ua Graph)",
            core_plan=f"Understand-Anything Knowledge Graph\nFolder: {folder}\n\n{summary}",
            source="codebase",
            source_id=str(folder).lower(),
            workspace_path=str(folder),
        )

        by_layer: Dict[str, List[dict]] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            layer_name = str(node.get("layer") or node.get("type") or "core").lower()
            by_layer.setdefault(layer_name, []).append(node)

        planet_by_node_id: Dict[str, PlanetNode] = {}
        for layer_name, n_list in list(by_layer.items())[:8]:
            mapped_layer = "core"
            if any(k in layer_name for k in ("ui", "view", "front", "component")):
                mapped_layer = "ui"
            elif any(k in layer_name for k in ("api", "service", "connect", "route")):
                mapped_layer = "integration"
            elif any(k in layer_name for k in ("arch", "config", "entry")):
                mapped_layer = "architecture"
            elif any(k in layer_name for k in ("test", "spec")):
                mapped_layer = "testing"

            planet = system.add_planet(
                title=f"Layer: {layer_name.title()}",
                summary=f"{len(n_list)} components in Understand-Anything '{layer_name}' layer.",
                layer=mapped_layer,
            )
            for n in n_list[:10]:
                n_title = str(n.get("label") or n.get("name") or n.get("id") or "Component")
                n_desc = str(n.get("summary") or n.get("description") or "")
                planet.add_satellite(
                    title=n_title[:38],
                    content=n_desc or json.dumps(n, ensure_ascii=False, indent=2)[:500],
                    visual_tag="code",
                )
                if n.get("id"):
                    planet_by_node_id[str(n["id"])] = planet

        for edge in edges[:30]:
            if not isinstance(edge, dict):
                continue
            s_id = str(edge.get("source", ""))
            t_id = str(edge.get("target", ""))
            if s_id in planet_by_node_id and t_id in planet_by_node_id:
                system.add_link(
                    planet_by_node_id[s_id].id,
                    planet_by_node_id[t_id].id,
                    label=str(edge.get("type") or edge.get("label") or "connected"),
                    relation_type="depends_on",
                )

        system.rebuild_links_and_tour()
        return system

    @staticmethod
    def _save_or_update(store: GalaxyStore, parsed: SolarSystem) -> SolarSystem:
        existing = store.get_by_project_name("codebase", parsed.project_name) or store.get_by_source_id(
            "codebase", parsed.source_id
        )
        if existing:
            user_planets = [p for p in existing.planets if p.is_user_modified]
            captured_links = existing.capture_user_links()
            existing.name = parsed.name
            existing.project_name = parsed.project_name
            existing.sun_title = parsed.sun_title
            if not existing.is_user_modified:
                ollama_tail = ""
                marker = "[OLLAMA VERIFIED ARCHITECTURE ANALYSIS"
                if marker in existing.core_plan:
                    idx = existing.core_plan.find(marker)
                    ollama_tail = "\n\n" + existing.core_plan[idx:]
                existing.core_plan = parsed.core_plan + ollama_tail
            existing.workspace_path = parsed.workspace_path
            existing.planets = parsed.planets
            for up in user_planets:
                if not any(p.title.lower() == up.title.lower() for p in existing.planets):
                    existing.planets.append(up)
            existing.links = parsed.links
            existing.restore_user_links(captured_links)
            existing.rebuild_links_and_tour()
            store.save()
            return existing
        return store.add_system(parsed)

    @staticmethod
    def scan_parent_projects_directory(
        parent_dir: Path,
        store: GalaxyStore,
    ) -> List[SolarSystem]:
        """Detects all sub-projects inside a parent directory and builds an architecture map for each."""
        if not parent_dir.exists() or not parent_dir.is_dir():
            return []

        results: List[SolarSystem] = []
        project_indicators = {
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "cargo.toml",
            "go.mod",
            "pom.xml",
            ".git",
            "composer.json",
            "gemfile",
            "docker-compose.yml",
            "docker-compose.yaml",
        }

        try:
            entries = list(parent_dir.iterdir())
        except Exception:
            return []

        for child in entries:
            if not child.is_dir():
                continue
            name_lower = child.name.lower()
            if name_lower in IGNORED_DIRS or name_lower.startswith((".", "$")):
                continue

            is_project = False
            for ind in project_indicators:
                if (child / ind).exists():
                    is_project = True
                    break

            if not is_project:
                try:
                    has_code = any(
                        p.suffix.lower() in CODE_EXTENSIONS
                        for p in child.iterdir()
                        if p.is_file()
                    )
                    if has_code:
                        is_project = True
                except Exception:
                    pass

            if is_project:
                try:
                    sys_obj = CodebaseImporter.analyze_directory(child, store)
                    if sys_obj:
                        sys_obj.merge_duplicate_nodes()
                        results.append(sys_obj)
                except Exception:
                    pass

        if results:
            store.save()
        return results

    @staticmethod
    def get_helios_projects_dir() -> Path:
        """Returns Helios's internal `projects/` directory, creating it if needed."""
        helios_root = Path(__file__).resolve().parent.parent
        proj_dir = helios_root / "projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        return proj_dir

    @staticmethod
    def scan_helios_projects(store: GalaxyStore) -> List[SolarSystem]:
        """Scans only Helios's internal `projects/` folder and builds architecture maps for projects placed there."""
        proj_dir = CodebaseImporter.get_helios_projects_dir()
        return CodebaseImporter.scan_parent_projects_directory(proj_dir, store)
