"""Scans local Antigravity, OpenAI Codex, and Claude Code session histories
and converts them into Project Name and Architectural Layer graphs.

Core Principles:
1. Every Project Core (Sun) clearly describes what the project does, its purpose, and its core architecture.
2. Trivial/noisy messages ("run code", "start file", "continue", raw error dumps, token strings,
   and internal system prompts) are filtered out.
3. Modules and sub-components are created from meaningful architectural features, modules,
   and technical decision headings rather than raw chat lines.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.models import LAYER_DEFINITIONS, GalaxyStore, PlanetNode, SolarSystem


def _tr_lower(text: str) -> str:
    """Lowercases text safely across ASCII and Unicode characters."""
    return text.replace("İ", "i").replace("I", "i").lower()


GENERIC_FOLDER_NAMES = {
    _tr_lower(Path.home().name),
    "users",
    "user",
    "desktop",
    "documents",
    "downloads",
    "projects",
    "source",
    "repos",
    "codex",
    "antigravity",
    "chatgpt",
    "claude",
    "brain",
    "scratch",
    "logs",
    "sessions",
    "archived_sessions",
    "system_generated",
    ".gemini",
    ".codex",
    ".claude",
    "c:",
    "d:",
    "xampp",
    "htdocs",
    "php",
    "node",
    "node.exe",
    "python",
    "python.exe",
    "scripts",
    "bin",
    "obj",
    "src",
    "dist",
    "build",
    "venv",
    ".venv",
    "appdata",
    "local",
    "roaming",
    "temp",
    "tmp",
    "cache",
}


# Curated architectural blueprint for Helios itself
PROJECT_BLUEPRINTS: Dict[str, Dict[str, object]] = {
    "Helios": {
        "purpose": (
            "Interactive desktop architecture mapping application that transforms AI project planning, "
            "coding agent session histories, and local codebases into a layered knowledge graph "
            "with a central Core Plan, Architectural Modules, Sub-Components, and source-grouped project regions."
        ),
        "stack": "Python 3 • PySide6 (QGraphicsScene/QGraphicsView) • AST Code Analysis • Live CLI/Skill Bridge",
        "highlights": (
            "• Central Project Overview & Core Plan pinned in the right inspector panel\n"
            "• Dedicated regions for Antigravity, OpenAI Codex, Claude Code, and Codebase projects\n"
            "• Directed Dependency Links, Fuzzy Search, and Guided Architecture Tour"
        ),
        "curated_planets": {
            "antigravity": [
                (
                    "Architecture & Concept Design",
                    "architecture",
                    (
                        "MODULE PURPOSE & ROLE:\n"
                        "Three-tier hierarchical knowledge graph architecture designed to keep a project's "
                        "primary objective and architectural rules visible across long AI coding sessions.\n\n"
                        "HOW IT WORKS & TECHNICAL FLOW:\n"
                        "• The 'Project Core (Sun)' sits at the center holding the project purpose and rules.\n"
                        "• Architectural layers (UI, Core, Integration, Security/Testing) orbit the center as module nodes.\n"
                        "• Concrete source files and features appear along each module's outer arc as sub-components.\n"
                        "• All data is persisted to `data/galaxy.json` via `GalaxyStore` in `core/models.py`."
                    ),
                    [
                        (
                            "Core Plan, Module & Component Hierarchy",
                            (
                                "WHAT IT DOES:\n"
                                "Separates project knowledge into 3 levels (Project Core → Architectural Module → Sub-Component) "
                                "so complex systems can be understood at a glance.\n\n"
                                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                                "• `SolarSystem` stores the project identity, workspace path, and `core_plan`.\n"
                                "• `PlanetNode` represents architectural layers (`architecture`, `ui`, `core`, `integration`, `testing`) arranged automatically via `rebalance_layout()`.\n"
                                "• `SatelliteNode` holds concrete source files, functions, and technical decisions under each layer.\n\n"
                                "RELATED FILE: `core/models.py`"
                            ),
                            "diagram",
                        ),
                        (
                            "Source-Based Region Grouping",
                            (
                                "WHAT IT DOES:\n"
                                "Groups projects developed across different AI tools (Antigravity, OpenAI Codex, Claude Code) "
                                "and local codebases under their own application headers without mixing them.\n\n"
                                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                                "• `SOURCE_CATEGORIES` defines the title, accent color, and display order for each source.\n"
                                "• In All Projects view, `recalculate_galaxy_sectors()` arranges each source's projects into non-overlapping dashboard grids.\n"
                                "• Counterparts of the same project across sources are linked via `find_related_systems()`.\n\n"
                                "RELATED FILE: `core/models.py`, `ui/space_canvas.py`"
                            ),
                            "idea",
                        ),
                    ],
                ),
                (
                    "Interactive Canvas & UI",
                    "ui",
                    (
                        "MODULE PURPOSE & ROLE:\n"
                        "PySide6 desktop interface layer where users inspect projects and architectural layers, "
                        "zoom and pan, and drag-and-drop nodes.\n\n"
                        "HOW IT WORKS & TECHNICAL FLOW:\n"
                        "• Custom graphics items (`SunGraphicsItem`, `PlanetGraphicsItem`, `SatelliteGraphicsItem`, `GalaxyClusterItem`) render on `SpaceCanvasView` (`QGraphicsView` / `QGraphicsScene`).\n"
                        "• Features a source-grouped project tree on the left, an interactive canvas with breadcrumb navigation in the center, and a pinned Project Core card + detail editor on the right.\n"
                        "• Supports drag-and-drop positioning, context menus, fuzzy search, and a step-by-step Architecture Tour."
                    ),
                    [
                        (
                            "Interactive Architecture Canvas (space_canvas.py)",
                            (
                                "WHAT IT DOES:\n"
                                "Graphics engine that renders projects both as summary cards in the 'All Projects' dashboard "
                                "and as core-module-component diagrams in 'Project View'.\n\n"
                                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                                "• `GalaxyClusterItem`: Rich project card in All Projects view showing the project name, purpose, layer tags, and component counts.\n"
                                "• `PlanetGraphicsItem` & `SatelliteGraphicsItem`: Draggable via `mouseMoveEvent` with automatic persistence.\n"
                                "• `ConstellationLinkItem`: Renders curved directed arrows indicating data flow and dependency direction.\n\n"
                                "RELATED FILE: `ui/space_canvas.py`"
                            ),
                            "code",
                        ),
                        (
                            "Guided Architecture Tour & Fuzzy Search",
                            (
                                "WHAT IT DOES:\n"
                                "Step-by-step 'Architecture Tour' showing which layer to explore first, paired with "
                                "instant search filtering that highlights matching nodes.\n\n"
                                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                                "• `start_or_advance_tour()`: Steps through Core Plan → Architecture → Core → Integration → UI → Testing and centers the camera on each module.\n"
                                "• `set_search_query()`: Searches all module and sub-component contents from the top bar, dimming non-matching nodes (`is_dimmed`).\n\n"
                                "RELATED FILE: `ui/space_canvas.py`, `ui/main_window.py`"
                            ),
                            "diagram",
                        ),
                    ],
                ),
                (
                    "AI Agent & Codebase Bridges",
                    "integration",
                    (
                        "MODULE PURPOSE & ROLE:\n"
                        "Data integration layer connecting local AI agent session logs (Antigravity, OpenAI Codex, Claude Code), "
                        "local project folders, and local Ollama LLM models to Helios.\n\n"
                        "HOW IT WORKS & TECHNICAL FLOW:\n"
                        "• `AgentHistoryImporter` scans agent logs, filters out noisy/trivial exchanges, and merges sessions belonging to the same project.\n"
                        "• `CodebaseImporter` analyzes any project folder using Python AST and structural regex to build file/class/function architecture maps.\n"
                        "• `OllamaEnhancer` optionally uses a local Ollama model to verify and enrich project summaries."
                    ),
                    [
                        (
                            "Agent History Scanner (agent_importer.py)",
                            (
                                "WHAT IT DOES:\n"
                                "Scans Antigravity (`~/.gemini/antigravity/brain`), OpenAI Codex (`~/.codex/sessions`), and Claude Code "
                                "histories to produce noise-free project maps.\n\n"
                                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                                "• `_is_noise_or_trivial_message()` filters out operational commands, raw stack traces, and internal system logs.\n"
                                "• `_infer_project_name()` and `_inspect_workspace_folder()` inspect real workspace files (`package.json`, `requirements.txt`, `README.md`) to infer the project purpose.\n"
                                "• `_merge_or_populate_system()` preserves user modifications (`is_user_modified`) during resyncs.\n\n"
                                "RELATED FILE: `connectors/agent_importer.py`"
                            ),
                            "code",
                        ),
                        (
                            "AST Code & .ua Analyzer (codebase_importer.py)",
                            (
                                "WHAT IT DOES:\n"
                                "Automatically converts any local code directory or Understand-Anything (`.ua`) knowledge graph "
                                "into architectural layers and file components.\n\n"
                                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                                "• Scans `.py`, `.ts`, `.tsx`, `.js`, `.go`, `.rs`, `.cs`, and `.md` files and classifies them into layers via `_classify_file_layer()`.\n"
                                "• Parses Python files with the `ast` module to extract module docstrings, classes, methods, function signatures, and cross-layer `import` dependencies.\n\n"
                                "RELATED FILE: `connectors/codebase_importer.py`"
                            ),
                            "code",
                        ),
                        (
                            "Ollama Verification & Live CLI Bridge",
                            (
                                "WHAT IT DOES:\n"
                                "Verifies project summaries with a local Ollama model and allows external AI agents "
                                "to update the Helios map live while working.\n\n"
                                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                                "• `connectors/ollama_enhancer.py`: Starts the local Ollama service in the background if needed and refines the core summary.\n"
                                "• `connectors/live_bridge.py`: Updates `galaxy.json` live from the CLI; the UI watches the file via `QFileSystemWatcher` and refreshes immediately.\n\n"
                                "RELATED FILE: `connectors/ollama_enhancer.py`, `connectors/live_bridge.py`"
                            ),
                            "code",
                        ),
                    ],
                ),
            ]
        },
    },
}


def _clean_user_request(raw_text: str) -> str:
    """Extracts the clean user message inside <USER_REQUEST> tags and strips system blocks."""
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw_text, re.DOTALL)
    if match:
        return match.group(1).strip()

    cleaned = re.sub(r"<environment_context>.*?</environment_context>", "", raw_text, flags=re.DOTALL)
    cleaned = re.sub(r"<permissions[^>]*>.*?</permissions>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<app-context>.*?</app-context>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<collaboration_mode>.*?</collaboration_mode>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<turn_aborted>.*?</turn_aborted>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<in-app-browser-context[^>]*>.*?</in-app-browser-context>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<in-app-browser-context[^>]*>", "", cleaned)
    cleaned = re.sub(r"<send_user_message_question_reply>.*?</send_user_message_question_reply>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<send_user_message_question_reply>", "", cleaned)
    return cleaned.strip()


def _is_noise_or_trivial_message(user_msg: str, ai_msg: str = "") -> bool:
    """Filters out empty messages, trivial operational commands, stack traces, and tokens."""
    u = user_msg.strip()
    if not u:
        return True

    low = _tr_lower(u)

    if any(
        noise in low
        for noise in (
            "the following is the codex agent history",
            "treat the transcript, tool call arguments",
            ">>> transcript start",
            "<in-app-browser-context",
            "<send_user_message_question_reply",
            "# files pasted by the user",
            "windows powershell",
            "copyright (c) microsoft",
            "traceback (most recent call last)",
            "fatal error:",
            "parse error:",
            "warning: include(",
            "warning: require_once(",
            "warning: undefined array key",
            "[warning: undefined",
            "maxlistenersexceededwarning",
            "contentscript.js:",
            "secure ssl/tls settings",
            "@[c:\\users",
            "my request:",
        )
    ):
        return True

    if re.search(r"[A-Za-z0-9_-]{22,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}", u):
        return True

    words = re.findall(r"[a-z0-9]+", low)
    if len(words) <= 3 and len(u) < 32:
        return True

    if re.match(
        r"^(?:run\s+code|start\s+app|start\s+server|continue|go\s+ahead|fix\s+it|looks\s+good)",
        low,
    ):
        return True

    return False


def _extract_clean_feature_title(user_msg: str, ai_msg: str) -> Optional[str]:
    """Extracts a clean sub-component title from meaningful architectural headings in the AI response."""
    skip_headings = {
        "summary",
        "conclusion",
        "question",
        "hello",
        "step by step",
        "what changed",
        "what was added",
        "installation",
        "current features",
        "pros (+)",
        "cons (-)",
        "overview",
        "test checklist",
    }

    if ai_msg:
        headings = re.findall(r"^#{1,3}\s+([^\n#]+)$", ai_msg, flags=re.MULTILINE)
        for h in headings:
            clean_h = re.sub(r"[*_`~]+", "", h).strip(" :-–—•")
            clean_h = re.sub(r"^\d+[\.\)]\s*", "", clean_h).strip()
            low_h = _tr_lower(clean_h)
            if 8 <= len(clean_h) <= 44 and not any(s in low_h for s in skip_headings):
                return clean_h

        bolds = re.findall(r"(?:^|\n)\s*(?:[-•*]|\d+\.)\s*\*\*([^*\n]{8,42})\*\*", ai_msg)
        for b in bolds:
            clean_b = b.strip(" :-–—")
            low_b = _tr_lower(clean_b)
            if 8 <= len(clean_b) <= 42 and not any(
                low_b.startswith(p) for p in ("yes", "no", "note", "warning", "great", "sure")
            ) and not any(s in low_b for s in skip_headings):
                return clean_b

    return None


def _normalize_project_folder_name(raw_folder: str, context_text: str = "") -> Optional[str]:
    """Normalizes a folder name into a clean project name."""
    cleaned = raw_folder.strip().strip("\\/\"'.,;:)(")
    if not cleaned or len(cleaned) <= 1:
        return None
    if not re.match(r"^[A-Za-z0-9_.\-]{2,36}$", cleaned):
        return None
    low = _tr_lower(cleaned)
    if low in GENERIC_FOLDER_NAMES or low.startswith((".", "-")):
        return None
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", low):
        return None
    if re.match(r"^\d{4}(-\d{2})?(-\d{2})?$", low):
        return None
    if re.search(r"\.(py|md|txt|json|jsonl|bat|ps1|sh|lnk|png|ico|jpg|zip|rar|sqlite|db|html|css|js|ts|exe)$", low):
        return None

    if "helios" in low:
        return "Helios"

    return cleaned


def _infer_project_name(
    paths: List[str],
    exchanges: List[Tuple[str, str, List[str]]],
    fallback_hint: str = "",
) -> Tuple[str, str]:
    """Infers the real project name and workspace path from file paths and session content."""
    all_text = " ".join(u + " " + a for u, a, _ in exchanges[:8]) + " " + fallback_hint
    folder_counter: Counter[str] = Counter()
    best_workspace_path = ""

    patterns = [
        r"[A-Za-z]:\\Users\\[^\\]+\\(?:Desktop|Documents|Projects|source|repos)(?:\\Codex\\\d{4}-\d{2}-\d{2})?(?:\\ChatGPT)?\\([A-Za-z0-9_.\-]+)",
        r"/Users/[^/]+/(?:Desktop|Documents|Projects|repos)/([A-Za-z0-9_.\-]+)",
        r"/home/[^/]+/(?:Desktop|Documents|Projects|repos)/([A-Za-z0-9_.\-]+)",
        r"[A-Za-z]:\\xampp\\htdocs\\([A-Za-z0-9_.\-]+)",
    ]
    for raw_p in list(paths) + [all_text]:
        norm_p = raw_p.replace("\\\\", "\\").replace("/", "\\")
        for pat in patterns:
            for m in re.finditer(pat, norm_p, flags=re.IGNORECASE):
                candidate = m.group(1)
                proj = _normalize_project_folder_name(candidate, all_text)
                if proj:
                    folder_counter[proj] += 3
                    if not best_workspace_path:
                        best_workspace_path = m.group(0)

    if folder_counter:
        top_project = folder_counter.most_common(1)[0][0]
        return top_project, best_workspace_path

    low_text = _tr_lower(all_text)
    if "helios" in low_text:
        return "Helios", ""

    gh_match = re.search(r"github\.com/[A-Za-z0-9_.-]+/([A-Za-z0-9_.-]+)", all_text)
    if gh_match:
        return gh_match.group(1), ""

    return "", ""


def _inspect_workspace_folder(workspace_path: str) -> Tuple[str, str, List[str]]:
    """Inspects the workspace directory (README, package.json, requirements.txt, source files)
    to extract the project purpose, tech stack, and key files.
    """
    if not workspace_path:
        return "", "", []
    ws = Path(workspace_path)
    if not ws.exists():
        return "", "", []
    if ws.is_file():
        ws = ws.parent
    if not ws.is_dir():
        return "", "", []

    purpose = ""
    techs: List[str] = []
    key_files: List[str] = []

    for doc_name in ("AGENTS.md", "README.md", "CLAUDE.md"):
        doc_p = ws / doc_name
        if doc_p.exists() and doc_p.is_file():
            try:
                raw_doc = doc_p.read_text(encoding="utf-8", errors="replace")
                paras = [
                    ln.strip()
                    for ln in raw_doc.splitlines()
                    if ln.strip() and not ln.strip().startswith(("#", "<!--", "```", "---", "|"))
                ]
                if paras:
                    purpose = " ".join(paras[:3])[:450]
                    break
            except Exception:
                pass

    pkg_json = ws / "package.json"
    if pkg_json.exists() and pkg_json.is_file():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            if not purpose and pkg.get("description"):
                purpose = str(pkg["description"]).strip()
            deps = list((pkg.get("dependencies") or {}).keys()) + list(
                (pkg.get("devDependencies") or {}).keys()
            )
            for d_name, label in [
                ("next", "Next.js"),
                ("react", "React"),
                ("typescript", "TypeScript"),
                ("tailwindcss", "Tailwind CSS"),
                ("@prisma/client", "Prisma"),
                ("zustand", "Zustand"),
                ("discord.js", "Discord.js"),
                ("express", "Express"),
                ("vite", "Vite"),
            ]:
                if d_name in deps and label not in techs:
                    techs.append(label)
        except Exception:
            pass

    req_txt = ws / "requirements.txt"
    if req_txt.exists() and req_txt.is_file():
        try:
            req_raw = req_txt.read_text(encoding="utf-8", errors="replace").lower()
            for r_key, label in [
                ("pyside6", "PySide6"),
                ("fastapi", "FastAPI"),
                ("flask", "Flask"),
                ("django", "Django"),
                ("faster-whisper", "Faster-Whisper"),
                ("edge-tts", "Edge-TTS"),
                ("openpyxl", "OpenPyXL"),
                ("pandas", "Pandas"),
                ("requests", "Requests"),
                ("onnxruntime", "ONNX Runtime"),
            ]:
                if r_key in req_raw and label not in techs:
                    techs.append(label)
        except Exception:
            pass

    ext_tech_map = {
        ".py": "Python",
        ".ts": "TypeScript",
        ".tsx": "React / TSX",
        ".js": "Node.js / JS",
        ".mjs": "Node.js (ESM)",
        ".php": "PHP",
        ".sql": "SQL",
    }
    try:
        for item in ws.iterdir():
            if item.is_file() and not item.name.startswith("."):
                ext = item.suffix.lower()
                if ext in ext_tech_map:
                    t_label = ext_tech_map[ext]
                    if t_label not in techs:
                        techs.append(t_label)
                    if len(key_files) < 8:
                        key_files.append(item.name)
    except Exception:
        pass

    return purpose, " • ".join(techs[:7]), key_files


def _build_project_core_plan(
    project_name: str,
    app_label: str,
    workspace_path: str,
    artifacts: List[Path],
    exchanges: List[Tuple[str, str, List[str]]],
) -> str:
    """Builds a clean Core Plan for the central node describing the project's purpose and architecture."""
    blueprint = PROJECT_BLUEPRINTS.get(project_name)

    purpose_text = ""
    stack_text = ""
    highlights_text = ""
    ws_purpose, ws_stack, ws_files = _inspect_workspace_folder(workspace_path)

    if blueprint:
        purpose_text = str(blueprint.get("purpose", ""))
        stack_text = str(blueprint.get("stack", "")) or ws_stack
        highlights_text = str(blueprint.get("highlights", ""))
    else:
        purpose_text = ws_purpose
        stack_text = ws_stack

        if not purpose_text and artifacts:
            for art in artifacts:
                try:
                    raw_art = art.read_text(encoding="utf-8", errors="replace")
                    paras = [
                        ln.strip()
                        for ln in raw_art.splitlines()
                        if ln.strip() and not ln.strip().startswith(("#", "- [", "`"))
                    ]
                    if paras:
                        purpose_text = " ".join(paras[:2])[:420]
                        break
                except Exception:
                    pass

        extracted_goals: List[str] = []
        for u_m, a_m, _ in exchanges:
            if _is_noise_or_trivial_message(u_m, a_m):
                continue
            feat = _extract_clean_feature_title(u_m, a_m)
            if feat and feat not in extracted_goals:
                extracted_goals.append(f"• {feat}")
            if not purpose_text:
                clean_u = u_m.strip().splitlines()[0][:240]
                if len(clean_u) > 20:
                    purpose_text = f"Architectural and development work for {project_name}: {clean_u}"

        if extracted_goals and not highlights_text:
            highlights_text = "\n".join(extracted_goals[:5])

    parts = [
        f"PROJECT PURPOSE & OVERVIEW (WHAT IT DOES):\n{purpose_text or f'{project_name} software project and architectural components.'}",
    ]
    if highlights_text:
        parts.append(f"\nCORE ARCHITECTURE & FEATURES:\n{highlights_text}")
    if stack_text:
        parts.append(f"\nTECH STACK:\n{stack_text}")
    if ws_files:
        parts.append(f"\nKEY FILES: {', '.join(ws_files[:6])}")

    meta_lines = [f"\nAPPLICATION SOURCE: {app_label}"]
    if workspace_path:
        meta_lines.append(f"WORKSPACE PATH: {workspace_path}")
    parts.append("\n".join(meta_lines))

    return "\n".join(parts)


def _merge_or_populate_system(
    existing: SolarSystem,
    new_core_plan: str,
    workspace_path: str,
    exchanges: List[Tuple[str, str, List[str]]],
    artifacts: Optional[List[Path]] = None,
) -> None:
    """Updates a project while preserving manual user edits and Ollama verification blocks."""
    if not existing.is_user_modified:
        ollama_tail = ""
        marker = "[OLLAMA VERIFIED ARCHITECTURE ANALYSIS"
        if marker in existing.core_plan:
            idx = existing.core_plan.find(marker)
            ollama_tail = "\n\n" + existing.core_plan[idx:]
        existing.core_plan = new_core_plan + ollama_tail

    if workspace_path and not existing.workspace_path:
        existing.workspace_path = workspace_path

    user_planets = [p for p in existing.planets if p.is_user_modified]
    user_sats_by_layer: Dict[str, List] = {}
    for p in existing.planets:
        mod_sats = [s for s in p.satellites if s.is_user_modified]
        if mod_sats:
            user_sats_by_layer.setdefault(p.layer, []).extend(mod_sats)

    captured_links = existing.capture_user_links()

    existing.planets = []
    existing.links = []
    _populate_clean_semantic_planets(existing, exchanges, artifacts)

    existing_titles = {_tr_lower(p.title): p for p in existing.planets}
    for up in user_planets:
        key = _tr_lower(up.title)
        if key in existing_titles:
            target_p = existing_titles[key]
            target_p.summary = up.summary
            target_p.color = up.color
            target_p.angle = up.angle
            target_p.orbit_radius = up.orbit_radius
            target_p.is_user_modified = True
        else:
            existing.planets.append(up)

    for layer_key, sat_list in user_sats_by_layer.items():
        target_p = next((p for p in existing.planets if p.layer == layer_key), None)
        if target_p:
            sat_titles = {_tr_lower(s.title) for s in target_p.satellites}
            for us in sat_list:
                if _tr_lower(us.title) not in sat_titles:
                    target_p.satellites.append(us)

    existing.restore_user_links(captured_links)
    existing.rebuild_links_and_tour()


def _classify_exchange_layer(title: str, user_msg: str, ai_msg: str, tools: List[str]) -> Tuple[str, str]:
    """Maps a feature or step to an architectural layer and sub-component visual tag."""
    combined = _tr_lower(f"{title}\n{user_msg[:300]}\n{ai_msg[:400]}\n{' '.join(tools)}")

    if any(k in combined for k in ("ui", "canvas", "window", "pyside", "css", "html", "color", "design", "mobile", "frontend", "react", "navbar", "page", "theme", "tui", "terminal", "view", "widget")):
        return "ui", "diagram"
    if any(k in combined for k in ("security", "audit", "tls", "cors", "zap", "rate", "limit", "test", "validation", "captcha", "allowlist", "auth", "permission")):
        return "testing", "code"
    if any(k in combined for k in ("search_web", "research", "github.com", "inspect", "compare", "analyze", "server", "ubuntu", "benchmark")):
        return "research", "thought"
    if any(k in combined for k in ("connector", "bridge", "mcp", "api", "importer", "skill", "plugin", "bot", "discord", "telegram", "whisper", "ollama", "webhook")):
        return "integration", "code"
    if any(k in combined for k in ("model", "database", "sqlite", "mysql", "prisma", "json", "store", "core", "class", "algorithm", "score", "chapter", "excel", "report", "schema")):
        return "core", "code"
    return "architecture", "idea"


def _populate_clean_semantic_planets(
    system: SolarSystem,
    exchanges: List[Tuple[str, str, List[str]]],
    artifacts: Optional[List[Path]] = None,
) -> None:
    """Builds the project's architectural layers and sub-components without conversational noise."""
    blueprint = PROJECT_BLUEPRINTS.get(system.project_name)
    curated_map = blueprint.get("curated_planets", {}) if blueprint else {}
    curated_list = curated_map.get(system.source) if isinstance(curated_map, dict) else None

    if curated_list:
        for p_title, p_layer, p_summary, sat_list in curated_list:
            _, color = LAYER_DEFINITIONS.get(p_layer, ("Module", "#4EA8DE"))
            planet = system.add_planet(
                title=p_title,
                summary=p_summary,
                color=color,
                layer=p_layer,
            )
            for s_title, s_content, s_tag in sat_list:
                planet.add_satellite(
                    title=s_title,
                    content=s_content,
                    visual_tag=s_tag,
                )
        system.rebuild_links_and_tour()
        return

    planet_by_layer: Dict[str, PlanetNode] = {p.layer: p for p in system.planets}
    seen_titles: Set[str] = set()

    layer_role_descriptions = {
        "architecture": "Manages the overall system architecture, configuration files, design rules, and roadmap.",
        "core": "Contains domain data models, database schemas, core algorithms, and primary business services.",
        "integration": "Manages external APIs, AI models, bots, data fetchers, and inter-system communication bridges.",
        "ui": "Provides user-facing screens, visual components, theme styling, and interactive workflows.",
        "testing": "Handles security checks, permission validation, error handling, and automated test suites.",
        "docs": "Contains technical design documents, architectural plans, task lists, and user guides.",
        "research": "Houses technical feasibility studies, library comparisons, and exploratory analyses.",
    }

    def get_or_create_layer_planet(layer_key: str) -> PlanetNode:
        if layer_key in planet_by_layer:
            return planet_by_layer[layer_key]
        title, color = LAYER_DEFINITIONS.get(layer_key, ("Project Module", "#4EA8DE"))
        role_desc = layer_role_descriptions.get(
            layer_key,
            "Contains the core components and technical decisions for this architectural layer.",
        )
        p = system.add_planet(
            title=title,
            summary=(
                f"MODULE PURPOSE & ROLE:\n"
                f"In {system.project_name}, {role_desc}\n\n"
                f"HOW IT WORKS & TECHNICAL FLOW:\n"
                f"• Sub-components under this module fulfill the '{title}' responsibilities of the project."
            ),
            color=color,
            layer=layer_key,
        )
        planet_by_layer[layer_key] = p
        return p

    if artifacts:
        for art in artifacts[:6]:
            try:
                art_text = art.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                continue
            if not art_text:
                continue
            h1_match = re.search(r"^#\s+(.+)$", art_text, flags=re.MULTILINE)
            art_title = h1_match.group(1).strip() if h1_match else art.stem.replace("_", " ").title()
            art_title = re.sub(r"[*_`#]+", "", art_title).strip()
            if len(art_title) > 40:
                art_title = art_title[:39].rstrip() + "…"
            norm_key = _tr_lower(art_title)
            if norm_key in seen_titles:
                continue
            seen_titles.add(norm_key)

            doc_planet = get_or_create_layer_planet("docs")
            doc_planet.add_satellite(
                title=art_title,
                content=(
                    f"WHAT IT DOES:\n"
                    f"Contains the technical plan, architectural design, and implementation steps defined in `{art.name}`.\n\n"
                    f"HOW IT WORKS & TECHNICAL DETAILS:\n"
                    f"{art_text[:950]}\n\n"
                    f"RELATED FILE: `{art}`"
                ),
                visual_tag="diagram",
                file_path=str(art),
            )

    for u_msg, ai_msg, tools in exchanges:
        if _is_noise_or_trivial_message(u_msg, ai_msg):
            continue
        if len(ai_msg.strip()) < 80:
            continue

        feat_title = _extract_clean_feature_title(u_msg, ai_msg)
        if not feat_title:
            continue

        norm_key = _tr_lower(feat_title)
        if norm_key in seen_titles:
            continue

        layer_key, v_tag = _classify_exchange_layer(feat_title, u_msg, ai_msg, tools)
        planet = get_or_create_layer_planet(layer_key)
        if len(planet.satellites) >= 6:
            continue

        seen_titles.add(norm_key)
        tool_line = f"\n\nTOOLS / OPERATIONS USED: {', '.join(tools[:5])}" if tools else ""
        planet.add_satellite(
            title=feat_title,
            content=(
                f"WHAT IT DOES:\n"
                f"{u_msg.strip()[:380]}\n\n"
                f"HOW IT WORKS & TECHNICAL DETAILS:\n"
                f"{ai_msg.strip()[:900]}"
                f"{tool_line}"
            ),
            visual_tag=v_tag,
        )

    for l_key, p_node in planet_by_layer.items():
        if p_node.satellites:
            role_desc = layer_role_descriptions.get(l_key, "")
            sat_bullets = "\n".join(f"• {s.title}" for s in p_node.satellites)
            p_node.summary = (
                f"MODULE PURPOSE & ROLE:\n"
                f"In {system.project_name}, {role_desc}\n\n"
                f"SUB-COMPONENTS IN THIS MODULE ({len(p_node.satellites)}):\n"
                f"{sat_bullets}\n\n"
                f"HOW IT WORKS & TECHNICAL FLOW:\n"
                f"• Click any sub-component under this module to inspect its purpose and technical implementation details."
            )

    system.rebuild_links_and_tour()


class AgentDetector:
    """Detects AI coding tools installed on the user's machine (Antigravity, Codex, Claude Code, Ollama)."""

    @staticmethod
    def detect_all() -> Dict[str, Dict[str, object]]:
        home = Path.home()
        results: Dict[str, Dict[str, object]] = {}

        # 1. Antigravity
        ag_path = home / ".gemini" / "antigravity" / "brain"
        ag_count = 0
        if ag_path.exists():
            try:
                ag_count = sum(
                    1
                    for d in ag_path.iterdir()
                    if d.is_dir()
                    and (d / ".system_generated" / "logs" / "transcript.jsonl").exists()
                )
            except Exception:
                ag_count = 0
        results["antigravity"] = {
            "name": "Google Antigravity",
            "brand": "Agentic Coding",
            "icon": "AG",
            "detected": ag_path.exists() and ag_count > 0,
            "count": ag_count,
            "status": f"{ag_count} sessions found" if ag_count > 0 else "No sessions found",
            "color": "#3B82F6",
            "path": "~/.gemini/antigravity/brain",
            "description": "Imports workspace history, architectural plans, and code decisions into the graph.",
            "tags": [],
        }

        # 2. OpenAI Codex
        codex_path = home / ".codex"
        codex_count = 0
        if codex_path.exists():
            try:
                codex_count = sum(1 for f in codex_path.glob("*") if f.is_file() or f.is_dir())
            except Exception:
                codex_count = 0
        results["codex"] = {
            "name": "OpenAI Codex",
            "brand": "CLI & Code Agent",
            "icon": "CX",
            "detected": codex_path.exists() and codex_count > 0,
            "count": codex_count,
            "status": f"{codex_count} records found" if codex_count > 0 else "Not detected",
            "color": "#10B981",
            "path": "~/.codex",
            "description": "Converts Codex CLI sessions and module history into structured projects.",
            "tags": [],
        }

        # 3. Claude Code
        claude_path = home / ".claude"
        claude_cli = shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")
        p_proj = (claude_path / "projects") if claude_path.exists() else None
        claude_count = 0
        if p_proj and p_proj.exists():
            try:
                claude_count = sum(1 for d in p_proj.iterdir() if d.is_dir())
            except Exception:
                claude_count = 0
        claude_detected = claude_path.exists() or bool(claude_cli)
        results["claude_code"] = {
            "name": "Claude Code",
            "brand": "Anthropic CLI",
            "icon": "CC",
            "detected": claude_detected,
            "count": claude_count,
            "status": f"{claude_count} projects found" if claude_detected else "Not installed",
            "color": "#D97706",
            "path": "~/.claude",
            "description": "Synchronizes Claude Code projects and CLAUDE.md architecture definitions.",
            "tags": [],
        }

        # 4. Ollama Local LLM
        ollama_cli = shutil.which("ollama") or (home / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe")
        ollama_exists = Path(str(ollama_cli)).exists() if ollama_cli else False
        ollama_models = []
        models_dir = home / ".ollama" / "models" / "manifests"
        if models_dir.exists():
            try:
                for f in models_dir.rglob("*"):
                    if f.is_file():
                        ollama_models.append(f"{f.parent.name}:{f.name}")
            except Exception:
                pass

        import urllib.request
        ollama_running = False
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=0.6) as resp:
                ollama_running = resp.status == 200
        except Exception:
            ollama_running = False

        results["ollama"] = {
            "name": "Ollama",
            "brand": "Local Language Model",
            "icon": "OL",
            "detected": ollama_exists or bool(ollama_models) or ollama_running,
            "count": len(ollama_models),
            "running": ollama_running,
            "models": ollama_models,
            "status": (
                f"{len(ollama_models)} models installed"
                if ollama_models
                else ("Service ready" if ollama_exists else "Not installed")
            ),
            "color": "#64748B",
            "path": "127.0.0.1:11434",
            "description": "Enriches architectural summaries offline using a local LLM (optional).",
            "tags": [],
        }

        # 5. Codebase Analysis
        results["codebase"] = {
            "name": "Local Code Analysis (AST)",
            "brand": "Built-in Engine",
            "icon": "AST",
            "detected": True,
            "count": 1,
            "status": "Always active",
            "color": "#0EA5E9",
            "path": "Local directories",
            "description": "Analyzes project folders directly from source code structure and imports.",
            "tags": [],
        }

        return results


class AgentHistoryImporter:
    """Scans local AI agent histories (Antigravity, Codex, Claude Code) and groups them by project."""

    @staticmethod
    def scan_all_into_store(store: GalaxyStore, max_sessions: int = 30) -> int:
        """Scans all supported local AI tools and adds projects under their respective source categories."""
        added_count = 0
        added_count += AgentHistoryImporter.scan_antigravity(store, max_sessions=max_sessions)
        added_count += AgentHistoryImporter.scan_codex(store, max_sessions=60)
        added_count += AgentHistoryImporter.scan_claude_code(store, max_sessions=max_sessions)
        store.save()
        return added_count

    @staticmethod
    def scan_selected_sources(
        store: GalaxyStore,
        sources: List[str],
        max_sessions: int = 25,
    ) -> int:
        """Scans only the AI sources selected by the user."""
        added_count = 0
        if "antigravity" in sources:
            added_count += AgentHistoryImporter.scan_antigravity(store, max_sessions=max_sessions)
        if "codex" in sources:
            added_count += AgentHistoryImporter.scan_codex(store, max_sessions=max_sessions)
        if "claude_code" in sources:
            added_count += AgentHistoryImporter.scan_claude_code(store, max_sessions=max_sessions)
        store.save()
        return added_count

    @staticmethod
    def scan_antigravity(store: GalaxyStore, max_sessions: int = 25) -> int:
        """Scans local Antigravity history (~/.gemini/antigravity/brain) and merges sessions by project name."""
        brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
        if not brain_dir.exists():
            return 0

        session_dirs = [d for d in brain_dir.iterdir() if d.is_dir()]
        session_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

        grouped: Dict[str, List[Tuple[str, Path, List[Tuple[str, str, List[str]]], List[Path], str]]] = {}

        for s_dir in session_dirs[:max_sessions]:
            session_id = s_dir.name
            transcript_path = s_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if not transcript_path.exists():
                continue

            exchanges, paths, artifacts = AgentHistoryImporter._extract_antigravity_data(
                s_dir, transcript_path
            )
            if not exchanges and not artifacts:
                continue

            project_name, workspace_path = _infer_project_name(paths, exchanges)
            if not project_name:
                continue
            grouped.setdefault(project_name, []).append(
                (session_id, s_dir, exchanges, artifacts, workspace_path)
            )

        updated_projects = 0
        for project_name, sess_list in grouped.items():
            sess_chronological = list(reversed(sess_list))
            all_exchanges: List[Tuple[str, str, List[str]]] = []
            all_artifacts: List[Path] = []
            workspace_path = ""

            for _, _, ex_list, arts, ws_path in sess_chronological:
                all_exchanges.extend(ex_list)
                all_artifacts.extend(arts)
                if ws_path and not workspace_path:
                    workspace_path = ws_path

            core_plan = _build_project_core_plan(
                project_name=project_name,
                app_label="Antigravity",
                workspace_path=workspace_path,
                artifacts=all_artifacts,
                exchanges=all_exchanges,
            )

            existing = store.get_by_project_name("antigravity", project_name)
            if existing:
                existing.name = project_name
                existing.project_name = project_name
                existing.sun_title = project_name
                _merge_or_populate_system(
                    existing, core_plan, workspace_path, all_exchanges, all_artifacts
                )
            else:
                system = SolarSystem(
                    name=project_name,
                    project_name=project_name,
                    sun_title=project_name,
                    core_plan=core_plan,
                    source="antigravity",
                    source_id=f"antigravity:{_tr_lower(project_name)}",
                    workspace_path=workspace_path,
                )
                _populate_clean_semantic_planets(system, all_exchanges, all_artifacts)
                store.add_system(system)

            updated_projects += 1

        return updated_projects

    @staticmethod
    def _extract_antigravity_data(
        session_dir: Path, transcript_path: Path
    ) -> Tuple[List[Tuple[str, str, List[str]]], List[str], List[Path]]:
        try:
            raw_content = transcript_path.read_text(encoding="utf-8", errors="replace")
            lines = raw_content.splitlines()
        except Exception:
            return [], [], []

        exchanges: List[Tuple[str, str, List[str]]] = []
        paths: List[str] = []
        current_user = ""
        current_ai = ""
        current_tools: List[str] = []

        for line in lines:
            if not line.strip():
                continue
            try:
                step = json.loads(line)
            except Exception:
                continue

            step_type = step.get("type", "")
            content = step.get("content", "") or ""

            if step_type == "USER_INPUT":
                cleaned = _clean_user_request(content)
                if cleaned:
                    if current_user:
                        exchanges.append((current_user, current_ai, current_tools))
                    current_user = cleaned
                    current_ai = ""
                    current_tools = []

            elif step_type == "PLANNER_RESPONSE":
                if content.strip():
                    current_ai = content.strip()
                for tc in step.get("tool_calls", []) or []:
                    t_name = tc.get("name", "tool")
                    args = tc.get("args", {}) or {}
                    for k in ("Path", "Directory", "CommandLine", "SearchPath"):
                        val = str(args.get(k, "")).strip("\"")
                        if val:
                            paths.append(val)
                    summary = (
                        str(args.get("toolSummary", "")).strip("\"")
                        or str(args.get("Path", "")).strip("\"")
                        or str(args.get("query", "")).strip("\"")
                    )
                    current_tools.append(f"{t_name}: {summary}".strip(": "))

        if current_user:
            exchanges.append((current_user, current_ai, current_tools))

        artifacts = [
            f for f in session_dir.glob("*.md") if f.is_file() and not f.name.startswith(".")
        ]
        return exchanges, paths, artifacts

    @staticmethod
    def scan_codex(store: GalaxyStore, max_sessions: int = 60) -> int:
        """Scans local OpenAI Codex sessions (~/.codex/sessions and archived_sessions)."""
        codex_dir = Path.home() / ".codex"
        if not codex_dir.exists():
            return 0

        thread_names: Dict[str, str] = {}
        index_path = codex_dir / "session_index.jsonl"
        if index_path.exists():
            try:
                for ln in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not ln.strip():
                        continue
                    item = json.loads(ln)
                    sid = str(item.get("id", ""))
                    tname = str(item.get("thread_name", "")).strip()
                    if sid and tname:
                        thread_names[sid] = tname
            except Exception:
                pass

        rollout_files: List[Path] = []
        for subdir_name in ("sessions", "archived_sessions"):
            s_dir = codex_dir / subdir_name
            if s_dir.exists():
                rollout_files.extend(s_dir.rglob("*.jsonl"))

        rollout_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        grouped: Dict[str, List[Tuple[str, List[Tuple[str, str, List[str]]], str]]] = {}

        for r_file in rollout_files[:max_sessions]:
            session_id, cwd, exchanges = AgentHistoryImporter._parse_codex_rollout(r_file)
            if not exchanges and not cwd:
                continue

            thread_hint = thread_names.get(session_id, "")
            paths = [cwd] if cwd else []
            project_name, ws_path = _infer_project_name(paths, exchanges, fallback_hint=thread_hint)
            if not project_name:
                continue
            grouped.setdefault(project_name, []).append((session_id, exchanges, ws_path or cwd))

        updated_projects = 0
        for project_name, sess_list in grouped.items():
            sess_chronological = list(reversed(sess_list))
            all_exchanges: List[Tuple[str, str, List[str]]] = []
            workspace_path = ""

            for _, ex_list, ws_path in sess_chronological:
                all_exchanges.extend(ex_list)
                if ws_path and not workspace_path:
                    workspace_path = ws_path

            core_plan = _build_project_core_plan(
                project_name=project_name,
                app_label="OpenAI Codex",
                workspace_path=workspace_path,
                artifacts=[],
                exchanges=all_exchanges,
            )

            existing = store.get_by_project_name("codex", project_name)
            if existing:
                existing.name = project_name
                existing.project_name = project_name
                existing.sun_title = project_name
                _merge_or_populate_system(
                    existing, core_plan, workspace_path, all_exchanges
                )
            else:
                system = SolarSystem(
                    name=project_name,
                    project_name=project_name,
                    sun_title=project_name,
                    core_plan=core_plan,
                    source="codex",
                    source_id=f"codex:{_tr_lower(project_name)}",
                    workspace_path=workspace_path,
                )
                _populate_clean_semantic_planets(system, all_exchanges)
                store.add_system(system)

            updated_projects += 1

        return updated_projects

    @staticmethod
    def _parse_codex_rollout(file_path: Path) -> Tuple[str, str, List[Tuple[str, str, List[str]]]]:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return "", "", []

        session_id = file_path.stem
        cwd = ""
        exchanges: List[Tuple[str, str, List[str]]] = []
        current_user = ""
        current_ai = ""
        current_tools: List[str] = []

        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            obj_type = obj.get("type", "")
            payload = obj.get("payload", {}) or {}

            if obj_type == "session_meta":
                session_id = str(payload.get("session_id") or payload.get("id") or session_id)
                cwd = str(payload.get("cwd") or "")
                roots = payload.get("runtime_workspace_roots") or []
                if roots and not cwd:
                    cwd = str(roots[0])

            elif obj_type == "response_item":
                p_type = payload.get("type", "")
                if p_type == "message":
                    role = payload.get("role", "")
                    content_items = payload.get("content") or []
                    text_parts = [
                        str(c.get("text", ""))
                        for c in content_items
                        if isinstance(c, dict) and c.get("text")
                    ]
                    full_text = "\n".join(text_parts).strip()

                    if "The following is the Codex agent history" in full_text:
                        continue

                    if role == "user":
                        cleaned = _clean_user_request(full_text)
                        if cleaned and not cleaned.startswith("<"):
                            if current_user:
                                exchanges.append((current_user, current_ai, current_tools))
                            current_user = cleaned
                            current_ai = ""
                            current_tools = []
                    elif role == "assistant" and full_text:
                        current_ai = full_text

                elif p_type in {"function_call", "custom_tool_call"}:
                    fn_name = str(payload.get("name", "tool"))
                    current_tools.append(fn_name)

        if current_user:
            exchanges.append((current_user, current_ai, current_tools))

        return session_id, cwd, exchanges

    @staticmethod
    def scan_claude_code(store: GalaxyStore, max_sessions: int = 12) -> int:
        """Scans local Claude Code project histories (~/.claude/projects) and merges by project name."""
        claude_dir = Path.home() / ".claude" / "projects"
        if not claude_dir.exists():
            return 0

        jsonl_files = list(claude_dir.rglob("*.jsonl"))
        jsonl_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        grouped: Dict[str, List[Tuple[List[Tuple[str, str, List[str]]], str]]] = {}

        for j_file in jsonl_files[:max_sessions]:
            try:
                lines = j_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            exchanges: List[Tuple[str, str, List[str]]] = []
            cwd = ""
            current_u = ""
            current_a = ""

            for line in lines[:120]:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if item.get("cwd") and not cwd:
                    cwd = str(item.get("cwd"))
                msg = item.get("message", {}) or {}
                role = msg.get("role") or item.get("type")
                content = msg.get("content") or item.get("display") or ""
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                if role == "user" and isinstance(content, str) and content.strip():
                    cleaned = _clean_user_request(content)
                    if cleaned:
                        if current_u:
                            exchanges.append((current_u, current_a, []))
                        current_u = cleaned
                        current_a = ""
                elif role == "assistant" and isinstance(content, str) and content.strip():
                    current_a = content.strip()

            if current_u:
                exchanges.append((current_u, current_a, []))
            if not exchanges:
                continue

            paths = [cwd] if cwd else [j_file.parent.name]
            project_name, ws_path = _infer_project_name(paths, exchanges)
            if not project_name:
                continue
            grouped.setdefault(project_name, []).append((exchanges, ws_path or cwd))

        updated_projects = 0
        for project_name, sess_list in grouped.items():
            all_exchanges: List[Tuple[str, str, List[str]]] = []
            workspace_path = ""
            for ex_list, ws_path in reversed(sess_list):
                all_exchanges.extend(ex_list)
                if ws_path and not workspace_path:
                    workspace_path = ws_path

            core_plan = _build_project_core_plan(
                project_name=project_name,
                app_label="Claude Code",
                workspace_path=workspace_path,
                artifacts=[],
                exchanges=all_exchanges,
            )

            existing = store.get_by_project_name("claude_code", project_name)
            if existing:
                existing.name = project_name
                existing.project_name = project_name
                existing.sun_title = project_name
                _merge_or_populate_system(
                    existing, core_plan, workspace_path, all_exchanges
                )
            else:
                system = SolarSystem(
                    name=project_name,
                    project_name=project_name,
                    sun_title=project_name,
                    core_plan=core_plan,
                    source="claude_code",
                    source_id=f"claude_code:{_tr_lower(project_name)}",
                    workspace_path=workspace_path,
                )
                _populate_clean_semantic_planets(system, all_exchanges)
                store.add_system(system)

            updated_projects += 1

        return updated_projects
