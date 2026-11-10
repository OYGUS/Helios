"""Live AI Agent & Codebase Analysis Bridge (CLI).

Allows Antigravity, Claude Code, or OpenAI Codex to update Helios in real time
from the command line or via an Agent Skill under the corresponding source category
(`antigravity`, `codex`, `claude_code`, `codebase`, `manual`) and Project Name,
adding or updating the Project Core (Sun), Modules (Planets), Sub-Components (Satellites),
and Data Flow Links, or analyzing a local project directory.

Example Usage:
    python -m connectors.live_bridge set-sun --project "Helios" --source antigravity --title "Helios" --plan "Details..."
    python -m connectors.live_bridge add-planet --project "Helios" --source antigravity --title "UI Layer" --layer ui --summary "..."
    python -m connectors.live_bridge add-moon --project "Helios" --source antigravity --planet "UI Layer" --title "Space Canvas" --content "..."
    python -m connectors.live_bridge analyze-folder --path "./my-project"
"""

from __future__ import annotations

import argparse
from pathlib import Path

from connectors.codebase_importer import CodebaseImporter
from core.models import GalaxyStore, SolarSystem


def _resolve_target_system(store: GalaxyStore, project_name: str, source: str) -> SolarSystem:
    """Finds or creates a SolarSystem for the given application source and project name."""
    if project_name:
        existing = store.get_by_project_name(source, project_name)
        if existing:
            return existing
        matched = [
            s
            for s in store.systems
            if s.source == source and project_name.lower() in s.name.lower()
        ]
        if matched:
            return matched[0]
        new_sys = SolarSystem(
            name=project_name.strip(),
            project_name=project_name.strip(),
            sun_title=project_name.strip(),
            source=source,
            source_id=f"{source}:{project_name.strip().lower()}",
        )
        return store.add_system(new_sys)
    return store.systems[0]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Helios Live AI Agent & Codebase Bridge (Antigravity / Codex / Claude Code / Codebase)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # 1. Create a SolarSystem or update its Sun (Core Plan)
    p_sun = sub.add_parser("set-sun", help="Updates the Project Core (Sun / Core Plan) of the specified project")
    p_sun.add_argument("--project", "--system", dest="project", default="", help="Project Name (e.g., Helios, MyProject)")
    p_sun.add_argument("--source", default="antigravity", help="antigravity, codex, claude_code, codebase, manual")
    p_sun.add_argument("--title", required=True, help="Core node title")
    p_sun.add_argument("--plan", required=True, help="Core architecture plan and summary text")

    # 2. Add a Planet (Architectural Module)
    p_planet = sub.add_parser("add-planet", help="Adds a new Architectural Module (Planet) around the Project Core")
    p_planet.add_argument("--project", "--system", dest="project", default="", help="Project Name")
    p_planet.add_argument("--source", default="antigravity", help="Application source")
    p_planet.add_argument("--title", required=True, help="Module title")
    p_planet.add_argument("--summary", default="", help="Module summary / description")
    p_planet.add_argument("--layer", default="core", help="architecture, ui, core, integration, testing, research, docs")
    p_planet.add_argument("--color", default=None, help="Hex color code (e.g., #4EA8DE)")

    # 3. Add a Moon (Sub-Component)
    p_moon = sub.add_parser("add-moon", help="Adds a Sub-Component (Moon / File / Note) around an Architectural Module")
    p_moon.add_argument("--project", "--system", dest="project", default="", help="Project Name")
    p_moon.add_argument("--source", default="antigravity", help="Application source")
    p_moon.add_argument("--planet", required=True, help="Target Module (Planet) title")
    p_moon.add_argument("--title", required=True, help="Sub-component title")
    p_moon.add_argument("--content", default="", help="Sub-component content / code / diagram")
    p_moon.add_argument("--tag", default="thought", help="thought, code, diagram, idea")

    # 4. Add a Constellation Link between Modules
    p_link = sub.add_parser("add-link", help="Adds a dependency or data flow link between two modules")
    p_link.add_argument("--project", "--system", dest="project", default="", help="Project Name")
    p_link.add_argument("--source", default="antigravity", help="Application source")
    p_link.add_argument("--from-planet", required=True, help="Source module title")
    p_link.add_argument("--to-planet", required=True, help="Target module title")
    p_link.add_argument("--label", default="connected", help="Relationship label")

    # 5. Analyze a Folder / Codebase
    p_folder = sub.add_parser("analyze-folder", help="Analyzes a local project directory and builds its architecture graph")
    p_folder.add_argument("--path", required=True, help="Path to the project directory to analyze")

    args = parser.parse_args()
    store = GalaxyStore()

    if args.cmd == "analyze-folder":
        sys_obj = CodebaseImporter.analyze_directory(Path(args.path), store)
        if sys_obj:
            print(f"[Helios] Codebase analyzed: {sys_obj.name} ({len(sys_obj.planets)} architectural modules)")
        else:
            print(f"[Helios] Could not analyze directory: {args.path}")
        return

    target_system = _resolve_target_system(store, getattr(args, "project", ""), getattr(args, "source", "antigravity"))

    if args.cmd == "set-sun":
        target_system.sun_title = args.title
        target_system.core_plan = args.plan
        target_system.rebuild_links_and_tour()
        store.save()
        print(f"[Helios] Core updated: [{target_system.source.upper()}] {target_system.name}")

    elif args.cmd == "add-planet":
        planet = target_system.add_planet(
            title=args.title,
            summary=args.summary,
            color=args.color,
            layer=args.layer,
        )
        target_system.rebuild_links_and_tour()
        store.save()
        print(f"[Helios] Module added: {planet.title} -> [{target_system.source.upper()}] {target_system.name}")

    elif args.cmd == "add-moon":
        planet_match = [
            p for p in target_system.planets if args.planet.lower() in p.title.lower()
        ]
        if not planet_match:
            target_planet = target_system.add_planet(title=args.planet)
        else:
            target_planet = planet_match[0]

        sat = target_planet.add_satellite(
            title=args.title,
            content=args.content,
            visual_tag=args.tag,
        )
        target_system.rebuild_links_and_tour()
        store.save()
        print(f"[Helios] Sub-component added: {sat.title} -> {target_planet.title} ({target_system.name})")

    elif args.cmd == "add-link":
        p_from = [p for p in target_system.planets if args.from_planet.lower() in p.title.lower()]
        p_to = [p for p in target_system.planets if args.to_planet.lower() in p.title.lower()]
        if p_from and p_to:
            target_system.add_link(p_from[0].id, p_to[0].id, label=args.label)
            store.save()
            print(f"[Helios] Link added: {p_from[0].title} -> {p_to[0].title}")


if __name__ == "__main__":
    main()
