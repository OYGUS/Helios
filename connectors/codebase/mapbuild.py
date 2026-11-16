"""Map building: the core plan text, the External Libraries module, file-level and
directory maps, and `build_code_map()` (it walks the directory once and fills a
`SolarSystem` in one of two modes).

**File-level map** — for small or flat projects: the layout is flat (fewer than two
directories with files, or almost everything at the root) OR the project has at most
`FILE_LEVEL_MAX_FILES` analysable code files (Markdown does not count as code).

* Each code file is one module (`kind="file"`, `file_path` = the file). Title = file
  name, or the relative path when two files share a name. `layer` comes from
  `_classify_file_layer()` (path/name tokens).
* Its sub-components are the definitions found in it (`kind` "function" / "class",
  with `signature`, `line`, `file_path`). Python classes additionally get their
  public methods as their own sub-components (`kind="method"`, title `Foo.bar()`)
  when the file has few definitions: a class with at most
  `METHOD_SATELLITE_MAX_PUBLIC` public methods is expanded, in source order, while the
  module's sub-component count stays at most `METHOD_SATELLITES_MAX_TOTAL`; otherwise
  methods stay listed inside the class sub-component. A Python file without
  definitions gets one `kind="constants"` sub-component with its top-level names.
  Module-level code that calls project definitions gets a `(module code)`
  sub-component. At most `SATELLITE_CAP` per module; truncation is stated.
* Links are file-to-file `depends_on` links, one per file pair, from resolved
  imports / includes / references. Labels name what is used when known:
  `imports islemler — calls gorev_yurut(), odak_modu()`.
* Markdown and small config/data files are listed (not parsed, except Markdown
  headings) in one "Docs & Data" module (`kind="layer"`, layer `docs`).
* At most `MAX_PLANETS` modules (including the Docs and External modules): the most
  significant files keep their own module (entry points first), the rest are listed
  in one "Other files" module (`kind="layer"`) whose sub-components are files.

**Directory map** — for larger projects with real directories: modules are the real
top-level directories (`kind="dir"`; root-level files form "Project Root"; a
dominant directory such as `src/` is descended into; the smallest are folded into
"Other"). Sub-components are the most significant files (`kind="file"`). File
imports are aggregated into module-to-module links labelled with the import count.
"""

from __future__ import annotations

import os
import posixpath
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.models import PlanetNode, SolarSystem
from connectors.codebase.constants import (
    CALL_ANALYSIS_MAX_CODE_FILES,
    CALL_ANALYSIS_MAX_FILE_BYTES,
    CODE_EXTENSIONS,
    DOCS_PLANET_TITLE,
    EXTERNAL_PLANET_TITLE,
    MAX_PLANETS,
    METHOD_SATELLITES_MAX_TOTAL,
    METHOD_SATELLITE_MAX_PUBLIC,
    MODULE_CODE_TITLE,
    OTHER_FILES_PLANET_TITLE,
    OTHER_PLANET_TITLE,
    ROOT_PLANET_TITLE,
    SATELLITE_CAP,
    SUMMARY_FILE_LIST_CAP,
)
from connectors.codebase.fileinfo import _Def, _FileInfo, _Method
from connectors.codebase.lang_common import _clip
from connectors.codebase.grouping import _Module, _group_modules, _majority_layer
from connectors.codebase.walk import _WalkExtras, _is_backup_dir, _should_skip_dir, _walk_project
from connectors.codebase.resolve import (
    _PathIndex,
    _detect_entries,
    _read_alias_configs,
    _resolve_html_refs,
    _resolve_js_imports,
    _resolve_php_includes,
    _resolve_php_uses,
    _resolve_python_imports,
)
from connectors.codebase.calls import _resolve_calls
from connectors.codebase.libraries import (
    _Manifests,
    _collect_externals,
    _collect_manifests,
    _detect_project_technologies,
)
from connectors.codebase.content import (
    _Analysis,
    _SatRefs,
    _about_line,
    _bullets,
    _capped,
    _def_content,
    _defined_names,
    _defines_line,
    _dep_label,
    _edge_label,
    _external_split,
    _file_content,
    _file_head,
    _keep_only_real_links,
    _layer_color,
    _layer_title,
    _method_entry,
    _names_satellite_content,
    _plural,
    _ref_lines,
    _sat_title,
    _short_file_label,
    _significance,
    _source_label,
    _uses_lines,
)
from connectors.codebase.purpose import _PurposeInfo, _collect_purpose, purpose_line
from connectors.codebase.identity import _analysis_identity


# ---------------------------------------------------------------------------
# Core plan
# ---------------------------------------------------------------------------
def _core_plan_text(an: _Analysis, manifests: _Manifests, skipped_large: int, grouping_desc: str, link_desc: str,
                    purpose: Optional[_PurposeInfo] = None) -> str:
    """The generated core plan. It opens with the `Purpose:` line (the project's own
    words when found, otherwise "Not written yet" plus the capabilities implied by
    libraries), then entry points, libraries and the map facts. README text other than
    the intro is never copied here."""
    folder, files = an.folder, an.files
    purpose = purpose if purpose is not None else _collect_purpose(an, manifests)

    all_external: Set[str] = set()
    for f in files:
        all_external |= f.external_imports
    detected_types, tech_stack, _pkg_summary = _detect_project_technologies(folder, all_external, manifests)

    lines: List[str] = [
        purpose_line(purpose.purpose, "readme" if purpose.purpose else "", purpose.evidence, purpose.purpose_from)
    ]
    lines.append("")
    if an.entries:
        for rel, ev in an.entries:
            lines.append(f"Entry point: {rel} — {ev}")
    else:
        lines.append("Entry point: none found (no `__main__` block, package.json main/bin/start, index.php or index.html)")
    if an.externals:
        lines.append(f"External libraries: {_capped([e.name for e in an.externals], 8)} — see '{EXTERNAL_PLANET_TITLE}'")
    if detected_types:
        lines.append("Type (from declared dependencies): " + ", ".join(detected_types))
    if tech_stack:
        lines.append(_bullets("Technologies", tech_stack, 8))
    lines.append("")
    lines.append(f"Map: {grouping_desc}")
    lines.append(f"Links: {link_desc}")
    n_rel = len(an.relations)
    call_line = f"Calls: {_plural(n_rel, 'resolved call relation')} between definitions (Python via ast; JS/TS and PHP name-based, limited to imported / included names)"
    lines.append(call_line)
    if an.extras.calls_disabled:
        lines.append(f"Call analysis skipped: the project has more than {CALL_ANALYSIS_MAX_CODE_FILES:,} code files")
    elif an.extras.calls_skipped_large:
        lines.append(f"Call analysis skipped for {_plural(an.extras.calls_skipped_large, 'file')} larger than {CALL_ANALYSIS_MAX_FILE_BYTES // 1000} KB")
    if an.alias_hits:
        lines.append(f"Path aliases: {_plural(an.alias_hits, 'import')} resolved through tsconfig/jsconfig paths or the Vite `@/` alias")
    if an.case_hits:
        lines.append(f"Case-insensitive matches: {_plural(an.case_hits, 'path')} matched only when ignoring letter case (Windows-style)")

    lang_counts = Counter(f.lang for f in files)
    total_lines = sum(f.lines for f in files)
    code_n = sum(1 for f in files if f.is_code)
    lines.append(f"Size: {_plural(code_n, 'code file')}, {_plural(len(files) - code_n, 'Markdown file')}, ~{total_lines:,} lines")
    lines.append("Languages: " + ", ".join(f"{k} ({v})" for k, v in lang_counts.most_common()))
    methods = Counter(f.method or "unparsed" for f in files)
    analysis = (
        f"Analysis: {methods.get('ast', 0)} Python files parsed with ast, "
        f"{methods.get('regex', 0)} files scanned with pattern matching, {methods.get('markdown', 0)} Markdown files"
    )
    if skipped_large:
        analysis += f", {skipped_large} counted but not parsed (size/count limits)"
    lines.append(analysis)
    lines.extend(purpose.facts)
    lines.append(f"Folder: {folder}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# External Libraries module (both modes)
# ---------------------------------------------------------------------------
def _add_external_planet(system: SolarSystem, an: _Analysis, title_of: Dict[str, str]) -> Optional[PlanetNode]:
    exts = an.externals
    if not exts:
        return None
    declared = sum(1 for e in exts if e.declared)
    eco = Counter(e.ecosystem for e in exts)
    summary = [
        f"{EXTERNAL_PLANET_TITLE} — {_plural(len(exts), 'third-party package')} used by the code",
        "Ecosystems: " + ", ".join(f"{k} ({v})" for k, v in eco.most_common()),
        f"Declared in a manifest: {declared} · imported but undeclared: {len(exts) - declared}",
        "Manifests read: requirements*.txt, pyproject.toml, Pipfile, setup.py, package.json, composer.json (root and direct sub-folders).",
        "Sorted by number of importing files.",
        "Found from import / use statements (Python ast; JS/TS and PHP pattern matching); standard library and Node built-ins excluded.",
        "No links are drawn to this module; each file lists its own external imports.",
    ]
    shown = exts[:SATELLITE_CAP]
    if len(exts) > len(shown):
        summary.append(f"+{len(exts) - len(shown)} more packages not shown as sub-components: " + _capped([e.name for e in exts[len(shown):]], 20))
    planet = system.add_planet(title=EXTERNAL_PLANET_TITLE, summary="\n".join(summary), color=_layer_color("integration"), layer="integration")
    planet.kind = "external"
    for e in shown:
        lines = [f"{e.name} — {e.ecosystem} package"]
        if e.importers:
            lines.append(f"Imported by ({len(e.importers)}): " + _capped([title_of.get(r, r) for r in sorted(e.importers)], 8))
        elif e.ecosystem == "Composer":
            lines.append("Used by: no `use` statement mapped to it (Composer packages are autoloaded)")
        if e.declared:
            lines.append(f"Declared in: {e.declared}" + (f" ({e.spec})" if e.spec else " (no version spec)"))
        else:
            lines.append("Declared: no (imported but not in any manifest)")
        if e.note:
            lines.append(f"Note: {e.note}")
        sat = planet.add_satellite(title=e.name, content="\n".join(lines), visual_tag="code")
        sat.kind = "package"
        sat.signature = e.spec
    return planet


# ---------------------------------------------------------------------------
# File-level map (small or flat projects)
# ---------------------------------------------------------------------------
def _file_planet_summary(f: _FileInfo, an: _Analysis, title_of: Dict[str, str], hidden: List[str], expanded: List[str], class_count: int) -> str:
    lines = [_file_head(f)]
    about = _about_line(f)
    if about:
        lines.append(about)
    defines = _defines_line(f)
    if defines:
        lines.append(defines)
    if expanded:
        lines.append("Methods shown as sub-components: " + ", ".join(expanded))
    elif class_count and f.method == "ast":
        lines.append(
            f"Methods are listed inside the class sub-components (shown separately only for classes with at most "
            f"{METHOD_SATELLITE_MAX_PUBLIC} public methods while the module has at most {METHOD_SATELLITES_MAX_TOTAL} sub-components)"
        )
    if hidden:
        lines.append(f"Not shown as sub-components: +{len(hidden)} more definitions ({_capped(hidden, 20)})")
    lines.extend(_uses_lines(f, an, title_of))
    std, third = _external_split(f)
    if third:
        lines.append("External: " + _capped(third, 20))
    if std:
        lines.append(("Standard library: " if f.ext == ".py" else "Built-in modules: ") + _capped(std, 20))
    if f.unresolved_includes:
        lines.append(f"Unresolved includes: {f.unresolved_includes} (dynamic paths or files not found)")
    ev = an.entry_evidence(f.rel)
    if ev:
        lines.append(f"Entry point: {ev}")
    if f.note:
        lines.append(f"Note: {f.note}")
    if f.calls_note:
        lines.append(f"Note: {f.calls_note}")
    lines.append(f"Layer: {_layer_title(f.layer)} (from the file path)")
    return "\n".join(lines)


def _file_significance(f: _FileInfo) -> float:
    return _significance(f) + len(f.deps) * 3


def _plan_file_satellites(f: _FileInfo, an: _Analysis) -> Tuple[List[Tuple[str, str, str, Optional[_Def], Optional[_Method]]], List[str], List[str]]:
    """[(key, title, kind, def, method)], hidden definition titles, expanded classes."""
    has_mod_calls = an.has_module_calls(f.rel)
    defs = f.defs[: SATELLITE_CAP - (1 if has_mod_calls else 0)]
    hidden = [d.title for d in f.defs[len(defs) :]]
    base_count = len(defs) + (1 if has_mod_calls else 0) + (1 if (not f.defs and f.top_names) else 0)
    expanded: Set[str] = set()
    if f.method == "ast":
        count = base_count
        for d in defs:
            if d.kind != "class":
                continue
            pub = [m for m in d.method_infos if m.vis == "public"]
            if 0 < len(pub) <= METHOD_SATELLITE_MAX_PUBLIC and count + len(pub) <= METHOD_SATELLITES_MAX_TOTAL:
                expanded.add(d.name)
                count += len(pub)
    plan: List[Tuple[str, str, str, Optional[_Def], Optional[_Method]]] = []
    for d in defs:
        kind = "function" if d.kind == "function" else "class"
        plan.append((d.key, d.title, kind, d, None))
        if d.name in expanded:
            for m in d.method_infos:
                if m.vis == "public":
                    plan.append((f"m:{d.name}.{m.name}", f"{d.name}.{m.name}()", "method", d, m))
    if not f.defs and f.top_names:
        plan.append(("names", f"Top-level names ({len(f.top_names)})", "constants", None, None))
    if has_mod_calls:
        plan.append(("mod", MODULE_CODE_TITLE, "function", None, None))
    exp_titles = [f"{d.name} ({sum(1 for m in d.method_infos if m.vis == 'public')})" for d in defs if d.name in expanded]
    return plan, hidden, exp_titles


def _build_file_level(system: SolarSystem, an: _Analysis, data_files: List[Tuple[str, int]]) -> Tuple[str, str]:
    """Fills `system` with one module per code file. Returns (grouping, link) descriptions."""
    folder, project_name = an.folder, an.project_name
    code = [f for f in an.files if f.is_code]
    docs = [f for f in an.files if not f.is_code]
    has_docs_planet = bool(docs or data_files)
    slots = MAX_PLANETS - (1 if has_docs_planet else 0) - (1 if an.externals else 0)
    entry_rels = {r for r, _ in an.entries}
    if len(code) > slots:
        ranked = sorted(code, key=lambda f: (f.rel not in entry_rels, -_file_significance(f), f.rel))
        as_planets = sorted(ranked[: slots - 1], key=lambda f: f.rel)
        overflow = sorted(ranked[slots - 1 :], key=lambda f: f.rel)
    else:
        as_planets, overflow = sorted(code, key=lambda f: f.rel), []

    name_counts = Counter(f.name for f in code)
    title_of: Dict[str, str] = {f.rel: (f.name if name_counts[f.name] == 1 else f.rel) for f in code}
    planet_of: Dict[str, PlanetNode] = {}
    refs = _SatRefs()
    pending: List[Tuple] = []  # (satellite, kind, payload...) filled after call refs are known

    for f in as_planets:
        plan, hidden, expanded = _plan_file_satellites(f, an)
        class_count = sum(1 for d in f.defs if d.kind != "function")
        planet = system.add_planet(
            title=title_of[f.rel],
            summary=_file_planet_summary(f, an, title_of, hidden, expanded, class_count),
            color=_layer_color(f.layer),
            layer=f.layer,
        )
        planet.kind = "file"
        planet.file_path = str(folder / f.rel)
        planet.is_entry = f.rel in entry_rels
        planet.defined_names = _defined_names(f)
        planet_of[f.rel] = planet
        for key, title, kind, d, m in plan:
            sat = planet.add_satellite(title=title, content="", visual_tag="code", file_path=str(folder / f.rel))
            sat.kind = kind
            if m is not None:
                sat.signature, sat.line = _method_entry(m), m.lineno
            elif d is not None:
                sat.signature, sat.line = d.signature, d.lineno
            if key != "names":
                refs.by_key[(f.rel, key)] = (planet, title)
            pending.append((sat, planet, f, key, d, m))

    if overflow:
        layer = _majority_layer(overflow)
        shown = sorted(overflow, key=lambda f: (-_file_significance(f), f.rel))[:SATELLITE_CAP]
        summary = [
            f"{OTHER_FILES_PLANET_TITLE} — {_plural(len(overflow), 'code file')} that did not fit as separate modules",
            f"At most {MAX_PLANETS} modules are shown; entry points and the files with the most imports, definitions and lines got their own module.",
            _bullets("Files", [f"{f.rel} ({f.lang}, {_plural(f.lines, 'line')})" for f in overflow], SUMMARY_FILE_LIST_CAP),
        ]
        if len(overflow) > len(shown):
            summary.append(f"+{len(overflow) - len(shown)} more files not shown as sub-components.")
        summary.append("Links to and from these files come from resolved imports and point to this module.")
        other = system.add_planet(title=OTHER_FILES_PLANET_TITLE, summary="\n".join(summary), color=_layer_color(layer), layer=layer)
        other.kind = "layer"
        for f in overflow:
            planet_of[f.rel] = other
        for f in shown:
            sat = other.add_satellite(title=title_of[f.rel], content="", visual_tag="code" if f.defs else "diagram", file_path=str(folder / f.rel))
            sat.kind = "file"
            sat.defined_names = _defined_names(f)
            refs.file_level[f.rel] = (other, title_of[f.rel])
            pending.append((sat, other, f, "file", None, None))

    if has_docs_planet:
        entries: List[Tuple[str, str, str, str]] = []  # (title, content, abs path, kind)
        for f in sorted(docs, key=lambda f: f.rel):
            body = [f"{f.name} — Markdown document, {f.size:,} bytes"]
            if f.doc:
                body.append("About: " + _clip(f.doc, 400))
            if f.headings:
                body.append(_bullets("Headings", f.headings))
            body.append(f"Path: {f.rel}")
            entries.append((f.rel, "\n".join(body), str(folder / f.rel), "document"))
        for rel, size in sorted(data_files):
            entries.append((rel, f"{posixpath.basename(rel)} — config / data file, {size:,} bytes\nListed only; its contents are not parsed.\nPath: {rel}", str(folder / rel), "document"))
        listing = [f"{f.rel} ({f.size:,} bytes)" for f in sorted(docs, key=lambda f: f.rel)] + [f"{r} ({s:,} bytes)" for r, s in sorted(data_files)]
        summary = (
            f"{DOCS_PLANET_TITLE} — {_plural(len(docs), 'Markdown file')}, {_plural(len(data_files), 'config / data file')}\n"
            "Markdown headings are extracted; config/data files are only listed with their size.\n"
            + _bullets("Files", listing, SUMMARY_FILE_LIST_CAP)
        )
        if len(entries) > SATELLITE_CAP:
            summary += f"\n+{len(entries) - SATELLITE_CAP} more files not shown as sub-components."
        dp = system.add_planet(title=DOCS_PLANET_TITLE, summary=summary, color=_layer_color("docs"), layer="docs")
        dp.kind = "layer"
        for title, content, path, kind in entries[:SATELLITE_CAP]:
            sat = dp.add_satellite(title=title, content=content, visual_tag="diagram", file_path=path)
            sat.kind = kind

    _add_external_planet(system, an, title_of)

    refs.fill(an.relations)
    for sat, planet, f, key, d, m in pending:
        calls, called_by, call_lines = _ref_lines(refs, _SatRefs.ref(planet, sat.title))
        sat.calls, sat.called_by = calls, called_by
        if key == "file":
            sat.content = _file_content(f, an, call_lines, title_of)
        elif key == "names":
            sat.content = _names_satellite_content(f)
        elif key == "mod":
            sat.content = "\n".join(
                [f"Module-level code of {f.name} (statements outside functions and classes, incl. any `if __name__ == \"__main__\"` block)"]
                + call_lines + [f"Location: {f.rel}", "Source: " + _source_label(f)]
            )
        else:
            sat.content = _def_content(d, f, an, call_lines, method=m)

    pairs_by_link: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
    resolved = 0
    for f in code:
        for dep in sorted(f.deps):
            resolved += 1
            sp, dp_ = planet_of.get(f.rel), planet_of.get(dep)
            if sp is None or dp_ is None or sp is dp_:
                continue
            pairs_by_link[(sp.id, dp_.id)].append((f.rel, dep))
    by_rel = an.by_rel
    for (sid, tid), pairs in pairs_by_link.items():
        if len(pairs) == 1:
            a, b = pairs[0]
            label = _dep_label(by_rel[a], b, an.calls_between(a, b))
            if planet_of[a].kind != "file" or planet_of[b].kind != "file":
                label = f"{_short_file_label(a)} → {_short_file_label(b)}: {label}"
        else:
            label = _edge_label(pairs)
        system.add_link(sid, tid, label=label, relation_type="depends_on")

    grouping = f"File-level map: every code file is one module ({_plural(len(code), 'code file')}; small or flat project)."
    if overflow:
        grouping += f" {len(overflow)} less significant files are listed in '{OTHER_FILES_PLANET_TITLE}'."
    if has_docs_planet:
        grouping += f" Markdown and config/data files are listed in '{DOCS_PLANET_TITLE}'."
    link_desc = f"{_plural(len(system.links), 'file link')} from {resolved} resolved project-local imports / includes / references"
    return grouping, link_desc


# ---------------------------------------------------------------------------
# Directory map (larger projects with real directories)
# ---------------------------------------------------------------------------
def _module_summary(mod: "_Module", an: _Analysis, shown: List[_FileInfo], deps_out: Dict[str, int], deps_in: Dict[str, int]) -> str:
    n = len(mod.files)
    total_lines = sum(f.lines for f in mod.files)
    if mod.kind == "dir":
        head = f"{mod.key}/ — directory, {_plural(n, 'file')}, ~{total_lines:,} lines"
    elif mod.kind == "root":
        head = f"{ROOT_PLANET_TITLE} — files directly in the project folder, {_plural(n, 'file')}, ~{total_lines:,} lines"
    else:
        head = f"{OTHER_PLANET_TITLE} — smaller directories folded together, {_plural(n, 'file')}, ~{total_lines:,} lines"
    lines = [head]
    langs = Counter(f.lang for f in mod.files)
    lines.append("Languages: " + ", ".join(f"{k} ({v})" for k, v in langs.most_common()))
    for f in mod.files:
        ev = an.entry_evidence(f.rel)
        if ev:
            lines.append(f"Entry point: {f.rel} — {ev}")
    if mod.folded:
        lines.append("Folded: " + _capped([f"{t} ({_plural(c, 'file')})" for t, c in mod.folded], 30))
    if deps_out:
        lines.append(_bullets("Uses", [f"{t} ({_plural(c, 'import')})" for t, c in sorted(deps_out.items(), key=lambda x: -x[1])]))
    if deps_in:
        lines.append(_bullets("Used by", [f"{t} ({_plural(c, 'import')})" for t, c in sorted(deps_in.items(), key=lambda x: -x[1])]))
    third = sorted({x for f in mod.files for x in _external_split(f)[1]})
    if third:
        lines.append("External: " + _capped(third, 20))
    names = sorted(f.rel for f in mod.files)
    lines.append(_bullets(f"Files ({n})", names, SUMMARY_FILE_LIST_CAP))
    hidden = n - len(shown)
    if hidden > 0:
        lines.append(
            f"+{hidden} more files not shown as sub-components (only the {len(shown)} most significant "
            "files by import fan-in, definitions and size are shown)."
        )
    layer = mod.layer
    votes = Counter(f.layer for f in mod.files)
    lines.append(f"Layer: {_layer_title(layer)} ({votes.get(layer, 0)} of {n} files, from path names)")
    methods = Counter(f.method or "unparsed" for f in mod.files)
    how = []
    if methods.get("ast"):
        how.append(f"{_plural(methods['ast'], 'Python file')} parsed with ast")
    if methods.get("regex"):
        how.append(f"{_plural(methods['regex'], 'file')} scanned with regex pattern matching")
    if methods.get("markdown"):
        how.append(_plural(methods["markdown"], "Markdown file"))
    if methods.get("unparsed"):
        how.append(f"{_plural(methods['unparsed'], 'file')} not parsed (too large or unreadable)")
    lines.append("Source: " + "; ".join(how))
    return "\n".join(lines)


def _build_dir_level(system: SolarSystem, an: _Analysis) -> Tuple[str, str]:
    folder, files = an.folder, an.files
    modules, grouping_desc = _group_modules(files, MAX_PLANETS - (1 if an.externals else 0))
    module_of: Dict[str, _Module] = {}
    for m in modules:
        for f in m.files:
            module_of[f.rel] = m

    edge_pairs: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
    resolved_imports = 0
    for f in files:
        for d in sorted(f.deps):
            resolved_imports += 1
            src_m, dst_m = module_of[f.rel], module_of[d]
            if src_m.key != dst_m.key:
                edge_pairs[(src_m.key, dst_m.key)].append((f.rel, d))

    deps_out: Dict[str, Dict[str, int]] = defaultdict(dict)
    deps_in: Dict[str, Dict[str, int]] = defaultdict(dict)
    title_of_mod = {m.key: m.title for m in modules}
    for (s, d), pairs in edge_pairs.items():
        cnt = len(set(pairs))
        deps_out[s][title_of_mod[d]] = cnt
        deps_in[d][title_of_mod[s]] = cnt

    entry_rels = {r for r, _ in an.entries}
    planet_of: Dict[str, PlanetNode] = {}
    refs = _SatRefs()
    pending: List[Tuple] = []
    file_title: Dict[str, str] = {f.rel: f.rel for f in files}
    for mod in modules:
        ranked = sorted(mod.files, key=lambda f: (f.rel not in entry_rels, -_significance(f), f.rel))
        shown = ranked[:SATELLITE_CAP]
        layer = mod.layer
        planet = system.add_planet(
            title=mod.title,
            summary=_module_summary(mod, an, shown, deps_out.get(mod.key, {}), deps_in.get(mod.key, {})),
            color=_layer_color(layer),
            layer=layer,
        )
        if mod.kind == "dir":
            planet.kind, planet.file_path = "dir", str(folder / mod.key)
        elif mod.kind == "root":
            planet.kind, planet.file_path = "dir", str(folder)
        else:
            planet.kind = "layer"
        planet.is_entry = any(f.rel in entry_rels for f in mod.files)
        planet_of[mod.key] = planet
        name_counts = Counter(f.name for f in shown)
        dup_names = {n for n, c in name_counts.items() if c > 1}
        for f in shown:
            title = _sat_title(f, mod, dup_names)
            sat = planet.add_satellite(title=title, content="", visual_tag="code" if (f.classes or f.functions) else "diagram", file_path=str(folder / f.rel))
            sat.kind = "document" if f.ext == ".md" else "file"
            sat.defined_names = _defined_names(f)
            refs.file_level[f.rel] = (planet, title)
            pending.append((sat, planet, f))

    _add_external_planet(system, an, file_title)
    refs.fill(an.relations)
    for sat, planet, f in pending:
        calls, called_by, call_lines = _ref_lines(refs, _SatRefs.ref(planet, sat.title))
        sat.calls, sat.called_by = calls, called_by
        sat.content = _file_content(f, an, call_lines, file_title)

    for (s, d), pairs in sorted(edge_pairs.items(), key=lambda kv: (title_of_mod[kv[0][0]], title_of_mod[kv[0][1]])):
        system.add_link(planet_of[s].id, planet_of[d].id, label=_edge_label(pairs), relation_type="depends_on")
    link_desc = f"{_plural(len(edge_pairs), 'module link')} from {resolved_imports} resolved project-local imports / includes / references"
    return grouping_desc, link_desc


@dataclass
class CodeMapStats:
    """What `build_code_map()` found. `mode` is "file", "dir" or "" (nothing analysable)."""

    mode: str = ""
    files: int = 0  # analysable files (code + Markdown)
    code_files: int = 0
    data_files: int = 0
    lines: int = 0
    planets: int = 0
    links: int = 0
    core_plan: str = ""
    calls: int = 0  # resolved call relations between definitions
    entries: List[str] = field(default_factory=list)  # entry point files (relative paths)
    externals: int = 0  # third-party packages listed in the External Libraries module
    purpose: str = ""  # the project's own words (description / README intro), or ""
    purpose_evidence: str = ""  # assembled code evidence (also written to the system)
    purpose_items: List[str] = field(default_factory=list)  # its labelled lines (for compose_purpose_evidence)
    backup_dirs: List[str] = field(default_factory=list)  # backup-named folders that were skipped
    identity: str = ""  # the identity line (also written to `system.identity`), see identity_line()


def count_code_files(folder: Path, limit: int = 1000) -> int:
    """Counts analysable code files (Markdown excluded) under `folder` with the same
    pruning as the full walk, without reading them; stops at `limit`."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(str(folder), topdown=True, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if not _should_skip_dir(d, filenames) and not _is_backup_dir(d) and not os.path.islink(os.path.join(dirpath, d))
        ]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if not fn.startswith(".") and ext in CODE_EXTENSIONS and ext != ".md":
                count += 1
                if count >= limit:
                    return count
    return count


def build_code_map(folder: Path, system: SolarSystem) -> CodeMapStats:
    """Analyses `folder` and appends the resulting modules (planets), sub-components and
    import links to `system` (expected to have no planets yet). Does not save, does not
    look up or choose the system's identity, and does not set `system.core_plan`; the
    generated core plan text is returned in `CodeMapStats.core_plan`."""
    folder = Path(folder)
    project_name = system.project_name or folder.name or str(folder)
    data_files: List[Tuple[str, int]] = []
    extras = _WalkExtras()
    files, skipped_large = _walk_project(folder, data_files, extras)
    stats = CodeMapStats(files=len(files), code_files=sum(1 for f in files if f.is_code), data_files=len(data_files))
    if not files:
        return stats
    stats.lines = sum(f.lines for f in files)

    rels = {f.rel for f in files}
    by_rel = {f.rel: f for f in files}
    index = _PathIndex(rels)
    package_dirs = [posixpath.dirname(c) for c in extras.configs if posixpath.basename(c).lower() == "package.json"]
    manifests = _collect_manifests(folder, package_dirs)
    aliases = _read_alias_configs(folder, extras.configs)
    src_dirs = {"/".join(r.split("/")[: i + 1]) for r in rels for i, p in enumerate(r.split("/")[:-1]) if p == "src"}

    _resolve_python_imports(files, rels)
    alias_hits = _resolve_js_imports(files, rels, index, aliases, src_dirs)
    _resolve_php_includes(files, index)
    php_unresolved = _resolve_php_uses(files)
    _resolve_html_refs(files, index)
    for f in files:
        for d in f.deps:
            by_rel[d].importers.add(f.rel)

    relations, py_bases = (set(), {}) if extras.calls_disabled else _resolve_calls(files, rels)
    an = _Analysis(
        folder=folder,
        project_name=project_name,
        files=files,
        by_rel=by_rel,
        relations=relations,
        py_bases=py_bases,
        entries=_detect_entries(files, by_rel, manifests.packages, index),
        externals=_collect_externals(files, manifests, php_unresolved),
        extras=extras,
        alias_hits=alias_hits,
        case_hits=index.case_hits,
    )

    if extras.detail:  # == code files <= FILE_LEVEL_MAX_FILES or a flat layout (decided by the walk)
        stats.mode = "file"
        grouping, link_desc = _build_file_level(system, an, data_files)
    else:
        stats.mode = "dir"
        grouping, link_desc = _build_dir_level(system, an)
    _keep_only_real_links(system)
    stats.planets = len(system.planets)
    stats.links = len(system.links)
    stats.calls = len(relations)
    stats.entries = [r for r, _ in an.entries]
    stats.externals = len(an.externals)
    purpose = _collect_purpose(an, manifests)
    stats.purpose, stats.purpose_evidence, stats.purpose_items = purpose.purpose, purpose.evidence, list(purpose.items)
    stats.backup_dirs = list(extras.backup_dirs)
    stats.core_plan = _core_plan_text(an, manifests, skipped_large, grouping, link_desc, purpose)
    stats.identity = system.identity = _analysis_identity(an, manifests)
    # The fresh system gets the deterministic results; refreshes go through merge_purpose().
    system.purpose_evidence = purpose.evidence
    if system.purpose_source != "user":
        if purpose.purpose:
            system.purpose, system.purpose_source = purpose.purpose, "readme"
            system.purpose_origin = purpose.origin
        elif system.purpose_source != "ai" or not system.purpose.strip():
            system.purpose, system.purpose_source = "", ""
    return stats
