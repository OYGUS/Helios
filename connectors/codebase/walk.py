"""The single directory walk (`_walk_project`): prunes dependency / build / VCS folders,
reads every analysable file exactly once and dispatches it to its analyser.

Backup folders: while walking, directories named `backup`, `backups`, `*-backup`,
`*_backup` or `*.bak` (case-insensitive) are skipped like dependency folders (they only
duplicate the project's files); the core plan says "Skipped N backup folders (...)".
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from connectors.codebase.constants import (
    CALL_ANALYSIS_MAX_CODE_FILES,
    CALL_ANALYSIS_MAX_FILE_BYTES,
    CODE_EXTENSIONS,
    DATA_EXTENSIONS,
    DATA_FILE_LIST_CAP,
    DATA_FILE_MAX_BYTES,
    DATA_FILE_NAMES,
    FILE_LEVEL_MAX_FILES,
    IGNORED_DIR_SUFFIXES,
    MAX_PARSED_FILES,
    MAX_PARSE_BYTES,
    _CONFIG_NAMES,
    _DOTNET_PROJECT_SUFFIXES,
    _IGNORED_DIRS_LOWER,
    _LOCK_FILE_NAMES,
)
from connectors.codebase.fileinfo import _FileInfo, _classify_file_layer
from connectors.codebase.purpose_text import (
    PURPOSE_CANDIDATE_CAP,
    README_TEXT_CAP,
    _README_ALT_NAMES,
    _is_purpose_candidate,
    _php_db_usage,
    _read_small_text,
    _text_purpose_bits,
)
from connectors.codebase.lang_python import _analyze_python_source
from connectors.codebase.lang_patterns import _analyze_generic_source, _analyze_markdown_text
from connectors.codebase.grouping import _layout_is_flat


# ---------------------------------------------------------------------------
# Directory walk
# ---------------------------------------------------------------------------
_BACKUP_DIR_RE = re.compile(r"^(?:backups?|.+[-_]backup|.+\.bak)$", re.IGNORECASE)


def _is_backup_dir(name: str) -> bool:
    """`backup`, `backups`, `*-backup`, `*_backup`, `*.bak` (case-insensitive): copies of
    the project that would only duplicate its files on the map."""
    return bool(_BACKUP_DIR_RE.match(name))


def _should_skip_dir(name: str, sibling_files: Iterable[str]) -> bool:
    low = name.lower()
    if name.startswith(".") or low in _IGNORED_DIRS_LOWER or low.endswith(IGNORED_DIR_SUFFIXES):
        return True
    if low in {"bin", "obj"}:
        return any(f.lower().endswith(_DOTNET_PROJECT_SUFFIXES) for f in sibling_files)
    return False


def _is_data_file(fn: str, ext: str) -> bool:
    low = fn.lower()
    if low in _LOCK_FILE_NAMES:
        return False
    return ext in DATA_EXTENSIONS or low in DATA_FILE_NAMES or low.endswith(".env.example")


@dataclass
class _WalkExtras:
    """Side results of `_walk_project`."""

    configs: List[str] = field(default_factory=list)  # tsconfig/jsconfig/package/composer.json (rel)
    backup_dirs: List[str] = field(default_factory=list)  # backup-named directories skipped (rel)
    readme_alt: Tuple[str, str] = ("", "")  # (rel, text) of a root README.rst / README.txt / README
    calls_disabled: bool = False  # project has more than CALL_ANALYSIS_MAX_CODE_FILES code files
    calls_skipped_large: int = 0  # files skipped by CALL_ANALYSIS_MAX_FILE_BYTES
    detail: bool = True  # file-level map expected: build signatures / class details


def _walk_project(
    folder: Path,
    data_files: Optional[List[Tuple[str, int]]] = None,
    extras: Optional[_WalkExtras] = None,
) -> Tuple[List[_FileInfo], int]:
    """Walks `folder` once, pruning ignored/dot/symlinked directories in place, then
    reads + analyses each analysable file exactly once. Returns (files, skipped_large).

    If `data_files` is a list, small config/data files (see `DATA_EXTENSIONS`) are
    appended to it as (relative path, size in bytes); they are never opened here."""
    extras = extras if extras is not None else _WalkExtras()
    entries: List[Tuple[str, str, str, int]] = []  # (full path, rel, ext, size)
    root = str(folder)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        kept = []
        for d in dirnames:
            if _should_skip_dir(d, filenames):
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                continue  # never follow directory symlinks (loops / outside trees)
            if _is_backup_dir(d):
                extras.backup_dirs.append(os.path.relpath(os.path.join(dirpath, d), root).replace(os.sep, "/"))
                continue
            kept.append(d)
        dirnames[:] = sorted(kept)
        at_root = os.path.normpath(dirpath) == os.path.normpath(root)

        for fn in sorted(filenames):
            if fn.startswith("."):
                continue  # also skips `.env` files: they are never read
            ext = os.path.splitext(fn)[1].lower()
            low = fn.lower()
            if low in _CONFIG_NAMES:
                extras.configs.append(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/"))
            if at_root and low in _README_ALT_NAMES and not extras.readme_alt[0]:
                extras.readme_alt = (fn, _read_small_text(os.path.join(dirpath, fn)))
            if ext not in CODE_EXTENSIONS:
                if data_files is not None and len(data_files) < DATA_FILE_LIST_CAP and _is_data_file(fn, ext):
                    full = os.path.join(dirpath, fn)
                    try:
                        if not os.path.islink(full):
                            size = os.stat(full).st_size
                            if size <= DATA_FILE_MAX_BYTES:
                                data_files.append((os.path.relpath(full, root).replace(os.sep, "/"), size))
                    except OSError:
                        pass
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue
            try:
                size = os.stat(full).st_size
            except OSError:
                continue
            entries.append((full, os.path.relpath(full, root).replace(os.sep, "/"), ext, size))

    code_count = sum(1 for e in entries if e[2] != ".md")
    extras.calls_disabled = code_count > CALL_ANALYSIS_MAX_CODE_FILES
    files: List[_FileInfo] = [
        _FileInfo(rel=rel, path=Path(full), ext=ext, lang=CODE_EXTENSIONS[ext], size=size) for full, rel, ext, size in entries
    ]
    # Same rule as build_code_map(): only file-level maps show signatures / class details.
    extras.detail = code_count <= FILE_LEVEL_MAX_FILES or _layout_is_flat(files)
    skipped = 0
    parsed = 0
    candidates = 0
    for info, (full, rel, ext, size) in zip(files, entries):
        info.layer = _classify_file_layer(rel)
        if candidates < PURPOSE_CANDIDATE_CAP and _is_purpose_candidate(rel, ext, info.layer):
            info.purpose_candidate = True
            candidates += 1
        if size > MAX_PARSE_BYTES:
            info.note = f"Not parsed: file is larger than {MAX_PARSE_BYTES // 1_000_000} MB."
            skipped += 1
            continue
        if parsed >= MAX_PARSED_FILES:
            info.note = f"Not parsed: project exceeds {MAX_PARSED_FILES} analysable files."
            skipped += 1
            continue
        try:
            with open(full, "rb") as fh:
                text = fh.read().decode("utf-8", errors="replace")
        except OSError as exc:
            info.note = f"Not parsed: could not read file ({exc.__class__.__name__})."
            continue
        parsed += 1
        info.lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        analyze_calls = not extras.calls_disabled
        if analyze_calls and size > CALL_ANALYSIS_MAX_FILE_BYTES and ext != ".md":
            analyze_calls = False
            extras.calls_skipped_large += 1
            info.calls_note = f"Call analysis skipped: file is larger than {CALL_ANALYSIS_MAX_FILE_BYTES // 1000} KB."
        try:
            if ext == ".py":
                _analyze_python_source(text, info, analyze_calls, extras.detail)
            elif ext == ".md":
                info.method = "markdown"
                info.doc, info.headings, info.wikilinks = _analyze_markdown_text(text)
                if "/" not in rel and posixpath.splitext(rel)[0].lower() == "readme":
                    info.readme_text = text[:README_TEXT_CAP]
            else:
                _analyze_generic_source(text, info, analyze_calls)
                if info.purpose_candidate:
                    _text_purpose_bits(text, info)
                elif ext == ".php" and ("PDO" in text or "mysqli" in text.lower()):
                    _php_db_usage(text, info)
        except RecursionError:
            info.note = "Analysis stopped: the file is too deeply nested to analyse."
    return files, skipped
