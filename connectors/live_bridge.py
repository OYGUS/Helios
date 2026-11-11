"""Live AI Agent & Codebase Analysis Bridge (CLI).

Allows Antigravity, Claude Code, or OpenAI Codex to update Helios in real time
from the command line or via an Agent Skill under the corresponding source category
(`antigravity`, `codex`, `claude_code`, `codebase`, `manual`) and Project Name,
adding or updating the Project Core (Sun), Modules (Planets), Sub-Components (Satellites),
and Data Flow Links, or analyzing a local project directory.

All mutating commands are idempotent: running the same command twice updates the
existing node (matched by exact, case-insensitive, trimmed title within the same
parent) instead of creating a duplicate.

Example Usage:
    python -m connectors.live_bridge set-sun --project "Helios" --source antigravity --title "Helios" --plan "Details..."
    python -m connectors.live_bridge add-planet --project "Helios" --source antigravity --title "UI Layer" --layer ui --summary "..."
    python -m connectors.live_bridge add-moon --project "Helios" --source antigravity --planet "UI Layer" --title "Space Canvas" --content "..."
    python -m connectors.live_bridge analyze-folder --path "./my-project"

Aliases matching the UI terminology: `set-core`, `add-module`, `add-component`.
Use `--data PATH` (before the command) to target a specific galaxy.json.

Exit codes: 0 success, 1 runtime problem (e.g. unknown module), 2 invalid arguments.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core.models import LAYER_DEFINITIONS, SOURCE_CATEGORIES, GalaxyStore, PlanetNode, SolarSystem

VISUAL_TAGS = ("thought", "code", "diagram", "idea")
SAVE_RETRIES = 3


class BridgeError(Exception):
    """Runtime failure reported to the user on stderr with exit code 1."""


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _resolve_target_system(store: GalaxyStore, project_name: str, source: str) -> SolarSystem:
    """Finds (exact, case-insensitive) or creates a SolarSystem for the given source and project name.

    A newly created system is appended without saving; the caller saves once.
    """
    name = project_name.strip()
    existing = store.get_by_project_name(source, name)
    if existing:
        return existing
    new_sys = SolarSystem(
        name=name,
        project_name=name,
        sun_title=name,
        source=source,
        source_id=f"{source}:{name.lower()}",
    )
    new_sys.rebuild_links_and_tour()
    store.systems.append(new_sys)
    return new_sys


def _find_planet(system: SolarSystem, title: str) -> Optional[PlanetNode]:
    """Exact (case-insensitive, trimmed) match first; then a unique whole-word prefix match."""
    target = _norm(title)
    if not target:
        return None
    for p in system.planets:
        if _norm(p.title) == target:
            return p
    prefix_re = re.compile(r"^" + re.escape(target) + r"(?!\w)")
    prefixed = [p for p in system.planets if prefix_re.match(_norm(p.title))]
    if len(prefixed) == 1:
        return prefixed[0]
    return None


def _require_planet(system: SolarSystem, title: str) -> PlanetNode:
    planet = _find_planet(system, title)
    if planet is None:
        available = ", ".join(f'"{p.title}"' for p in system.planets) or "(none)"
        raise BridgeError(
            f'Module "{title}" not found in project "{system.name}". Available modules: {available}'
        )
    return planet


def _cmd_set_sun(store: GalaxyStore, args: argparse.Namespace) -> str:
    system = _resolve_target_system(store, args.project, args.source)
    system.sun_title = args.title
    system.core_plan = args.plan
    system.rebuild_links_and_tour()
    return f"[Helios] Core updated: [{system.source.upper()}] {system.name}"


def _cmd_add_planet(store: GalaxyStore, args: argparse.Namespace) -> str:
    system = _resolve_target_system(store, args.project, args.source)
    title = args.title.strip()
    existing = next((p for p in system.planets if _norm(p.title) == _norm(title)), None)
    if existing is None:
        planet = system.add_planet(
            title=title,
            summary=args.summary or "",
            color=args.color,
            layer=args.layer or "core",
        )
        action = "added"
    else:
        planet = existing
        if args.summary is not None:
            planet.summary = args.summary
        if args.layer is not None:
            planet.layer = args.layer
            if args.color is None and args.layer in LAYER_DEFINITIONS:
                planet.color = LAYER_DEFINITIONS[args.layer][1]
        if args.color is not None:
            planet.color = args.color
        action = "updated"
    system.rebuild_links_and_tour()
    return f"[Helios] Module {action}: {planet.title} -> [{system.source.upper()}] {system.name}"


def _cmd_add_moon(store: GalaxyStore, args: argparse.Namespace) -> str:
    system = _resolve_target_system(store, args.project, args.source)
    planet = _find_planet(system, args.planet)
    if planet is None:
        if not args.create_planet:
            _require_planet(system, args.planet)  # raises with the list of modules
        planet = system.add_planet(title=args.planet.strip())
    title = args.title.strip()
    existing = next((s for s in planet.satellites if _norm(s.title) == _norm(title)), None)
    if existing is None:
        sat = planet.add_satellite(
            title=title,
            content=args.content or "",
            visual_tag=args.tag or "thought",
        )
        action = "added"
    else:
        sat = existing
        if args.content is not None:
            sat.content = args.content
        if args.tag is not None:
            sat.visual_tag = args.tag
        action = "updated"
    system.rebuild_links_and_tour()
    return f"[Helios] Sub-component {action}: {sat.title} -> {planet.title} ({system.name})"


def _cmd_add_link(store: GalaxyStore, args: argparse.Namespace) -> str:
    system = store.get_by_project_name(args.source, args.project.strip())
    if system is None:
        raise BridgeError(f'Project "{args.project}" (source {args.source}) not found.')
    p_from = _require_planet(system, args.from_planet)
    p_to = _require_planet(system, args.to_planet)
    if p_from.id == p_to.id:
        raise BridgeError(f'Cannot link module "{p_from.title}" to itself.')
    for link in system.links:
        if link.source_planet_id == p_from.id and link.target_planet_id == p_to.id:
            if _norm(link.label) == _norm(args.label):
                return f"[Helios] Link unchanged (already exists): {p_from.title} -> {p_to.title}"
            # SolarSystem.add_link keeps one link per ordered pair; relabel it.
            link.label = args.label
            return f"[Helios] Link updated: {p_from.title} -> {p_to.title} ({args.label})"
    system.add_link(p_from.id, p_to.id, label=args.label)
    return f"[Helios] Link added: {p_from.title} -> {p_to.title} ({args.label})"


def _apply_and_save(store: GalaxyStore, mutate: Callable[[GalaxyStore], str]) -> str:
    """Applies `mutate` and saves; if another process changed the file meanwhile,
    reloads and re-applies (mutations are idempotent) instead of overwriting it."""
    message = mutate(store)
    for _ in range(SAVE_RETRIES):
        if not store.reload_if_changed():
            if store.load_error:
                raise BridgeError(store.load_error)
            store.save()
            return message
        message = mutate(store)
    raise BridgeError("Project file keeps changing on disk; giving up to avoid overwriting it.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m connectors.live_bridge",
        description="Helios Live AI Agent & Codebase Bridge (Antigravity / Codex / Claude Code / Codebase)",
    )
    parser.add_argument("--data", default=None, help="Path to a galaxy.json file (default: Helios data/galaxy.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sources = sorted(SOURCE_CATEGORIES.keys())
    layers = list(LAYER_DEFINITIONS.keys())

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", "--system", dest="project", required=True, help="Project Name (e.g., Helios)")
        p.add_argument("--source", default="antigravity", choices=sources, help="Application source")

    # 1. Create a SolarSystem or update its Sun (Core Plan)
    p_sun = sub.add_parser("set-sun", aliases=["set-core"], help="Updates the Project Core (Sun / Core Plan)")
    add_common(p_sun)
    p_sun.add_argument("--title", required=True, help="Core node title")
    p_sun.add_argument("--plan", required=True, help="Core architecture plan and summary text")
    p_sun.set_defaults(cmd="set-sun")

    # 2. Add or update a Planet (Architectural Module)
    p_planet = sub.add_parser("add-planet", aliases=["add-module"], help="Adds or updates an Architectural Module")
    add_common(p_planet)
    p_planet.add_argument("--title", required=True, help="Module title")
    p_planet.add_argument("--summary", default=None, help="Module summary / description")
    p_planet.add_argument("--layer", default=None, choices=layers, help="Architectural layer (default: core)")
    p_planet.add_argument("--color", default=None, help="Hex color code (e.g., #4EA8DE)")
    p_planet.set_defaults(cmd="add-planet")

    # 3. Add or update a Moon (Sub-Component)
    p_moon = sub.add_parser("add-moon", aliases=["add-component"], help="Adds or updates a Sub-Component of a Module")
    add_common(p_moon)
    p_moon.add_argument("--planet", "--module", dest="planet", required=True, help="Target Module title (exact)")
    p_moon.add_argument("--title", required=True, help="Sub-component title")
    p_moon.add_argument("--content", default=None, help="Sub-component content / code / diagram")
    p_moon.add_argument("--tag", default=None, choices=VISUAL_TAGS, help="Visual tag (default: thought)")
    p_moon.add_argument("--create-planet", "--create-module", dest="create_planet", action="store_true",
                        help="Create the target module if it does not exist")
    p_moon.set_defaults(cmd="add-moon")

    # 4. Add a Constellation Link between Modules
    p_link = sub.add_parser("add-link", help="Adds a dependency or data flow link between two modules")
    add_common(p_link)
    p_link.add_argument("--from-planet", "--from-module", dest="from_planet", required=True, help="Source module title")
    p_link.add_argument("--to-planet", "--to-module", dest="to_planet", required=True, help="Target module title")
    p_link.add_argument("--label", default="connected", help="Relationship label")
    p_link.set_defaults(cmd="add-link")

    # 5. Analyze a Folder / Codebase
    p_folder = sub.add_parser("analyze-folder", help="Analyzes a local project directory and builds its architecture graph")
    p_folder.add_argument("--path", required=True, help="Path to the project directory to analyze")
    p_folder.set_defaults(cmd="analyze-folder")
    return parser


COMMANDS = {
    "set-sun": _cmd_set_sun,
    "add-planet": _cmd_add_planet,
    "add-moon": _cmd_add_moon,
    "add-link": _cmd_add_link,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed the message to stderr
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.cmd != "analyze-folder" and not args.project.strip():
        print("error: --project must not be empty", file=sys.stderr)
        return 2

    try:
        # A fresh store is constructed right before the single mutation (see GalaxyStore docstring).
        store = GalaxyStore(Path(args.data) if args.data else None)
        if store.load_error:
            raise BridgeError(store.load_error)

        if args.cmd == "analyze-folder":
            from connectors.codebase_importer import CodebaseImporter

            folder = Path(args.path)
            if not folder.is_dir():
                raise BridgeError(f"Directory not found: {args.path}")
            sys_obj = CodebaseImporter.analyze_directory(folder, store)
            if not sys_obj:
                raise BridgeError(f"Could not analyze directory: {args.path}")
            print(f"[Helios] Codebase analyzed: {sys_obj.name} ({len(sys_obj.planets)} architectural modules)")
            return 0

        handler = COMMANDS[args.cmd]
        message = _apply_and_save(store, lambda s: handler(s, args))
        print(message)
        return 0
    except BridgeError as exc:
        print(f"[Helios] error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[Helios] error: could not write project file: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
