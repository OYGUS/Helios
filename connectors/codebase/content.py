"""Text helpers, the analysis result shared by both map modes (`_Analysis`) and sub-component content.
"""

from __future__ import annotations

import os
import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from core.models import LAYER_DEFINITIONS, PlanetNode, SolarSystem
from connectors.codebase.constants import CALL_REF_CAP, DEFINED_NAMES_CAP, _STDLIB_MODULES
from connectors.codebase.fileinfo import _Def, _FileInfo, _Method
from connectors.codebase.lang_common import _clip
from connectors.codebase.grouping import _Module
from connectors.codebase.walk import _WalkExtras
from connectors.codebase.calls import Relation
from connectors.codebase.libraries import _External


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def _significance(f: _FileInfo) -> float:
    return len(f.importers) * 5 + f.def_count * 2 + min(f.lines, 3000) / 60.0 + (2 if f.doc else 0)


def _plural(n: int, word: str, plural: str = "") -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _capped(items: Sequence[str], cap: int, sep: str = ", ") -> str:
    items = list(items)
    text = sep.join(items[:cap])
    if len(items) > cap:
        text += f"{sep if sep.strip() == '' else ' '}+{len(items) - cap} more"
    return text


def _bullets(title: str, items: Sequence[str], cap: int = 12) -> str:
    items = list(items)
    lines = [f"{title}:"] + [f"• {x}" for x in items[:cap]]
    if len(items) > cap:
        lines.append(f"• +{len(items) - cap} more")
    return "\n".join(lines)


def _source_label(f: _FileInfo) -> str:
    return {
        "ast": "Python ast",
        "regex": "regex pattern matching, may miss or misidentify definitions",
        "markdown": "Markdown headings",
    }.get(f.method, "not parsed")


def _how_defs_found(f: _FileInfo) -> str:
    if f.method == "ast":
        return "found with the Python `ast` module"
    if f.method == "regex":
        return "found by regular-expression pattern matching (may miss or misidentify definitions)"
    return "not parsed"


def _file_head(f: _FileInfo) -> str:
    size = _plural(f.lines, "line") if f.lines else f"{f.size:,} bytes"
    return f"{f.name} — {f.lang} file, {size}"


def _about_line(f: _FileInfo) -> str:
    if f.ext == ".py" and f.doc:
        return "About: " + _clip(f.doc, 300)
    if f.lead_comment:
        return "About (first comment): " + _clip(f.lead_comment, 300)
    if f.doc:
        return "About (leading comment): " + _clip(f.doc, 300)
    return ""


def _defines_line(f: _FileInfo) -> str:
    if f.defs:
        funcs = sum(1 for d in f.defs if d.kind == "function")
        types = len(f.defs) - funcs
        word = "class" if f.method == "ast" else "type"
        return f"Defines: {_plural(funcs, 'function')}, {_plural(types, word, word + ('es' if word == 'class' else 's'))} — {_source_label(f)}"
    if f.top_names:
        return f"Defines: no functions or classes; {_plural(len(f.top_names), 'top-level name')} — Python ast"
    if f.method in ("ast", "regex"):
        return f"Defines: nothing found — {_source_label(f)}"
    return ""


def _external_split(f: _FileInfo) -> Tuple[List[str], List[str]]:
    """(standard-library / built-in modules, third-party package names) imported by `f`."""
    if f.ext == ".py":
        std = sorted(n for n in f.external_imports if n in _STDLIB_MODULES)
        third = sorted(n for n in f.external_imports if n not in _STDLIB_MODULES)
        return std, third
    return sorted(f.builtin_imports), sorted(f.external_imports)


def _key_display(key: str) -> str:
    if key == "mod":
        return "(module code)"
    kind, _, name = key.partition(":")
    return f"{name}()"


def _dep_label(f: _FileInfo, target: str, called: Sequence[str] = ()) -> str:
    """`imports islemler — calls gorev_yurut(), odak_modu()` style label."""
    mod, names = f.dep_labels.get(target, (target, []))
    verb = f.dep_verbs.get(target, "imports")
    names = [n for n in names if n and n != "*"]
    if called:
        label = f"{verb} {mod} — calls " + ", ".join(called[:3]) + (f" +{len(called) - 3}" if len(called) > 3 else "")
    else:
        label = f"{verb} {mod}"
        if names:
            label += f" ({', '.join(names[:4])}{', …' if len(names) > 4 else ''})"
    return label


def _import_label(module_text: str, names: Sequence[str]) -> str:
    label = f"imports {module_text}"
    names = [n for n in names if n and n != "*"]
    if names:
        label += f" ({', '.join(names[:4])}{', …' if len(names) > 4 else ''})"
    return label


def _short_file_label(rel: str) -> str:
    base = posixpath.basename(rel)
    parent = posixpath.basename(posixpath.dirname(rel))
    if parent and (base.startswith("__init__") or base.startswith("index.")):
        return f"{parent}/{base}"
    return base


def _edge_label(pairs: List[Tuple[str, str]]) -> str:
    uniq = list(dict.fromkeys(pairs))
    n = len(uniq)
    shown = list(dict.fromkeys(f"{_short_file_label(a)} → {_short_file_label(b)}" for a, b in uniq))
    examples = ", ".join(shown[:2])
    more = ", …" if n > 2 else ""
    return f"{n} import{'s' if n != 1 else ''} ({examples}{more})"


def _sat_title(f: _FileInfo, mod: "_Module", dup_names: Set[str]) -> str:
    if f.name not in dup_names:
        return f.name
    if mod.kind == "dir" and f.rel.startswith(mod.key + "/"):
        return f.rel[len(mod.key) + 1 :]
    return f.rel


def _normalized_path_key(path: str) -> str:
    """Identity key for a folder: normalised absolute path (case-folded on Windows)."""
    if not path:
        return ""
    try:
        p = os.path.abspath(os.path.expanduser(path))
    except Exception:
        p = path
    return os.path.normcase(os.path.normpath(p))


def _keep_only_real_links(system: SolarSystem) -> None:
    """Runs the model's layout/tour rebuild but discards any links it invents.

    `SolarSystem.rebuild_links_and_tour()` (core/models.py) adds default layer links
    whenever a system has no links; imported maps must only show real imports."""
    real = list(system.links)
    system.rebuild_links_and_tour()
    system.links = real


def _layer_color(layer: str) -> str:
    return LAYER_DEFINITIONS.get(layer, ("", "#4EA8DE"))[1]


def _layer_title(layer: str) -> str:
    return LAYER_DEFINITIONS.get(layer, (layer, ""))[0]


def _list_block(title: str, items: Sequence[str], cap: int = 12) -> str:
    return "\n" + _bullets(title, items, cap)


# ---------------------------------------------------------------------------
# Analysis result shared by both map modes
# ---------------------------------------------------------------------------
@dataclass
class _Analysis:
    folder: Path
    project_name: str
    files: List[_FileInfo]
    by_rel: Dict[str, _FileInfo]
    relations: Set[Relation]
    py_bases: Dict[Tuple[str, str], List[Tuple[str, str]]]
    entries: List[Tuple[str, str]]
    externals: List[_External]
    extras: _WalkExtras
    alias_hits: int = 0
    case_hits: int = 0
    _pair_calls: Dict[Tuple[str, str], Set[str]] = field(default_factory=dict)
    _mod_callers: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        pairs: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        for s, sk, d, k in self.relations:
            pairs[(s, d)].add(_key_display(k))
            if sk == "mod":
                self._mod_callers.add(s)
        self._pair_calls = dict(pairs)
        self._out: Dict[str, List[str]] = defaultdict(list)
        self._in: Dict[str, List[str]] = defaultdict(list)
        for s, d in self._pair_calls:
            if s != d:
                self._out[s].append(d)
                self._in[d].append(s)

    def entry_evidence(self, rel: str) -> str:
        return next((ev for r, ev in self.entries if r == rel), "")

    def calls_between(self, src: str, dst: str) -> List[str]:
        return sorted(self._pair_calls.get((src, dst), ()))

    def has_module_calls(self, rel: str) -> bool:
        return rel in self._mod_callers

    def callees_of(self, rel: str) -> List[str]:
        return sorted(self._out.get(rel, ()))

    def callers_of(self, rel: str) -> List[str]:
        return sorted(self._in.get(rel, ()))


class _SatRefs:
    """Maps (file, definition key) to "<module title>::<sub-component title>" references
    and collects the resolved call lists for each sub-component."""

    def __init__(self) -> None:
        self.by_key: Dict[Tuple[str, str], Tuple[PlanetNode, str]] = {}
        self.file_level: Dict[str, Tuple[PlanetNode, str]] = {}  # file -> file sub-component
        self.calls: Dict[str, Set[str]] = defaultdict(set)
        self.called_by: Dict[str, Set[str]] = defaultdict(set)

    @staticmethod
    def ref(planet: PlanetNode, title: str) -> str:
        return f"{planet.title}::{title}"

    def lookup(self, file: str, key: str) -> Optional[str]:
        hit = self.by_key.get((file, key))
        if hit is None and key.startswith("m:"):
            hit = self.by_key.get((file, "c:" + key[2:].split(".")[0]))
        if hit is None:
            hit = self.file_level.get(file)
        return self.ref(*hit) if hit else None

    def fill(self, relations: Set[Relation]) -> None:
        for sf, sk, tf, tk in relations:
            a, b = self.lookup(sf, sk), self.lookup(tf, tk)
            if a and b and a != b:
                self.calls[a].add(b)
                self.called_by[b].add(a)


def _ref_lines(refs: _SatRefs, ref: str, text_cap: int = 8) -> Tuple[List[str], List[str], List[str]]:
    calls = sorted(refs.calls.get(ref, ()))
    called_by = sorted(refs.called_by.get(ref, ()))
    lines = []
    if calls:
        lines.append("Calls: " + _capped(calls, text_cap))
    if called_by:
        lines.append("Called by: " + _capped(called_by, text_cap))
    return calls[:CALL_REF_CAP], called_by[:CALL_REF_CAP], lines


# ---------------------------------------------------------------------------
# Sub-component content
# ---------------------------------------------------------------------------
def _method_entry(m: _Method) -> str:
    marks = [f"@{x}" for x in m.flags if x in ("staticmethod", "classmethod", "property", "cached_property", "abstractmethod")]
    marks += [x for x in m.flags if x in ("static", "abstract", "get", "set")]
    sig = m.signature or f"{m.name}()"
    if m.vis in ("private", "protected", "dunder") and not sig.startswith(m.vis):
        sig = f"{sig} [{m.vis}]"
    return " ".join(marks + [sig])


def _def_content(d: _Def, f: _FileInfo, an: _Analysis, call_lines: List[str], method: Optional[_Method] = None) -> str:
    lines: List[str] = []
    if method is not None:
        lines.append(f"Signature: {_method_entry(method)}")
        if method.doc:
            lines.append(f"About: {method.doc}")
        lines.append(f"Method of: class {d.name} ({'public' if method.vis == 'public' else method.vis})")
        lines.extend(call_lines)
        lines.append(f"Location: {f.rel}, line {method.lineno}")
        lines.append(f"Source: {_source_label(f)}")
        return "\n".join(lines)
    if d.signature:
        lines.append(f"Signature: {d.signature}")
    if d.class_notes and d.kind != "function":
        lines.append("Kind: " + ", ".join(d.class_notes))
    if d.doc:
        lines.append(("About: " if d.doc_source == "docstring" else "Comment above: ") + d.doc)
    if d.decorators:
        lines.append("Decorators: " + _capped(d.decorators, 4))
    if d.exported is not None:
        lines.append("Exported: " + ("yes (default export)" if d.is_default else "yes" if d.exported else "no"))
    if d.kind != "function":
        if d.bases:
            resolved = {}
            for tf, tk in an.py_bases.get((f.rel, d.name), []):
                resolved[tk[2:]] = (tf, tk)
            parts = []
            for b in d.bases:
                short = b.split("[")[0].split(".")[-1]
                hit = resolved.get(short)
                if hit:
                    parts.append(f"{posixpath.basename(hit[0])}::class {short}")
                else:
                    parts.append(f"{b} (not in this project)" if f.method == "ast" else b)
            lines.append(("Inherits from: " if f.method == "ast" else "Extends: ") + ", ".join(parts))
        if d.implements:
            lines.append("Implements: " + ", ".join(d.implements))
        if d.fields:
            label = "Members" if "Enum" in d.class_notes else "Fields"
            lines.append(f"{label} ({len(d.fields)}): " + _capped(d.fields, 12))
        if d.method_infos:
            order = {"public": 0, "protected": 1, "private": 2, "dunder": 3}
            ms = sorted(d.method_infos, key=lambda m: (order.get(m.vis, 4), m.lineno))
            pub = sum(1 for m in ms if m.vis == "public")
            lines.append(f"Methods ({len(ms)}, {pub} public):")
            lines.extend(f"• {_method_entry(m)}" for m in ms[:12])
            if len(ms) > 12:
                lines.append(f"• +{len(ms) - 12} more")
    lines.extend(call_lines)
    lines.append(f"Location: {f.rel}, line {d.lineno}")
    lines.append(f"Source: {_source_label(f)}")
    return "\n".join(lines)


def _defined_names(f: _FileInfo, cap: int = DEFINED_NAMES_CAP) -> List[str]:
    """Every definition of `f` for search, unclipped (at most `cap`): `_Def.title` of each
    function / class-like definition, then "Class.method()" for its methods (dunder
    methods such as ``__init__`` are left out). Stored as `defined_names`."""
    out: List[str] = []
    seen: Set[str] = set()
    for d in f.defs:
        names = [d.title]
        if d.kind != "function":
            methods = [m.name for m in d.method_infos] or d.method_names or d.methods
            names += [f"{d.name}.{m}()" for m in methods if not (m.startswith("__") and m.endswith("__"))]
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
                if len(out) >= cap:
                    return out
    return out


def _file_content(f: _FileInfo, an: _Analysis, call_lines: List[str], title_of: Dict[str, str]) -> str:
    lines = [_file_head(f)]
    about = _about_line(f)
    if f.ext == ".md":
        about = "About: " + _clip(f.doc, 300) if f.doc else ""
    if about:
        lines.append(about)
    if f.defs:
        lines.append("Defines: " + _capped([d.title for d in f.defs], 8))
    elif f.headings:
        lines.append("Sections: " + _capped(f.headings, 8))
    if f.deps:
        lines.append("Uses: " + _capped([title_of.get(d, d) for d in sorted(f.deps)], 8))
    if f.importers:
        lines.append("Used by: " + _capped([title_of.get(i, i) for i in sorted(f.importers)], 8))
    out_calls = an.callees_of(f.rel)
    in_calls = an.callers_of(f.rel)
    if out_calls:
        lines.append("Calls into: " + _capped([f"{title_of.get(t, t)} ({_capped(an.calls_between(f.rel, t), 3)})" for t in out_calls], 6, "; "))
    if in_calls:
        lines.append("Called from: " + _capped([f"{title_of.get(s, s)} ({_capped(an.calls_between(s, f.rel), 3)})" for s in in_calls], 6, "; "))
    # `call_lines` would repeat "Calls into" / "Called from"; the references stay in calls / called_by.
    std, third = _external_split(f)
    if third:
        lines.append("External: " + _capped(third, 10))
    ev = an.entry_evidence(f.rel)
    if ev:
        lines.append(f"Entry point: {ev}")
    if f.note:
        lines.append(f"Note: {f.note}")
    lines.append(f"Path: {f.rel}")
    lines.append(f"Source: {_source_label(f)}")
    return "\n".join(lines)


def _names_satellite_content(f: _FileInfo, cap: int = 60) -> str:
    lines = [f"Top-level names of {f.name} (constants / settings); values are not evaluated."]
    lines += [f"• {n} (line {ln})" for n, ln in f.top_names[:cap]]
    if len(f.top_names) > cap:
        lines.append(f"• +{len(f.top_names) - cap} more")
    lines.append("Source: Python ast")
    return "\n".join(lines)


def _uses_lines(f: _FileInfo, an: _Analysis, title_of: Dict[str, str]) -> List[str]:
    out = []
    if f.deps:
        out.append(_bullets("Uses", [f"{title_of.get(d, d)}: {_dep_label(f, d, an.calls_between(f.rel, d))}" for d in sorted(f.deps)]))
    if f.importers:
        items = []
        for i in sorted(f.importers):
            called = an.calls_between(i, f.rel)
            items.append(title_of.get(i, i) + (f": calls {_capped(called, 3)}" if called else ""))
        out.append(_bullets("Used by", items))
    return out
