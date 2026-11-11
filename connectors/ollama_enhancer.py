"""Local Ollama LLM Verification and Project Summary Enhancer.

Core Principle:
- Helios extracts each project's purpose, architecture, and modules independently
  using its deterministic codebase and session analysis engine without requiring Ollama.
- This module uses a locally installed Ollama model (e.g., `gemma3:4b`) as an optional
  verifier and enhancer: it inspects the project's actual file structure and modules
  to refine and enrich the Project Core (Sun) summary.
- It can also write a short prose description of each module (`PlanetNode.ai_summary`)
  from evidence gathered off the UI thread (a Python outline built with `ast`, file
  headers, file names, session contents). The model only ever produces text: the
  graph's structure (modules, sub-components, links) comes solely from the
  deterministic importers and is never changed by anything in this module.
- It can suggest the project's purpose (1-2 plain sentences: what the project is and
  what it is for) from `SolarSystem.purpose_evidence` alone (`describe_purpose`). The
  answer is only ever a suggestion (`SolarSystem.purpose_suggestion`, stored through
  `connectors.purpose_suggestion.set_purpose_suggestion`); it becomes the purpose only
  when the user accepts it. An answer of `UNKNOWN` (the evidence is too thin) is
  rejected. The model is only asked when the evidence is strong enough
  (`connectors.purpose_evidence.assess_purpose_evidence`: a description, README text,
  descriptive header comments / user-facing text or session requests); for weak
  evidence no request is made and nothing is suggested.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

from PySide6.QtCore import QThread, Signal

from connectors import purpose_quality
from connectors.purpose_evidence import assess_purpose_evidence, purpose_evidence_is_strong, session_line_first
from connectors.purpose_suggestion import manual_suggestion_allowed, needs_auto_suggestion
from core.models import SolarSystem

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MARKER = "OLLAMA VERIFIED ARCHITECTURE ANALYSIS"

# Context window requested from Ollama, and the explicit prompt budget that keeps the
# prompt (~3-4 chars per token) plus the generated answer (num_predict) inside it.
OLLAMA_NUM_CTX = 4096
OLLAMA_NUM_PREDICT = 165
MAX_PROMPT_CHARS = 9000
MAX_PLAN_CHARS = 1200
MAX_MODULES = 8
MAX_MODULE_SUMMARY_CHARS = 200
MAX_EVIDENCE_CHARS = 1000
MAX_PROJECT_TITLE_CHARS = 120

PROMPT_TASK_INSTRUCTIONS = (
    "YOUR TASK:\n"
    "Summarize WHAT THIS PROJECT DOES, ITS DOMAIN, and HOW IT WORKS in clear, concise English "
    "using at most 3 short bullet points (no more than 80-100 words total).\n"
    "- Do not write an introductory sentence; start directly with '• ' bullet points."
)

# Bypass system proxy/VPN settings for local 127.0.0.1 requests
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# --- Module descriptions (PlanetNode.ai_summary) ---------------------------------
# Store setting holding the language name passed to the model ("English", "Türkçe", ...).
LANGUAGE_SETTING_KEY = "ollama_language"
DEFAULT_LANGUAGE = "English"
SUPPORTED_LANGUAGES: Tuple[str, ...] = ("English", "Türkçe")

MODULE_NUM_PREDICT = 140
MODULE_TEMPERATURE = 0.1
MODULE_REQUEST_TIMEOUT_S = 120.0
MAX_MODULE_OUTPUT_CHARS = 600
MAX_MODULE_EVIDENCE_CHARS = 6000
MAX_FILE_READ_BYTES = 1_000_000  # never read source files larger than 1 MB
DIR_FILE_HEAD_BYTES = 4096  # bytes read per file to find a directory entry's first doc line
MAX_DIR_FILES_READ = 25
MAX_SNAPSHOT_SATELLITES = 40
MAX_SNAPSHOT_SUMMARY_CHARS = 600
MAX_SNAPSHOT_SAT_CONTENT_CHARS = 700
MAX_HISTORY_SESSIONS = 12
MAX_CONSECUTIVE_FAILURES = 3

MODULE_TASK_INSTRUCTIONS = (
    "YOUR TASK:\n"
    "Explain in 1-3 short sentences what this module/file does and its role in the project, "
    "based ONLY on the evidence above.\n"
    "- Do not invent features, files or dependencies that are not in the evidence.\n"
    "- If the evidence is insufficient, say only what is visible, without guessing.\n"
    "- No preamble, no markdown headings.\n"
    "- Write in {language}."
)

# --- Project purpose (SolarSystem.purpose) -------------------------------------
PURPOSE_NUM_PREDICT = 120
PURPOSE_TEMPERATURE = 0.1
MAX_PURPOSE_PROMPT_CHARS = 4000
MAX_PURPOSE_OUTPUT_CHARS = 400  # same limit as purpose_quality.MAX_CHARS (longer answers are rejected)
MAX_PURPOSE_CONTEXT_CHARS = 300  # "Project purpose: ..." line in module prompts
PURPOSE_UNKNOWN = "UNKNOWN"

PURPOSE_TASK_INSTRUCTIONS = (
    "YOUR TASK:\n"
    "In at most 2 short sentences, say what this project is for, in plain words a non-programmer "
    "understands. Start with what it is (e.g. 'A local voice assistant that...').\n"
    "- Base it ONLY on the evidence above and reuse its key domain words (feature names, the "
    "words the user used); do not invent features or guess from names alone.\n"
    "- No hedging words (likely, possibly, appears, seems); do not just restate the project name; "
    "do not list file names, libraries or technical structure.\n"
    "- If the evidence does not say what it is for, answer exactly: UNKNOWN\n"
    "- No preamble. Write in {language}."
)

#: Suffix of a rejected `describe_purpose` message: "... (reason: too_long)." (see
#: `purpose_rejection_reason`; codes are `purpose_quality.REASONS` plus "echo").
_PURPOSE_REASON_RE = re.compile(r"\(reason: ([a-z_]+)\)\.?$")

HISTORY_TASK_INSTRUCTIONS = (
    "YOUR TASK:\n"
    "Summarize in up to 3 short bullet points (start each with '• ') what was worked on in "
    "these sessions, based ONLY on the evidence above.\n"
    "- Do not invent features, files or dependencies that are not in the evidence.\n"
    "- If the evidence is insufficient, say only what is visible, without guessing.\n"
    "- No preamble, no markdown headings.\n"
    "- Write in {language}."
)

# Fragments of the prompt scaffolding; an answer containing one is an echo, not a description.
_ECHO_MARKERS: Tuple[str, ...] = (
    "your task:",
    "based only on the evidence above",
    "do not invent features, files or dependencies",
    "no preamble, no markdown headings",
    "you are a senior software engineer documenting",
    "analysis summary:",
    "components found by analysis:",
    "say what this project is and what it is used for",
    "base it only on the evidence above",
    "evidence about the project",
)

_KIND_LABELS = {
    "file": "source file",
    "dir": "directory / package",
    "layer": "architectural layer (group of modules)",
    "history": "AI coding-agent session history",
}


@dataclass(frozen=True)
class PlanetSnapshot:
    """Immutable copy of the PlanetNode fields a module description reads.

    Taken on the UI thread. `satellite_paths` / `satellite_contents` are parallel to
    `satellite_titles`; contents are only kept for `kind == "history"` planets.
    """

    id: str
    title: str
    kind: str
    layer: str
    file_path: str
    summary: str
    satellite_titles: Tuple[str, ...]
    satellite_paths: Tuple[str, ...] = ()
    satellite_contents: Tuple[str, ...] = ()
    ai_summary: str = ""

    @property
    def has_evidence(self) -> bool:
        return bool(self.file_path.strip() or any(t.strip() for t in self.satellite_titles) or self.summary.strip())


def snapshot_planet(planet) -> PlanetSnapshot:
    """Copies the data needed for a module description out of a PlanetNode."""
    if isinstance(planet, PlanetSnapshot):
        return planet
    kind = str(getattr(planet, "kind", "") or "")
    sats = list(getattr(planet, "satellites", []) or [])[:MAX_SNAPSHOT_SATELLITES]
    contents: Tuple[str, ...] = ()
    if kind == "history":
        contents = tuple(_clip(str(s.content or ""), MAX_SNAPSHOT_SAT_CONTENT_CHARS) for s in sats)
    return PlanetSnapshot(
        id=str(planet.id),
        title=str(planet.title or ""),
        kind=kind,
        layer=str(getattr(planet, "layer", "") or ""),
        file_path=str(getattr(planet, "file_path", "") or ""),
        summary=_clip(str(planet.summary or ""), MAX_SNAPSHOT_SUMMARY_CHARS),
        satellite_titles=tuple(str(s.title or "") for s in sats),
        satellite_paths=tuple(str(getattr(s, "file_path", "") or "") for s in sats),
        satellite_contents=contents,
        ai_summary=str(getattr(planet, "ai_summary", "") or ""),
    )


@dataclass(frozen=True)
class SystemSnapshot:
    """Immutable copy of the SolarSystem fields the Ollama enhancer reads.

    Taken on the UI thread so the background worker never touches live model objects.
    """

    id: str
    name: str
    project_name: str
    core_plan: str
    workspace_path: str
    # (module title, module summary, first sub-component titles)
    modules: Tuple[Tuple[str, str, Tuple[str, ...]], ...]
    # Per-module snapshots for module descriptions (see PlanetSnapshot).
    planets: Tuple[PlanetSnapshot, ...] = ()
    # Project purpose (see SolarSystem.purpose / purpose_source / purpose_evidence).
    purpose: str = ""
    purpose_source: str = ""
    purpose_evidence: str = ""
    # AI suggestion state (see SolarSystem.purpose_suggestion / connectors.purpose_suggestion).
    purpose_suggestion: str = ""
    purpose_suggestion_key: str = ""
    purpose_dismissed_key: str = ""

    @property
    def display_name(self) -> str:
        return self.project_name or self.name

    def planet_snapshot(self, planet_id: str) -> Optional[PlanetSnapshot]:
        for ps in self.planets:
            if ps.id == planet_id:
                return ps
        return None


def snapshot_system(system: Union[SolarSystem, SystemSnapshot]) -> SystemSnapshot:
    """Copies the data needed for an Ollama enhancement out of a SolarSystem."""
    if isinstance(system, SystemSnapshot):
        return system
    modules = tuple(
        (
            str(p.title or ""),
            str(p.summary or ""),
            tuple(str(s.title or "") for s in list(p.satellites)[:6]),
        )
        for p in list(system.planets)
    )
    return SystemSnapshot(
        id=str(system.id),
        name=str(system.name or ""),
        project_name=str(system.project_name or ""),
        core_plan=str(system.core_plan or ""),
        workspace_path=str(system.workspace_path or ""),
        modules=modules,
        planets=tuple(snapshot_planet(p) for p in list(system.planets)),
        purpose=str(getattr(system, "purpose", "") or ""),
        purpose_source=str(getattr(system, "purpose_source", "") or ""),
        purpose_evidence=str(getattr(system, "purpose_evidence", "") or ""),
        purpose_suggestion=str(getattr(system, "purpose_suggestion", "") or ""),
        purpose_suggestion_key=str(getattr(system, "purpose_suggestion_key", "") or ""),
        purpose_dismissed_key=str(getattr(system, "purpose_dismissed_key", "") or ""),
    )


def strip_ollama_block(plan: str) -> str:
    """Removes a previously appended Ollama analysis block from a core plan."""
    base_plan = plan
    if OLLAMA_MARKER in base_plan:
        idx = base_plan.find(f"\n\n[{OLLAMA_MARKER}")
        if idx != -1:
            base_plan = base_plan[:idx].strip()
    return base_plan


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:max(limit, 0)]
    return text[: limit - 1].rstrip() + "…"


def _first_meaningful_text(summary: str, limit: int = MAX_MODULE_SUMMARY_CHARS) -> str:
    """Returns the first meaningful ~`limit` chars of a module summary on one line.

    Skips empty lines and heading-style lines such as "WHAT DOES IT DO?" or "HOW IT WORKS:".
    """
    parts: List[str] = []
    for raw in summary.splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line:
            continue
        is_heading = line.upper() == line and line.endswith((":", "?")) and len(line) < 60
        if is_heading:
            continue
        parts.append(line)
        if sum(len(p) + 1 for p in parts) >= limit:
            break
    return _clip(" ".join(parts), limit)


def build_prompt(snapshot: SystemSnapshot, workspace_evidence: str = "") -> str:
    """Builds the verification prompt within MAX_PROMPT_CHARS.

    The header and the task instructions are always included in full; the plan,
    module list and file evidence are truncated to fit the remaining budget.
    """
    title = _clip(snapshot.display_name, MAX_PROJECT_TITLE_CHARS)
    header = (
        f"You are a senior software architect. Review the architectural summary, modules, "
        f"and file evidence for the project '{title}' below.\n\n"
    )
    budget = MAX_PROMPT_CHARS - len(header) - len(PROMPT_TASK_INSTRUCTIONS)

    base_plan = strip_ollama_block(snapshot.core_plan)
    plan_prefix, plan_suffix = "CURRENT SUMMARY:\n", "\n\n"
    plan_room = max(0, min(MAX_PLAN_CHARS, budget - len(plan_prefix) - len(plan_suffix)))
    plan_section = f"{plan_prefix}{_clip(base_plan, plan_room)}{plan_suffix}"
    budget -= len(plan_section)

    mod_prefix, mod_suffix = "ARCHITECTURAL MODULES:\n", "\n\n"
    mod_lines: List[str] = []
    room = budget - len(mod_prefix) - len(mod_suffix)
    for title_m, summary_m, sat_titles in snapshot.modules[:MAX_MODULES]:
        sat_names = ", ".join(t for t in sat_titles if t)
        line = (
            f"- {_clip(title_m, 80)}: {_first_meaningful_text(summary_m)} "
            f"(Sub-components: {_clip(sat_names, 160)})"
        )
        if len(line) + 1 > room:
            break
        mod_lines.append(line)
        room -= len(line) + 1
    mod_section = f"{mod_prefix}" + "\n".join(mod_lines) + mod_suffix
    if len(mod_section) <= budget:
        budget -= len(mod_section)
    else:
        mod_section = ""

    ev_section = ""
    if workspace_evidence:
        ev_prefix, ev_suffix = "DIRECTORY & FILE EVIDENCE:\n", "\n\n"
        ev_room = min(MAX_EVIDENCE_CHARS, budget - len(ev_prefix) - len(ev_suffix))
        if ev_room > 40:
            ev_section = f"{ev_prefix}{_clip(workspace_evidence, ev_room)}{ev_suffix}"

    return header + plan_section + mod_section + ev_section + PROMPT_TASK_INSTRUCTIONS


def format_enhanced_plan(base_plan: str, model_name: str, ai_text: str) -> str:
    """Formats the core plan with the Ollama block (format parsed by other modules)."""
    return (
        f"{base_plan}\n\n"
        f"[{OLLAMA_MARKER} — {model_name}]\n"
        f"{ai_text}"
    )


def _clean_ai_text(ai_text: str) -> str:
    # Strip markdown bolding and conversational prefixes, and finish on a complete sentence
    clean_ai = ai_text.replace("**", "").strip()
    lines = [
        ln.strip()
        for ln in clean_ai.splitlines()
        if ln.strip()
        and not ln.strip().lower().startswith(("here is", "here's", "sure", "certainly"))
    ]
    clean_ai = "\n".join(lines).strip()
    if clean_ai and clean_ai[-1] not in ".!?)":
        last_dot = max(clean_ai.rfind("."), clean_ai.rfind("!"))
        if last_dot > len(clean_ai) // 2:
            clean_ai = clean_ai[: last_dot + 1].strip()
    return clean_ai


# ----------------------------------------------------------------------
# Module descriptions: evidence (worker thread), prompt, output cleaning
# ----------------------------------------------------------------------
_PY_CODING_LINE = re.compile(r"^#.*coding[:=]")
_GENERIC_DEF_PATTERN = re.compile(
    r"^\s*(?:"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|enum|type|struct|trait|impl|namespace|module)\b"
    r"|(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\(|function\b)"
    r"|(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+\w+"
    r"|func\s+[\w(]"
    r"|def\s+\w+"
    r"|(?:public|private|protected|internal|static)\b.*\("
    r"|import\b|from\s+\S+\s+import\b|#include\b|using\s+[\w.]+;|package\s+[\w.]+|require\s*\("
    r")"
)
_GENERIC_HEAD_LINES = 30
_GENERIC_MAX_DEF_LINES = 60


def _language_name(language: Optional[str]) -> str:
    """Normalises the language setting to a short one-line name (any string works)."""
    text = " ".join(str(language or "").split())
    return _clip(text, 40) if text else DEFAULT_LANGUAGE


def _read_text_limited(path: Path, max_bytes: Optional[int] = None) -> Optional[str]:
    """Reads a regular file as UTF-8 (errors replaced); None if missing, unreadable or > 1 MB."""
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size > MAX_FILE_READ_BYTES:
            return None
        with path.open("rb") as fh:
            raw = fh.read(max_bytes if max_bytes is not None else MAX_FILE_READ_BYTES)
        return raw.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _first_doc_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def _python_outline(source: str) -> Optional[str]:
    """Condensed outline of a Python file: docstring, header comments, imports, top-level defs."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    parts: List[str] = []

    header: List[str] = []
    for ln in source.splitlines()[:40]:
        s = ln.strip()
        if not s:
            continue
        if not s.startswith("#"):
            break
        if s.startswith("#!") or _PY_CODING_LINE.match(s):
            continue
        header.append(s)
    if header:
        parts.append("Header comments:\n" + "\n".join(header[:12]))

    doc = ast.get_docstring(tree)
    if doc:
        parts.append("Module docstring:\n" + _clip(doc, 700))

    imports: List[str] = []
    defs: List[str] = []
    has_main = False
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            try:
                imports.append(_clip(ast.unparse(node), 160))
            except Exception:
                continue
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                sig = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({ast.unparse(node.args)})"
                if node.returns is not None:
                    sig += f" -> {ast.unparse(node.returns)}"
            except Exception:
                sig = f"def {node.name}(...)"
            line = _clip(sig, 200)
            first = _first_doc_line(ast.get_docstring(node) or "")
            if first:
                line += f"  # {_clip(first, 140)}"
            defs.append(line)
        elif isinstance(node, ast.ClassDef):
            try:
                bases = ", ".join(ast.unparse(b) for b in node.bases)
            except Exception:
                bases = ""
            line = _clip(f"class {node.name}({bases})" if bases else f"class {node.name}", 200)
            first = _first_doc_line(ast.get_docstring(node) or "")
            if first:
                line += f"  # {_clip(first, 140)}"
            methods = [
                n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if methods:
                shown = ", ".join(methods[:12]) + (", ..." if len(methods) > 12 else "")
                line += f"\n    methods: {shown}"
            defs.append(line)
        elif isinstance(node, ast.If):
            try:
                has_main = has_main or "__main__" in ast.unparse(node.test)
            except Exception:
                pass
    if imports:
        parts.append("Imports:\n" + "\n".join(imports[:30]))
    if defs:
        parts.append("Top-level definitions:\n" + "\n".join(defs[:60]))
    if has_main:
        parts.append("Has an `if __name__ == \"__main__\":` entry point.")
    if not parts:
        return None
    return "\n\n".join(parts)


def _generic_outline(source: str, suffix: str) -> str:
    """First lines of a non-Python file plus lines that look like definitions/imports/headings."""
    lines = source.splitlines()
    head = [ln.rstrip() for ln in lines[:_GENERIC_HEAD_LINES]]
    is_markdown = suffix in {".md", ".markdown", ".rst"}
    picked: List[str] = []
    for ln in lines[_GENERIC_HEAD_LINES:]:
        if is_markdown:
            if ln.lstrip().startswith("#"):
                picked.append(ln.strip())
        elif _GENERIC_DEF_PATTERN.match(ln):
            picked.append(_clip(ln.strip(), 200))
        if len(picked) >= _GENERIC_MAX_DEF_LINES:
            break
    text = "First lines:\n" + "\n".join(head).strip()
    if picked:
        label = "Headings further down:" if is_markdown else "Definitions and imports further down:"
        text += f"\n\n{label}\n" + "\n".join(picked)
    return text


def _first_comment_line(head: str, suffix: str) -> str:
    """First docstring/comment/heading line of a file head (cheap, no parsing)."""
    opened = False  # a bare docstring/comment opener was seen; its text follows on the next line
    for raw in head.splitlines()[:60]:
        s = raw.strip()
        if not s or s.startswith("#!") or _PY_CODING_LINE.match(s):
            continue
        for prefix in ('"""', "'''", "/**", "/*", "//", "<!--", "#", "--", ";", "*"):
            if s.startswith(prefix):
                body = s[len(prefix):].strip().strip('"\'').rstrip("*/").rstrip("-->").strip()
                body = body.lstrip("#").strip()
                if body:
                    return _clip(body, 140)
                opened = True
                break
        else:
            if opened or suffix in {".md", ".markdown", ".txt", ".rst"}:
                return _clip(s.strip('"\''), 140)
            return ""
    return ""


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child_r, parent_r = child.resolve(), parent.resolve()
        return os.path.commonpath([str(child_r), str(parent_r)]) == str(parent_r)
    except (OSError, ValueError):
        return False


def gather_module_evidence(planet: PlanetSnapshot) -> str:
    """Collects evidence text for one module. Runs in the worker thread.

    Only the paths stored in the snapshot are read (the planet's own file for
    `kind == "file"`; for `"dir"` the satellites' files inside that directory).
    Missing files (e.g. paths from another machine) and files > 1 MB are skipped.
    """
    kind = planet.kind
    if kind == "file" and planet.file_path.strip():
        path = Path(planet.file_path)
        source = _read_text_limited(path)
        if source is None:
            return ""
        suffix = path.suffix.lower()
        outline = _python_outline(source) if suffix in {".py", ".pyw"} else None
        if outline is None:
            outline = _generic_outline(source, suffix)
        return _clip(f"File: {path.name}\n\n{outline}", MAX_MODULE_EVIDENCE_CHARS)

    if kind == "dir":
        base = Path(planet.file_path) if planet.file_path.strip() else None
        lines: List[str] = []
        reads = 0
        paths = planet.satellite_paths or ("",) * len(planet.satellite_titles)
        for title, sat_path in zip(planet.satellite_titles, paths):
            if not title.strip():
                continue
            entry = f"- {_clip(title, 100)}"
            if sat_path.strip() and reads < MAX_DIR_FILES_READ:
                p = Path(sat_path)
                if base is None or _is_within(p, base):
                    reads += 1
                    head = _read_text_limited(p, DIR_FILE_HEAD_BYTES)
                    first = _first_comment_line(head, p.suffix.lower()) if head else ""
                    if first:
                        entry += f": {first}"
            lines.append(entry)
        if not lines:
            return ""
        return _clip("Files in this directory:\n" + "\n".join(lines), MAX_MODULE_EVIDENCE_CHARS)

    if kind == "history":
        lines = []
        contents = planet.satellite_contents or ("",) * len(planet.satellite_titles)
        for title, content in list(zip(planet.satellite_titles, contents))[:MAX_HISTORY_SESSIONS]:
            body = " ".join(content.split())
            lines.append(f"- Session '{_clip(title, 100)}': {body}" if body else f"- Session '{_clip(title, 100)}'")
        if not lines:
            return ""
        return _clip("Sessions:\n" + "\n".join(lines), MAX_MODULE_EVIDENCE_CHARS)

    return ""  # "layer" / "": titles only (they are part of the prompt itself)


def module_task_instructions(kind: str, language: Optional[str]) -> str:
    template = HISTORY_TASK_INSTRUCTIONS if kind == "history" else MODULE_TASK_INSTRUCTIONS
    return template.format(language=_language_name(language))


def build_module_prompt(
    project_name: str,
    planet_snapshot: PlanetSnapshot,
    evidence_text: str,
    language: Optional[str] = DEFAULT_LANGUAGE,
    project_purpose: str = "",
) -> str:
    """Builds the module-description prompt within MAX_PROMPT_CHARS.

    The header and the task instructions are always included in full; the
    deterministic summary, the component list and the evidence are truncated
    to fit the remaining budget. A known project purpose adds one
    "Project purpose: ..." context line (at most MAX_PURPOSE_CONTEXT_CHARS).
    """
    ps = planet_snapshot
    project = _clip(project_name or "", MAX_PROJECT_TITLE_CHARS) or "(unnamed)"
    instructions = module_task_instructions(ps.kind, language)
    kind_label = _KIND_LABELS.get(ps.kind, "module")
    header_lines = [
        f"You are a senior software engineer documenting the project '{project}'. "
        f"Below is evidence that static analysis found about one {kind_label}.",
    ]
    purpose_text = " ".join((project_purpose or "").split())
    if purpose_text:
        header_lines.append(f"Project purpose: {_clip(purpose_text, MAX_PURPOSE_CONTEXT_CHARS)}")
    header_lines += [
        "",
        f"MODULE: {_clip(ps.title, 120)}",
        f"KIND: {kind_label}",
    ]
    if ps.layer:
        header_lines.append(f"LAYER: {_clip(ps.layer, 40)}")
    if ps.file_path.strip():
        name = Path(ps.file_path.replace("\\", "/")).name or ps.file_path
        header_lines.append(f"PATH: {_clip(name, 160)}")
    header = "\n".join(header_lines) + "\n\n"
    budget = MAX_PROMPT_CHARS - len(header) - len(instructions)

    sections: List[str] = []
    summary = _first_meaningful_text(ps.summary, MAX_SNAPSHOT_SUMMARY_CHARS) if ps.summary else ""
    if summary:
        prefix, suffix = "ANALYSIS SUMMARY:\n", "\n\n"
        room = min(MAX_SNAPSHOT_SUMMARY_CHARS, budget - len(prefix) - len(suffix))
        if room > 40:
            sec = f"{prefix}{_clip(summary, room)}{suffix}"
            sections.append(sec)
            budget -= len(sec)

    titles = [t for t in ps.satellite_titles if t.strip()]
    if titles and ps.kind != "history":
        prefix, suffix = "COMPONENTS FOUND BY ANALYSIS:\n", "\n\n"
        room = min(1500, budget - len(prefix) - len(suffix))
        if room > 40:
            sec = f"{prefix}{_clip(', '.join(titles), room)}{suffix}"
            sections.append(sec)
            budget -= len(sec)

    if evidence_text and evidence_text.strip():
        prefix, suffix = "EVIDENCE:\n", "\n\n"
        room = min(MAX_MODULE_EVIDENCE_CHARS, budget - len(prefix) - len(suffix))
        if room > 40:
            sec = f"{prefix}{_clip(evidence_text, room)}{suffix}"
            sections.append(sec)
            budget -= len(sec)

    return header + "".join(sections) + instructions


_EXTRA_CHATTY_PREFIXES = ("okay", "ok,", "of course", "description:", "summary:", "answer:")
_BULLET_RE = re.compile(r"^(?:[*\-•]|\d+[.)])\s+")
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")


def clean_module_text(raw: str, history: bool = False) -> str:
    """Cleans a module description: no markdown, no chatty prefix, sentence end, <= 600 chars."""
    text = _clean_ai_text(raw or "")
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = text.replace("`", "")
    lines: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if re.match(r"^#{1,6}\s", s):
            continue  # a markdown heading line ("## Overview") is scaffolding, not content
        if not s or s.lower().startswith(_EXTRA_CHATTY_PREFIXES):
            continue
        is_bullet = bool(_BULLET_RE.match(s))
        s = _BULLET_RE.sub("", s).strip()
        if not s:
            continue
        lines.append(("• " + s) if (history and is_bullet) else s)
    if history:
        if any(ln.startswith("• ") for ln in lines):
            lines = [ln for ln in lines if ln.startswith("• ")][:3]
        text = "\n".join(lines)
    else:
        text = " ".join(lines)
    text = text.strip()

    if len(text) > MAX_MODULE_OUTPUT_CHARS:
        cut = text[:MAX_MODULE_OUTPUT_CHARS]
        ends = [m.end() for m in _SENTENCE_END_RE.finditer(cut)]
        if ends and ends[-1] >= MAX_MODULE_OUTPUT_CHARS // 3:
            text = cut[: ends[-1]].strip()
        else:
            text = cut[: MAX_MODULE_OUTPUT_CHARS - 1].rstrip() + "…"
    elif text and text[-1] not in ".!?)…":
        # Generation was cut by num_predict: end on the last complete sentence if any.
        ends = [m.end() for m in _SENTENCE_END_RE.finditer(text)]
        if ends:
            text = text[: ends[-1]].strip()
    return text


def module_text_problem(raw: str, cleaned: str) -> Optional[str]:
    """Returns why a model answer is unusable (empty, garbage, prompt echo), or None if fine."""
    if not cleaned.strip():
        return "Ollama returned an empty response."
    if not any(ch.isalnum() for ch in cleaned):
        return "Ollama returned an unusable response (no words)."
    lowered = f"{raw}\n{cleaned}".lower()
    if any(marker in lowered for marker in _ECHO_MARKERS):
        return "Ollama echoed the prompt instead of describing the module."
    return None


def _generate(prompt: str, model: str, temperature: float, num_predict: int) -> Tuple[Optional[str], Optional[str]]:
    """One non-streaming /api/generate request through `_DIRECT_OPENER`.
    Returns (raw answer, None) or (None, error message)."""
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": OLLAMA_NUM_CTX,
                "num_predict": num_predict,
            },
        }
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _DIRECT_OPENER.open(req, timeout=MODULE_REQUEST_TIMEOUT_S) as resp:
            result = json.loads(resp.read().decode("utf-8", errors="replace"))
            raw = str(result.get("response", "") or "")
            api_error = str(result.get("error", "") or "").strip()
    except urllib.error.HTTPError as http_err:
        err_body = ""
        try:
            err_json = json.loads(http_err.read().decode("utf-8", errors="replace"))
            err_body = str(err_json.get("error", ""))
        except Exception:
            pass
        detail = f" ({err_body})" if err_body else ""
        return None, f"Ollama model error: {http_err}{detail}"
    except Exception as exc:
        return None, f"Ollama response error: {exc}"
    if not raw.strip() and api_error:
        return None, f"Ollama model error: {api_error}"
    return raw, None


def describe_module(
    snapshot: PlanetSnapshot,
    model: str,
    language: Optional[str] = DEFAULT_LANGUAGE,
    project_name: str = "",
    project_purpose: str = "",
) -> Tuple[bool, str, Optional[str]]:
    """Writes a prose description of one module: returns (ok, message, text or None).

    Pure with respect to the model objects (reads only the snapshot, the files it
    names, and the Ollama HTTP API), so it is safe in a worker thread. The text is
    only prose; it can never add, rename or remove nodes or links. Does not start
    the Ollama server (the caller does that once per run).
    """
    evidence = gather_module_evidence(snapshot)
    prompt = build_module_prompt(project_name, snapshot, evidence, language, project_purpose)
    raw, error = _generate(prompt, model, MODULE_TEMPERATURE, MODULE_NUM_PREDICT)
    if raw is None:
        return False, error or "Ollama returned no response.", None
    cleaned = clean_module_text(raw, history=snapshot.kind == "history")
    problem = module_text_problem(raw, cleaned)
    if problem:
        return False, problem, None
    return True, f"Module '{snapshot.title}' described with Ollama ({model}).", cleaned


# ---------------------------------------------------------------------------
# Project purpose
# ---------------------------------------------------------------------------
def purpose_task_instructions(language: Optional[str]) -> str:
    return PURPOSE_TASK_INSTRUCTIONS.format(language=_language_name(language))


def build_purpose_prompt(project_name: str, evidence: str, language: Optional[str] = DEFAULT_LANGUAGE) -> str:
    """Builds the project-purpose prompt within MAX_PURPOSE_PROMPT_CHARS. The header
    and the task instructions are always included in full; the evidence (the only
    input, `SolarSystem.purpose_evidence`) is truncated to the remaining budget."""
    project = _clip(project_name or "", MAX_PROJECT_TITLE_CHARS) or "(unnamed)"
    instructions = purpose_task_instructions(language)
    header = (
        f"Below is evidence about the project '{project}', collected by static analysis of its files "
        "(each line says where it came from).\n\n"
    )
    prefix, suffix = "EVIDENCE:\n", "\n\n"
    room = MAX_PURPOSE_PROMPT_CHARS - len(header) - len(instructions) - len(prefix) - len(suffix)
    body = ""
    text = session_line_first((evidence or "").strip())  # old stored evidence had it last
    if text and room > 40:
        body = f"{prefix}{_clip(text, room)}{suffix}"
    return header + body + instructions


def is_unknown_answer(raw: str) -> bool:
    """True if the model answered UNKNOWN (any case / punctuation / markdown) or an
    obvious variant ("I don't know", "not enough information", "bilinmiyor", ...; see
    `purpose_quality.is_unknown`)."""
    return purpose_quality.is_unknown(raw or "")


_PURPOSE_PREAMBLE_RE = re.compile(
    r"^\s*(?:sure[!,.]?\s*)?(?:here(?: is|'s)|okay|ok|the purpose|purpose|answer)[^:\n]{0,40}:[ \t]*(?=\S)",
    re.IGNORECASE,
)


_PURPOSE_OPENER_RE = re.compile(
    r"^(?:(?:okay|ok|of course|sure|certainly)\b[!,.]*\s*)?(?:(?:description|summary|answer|purpose)\s*:\s*)?",
    re.IGNORECASE,
)
_PURPOSE_LEAD_LINE_RE = re.compile(r"^here\b[^:\n]{0,40}:$", re.IGNORECASE)


def clean_purpose_text(raw: str) -> str:
    """Tidies a purpose answer without shortening it: a one-line preamble such as "Here is
    the purpose: ..." or a chatty first line ("Okay,", "Sure! Here it is:") is cut off,
    markdown, bullets and extra whitespace are removed. Sentences are never dropped, so an
    over-long answer stays visible to `purpose_quality.check_purpose_suggestion`."""
    text = _PURPOSE_PREAMBLE_RE.sub("", raw or "", count=1)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", text)
    lines: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or re.match(r"^#{1,6}\s", s):
            continue
        if not lines:  # a chatty opener: "Okay,", "Sure! Here it is:", "Answer: ..."
            s = _PURPOSE_OPENER_RE.sub("", s).strip()
            if not s or _PURPOSE_LEAD_LINE_RE.match(s):
                continue
        s = _BULLET_RE.sub("", s).strip()
        if s:
            lines.append(s)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def purpose_rejection_reason(message: str) -> str:
    """The reason code of a rejected `describe_purpose` message ("" if it has none):
    one of `purpose_quality.REASONS` ("empty", "unknown", "too_long", "name_only",
    "generic", "no_overlap") or "echo" (the prompt was echoed)."""
    m = _PURPOSE_REASON_RE.search(message or "")
    return m.group(1) if m else ""


def describe_purpose(
    project_name: str,
    evidence: str,
    model: str,
    language: Optional[str] = DEFAULT_LANGUAGE,
) -> Tuple[bool, str, Optional[str]]:
    """Writes the project's purpose from `evidence` only: returns (ok, message, text or None).

    Same conventions as `describe_module` (HTTP helper, `num_ctx`, low temperature,
    rejection of empty / garbage / echoed answers). Weak evidence (see
    `purpose_evidence.assess_purpose_evidence`) returns ok=False without any request:
    a small model guesses on thin evidence. The tidied answer (`clean_purpose_text`, not
    shortened) must pass `purpose_quality.check_purpose_suggestion` (not UNKNOWN, at most
    2 sentences / 400 characters, not generic, not only the name, sharing a word with
    the evidence); otherwise ok=False and the message ends with "(reason: <code>)."
    (`purpose_rejection_reason`). Pure: safe in a worker thread."""
    if not (evidence or "").strip():
        return False, "No purpose evidence for this project.", None
    strength = assess_purpose_evidence(evidence, project_name)
    if not strength.strong:
        return False, f"Ollama was not asked about '{project_name}': {strength.explanation}.", None
    prompt = build_purpose_prompt(project_name, evidence, language)
    raw, error = _generate(prompt, model, PURPOSE_TEMPERATURE, PURPOSE_NUM_PREDICT)
    if raw is None:
        return False, error or "Ollama returned no response.", None
    if is_unknown_answer(raw):
        return False, (f"Ollama could not tell what '{project_name}' is for from the evidence "
                       "(answered UNKNOWN) (reason: unknown)."), None
    cleaned = clean_purpose_text(raw)
    lowered = f"{raw}\n{cleaned}".lower()
    if any(marker in lowered for marker in _ECHO_MARKERS):
        return False, "Ollama echoed the prompt instead of writing the purpose (reason: echo).", None
    quality = purpose_quality.check_purpose_suggestion(cleaned, evidence, project_name)
    if not quality.ok:
        return False, f"No suggestion for '{project_name}': {quality.detail} (reason: {quality.reason}).", None
    return True, f"Purpose of '{project_name}' written with Ollama ({model}).", cleaned


class OllamaEnhancer:
    """Helper class that communicates with the local Ollama service and enhances project summaries."""

    @staticmethod
    def is_server_running(timeout: float = 1.5) -> bool:
        """Checks whether the Ollama HTTP API service is running."""
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
            with _DIRECT_OPENER.open(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def detect_installed_models() -> List[str]:
        """Detects installed models from both the active Ollama API and ~/.ollama/models."""
        models: List[str] = []

        # 1. Read from the live API if running
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
            with _DIRECT_OPENER.open(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                for item in data.get("models", []):
                    name = str(item.get("name", "")).strip()
                    if name and name not in models:
                        models.append(name)
        except Exception:
            pass

        # 2. Scan ~/.ollama/models/manifests on disk (discovers models even when server is stopped)
        manifests_root = (
            Path.home() / ".ollama" / "models" / "manifests" / "registry.ollama.ai" / "library"
        )
        if manifests_root.exists():
            try:
                for model_dir in manifests_root.iterdir():
                    if model_dir.is_dir():
                        for tag_file in model_dir.iterdir():
                            if tag_file.is_file():
                                candidate = f"{model_dir.name}:{tag_file.name}"
                                if candidate not in models:
                                    models.append(candidate)
            except Exception:
                pass

        return models

    @staticmethod
    def get_preferred_model(chosen_model: Optional[str] = None) -> Optional[str]:
        """Returns the requested model or the best available installed model."""
        models = OllamaEnhancer.detect_installed_models()
        if not models:
            return None
        if chosen_model:
            for m in models:
                if m.lower() == chosen_model.lower() or chosen_model.lower() in m.lower():
                    return m
        # Priority order: Qwen or Gemma
        for m in models:
            if "qwen2.5" in m.lower():
                return m
        for m in models:
            if "gemma3" in m.lower():
                return m
        return models[0]

    @staticmethod
    def ensure_server_running() -> bool:
        """Silently starts the Ollama service in the background if it is not running."""
        if OllamaEnhancer.is_server_running(timeout=1.0):
            return True

        try:
            creation_flags = 0
            if sys.platform.startswith("win"):
                creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            env = os.environ.copy()
            env["OLLAMA_VULKAN"] = "0"
            env["OLLAMA_NOPRUNE"] = "1"
            subprocess.Popen(
                ["ollama", "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=creation_flags,
            )
        except Exception:
            return False

        for _ in range(32):
            time.sleep(0.5)
            if OllamaEnhancer.is_server_running(timeout=1.2):
                return True
        return False

    @staticmethod
    def _collect_workspace_evidence(system: Union[SolarSystem, SystemSnapshot]) -> str:
        """Collects file and structural evidence from the project's workspace directory if present.

        Only reads `workspace_path`, so it accepts a SolarSystem or a SystemSnapshot.
        """
        ws_str = str(system.workspace_path or "").strip()
        if not ws_str:
            return ""
        ws = Path(ws_str)
        if not ws.exists() or not ws.is_dir():
            return ""

        evidence_lines: List[str] = [f"Workspace Directory: {ws}"]

        # Important configuration and documentation files
        for cfg_name in ("README.md", "AGENTS.md", "package.json", "requirements.txt", "pyproject.toml"):
            cfg_path = ws / cfg_name
            if cfg_path.exists() and cfg_path.is_file():
                try:
                    raw = cfg_path.read_text(encoding="utf-8", errors="replace").strip()
                    evidence_lines.append(f"--- {cfg_name} ---\n{raw[:600]}")
                except Exception:
                    pass

        # Main source files in the root folder
        try:
            code_files = [
                p.name
                for p in ws.iterdir()
                if p.is_file()
                and p.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".php", ".sql", ".json", ".cmd", ".bat"}
            ][:18]
            if code_files:
                evidence_lines.append("Root Directory Files: " + ", ".join(code_files))
        except Exception:
            pass

        return "\n".join(evidence_lines)

    @staticmethod
    def build_enhanced_plan(
        snapshot: Union[SolarSystem, SystemSnapshot],
        model_name: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str]]:
        """Pure enhancement step: returns (ok, message, new_core_plan or None).

        Reads only the given snapshot (plus the workspace folder on disk and the
        Ollama HTTP API); it never mutates a SolarSystem, so it is safe to call
        from a worker thread. Apply the returned plan on the UI thread.
        """
        snap = snapshot_system(snapshot)
        chosen_model = model_name or OllamaEnhancer.get_preferred_model()
        if not chosen_model:
            return False, "No installed Ollama model found on this computer.", None

        if not OllamaEnhancer.ensure_server_running():
            return False, "Failed to start Ollama service (ollama serve is not responding).", None

        base_plan = strip_ollama_block(snap.core_plan)
        workspace_evidence = OllamaEnhancer._collect_workspace_evidence(snap)
        prompt = build_prompt(snap, workspace_evidence)

        payload = json.dumps(
            {
                "model": chosen_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_ctx": OLLAMA_NUM_CTX,
                    "num_predict": OLLAMA_NUM_PREDICT,
                },
            }
        ).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{OLLAMA_BASE_URL}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _DIRECT_OPENER.open(req, timeout=90.0) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
                ai_text = str(result.get("response", "")).strip()
                api_error = str(result.get("error", "") or "").strip()
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_json = json.loads(http_err.read().decode("utf-8", errors="replace"))
                err_body = str(err_json.get("error", ""))
            except Exception:
                pass
            detail = f" ({err_body})" if err_body else ""
            return False, f"Ollama model error: {http_err}{detail}", None
        except Exception as exc:
            return False, f"Ollama response error: {exc}", None

        if not ai_text:
            if api_error:
                return False, f"Ollama model error: {api_error}", None
            return False, "Ollama returned an empty response.", None

        clean_ai = _clean_ai_text(ai_text)
        new_plan = format_enhanced_plan(base_plan, chosen_model, clean_ai)
        return (
            True,
            f"'{snap.project_name}' summary verified and enhanced with Ollama ({chosen_model}).",
            new_plan,
        )

    @staticmethod
    def enhance_system(
        system: SolarSystem,
        model_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Verifies and enriches the core plan of a project (SolarSystem) using an Ollama model.

        Synchronous backwards-compatible wrapper around `build_enhanced_plan` that applies
        the result to `system` directly. Call it only from the thread that owns `system`.
        """
        ok, msg, new_plan = OllamaEnhancer.build_enhanced_plan(snapshot_system(system), model_name)
        if ok and new_plan is not None:
            system.core_plan = new_plan
            system.ollama_enhanced = True
        return ok, msg


class OllamaEnhanceWorker(QThread):
    """Background worker thread that runs Ollama analysis without freezing the UI.

    Thread-safety: the constructor (called on the UI thread) copies each system into an
    immutable `SystemSnapshot`; `run()` only works on those snapshots and never touches
    SolarSystem objects. Each successful result is emitted as
    `systemEnhanced(system_id, new_core_plan)` and must be applied on the UI thread.

    Module descriptions (`describe_modules=True`): after the project-level summary
    (skipped when `summarize_project=False`), one request per module is made and each
    result is emitted as `moduleDescribed(system_id, planet_id, text)`, also to be
    applied on the UI thread (by planet id, checking the snapshot title). Modules that
    already have an `ai_summary` are skipped unless `force` is set; modules without any
    evidence are always skipped. `requestInterruption()` is honoured between requests
    and also while a request is in flight (the HTTP call runs in a helper thread whose
    late answer is discarded). A failed request is counted and the run continues; after
    `MAX_CONSECUTIVE_FAILURES` failures in a row the run stops.

    Purpose suggestion (`write_purpose=True`, the default): for each system, BEFORE its
    project summary and modules, one request asks for a purpose from `purpose_evidence`
    and emits `purposeSuggested(system_id, text)`. The UI thread stores it only as a
    suggestion (`purpose_suggestion.set_purpose_suggestion`), never as the purpose, and
    it is not used as the purpose context of module prompts. Which systems are asked:
    automatic runs (`purpose_manual=False`) follow `purpose_suggestion.needs_auto_suggestion`
    (empty purpose, strong evidence, no suggestion or dismissal for the current evidence);
    a manual request (`purpose_manual=True`, "Suggest with AI") follows
    `manual_suggestion_allowed` (any purpose except "user" / "readme", strong evidence,
    dismissals ignored). `force` only applies to module descriptions. Weak evidence never
    makes a request. `purpose_only=True` runs only this step. A failed or rejected answer
    (`UNKNOWN`, the quality check, no response) counts as failed and emits
    `purposeRejected(system_id, message)`.
    """

    progressText = Signal(str)
    systemEnhanced = Signal(str, str)  # (system id, new core_plan)
    moduleDescribed = Signal(str, str, str)  # (system id, planet id, description)
    purposeSuggested = Signal(str, str)  # (system id, suggested purpose text — never the purpose)
    purposeRejected = Signal(str, str)  # (system id, why no suggestion was made)
    finishedEnhance = Signal(bool, str)

    def __init__(
        self,
        systems: Sequence[Union[SolarSystem, SystemSnapshot]],
        model_name: Optional[str] = None,
        parent=None,
        force: bool = False,
        language: Optional[str] = DEFAULT_LANGUAGE,
        describe_modules: bool = True,
        summarize_project: bool = True,
        write_purpose: bool = True,
        purpose_only: bool = False,
        purpose_manual: bool = False,
    ) -> None:
        super().__init__(parent)
        self.snapshots: List[SystemSnapshot] = [snapshot_system(s) for s in systems]
        self.model_name = model_name
        self.force = bool(force)
        self.language = _language_name(language)
        self.describe_modules = bool(describe_modules)
        self.summarize_project = bool(summarize_project)
        self.stats = {"described": 0, "skipped": 0, "failed": 0}
        self.purpose_only = bool(purpose_only)
        self.write_purpose = bool(write_purpose) or self.purpose_only
        self.purpose_manual = bool(purpose_manual)
        self.purpose_stats = {"written": 0, "skipped": 0, "failed": 0}

    def purpose_needed(self, snap: SystemSnapshot) -> bool:
        """True if this run asks for a purpose suggestion for `snap` (see the class docstring)."""
        if not self.write_purpose or not snap.purpose_evidence.strip():
            return False
        if not purpose_evidence_is_strong(snap.purpose_evidence, snap.display_name):
            return False
        if self.purpose_manual:
            return manual_suggestion_allowed(snap)
        return needs_auto_suggestion(snap)

    def snapshot_for(self, system_id: str) -> Optional[SystemSnapshot]:
        for snap in self.snapshots:
            if snap.id == system_id:
                return snap
        return None

    def planet_snapshot_for(self, system_id: str, planet_id: str) -> Optional[PlanetSnapshot]:
        snap = self.snapshot_for(system_id)
        return snap.planet_snapshot(planet_id) if snap is not None else None

    def planets_to_describe(self, snap: SystemSnapshot) -> Tuple[List[PlanetSnapshot], int]:
        """Returns (planets that get a request, number skipped)."""
        todo: List[PlanetSnapshot] = []
        skipped = 0
        for ps in snap.planets:
            if not ps.has_evidence or (ps.ai_summary.strip() and not self.force):
                skipped += 1
            else:
                todo.append(ps)
        return todo, skipped

    def _interruptible(self, fn: Callable[[], tuple]) -> Optional[tuple]:
        """Runs a blocking request in a daemon helper thread; None if interrupted meanwhile."""
        box: dict = {}

        def target() -> None:
            try:
                box["result"] = fn()
            except Exception as exc:  # reported like any other failed request
                box["error"] = exc

        helper = threading.Thread(target=target, name="helios-ollama-request", daemon=True)
        helper.start()
        while helper.is_alive():
            helper.join(0.05)
            if self.isInterruptionRequested():
                return None
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # never let an exception escape the thread silently
            self.finishedEnhance.emit(False, f"Ollama analysis failed: {exc}")

    def _run(self) -> None:
        total = len(self.snapshots)
        if total == 0:
            self.finishedEnhance.emit(False, "No projects found to analyze.")
            return

        model = self.model_name or OllamaEnhancer.get_preferred_model() or "gemma3:4b"
        success_count = 0
        last_msg = ""
        consecutive_failures = 0
        stopped_reason = ""
        stats = self.stats
        p_stats = self.purpose_stats
        summarize = self.summarize_project and not self.purpose_only
        describe = self.describe_modules and not self.purpose_only
        purpose_todo = [s.id for s in self.snapshots if self.purpose_needed(s)] if self.write_purpose else []
        purpose_idx = 0

        for idx, snap in enumerate(self.snapshots, start=1):
            if self.isInterruptionRequested():
                stopped_reason = "Stopped by user."
                break

            project_purpose = snap.purpose
            if self.write_purpose:
                if snap.id in purpose_todo:
                    purpose_idx += 1
                    self.progressText.emit(
                        f"Ollama ({model}) — suggesting a purpose {purpose_idx}/{len(purpose_todo)}: {snap.display_name}…"
                    )
                    try:
                        res = self._interruptible(
                            lambda s=snap: describe_purpose(s.display_name, s.purpose_evidence, model, self.language)
                        )
                    except Exception as exc:
                        res = (False, f"Ollama response error: {exc}", None)
                    if res is None:
                        stopped_reason = "Stopped by user."
                        break
                    ok, msg, text = res
                    if ok and text:
                        p_stats["written"] += 1
                        consecutive_failures = 0
                        # Only a suggestion: module prompts keep the real purpose as context.
                        self.purposeSuggested.emit(snap.id, text)
                    else:
                        p_stats["failed"] += 1
                        self.purposeRejected.emit(snap.id, str(msg or ""))
                        consecutive_failures += 1
                        last_msg = msg
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            stopped_reason = f"Stopped after {consecutive_failures} consecutive failed requests: {msg}"
                            break
                else:
                    p_stats["skipped"] += 1
            if self.purpose_only:
                continue

            if summarize:
                self.progressText.emit(
                    f"Ollama ({model}) analyzing ({idx}/{total}): {snap.display_name}..."
                )
                try:
                    res = self._interruptible(
                        lambda s=snap: OllamaEnhancer.build_enhanced_plan(s, model_name=model)
                    )
                except Exception as exc:
                    res = (False, f"Ollama response error: {exc}", None)
                if res is None:
                    stopped_reason = "Stopped by user."
                    break
                ok, msg, new_plan = res
                last_msg = msg
                if ok and new_plan is not None:
                    success_count += 1
                    consecutive_failures = 0
                    self.systemEnhanced.emit(snap.id, new_plan)
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        stopped_reason = f"Stopped after {consecutive_failures} consecutive failed requests: {msg}"
                        break

            if not describe:
                continue
            todo, skipped = self.planets_to_describe(snap)
            stats["skipped"] += skipped
            count = len(todo)
            for m_idx, ps in enumerate(todo, start=1):
                if self.isInterruptionRequested():
                    stopped_reason = "Stopped by user."
                    break
                self.progressText.emit(
                    f"Ollama ({model}) — {snap.display_name}: module {m_idx}/{count} {ps.title}…"
                )
                try:
                    res = self._interruptible(
                        lambda p=ps, s=snap, pp=project_purpose: describe_module(p, model, self.language, s.display_name, pp)
                    )
                except Exception as exc:
                    res = (False, f"Ollama response error: {exc}", None)
                if res is None:
                    stopped_reason = "Stopped by user."
                    break
                ok, msg, text = res
                if ok and text:
                    stats["described"] += 1
                    consecutive_failures = 0
                    self.moduleDescribed.emit(snap.id, ps.id, text)
                else:
                    stats["failed"] += 1
                    consecutive_failures += 1
                    last_msg = msg
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        stopped_reason = f"Stopped after {consecutive_failures} consecutive failed requests: {msg}"
                        break
            if stopped_reason:
                break

        parts: List[str] = []
        if summarize and success_count > 0:
            parts.append(
                f"Enhanced core architecture summaries for {success_count}/{total} projects with Ollama ({model})."
            )
        if self.write_purpose and (self.purpose_only or p_stats["written"] or p_stats["failed"]):
            parts.append(
                f"Purpose suggestions ({model}): {p_stats['written']} suggested, "
                f"{p_stats['skipped']} skipped, {p_stats['failed']} failed."
            )
        if describe:
            parts.append(
                f"Module descriptions ({model}): {stats['described']} described, "
                f"{stats['skipped']} skipped, {stats['failed']} failed."
            )
        if stopped_reason:
            parts.append(stopped_reason)

        produced = success_count + stats["described"] + p_stats["written"]
        project_failed = summarize and success_count == 0
        failed = stats["failed"] + p_stats["failed"]
        if produced > 0 or (not project_failed and failed == 0 and not stopped_reason.startswith("Stopped after")):
            self.finishedEnhance.emit(True, " ".join(parts))
        else:
            fail_msg = last_msg or "Ollama analysis could not be completed."
            if describe or self.purpose_only or stopped_reason:
                fail_msg = " ".join([fail_msg] + parts)
            self.finishedEnhance.emit(False, fail_msg)


class OllamaPullWorker(QThread):
    """Background worker thread that downloads a new model via the Ollama API."""

    progressText = Signal(str)
    finishedPull = Signal(bool, str)

    def __init__(self, model_name: str, parent: Optional[object] = None) -> None:
        super().__init__(parent)
        self.model_name = model_name.strip()

    def run(self) -> None:
        if not self.model_name:
            self.finishedPull.emit(False, "Please enter a valid model name.")
            return

        if not OllamaEnhancer.ensure_server_running():
            self.finishedPull.emit(False, "Failed to start the Ollama service.")
            return

        self.progressText.emit(f"Starting download for '{self.model_name}'...")
        payload = json.dumps({"name": self.model_name, "stream": True}).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            stream_error = ""
            with _DIRECT_OPENER.open(req, timeout=900.0) as resp:
                for line in resp:
                    if not line or not line.strip():
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    # Ollama reports pull failures inside the stream: {"error": "..."}
                    if chunk.get("error"):
                        stream_error = str(chunk.get("error"))
                        break
                    status = chunk.get("status", "")
                    completed = chunk.get("completed", 0) or 0
                    total = chunk.get("total", 0) or 0
                    try:
                        if total > 0 and completed > 0:
                            pct = round((completed / total) * 100, 1)
                            self.progressText.emit(f"{status}: {pct}%")
                        elif status:
                            self.progressText.emit(status)
                    except Exception:
                        pass
            if stream_error:
                self.finishedPull.emit(False, f"Download failed for '{self.model_name}': {stream_error}")
                return
            self.finishedPull.emit(True, f"'{self.model_name}' downloaded and registered!")
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_json = json.loads(http_err.read().decode("utf-8", errors="replace"))
                err_body = str(err_json.get("error", ""))
            except Exception:
                pass
            detail = f" ({err_body})" if err_body else ""
            self.finishedPull.emit(False, f"Download error: {http_err}{detail}")
        except Exception as exc:
            self.finishedPull.emit(False, f"Download error: {exc}")
