"""Helios data models, source categories, and local storage layer."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def generate_id() -> str:
    """Generates a unique short identifier for each node."""
    return uuid.uuid4().hex[:8]


def current_timestamp() -> str:
    """Returns a readable date-time string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def normalize_project_name(name: str) -> str:
    """Normalizes a project name by stripping case, accents, punctuation, and common suffixes."""
    if not name:
        return ""
    s = name.strip().lower()
    rep = {
        "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c",
        "İ": "i", "Ğ": "g", "Ü": "u", "Ş": "s", "Ö": "o", "Ç": "c",
    }
    for tr, en in rep.items():
        s = s.replace(tr, en)
    s = re.sub(r"[\s\-_&]+", " ", s).strip()
    for suffix in (" project", " system", " app", " service"):
        if s.endswith(suffix) and len(s) > len(suffix) + 2:
            s = s[: -len(suffix)].strip()
    return s


# Source / Application Categories
SOURCE_CATEGORIES: Dict[str, Dict[str, object]] = {
    "antigravity": {
        "title": "Antigravity",
        "subtitle": "Agentic Coding",
        "short": "Antigravity",
        "badge": "ANTIGRAVITY",
        "color": "#8B5CF6",
        "sector_center": (-620.0, -360.0),
        "order": 1,
    },
    "codex": {
        "title": "OpenAI Codex",
        "subtitle": "CLI & Code Agent",
        "short": "OpenAI Codex",
        "badge": "OPENAI CODEX",
        "color": "#10B981",
        "sector_center": (620.0, -360.0),
        "order": 2,
    },
    "claude_code": {
        "title": "Claude Code",
        "subtitle": "Anthropic CLI",
        "short": "Claude Code",
        "badge": "CLAUDE CODE",
        "color": "#F59E0B",
        "sector_center": (-620.0, 420.0),
        "order": 3,
    },
    "codebase": {
        "title": "Codebase Analysis",
        "subtitle": "AST & Source Code",
        "short": "Codebase",
        "badge": "CODEBASE",
        "color": "#3B82F6",
        "sector_center": (620.0, 420.0),
        "order": 4,
    },
    "manual": {
        "title": "Personal Projects",
        "subtitle": "Architecture Studio",
        "short": "Personal",
        "badge": "PERSONAL",
        "color": "#D4A574",
        "sector_center": (0.0, 420.0),
        "order": 5,
    },
}


def get_source_meta(source: str) -> Dict[str, object]:
    """Returns metadata (title, color, sector info) for the given source key."""
    return SOURCE_CATEGORIES.get(source, SOURCE_CATEGORIES["manual"])


# Architectural Layer Definitions and Color Palette
LAYER_DEFINITIONS: Dict[str, Tuple[str, str]] = {
    "architecture": ("Architecture & Planning", "#D4A574"),
    "ui": ("UI & Visualization", "#3B82F6"),
    "core": ("Core & Data Model", "#10B981"),
    "integration": ("Integration & Bridges (API/CLI)", "#8B5CF6"),
    "testing": ("Security, Testing & Validation", "#F43F5E"),
    "research": ("Research & Analysis", "#06B6D4"),
    "docs": ("Documentation & Knowledge Base", "#F59E0B"),
}

# Data Flow and Inter-Module Connection Types
RELATION_TYPES: Dict[str, Tuple[str, str]] = {
    "flows_to": ("Data Flow (→)", "#3B82F6"),
    "api_call": ("API / Service Call (⇄)", "#8B5CF6"),
    "depends_on": ("Module Dependency (⇢)", "#10B981"),
    "triggers": ("Event / Trigger (⚡)", "#F59E0B"),
    "validates": ("Security / Validation (✓)", "#F43F5E"),
}


@dataclass
class CanvasNote:
    """Free-form architectural note, rule, or task card placed on the canvas."""

    id: str = field(default_factory=generate_id)
    title: str = "Architecture Note"
    content: str = "Add data flow rules, action items, or technical design notes here."
    color: str = "#F59E0B"
    x: float = -340.0
    y: float = -240.0
    width: float = 215.0
    height: float = 108.0
    created_at: str = field(default_factory=current_timestamp)


@dataclass
class SatelliteNode:
    """Sub-component node orbiting around a module/layer."""

    id: str = field(default_factory=generate_id)
    title: str = "New Component"
    content: str = ""
    visual_tag: str = "thought"  # thought, code, diagram, warning, idea
    status: str = "completed"  # completed, in_progress, planned
    angle: float = 0.0
    distance: float = 105.0
    orbit_speed: float = 0.35
    file_path: str = ""
    is_user_modified: bool = False
    created_at: str = field(default_factory=current_timestamp)
    # --- Structured facts filled by the code importers (all optional) ---------
    # What this sub-component is: "function", "class", "method", "constants",
    # "file", "package" (external library), "session", "document" or "".
    kind: str = ""
    # Declaration as found in source, e.g. "def odak_modu(aktif: bool) -> str".
    signature: str = ""
    # 1-based line number of the definition in `file_path` (0 = unknown).
    line: int = 0
    # Resolved call relations to other sub-components of the same project, as
    # "<module title>::<sub-component title>" references (e.g. "mikrofon.py::dinle_ve_yaz()").
    calls: List[str] = field(default_factory=list)
    called_by: List[str] = field(default_factory=list)

    @property
    def orbit_angle(self) -> float:
        return self.angle

    @orbit_angle.setter
    def orbit_angle(self, val: float) -> None:
        self.angle = val

    @property
    def orbit_radius(self) -> float:
        return self.distance

    @orbit_radius.setter
    def orbit_radius(self, val: float) -> None:
        self.distance = val


@dataclass
class PlanetLink:
    """Directed dependency or data flow connection between modules."""

    id: str = field(default_factory=generate_id)
    source_planet_id: str = ""
    target_planet_id: str = ""
    label: str = "connected"
    relation_type: str = "flows_to"
    description: str = ""
    is_user_modified: bool = False

    @property
    def source_id(self) -> str:
        return self.source_planet_id

    @property
    def target_id(self) -> str:
        return self.target_planet_id


@dataclass
class PlanetNode:
    """Node representing a primary module or architectural layer of a project."""

    id: str = field(default_factory=generate_id)
    title: str = "New Module"
    summary: str = ""
    color: str = "#3B82F6"
    layer: str = "core"
    status: str = "completed"
    orbit_radius: float = 260.0
    angle: float = 0.0
    orbit_speed: float = 0.12
    is_user_modified: bool = False
    # What this module stands for: "file" (one source file), "dir" (a directory /
    # package), "layer" (a layer group), "history" (agent session history),
    # "external" (third-party libraries the project uses), or ""
    # (hand-made / unknown). Set by the importers; older files load with "".
    kind: str = ""
    # Path of the file or directory this module represents (empty if none).
    file_path: str = ""
    # Optional description written by a local LLM (Ollama). Never contains nodes
    # or links, only prose about what the importers found. Importers keep it
    # across re-scans for modules with the same title.
    ai_summary: str = ""
    # True for the module where the program starts (e.g. the file with the
    # `if __name__ == "__main__"` block, `main.py`, the package.json "main").
    is_entry: bool = False
    satellites: List[SatelliteNode] = field(default_factory=list)
    created_at: str = field(default_factory=current_timestamp)

    @property
    def orbit_angle(self) -> float:
        return self.angle

    @orbit_angle.setter
    def orbit_angle(self, val: float) -> None:
        self.angle = val

    def remove_satellite(self, sat: SatelliteNode) -> None:
        if sat in self.satellites:
            self.satellites.remove(sat)
            self.rebalance_satellites()

    def add_satellite(
        self,
        title: str,
        content: str = "",
        visual_tag: str = "thought",
        file_path: str = "",
    ) -> SatelliteNode:
        """Adds a sub-component along the outward arc of the module without overlapping."""
        sat = SatelliteNode(
            title=title,
            content=content,
            visual_tag=visual_tag,
            angle=self.angle,
            distance=105.0,
            file_path=file_path,
        )
        self.satellites.append(sat)
        self.rebalance_satellites()
        return sat

    def add_satellite_at_offset(
        self,
        dx: float,
        dy: float,
        title: str,
        content: str = "",
        visual_tag: str = "code",
        file_path: str = "",
    ) -> SatelliteNode:
        """Places a sub-component at the exact offset (dx, dy) clicked by the user."""
        dist = max(62.0, min(260.0, round(math.hypot(dx, dy), 1)))
        ang = round(math.degrees(math.atan2(dy, dx)) % 360.0, 1)
        sat = SatelliteNode(
            title=title,
            content=content,
            visual_tag=visual_tag,
            angle=ang,
            distance=dist,
            file_path=file_path,
            is_user_modified=True,
        )
        self.satellites.append(sat)
        return sat

    def rebalance_satellites(self, force: bool = False) -> None:
        """Distributes sub-components evenly along the outward-facing arc of the module."""
        total = len(self.satellites)
        if total == 0:
            return
        if total == 1:
            if force or not self.satellites[0].is_user_modified:
                self.satellites[0].angle = self.angle % 360.0
                self.satellites[0].distance = 105.0
            return

        spread = min(160.0, 42.0 * (total - 1))
        start_angle = self.angle - spread / 2.0
        step = spread / max(1, total - 1)
        for idx, s in enumerate(self.satellites):
            if not force and s.is_user_modified:
                continue
            s.angle = round((start_angle + idx * step) % 360.0, 1)
            s.distance = 108.0 + (idx % 2) * 24.0


@dataclass
class SolarSystem:
    """Structure representing a single project and its architectural knowledge graph."""

    id: str = field(default_factory=generate_id)
    name: str = "New Project"
    project_name: str = ""
    sun_title: str = "Project Core"
    core_plan: str = (
        "The primary objective and core architectural rules of this project."
    )
    source: str = "manual"
    sources: List[str] = field(default_factory=list)
    source_id: str = ""
    workspace_path: str = ""
    galaxy_x: float = 0.0
    galaxy_y: float = 0.0
    is_user_modified: bool = False
    ollama_enhanced: bool = False
    # What the project is FOR, in one to three plain sentences (not its structure).
    purpose: str = ""
    # Where `purpose` came from: "user" (typed by the user; never overwritten),
    # "ai" (a local-LLM suggestion the user accepted), "readme" (taken from the
    # project's own description), or "" (unknown yet).
    purpose_source: str = ""
    # Compact, human-readable evidence the importers collected about what the
    # project does (description lines, header comments, user-facing strings,
    # capabilities implied by libraries, first session requests). Shown as
    # "based on" and used as the only input when the LLM writes `purpose`.
    purpose_evidence: str = ""
    # A sentence the local LLM suggested from `purpose_evidence`. Never the purpose: it is
    # shown as "Suggested" until the user accepts it (then it becomes `purpose` with
    # source "ai"), edits it (source "user") or dismisses it. Only kept while the purpose
    # is empty or "ai" (see `connectors.purpose_suggestion`).
    purpose_suggestion: str = ""
    # `purpose_evidence.evidence_key` of the evidence the suggestion was made from.
    purpose_suggestion_key: str = ""
    # Evidence key at the time the user dismissed a suggestion: no automatic suggestion
    # is made again until the evidence changes (a manual request still is).
    purpose_dismissed_key: str = ""
    # What the project IS, as one short fact line ("Python desktop app · PySide6 ·
    # entry: main.py"). Deterministic: built by `connectors.codebase_importer.identity_line`
    # from the code analysis only, never by the AI, never a purpose, not user-editable,
    # and recomputed (replaced) on every scan. "" = not scanned yet (the UI then derives a
    # best-effort value from the stored analysis facts, see `stored_identity`).
    identity: str = ""
    planets: List[PlanetNode] = field(default_factory=list)
    links: List[PlanetLink] = field(default_factory=list)
    notes: List[CanvasNote] = field(default_factory=list)
    tour_steps: List[str] = field(default_factory=list)
    updated_at: str = field(default_factory=current_timestamp)

    def __post_init__(self) -> None:
        if not self.project_name:
            self.project_name = self.name
        if not self.sources and self.source:
            self.sources = [self.source]

    def get_source_labels(self) -> str:
        """Returns short labels for all sources associated with this project."""
        all_s = self.sources if self.sources else [self.source]
        labels = [SOURCE_CATEGORIES.get(s, {}).get("short", s) for s in all_s]
        return " • ".join(labels)

    def add_planet_at_pos(
        self,
        x: float,
        y: float,
        title: str,
        summary: str = "",
        color: Optional[str] = None,
        layer: str = "core",
    ) -> PlanetNode:
        """Places a new module at the exact (x, y) scene coordinates clicked by the user."""
        if not color and layer in LAYER_DEFINITIONS:
            color = LAYER_DEFINITIONS[layer][1]
        chosen_color = color or "#3B82F6"
        dist = max(125.0, min(950.0, round(math.hypot(x, y), 1)))
        ang = round(math.degrees(math.atan2(y, x)) % 360.0, 1)
        planet = PlanetNode(
            title=title,
            summary=summary,
            color=chosen_color,
            layer=layer,
            orbit_radius=dist,
            angle=ang,
            is_user_modified=True,
        )
        self.planets.append(planet)
        self.updated_at = current_timestamp()
        return planet

    def add_note(
        self,
        x: float,
        y: float,
        title: str = "Architecture Note",
        content: str = "",
        color: str = "#F59E0B",
    ) -> CanvasNote:
        """Adds a free-form architecture note box at (x, y) on the canvas."""
        note = CanvasNote(
            title=title,
            content=content
            or "Add data flow rules, action items, or technical design notes here.",
            color=color,
            x=round(x, 1),
            y=round(y, 1),
        )
        self.notes.append(note)
        self.updated_at = current_timestamp()
        return note

    def remove_note(self, note: CanvasNote) -> None:
        if note in self.notes:
            self.notes.remove(note)
            self.updated_at = current_timestamp()

    def remove_link(self, link: PlanetLink) -> None:
        if link in self.links:
            self.links.remove(link)
            self.updated_at = current_timestamp()

    def capture_user_links(self) -> List[Tuple[PlanetLink, str, str]]:
        """Captures user-drawn data flow links along with their connected module titles."""
        title_by_id = {p.id: p.title.strip().lower() for p in self.planets}
        captured: List[Tuple[PlanetLink, str, str]] = []
        for link in self.links:
            if not link.is_user_modified:
                continue
            src_t = title_by_id.get(link.source_planet_id)
            dst_t = title_by_id.get(link.target_planet_id)
            if src_t and dst_t:
                captured.append((link, src_t, dst_t))
        return captured

    def restore_user_links(self, captured: List[Tuple[PlanetLink, str, str]]) -> None:
        """Re-attaches links captured via `capture_user_links` to the updated module IDs."""
        id_by_title: Dict[str, str] = {}
        for p in self.planets:
            id_by_title.setdefault(p.title.strip().lower(), p.id)
        for link, src_t, dst_t in captured:
            src_id = id_by_title.get(src_t)
            dst_id = id_by_title.get(dst_t)
            if not src_id or not dst_id or src_id == dst_id:
                continue
            self.links = [
                l
                for l in self.links
                if not (l.source_planet_id == src_id and l.target_planet_id == dst_id)
            ]
            link.source_planet_id = src_id
            link.target_planet_id = dst_id
            self.links.append(link)

    def merge_duplicate_nodes(self) -> int:
        """Deduplicates and merges modules and sub-components with identical titles."""
        if not self.planets:
            return 0

        merged_count = 0
        unique_planets: Dict[str, PlanetNode] = {}
        id_remap: Dict[str, str] = {}

        for p in self.planets:
            key = p.title.strip().lower()
            if key not in unique_planets:
                unique_planets[key] = p
                id_remap[p.id] = p.id
            else:
                target_p = unique_planets[key]
                id_remap[p.id] = target_p.id
                merged_count += 1
                if p.summary and p.summary.strip() and p.summary not in target_p.summary:
                    if not target_p.summary.strip():
                        target_p.summary = p.summary
                    elif len(p.summary) > len(target_p.summary):
                        target_p.summary = p.summary
                for sat in p.satellites:
                    target_p.satellites.append(sat)
                if p.is_user_modified:
                    target_p.is_user_modified = True

        self.planets = list(unique_planets.values())

        for p in self.planets:
            unique_sats: Dict[str, SatelliteNode] = {}
            for s in p.satellites:
                s_key = s.title.strip().lower()
                if s_key not in unique_sats:
                    unique_sats[s_key] = s
                else:
                    target_s = unique_sats[s_key]
                    merged_count += 1
                    if s.file_path and not target_s.file_path:
                        target_s.file_path = s.file_path
                    if s.content and len(s.content) > len(target_s.content):
                        target_s.content = s.content
                    if s.is_user_modified:
                        target_s.is_user_modified = True
            p.satellites = list(unique_sats.values())
            p.rebalance_satellites()

        new_links: List[PlanetLink] = []
        seen_links = set()
        for link in self.links:
            new_src = id_remap.get(link.source_planet_id, link.source_planet_id)
            new_dst = id_remap.get(link.target_planet_id, link.target_planet_id)
            if new_src == new_dst:
                continue
            pair = (new_src, new_dst)
            if pair in seen_links:
                continue
            seen_links.add(pair)
            link.source_planet_id = new_src
            link.target_planet_id = new_dst
            new_links.append(link)
        self.links = new_links

        if merged_count > 0:
            self.rebalance_layout()
            self.updated_at = current_timestamp()
        return merged_count

    def apply_template(self, template_key: str) -> None:
        """Applies a predefined architectural layer and data flow template to a new project."""
        if template_key == "blank":
            return

        if template_key == "fullstack":
            p_ui = self.add_planet_at_pos(
                -260.0,
                -140.0,
                "Frontend & Client UI",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Manages user-facing screens, forms, client state, and interactive views.\n\n"
                    "HOW IT WORKS & TECHNICAL FLOW:\n"
                    "• Captures user actions and dispatches requests to the API layer."
                ),
                layer="ui",
            )
            p_ui.add_satellite(
                "Views & UI Components",
                "WHAT IT DOES:\nHouses application pages and reusable interface components.",
                "diagram",
            )
            p_ui.add_satellite(
                "Client State Management",
                "WHAT IT DOES:\nKeeps session and view data synchronized on the client side.",
                "code",
            )

            p_api = self.add_planet_at_pos(
                260.0,
                -140.0,
                "API & Service Layer (Backend)",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Handles incoming client requests, enforces business rules, and communicates with external services.\n\n"
                    "HOW IT WORKS & TECHNICAL FLOW:\n"
                    "• Validates payloads, coordinates with the Core/Database layer, and returns structured responses."
                ),
                layer="integration",
            )
            p_api.add_satellite(
                "REST / GraphQL Endpoints",
                "WHAT IT DOES:\nDefines routes and data contracts between client and server.",
                "code",
            )

            p_core = self.add_planet_at_pos(
                240.0,
                185.0,
                "Core & Database (Core/DB)",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Contains domain models, database schemas, and core business logic.\n\n"
                    "HOW IT WORKS & TECHNICAL FLOW:\n"
                    "• Persists and queries relational or document data reliably."
                ),
                layer="core",
            )
            p_core.add_satellite(
                "Database Schema & ORM",
                "WHAT IT DOES:\nModels primary domain entities and storage tables.",
                "code",
            )

            p_sec = self.add_planet_at_pos(
                -240.0,
                185.0,
                "Security, Auth & Testing",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Handles authentication, role-based access control, and automated test suites.\n\n"
                    "HOW IT WORKS & TECHNICAL FLOW:\n"
                    "• Protects API endpoints and validates system behavior."
                ),
                layer="testing",
            )
            p_sec.add_satellite(
                "Authentication (JWT / Session)",
                "WHAT IT DOES:\nVerifies user identity and role permissions.",
                "code",
            )

            self.add_link(p_ui.id, p_api.id, "HTTP / JSON Request", "api_call")
            self.add_link(p_api.id, p_core.id, "Read / Write Data", "flows_to")
            self.add_link(p_sec.id, p_api.id, "Auth Validation", "validates")
            self.add_link(p_ui.id, p_sec.id, "Session Check", "depends_on")

        elif template_key == "ai_agent":
            p_in = self.add_planet_at_pos(
                -270.0,
                -110.0,
                "Command & Interaction UI",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Receives tasks and commands from the user via CLI, chat, or desktop interface.\n\n"
                    "HOW IT WORKS & TECHNICAL FLOW:\n"
                    "• Parses input and forwards structured context to the reasoning engine."
                ),
                layer="ui",
            )
            p_in.add_satellite("Input & Command Parser", "WHAT IT DOES:\nParses user requests.", "diagram")

            p_brain = self.add_planet_at_pos(
                250.0,
                -110.0,
                "LLM & Model Bridge",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Manages prompt construction and inference with local (Ollama) or cloud LLMs.\n\n"
                    "HOW IT WORKS & TECHNICAL FLOW:\n"
                    "• Sends system context to the model and parses structured responses."
                ),
                layer="integration",
            )
            p_brain.add_satellite("Prompt & Model Router", "WHAT IT DOES:\nDispatches prompts to the active model.", "code")

            p_exec = self.add_planet_at_pos(
                230.0,
                190.0,
                "Task & Tool Engine (Core)",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Translates model decisions into file operations, tool calls, or automation steps.\n\n"
                    "HOW IT WORKS & TECHNICAL FLOW:\n"
                    "• Executes tool calls in a controlled sequence."
                ),
                layer="core",
            )
            p_exec.add_satellite("Action & Tool Runner", "WHAT IT DOES:\nExecutes selected tools and commands.", "code")

            p_mem = self.add_planet_at_pos(
                -230.0,
                190.0,
                "Memory & Context Store",
                (
                    "MODULE PURPOSE & ROLE:\n"
                    "Persists conversation history, project state, and learned rules."
                ),
                layer="architecture",
            )
            p_mem.add_satellite("Persistent Store (JSON/SQLite)", "WHAT IT DOES:\nRetains long-term context.", "idea")

            self.add_link(p_in.id, p_brain.id, "Command & Context", "flows_to")
            self.add_link(p_brain.id, p_exec.id, "Tool / Action Call", "triggers")
            self.add_link(p_exec.id, p_mem.id, "Store Result", "flows_to")
            self.add_link(p_mem.id, p_brain.id, "Historical Context", "depends_on")

        elif template_key == "data_pipeline":
            p_src = self.add_planet_at_pos(
                -280.0,
                0.0,
                "1. Data Ingestion & Source",
                "MODULE PURPOSE & ROLE:\nCollects raw data from external APIs, files, or web sources.",
                layer="integration",
            )
            p_src.add_satellite("Data Fetcher / Collector", "WHAT IT DOES:\nFetches raw input data periodically.", "code")

            p_proc = self.add_planet_at_pos(
                0.0,
                -230.0,
                "2. Processing & Analysis Engine",
                "MODULE PURPOSE & ROLE:\nCleans, filters, and transforms raw data into structured metrics.",
                layer="core",
            )
            p_proc.add_satellite("Data Cleaning & Computation", "WHAT IT DOES:\nFilters anomalies and computes metrics.", "code")

            p_out = self.add_planet_at_pos(
                280.0,
                0.0,
                "3. Reporting & Output View",
                "MODULE PURPOSE & ROLE:\nPresents processed results as charts, tables, or notifications.",
                layer="ui",
            )
            p_out.add_satellite("Visual Report / Export", "WHAT IT DOES:\nRenders output for end users.", "diagram")

            self.add_link(p_src.id, p_proc.id, "Raw Data Stream", "flows_to")
            self.add_link(p_proc.id, p_out.id, "Processed Results", "flows_to")

        for l in self.links:
            l.is_user_modified = True
        self.rebuild_links_and_tour()

    def add_planet(
        self,
        title: str,
        summary: str = "",
        color: Optional[str] = None,
        layer: str = "core",
    ) -> PlanetNode:
        """Adds a new module/layer node around the center and balances layout angles."""
        palette = [
            "#3B82F6",
            "#10B981",
            "#8B5CF6",
            "#D4A574",
            "#F59E0B",
            "#F43F5E",
            "#06B6D4",
        ]
        idx = len(self.planets)
        if not color and layer in LAYER_DEFINITIONS:
            color = LAYER_DEFINITIONS[layer][1]
        chosen_color = color or palette[idx % len(palette)]

        planet = PlanetNode(
            title=title,
            summary=summary,
            color=chosen_color,
            layer=layer,
            orbit_radius=255.0,
            angle=0.0,
        )
        self.planets.append(planet)
        self.rebalance_layout()
        self.updated_at = current_timestamp()
        return planet

    def remove_planet(self, planet: PlanetNode) -> None:
        if planet in self.planets:
            self.planets.remove(planet)
            self.links = [
                l
                for l in self.links
                if l.source_planet_id != planet.id and l.target_planet_id != planet.id
            ]
            self.rebuild_links_and_tour()

    def find_parent_planet(self, sat: SatelliteNode) -> Optional[PlanetNode]:
        for p in self.planets:
            if sat in p.satellites:
                return p
        return None

    def rebalance_layout(self, force: bool = False) -> None:
        """Distributes modules evenly around the center with non-overlapping radii."""
        count = len(self.planets)
        if count == 0:
            return
        base_radius = 245.0 if count <= 4 else 285.0
        for idx, p in enumerate(self.planets):
            if force or not p.is_user_modified:
                p.angle = round((idx * (360.0 / count) - 90.0) % 360.0, 1)
                p.orbit_radius = base_radius + (idx % 2) * 35.0 if count > 4 else base_radius
            if force:
                p.is_user_modified = False
                for s in p.satellites:
                    s.is_user_modified = False
            p.rebalance_satellites(force=force)

    def add_link(
        self,
        source_planet_id: str,
        target_planet_id: str,
        label: str = "connected",
        relation_type: str = "flows_to",
        user_drawn: bool = False,
    ) -> Optional[PlanetLink]:
        """Adds a directed connection between two modules."""
        if not source_planet_id or not target_planet_id or source_planet_id == target_planet_id:
            return None
        for existing in self.links:
            if (
                existing.source_planet_id == source_planet_id
                and existing.target_planet_id == target_planet_id
            ):
                if user_drawn:
                    existing.relation_type = relation_type
                    existing.is_user_modified = True
                return existing
        link = PlanetLink(
            source_planet_id=source_planet_id,
            target_planet_id=target_planet_id,
            label=label,
            relation_type=relation_type,
            is_user_modified=user_drawn,
        )
        self.links.append(link)
        return link

    def rebuild_links_and_tour(self) -> None:
        """Balances module angles, builds default architectural links, and orders the guided tour."""
        self.rebalance_layout()
        if not self.planets:
            self.tour_steps = []
            return

        layer_priority = {
            "architecture": 1,
            "docs": 2,
            "research": 3,
            "core": 4,
            "integration": 5,
            "ui": 6,
            "testing": 7,
        }
        ordered_planets = sorted(
            self.planets,
            key=lambda p: (layer_priority.get(p.layer, 5), p.angle),
        )
        self.tour_steps = [p.id for p in ordered_planets]

        # Default layer links are a drawing aid for hand-made (manual) projects only.
        # Imported projects (codebase / agent sources) must show nothing but links
        # that were actually found in the source, so none are invented for them.
        if self.source == "manual" and not self.links and len(ordered_planets) > 1:
            by_layer: Dict[str, List[PlanetNode]] = {}
            for p in self.planets:
                by_layer.setdefault(p.layer, []).append(p)

            for arch_p in by_layer.get("architecture", []):
                for target_layer, lbl in [("core", "defines"), ("ui", "structures"), ("integration", "configures")]:
                    for tp in by_layer.get(target_layer, []):
                        self.add_link(arch_p.id, tp.id, lbl, "flows_to")

            for ui_p in by_layer.get("ui", []):
                for core_p in by_layer.get("core", []):
                    self.add_link(ui_p.id, core_p.id, "data model", "depends_on")
                for int_p in by_layer.get("integration", []):
                    self.add_link(ui_p.id, int_p.id, "service call", "depends_on")

            for int_p in by_layer.get("integration", []):
                for core_p in by_layer.get("core", []):
                    self.add_link(int_p.id, core_p.id, "data flow", "depends_on")

            if not self.links:
                for i in range(len(ordered_planets) - 1):
                    self.add_link(
                        ordered_planets[i].id,
                        ordered_planets[i + 1].id,
                        "next layer",
                        "flows_to",
                    )


# Top-level `format_version` of galaxy.json. Files without it (or older) are migrated on
# load (`GalaxyStore.load`, in memory); `save` always writes the current value.
#   2: an AI-written purpose is only a suggestion (`SolarSystem.purpose_suggestion`); old
#      `purpose_source == "ai"` purposes become suggestions (they were never accepted).
GALAXY_FORMAT_VERSION = 2


def file_format_version(raw: object) -> int:
    """The `format_version` of a parsed galaxy.json payload (0 when missing / invalid)."""
    value = raw.get("format_version") if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def atomic_write_text(path: Path, text: str) -> None:
    """Writes `text` (UTF-8) to `path` atomically.

    The data goes to a temp file in the same directory, is flushed and fsynced,
    then moved over the target with `os.replace`, so readers (e.g. the GUI file
    watcher) see either the old or the new file, never a half-written one.
    On Windows `os.replace` can briefly fail with PermissionError while another
    process holds the target open, so it is retried a few times.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(10):
            try:
                os.replace(tmp_name, str(path))
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class GalaxyStore:
    """Persists all projects in a local JSON file (`data/galaxy.json`).

    Concurrency model: the GUI and the CLI bridge (`connectors.live_bridge`) may
    both write the same file. Writes are atomic (`atomic_write_text`). There is
    no file lock; instead short-lived writers (the CLI bridge) construct a fresh
    store right before applying their single mutation and call
    `reload_if_changed()` just before saving, re-applying the mutation if
    another process changed the file in the meantime. A small race window
    between that check and the final `os.replace` remains.

    Load failures never destroy data: an unreadable file is copied to a
    timestamped backup (`backup_path`) on first load, `load_error` describes the
    problem, and a failed reload keeps the in-memory projects untouched.
    """

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        if storage_path:
            self.file_path = Path(storage_path)
            self.data_dir = self.file_path.parent
        else:
            self.data_dir = base_dir / "data"
            self.file_path = self.data_dir / "galaxy.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.systems: List[SolarSystem] = []
        self.last_written_text: str = ""
        self.load_error: Optional[str] = None
        self.backup_path: Optional[Path] = None
        self._loaded_once = False
        self._write_blocked_reason: Optional[str] = None
        self._undo_stack: List[str] = []
        self._redo_stack: List[str] = []
        self.max_history = 30
        # `format_version` of the file as last loaded (0 = written before versioning) and
        # how many old "ai" purposes that load turned into suggestions.
        self.loaded_format_version: int = GALAXY_FORMAT_VERSION
        self.migrated_purposes: int = 0
        self.load()

    # ------------------------------------------------------------------
    # Settings Management (settings.json)
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: object = None) -> object:
        """Reads a setting from local settings.json."""
        if not self.settings_path.exists():
            return default
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data.get(key, default)
        except Exception:
            return default

    def set_setting(self, key: str, value: object) -> None:
        """Writes a setting to local settings.json."""
        data: Dict[str, object] = {}
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[key] = value
        try:
            atomic_write_text(self.settings_path, json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def merge_systems(self, primary: SolarSystem, others: List[SolarSystem]) -> Tuple[int, int]:
        """Merges secondary projects (`others`) into `primary`.
        Returns: (merged_projects_count, merged_modules_count)
        """
        if not others:
            return 0, 0

        merged_projects_count = 0
        merged_nodes_count = 0

        all_sources: List[str] = []
        if primary.source:
            all_sources.append(primary.source)
        for ext_s in getattr(primary, "sources", []):
            if ext_s not in all_sources:
                all_sources.append(ext_s)

        for other in others:
            if other.id == primary.id:
                continue
            merged_projects_count += 1
            if other.source and other.source not in all_sources:
                all_sources.append(other.source)
            for ext_s in getattr(other, "sources", []):
                if ext_s not in all_sources:
                    all_sources.append(ext_s)

            if other.core_plan and other.core_plan.strip():
                if other.core_plan.strip() not in primary.core_plan:
                    cat_meta = SOURCE_CATEGORIES.get(other.source, {})
                    src_title = cat_meta.get("title", other.source)
                    primary.core_plan += f"\n\n--- [{src_title} Architecture Plan] ---\n{other.core_plan}"

            id_map: Dict[str, str] = {}
            for p_other in other.planets:
                match = None
                p_other_key = p_other.title.strip().lower()
                for p_prim in primary.planets:
                    if p_prim.title.strip().lower() == p_other_key:
                        match = p_prim
                        break
                if match:
                    id_map[p_other.id] = match.id
                    if p_other.summary and p_other.summary.strip() and p_other.summary not in match.summary:
                        if not match.summary.strip():
                            match.summary = p_other.summary
                        elif len(p_other.summary) > len(match.summary):
                            match.summary = p_other.summary
                    for sat in p_other.satellites:
                        match.satellites.append(copy.deepcopy(sat))
                else:
                    new_p = copy.deepcopy(p_other)
                    id_map[p_other.id] = new_p.id
                    primary.planets.append(new_p)

            for link in other.links:
                new_src = id_map.get(link.source_planet_id, link.source_planet_id)
                new_dst = id_map.get(link.target_planet_id, link.target_planet_id)
                if new_src != new_dst:
                    primary.links.append(
                        PlanetLink(
                            source_planet_id=new_src,
                            target_planet_id=new_dst,
                            label=link.label,
                            relation_type=getattr(link, "relation_type", "flows_to"),
                            description=getattr(link, "description", ""),
                            is_user_modified=link.is_user_modified,
                        )
                    )

            for note in other.notes:
                primary.notes.append(copy.deepcopy(note))

            if not (primary.identity or "").strip() and (other.identity or "").strip():
                primary.identity = other.identity

            if other in self.systems:
                self.systems.remove(other)

        primary.sources = all_sources
        primary.is_user_modified = True
        mod_merged = primary.merge_duplicate_nodes()
        merged_nodes_count += mod_merged
        return merged_projects_count, merged_nodes_count

    def merge_cross_source_duplicates(self) -> Tuple[int, int]:
        """Merges projects with the same normalized name across different sources into a single project.

        Returns: (merged_projects_count, merged_modules_count)
        """
        from collections import defaultdict

        grouped = defaultdict(list)
        for s in list(self.systems):
            key = normalize_project_name(s.project_name or s.name)
            grouped[key].append(s)

        def source_priority(s: SolarSystem) -> tuple:
            order = {"codebase": 0, "antigravity": 1, "claude_code": 2, "codex": 3, "manual": 4}
            return (order.get(s.source, 99), -len(s.planets), -(len(s.core_plan or "")))

        total_proj_merged = 0
        total_mod_merged = 0

        for key, group in grouped.items():
            if len(group) <= 1:
                continue
            group.sort(key=source_priority)
            primary = group[0]
            others = group[1:]
            p_cnt, m_cnt = self.merge_systems(primary, others)
            total_proj_merged += p_cnt
            total_mod_merged += m_cnt

        return total_proj_merged, total_mod_merged

    def merge_all_duplicates(self) -> Tuple[int, int]:
        """Merges duplicate projects across sources and deduplicates modules/sub-components."""
        self.push_undo()
        proj_count, node_count = self.merge_cross_source_duplicates()
        for sys_obj in self.systems:
            node_count += sys_obj.merge_duplicate_nodes()
        if proj_count > 0 or node_count > 0:
            self.save()
        return proj_count, node_count

    # ------------------------------------------------------------------
    # Undo (Ctrl+Z) / Redo (Ctrl+Y) History
    # ------------------------------------------------------------------
    def _snapshot(self) -> str:
        return json.dumps([asdict(s) for s in self.systems], ensure_ascii=False)

    def push_undo(self) -> None:
        """Pushes the current state onto the undo stack prior to a modification."""
        snap = self._snapshot()
        if self._undo_stack and self._undo_stack[-1] == snap:
            return
        self._undo_stack.append(snap)
        if len(self._undo_stack) > self.max_history:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(self._snapshot())
        self._restore_snapshot(self._undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(self._snapshot())
        self._restore_snapshot(self._redo_stack.pop())
        return True

    def _restore_snapshot(self, snap: str) -> None:
        data_list = json.loads(snap)
        restored = [s for s in (self._parse_system(d) for d in data_list) if s is not None]
        if restored:
            self.systems = restored
        self.save()

    @staticmethod
    def _parse_system(sys_data: dict) -> Optional[SolarSystem]:
        """Converts a JSON dictionary into a SolarSystem instance."""
        sid = str(sys_data.get("source_id", ""))
        sname = str(sys_data.get("name", ""))
        if sys_data.get("source") == "codex" and (
            sid.startswith(".codex")
            or sid in {"plugin", "marketplace", "skills-curated-cache", "models_cache"}
        ):
            return None
        if sname.startswith(("[Antigravity] ", "[Codex] ", "[Claude Code] ")):
            return None

        planets: List[PlanetNode] = []
        for p_data in sys_data.get("planets", []):
            sats = [
                SatelliteNode(
                    **{k: v for k, v in s_data.items() if k in SatelliteNode.__dataclass_fields__}
                )
                for s_data in p_data.get("satellites", [])
            ]
            p_filtered = {
                k: v
                for k, v in p_data.items()
                if k in PlanetNode.__dataclass_fields__ and k != "satellites"
            }
            p_filtered["satellites"] = sats
            planets.append(PlanetNode(**p_filtered))

        links = [
            PlanetLink(**{k: v for k, v in l_data.items() if k in PlanetLink.__dataclass_fields__})
            for l_data in sys_data.get("links", [])
        ]
        notes = [
            CanvasNote(**{k: v for k, v in n_data.items() if k in CanvasNote.__dataclass_fields__})
            for n_data in sys_data.get("notes", [])
        ]

        s_filtered = {
            k: v
            for k, v in sys_data.items()
            if k in SolarSystem.__dataclass_fields__ and k not in {"planets", "links", "notes"}
        }
        s_filtered["planets"] = planets
        s_filtered["links"] = links
        s_filtered["notes"] = notes
        sys_obj = SolarSystem(**s_filtered)
        sys_obj.rebuild_links_and_tour()
        return sys_obj

    def load(self) -> bool:
        """Loads projects from disk. Returns True on success.

        - Missing file on first load: creates the starter project and saves it.
        - Missing file on reload: keeps the in-memory projects, returns False.
        - Unreadable / invalid file on first load: copies it to
          `galaxy.corrupt-YYYYmmdd-HHMMSS.json` (`self.backup_path`), then falls
          back to the starter project. If the backup cannot be written, the
          starter project is still shown but `save()` refuses to overwrite the file.
        - Unreadable / invalid file on reload: keeps the in-memory projects and
          does not touch the disk.
        `self.load_error` holds a readable message on failure, None on success.
        """
        first_load = not self._loaded_once

        if not self.file_path.exists():
            if not first_load:
                self.load_error = f"Project file not found: {self.file_path}"
                return False
            self._loaded_once = True
            self.load_error = None
            self.systems = [self._create_starter_system()]
            self.recalculate_galaxy_sectors()
            self.save()
            return True

        try:
            raw_bytes = self.file_path.read_bytes()
            text = raw_bytes.decode("utf-8")
            raw = json.loads(text)
            if not isinstance(raw, dict) or not isinstance(raw.get("systems", []), list):
                raise ValueError("top-level JSON must be an object with a 'systems' list")
            loaded = [
                s for s in (self._parse_system(d) for d in raw.get("systems", [])) if s is not None
            ]
        except Exception as exc:
            message = f"Could not read project file {self.file_path}: {type(exc).__name__}: {exc}"
            if not first_load:
                # Live reload path: keep what we have, leave the file alone.
                self.load_error = message
                return False
            self._loaded_once = True
            self.backup_path = None
            try:
                self.backup_path = self._write_corrupt_backup()
                self.load_error = f"{message}. A backup was saved to {self.backup_path}."
            except Exception as backup_exc:
                self._write_blocked_reason = (
                    f"{message}. Backup failed ({backup_exc}); refusing to overwrite the file."
                )
                self.load_error = self._write_blocked_reason
            self.systems = [self._create_starter_system()]
            self.recalculate_galaxy_sectors()
            return False

        if first_load and not loaded:
            loaded = [self._create_starter_system()]
        self.loaded_format_version = file_format_version(raw)
        self.migrated_purposes = 0
        if self.loaded_format_version < GALAXY_FORMAT_VERSION:
            # In memory only; the next normal save writes the current format_version.
            from connectors.purpose_suggestion import migrate_ai_purposes_to_suggestions

            self.migrated_purposes = migrate_ai_purposes_to_suggestions(loaded)
        self._loaded_once = True
        self.systems = loaded
        self.recalculate_galaxy_sectors()
        # Normalize line endings like Path.read_text so is_own_write() comparisons match.
        self.last_written_text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.load_error = None
        self._write_blocked_reason = None
        return True

    def _write_corrupt_backup(self) -> Path:
        """Copies the current (unreadable) file to a timestamped backup next to it."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem, suffix = self.file_path.stem, self.file_path.suffix or ".json"
        candidate = self.file_path.with_name(f"{stem}.corrupt-{stamp}{suffix}")
        counter = 1
        while candidate.exists():
            candidate = self.file_path.with_name(f"{stem}.corrupt-{stamp}-{counter}{suffix}")
            counter += 1
        shutil.copy2(str(self.file_path), str(candidate))
        return candidate

    def reload_if_changed(self) -> bool:
        """Reloads from disk if the file differs from what this store last loaded or wrote.

        Returns True if a reload happened and succeeded. Used by the CLI bridge
        right before saving, so it re-applies its mutation instead of
        overwriting changes another process (e.g. the GUI) just made.
        """
        if self.is_own_write():
            return False
        return self.load()

    def recalculate_galaxy_sectors(self) -> None:
        """Positions project cards into a non-overlapping 2-column dashboard layout grouped by source."""
        by_source: Dict[str, List[SolarSystem]] = {}
        for s in self.systems:
            src = s.source if s.source in SOURCE_CATEGORIES else "manual"
            by_source.setdefault(src, []).append(s)

        col_spacing = 304.0
        row_spacing = 172.0

        ordered_sources = [
            src for src in SOURCE_CATEGORIES.keys() if by_source.get(src)
        ]
        col_x_centers = [-540.0, 540.0]
        col_y_cursors = [-420.0, -420.0]

        for s_idx, src in enumerate(ordered_sources):
            sys_list = by_source[src]
            sys_list.sort(key=lambda x: x.name.lower())
            count = len(sys_list)
            cols = min(3, max(1, count))
            rows = math.ceil(count / cols)

            col_bucket = 0 if col_y_cursors[0] <= col_y_cursors[1] else 1
            if len(ordered_sources) == 1:
                cx = 0.0
            else:
                cx = col_x_centers[col_bucket]

            top_y = col_y_cursors[col_bucket]
            first_row_y = top_y + 92.0

            for idx, sys_obj in enumerate(sys_list):
                r = idx // cols
                c = idx % cols
                items_in_this_row = min(cols, count - r * cols)
                offset_x = (c - (items_in_this_row - 1) / 2.0) * col_spacing
                sys_obj.galaxy_x = round(float(cx) + offset_x, 1)
                sys_obj.galaxy_y = round(first_row_y + r * row_spacing, 1)

            section_height = 92.0 + rows * row_spacing + 70.0
            col_y_cursors[col_bucket] += section_height

        self.systems.sort(
            key=lambda s: (
                int(get_source_meta(s.source).get("order", 99)),  # type: ignore[arg-type]
                s.name.lower(),
            )
        )

    def save(self) -> None:
        if self._write_blocked_reason:
            raise RuntimeError(self._write_blocked_reason)
        self.recalculate_galaxy_sectors()
        payload = {
            "format_version": GALAXY_FORMAT_VERSION,
            "updated_at": current_timestamp(),
            "systems": [asdict(s) for s in self.systems],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        atomic_write_text(self.file_path, text)
        self.last_written_text = text

    def is_own_write(self) -> bool:
        """Checks if the file on disk matches the last content written by Helios itself."""
        try:
            return self.file_path.read_text(encoding="utf-8") == self.last_written_text
        except Exception:
            return True

    def get_by_source_id(self, source: str, source_id: str) -> Optional[SolarSystem]:
        for system in self.systems:
            if system.source == source and system.source_id == source_id:
                return system
        return None

    def get_by_project_name(self, source: str, project_name: str) -> Optional[SolarSystem]:
        target = project_name.strip().lower()
        for system in self.systems:
            if system.source == source and (
                system.project_name.strip().lower() == target
                or system.name.strip().lower() == target
            ):
                return system
        return None

    @property
    def storage_path(self) -> Path:
        return self.file_path

    def grouped_by_source(self) -> Dict[str, List[SolarSystem]]:
        """Returns projects grouped by their application source category."""
        grouped: Dict[str, List[SolarSystem]] = {k: [] for k in SOURCE_CATEGORIES}
        for s in self.systems:
            key = s.source if s.source in grouped else "manual"
            grouped[key].append(s)
        for key in grouped:
            grouped[key].sort(key=lambda x: (x.project_name or x.name).lower())
        return grouped

    def remove_system(self, system: SolarSystem) -> None:
        if system in self.systems and len(self.systems) > 1:
            self.systems.remove(system)
            self.save()

    def clear_all(self) -> None:
        """Clears all systems from the store."""
        self.push_undo()
        self.systems = []
        self.save()

    def grouped_by_project(self) -> Dict[str, List[SolarSystem]]:
        """Returns projects grouped by their normalized project name across sources."""
        grouped: Dict[str, List[SolarSystem]] = {}
        display_names: Dict[str, str] = {}
        for s in self.systems:
            key = normalize_project_name(s.project_name or s.name)
            if key not in display_names:
                display_names[key] = s.project_name or s.name
            grouped.setdefault(key, []).append(s)

        sorted_keys = sorted(grouped.keys(), key=lambda k: display_names[k].lower())
        return {display_names[k]: grouped[k] for k in sorted_keys}

    def find_related_systems(self, system: SolarSystem) -> List[SolarSystem]:
        """Finds counterparts of the same project in other application sources."""
        target = normalize_project_name(system.project_name or system.name)
        if not target:
            return []
        related: List[SolarSystem] = []
        for s in self.systems:
            if s.id == system.id:
                continue
            cand = normalize_project_name(s.project_name or s.name)
            if cand == target:
                related.append(s)
        return related

    def add_system(self, system: SolarSystem) -> SolarSystem:
        system.rebuild_links_and_tour()
        self.systems.append(system)
        self.save()
        return system

    @staticmethod
    def _create_starter_system() -> SolarSystem:
        system = SolarSystem(
            name="Helios",
            project_name="Helios",
            sun_title="Helios Architecture Overview",
            core_plan=(
                "PROJECT PURPOSE & OVERVIEW (WHAT IT DOES):\n"
                "Desktop application that transforms project plans, AI coding sessions "
                "(Antigravity, OpenAI Codex, Claude Code), and local codebases into an interactive, "
                "layered architectural knowledge graph.\n\n"
                "CORE ARCHITECTURE & FEATURES:\n"
                "• Core Node (Project Overview): Holds the project purpose, architectural rules, and optional local Ollama verification.\n"
                "• Module Nodes (Layers): Represent architectural layers (UI, Core, Integration, Security/Testing) and their responsibilities.\n"
                "• Sub-Components: Detail the source files, classes, functions, and technical decisions within each layer."
            ),
            source="manual",
            source_id="helios-starter",
            workspace_path=str(Path(__file__).resolve().parent.parent),
        )

        p_arch = system.add_planet(
            title="Architecture & Knowledge Graph",
            summary=(
                "MODULE PURPOSE & ROLE:\n"
                "Groups projects into dedicated source regions and visualizes inter-module "
                "data and import dependencies with directed edges.\n\n"
                "HOW IT WORKS & TECHNICAL FLOW:\n"
                "• Each project is stored under its source category (`Antigravity`, `OpenAI Codex`, `Claude Code`, `Codebase Analysis`, `Personal Projects`).\n"
                "• Counterparts of the same project across different tools are linked for one-click switching."
            ),
            layer="architecture",
        )
        p_arch.add_satellite(
            title="Source-Based Grouping",
            content=(
                "WHAT IT DOES:\n"
                "Prevents projects from different AI tools and local folders from mixing together, "
                "organizing them under clear category headers.\n\n"
                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                "• Groups projects via `SOURCE_CATEGORIES` and computes non-overlapping dashboard coordinates with `recalculate_galaxy_sectors()`.\n\n"
                "RELATED FILE: `core/models.py`"
            ),
            visual_tag="diagram",
        )
        p_arch.add_satellite(
            title="Step-by-Step Architecture Tour",
            content=(
                "WHAT IT DOES:\n"
                "Guides users through a project's internal structure in logical order "
                "(Architecture → Core → Integration → UI → Testing).\n\n"
                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                "• `rebuild_links_and_tour()` orders modules by layer priority so the `Architecture Tour` action focuses each step sequentially.\n\n"
                "RELATED FILE: `core/models.py`"
            ),
            visual_tag="idea",
        )

        p_ui = system.add_planet(
            title="Interactive Canvas (PySide6)",
            summary=(
                "MODULE PURPOSE & ROLE:\n"
                "Renders the All Projects dashboard and project-level module graphs on `QGraphicsScene` "
                "with smooth zoom/pan, drag-and-drop positioning, and context menus.\n\n"
                "HOW IT WORKS & TECHNICAL FLOW:\n"
                "• In All Projects view, renders summary cards showing each project's purpose, module tags, and component counts.\n"
                "• Clicking a project card opens its internal architectural graph."
            ),
            layer="ui",
        )
        p_ui.add_satellite(
            title="Pinned Project Summary & Inspector",
            content=(
                "WHAT IT DOES:\n"
                "Keeps the project's core summary pinned at the top of the right panel regardless of which node is selected, "
                "while displaying the selected node's detailed editor below.\n\n"
                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                "• Dynamically updates title, layer/type selector, and description fields; preserves manual edits with `is_user_modified=True`.\n\n"
                "RELATED FILE: `ui/main_window.py`"
            ),
            visual_tag="diagram",
        )
        p_ui.add_satellite(
            title="Instant Search & Drag-and-Drop",
            content=(
                "WHAT IT DOES:\n"
                "Filters modules and sub-components in real time from the top search bar and allows "
                "repositioning nodes directly with the mouse.\n\n"
                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                "• dims non-matching nodes during search and updates polar coordinates (`angle`, `orbit_radius`) on drag.\n\n"
                "RELATED FILE: `ui/space_canvas.py`"
            ),
            visual_tag="code",
        )

        p_int = system.add_planet(
            title="AI Agent, AST & Ollama Bridges",
            summary=(
                "MODULE PURPOSE & ROLE:\n"
                "Connects local AI agent histories (`transcript.jsonl`, `~/.codex`, `~/.claude`), "
                "source code directories (Python AST), and optional local Ollama models to Helios.\n\n"
                "HOW IT WORKS & TECHNICAL FLOW:\n"
                "• Filters out conversational noise to extract real architectural features.\n"
                "• Optionally connects to local Ollama models to verify and enrich project summaries."
            ),
            layer="integration",
        )
        p_int.add_satellite(
            title="Noise-Filtered Session Importer",
            content=(
                "WHAT IT DOES:\n"
                "Strips trivial operational commands and system tags from session logs, so sessions "
                "can be listed as work history next to a map built from the real code.\n\n"
                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                "• Finds each session's project folder, builds the map from the code in it, and lists sessions in one Work History module.\n\n"
                "RELATED FILE: `connectors/agent_importer.py`"
            ),
            visual_tag="code",
        )
        p_int.add_satellite(
            title="Local Ollama Verifier",
            content=(
                "WHAT IT DOES:\n"
                "Uses a locally installed LLM via Ollama to verify and refine project summaries based on actual workspace files.\n\n"
                "HOW IT WORKS & TECHNICAL DETAILS:\n"
                "• Connects to `127.0.0.1:11434` bypassing system proxies and starts `ollama serve` in the background if needed.\n\n"
                "RELATED FILE: `connectors/ollama_enhancer.py`"
            ),
            visual_tag="code",
        )

        system.rebuild_links_and_tour()
        return system
