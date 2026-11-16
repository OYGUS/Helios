"""The ``ignored_folders`` setting: folders removed with "never import this folder again".

Pure helpers over a `GalaxyStore` (normalise, list, add, remove, covering entries) plus
`folder_is_ignored`, which also asks the importer's `is_ignored_folder` when it exists."""

from __future__ import annotations

import os
from typing import List

from connectors import codebase_importer as codebase_importer_module
from core.models import GalaxyStore


IGNORED_FOLDERS_KEY = "ignored_folders"


def normalize_folder(path: object) -> str:
    """Absolute, normalised folder path as stored in the ``ignored_folders`` setting."""
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(os.path.expanduser(text)))


def _folder_key(path: str) -> str:
    return os.path.normcase(normalize_folder(path)).rstrip("\\/")


def ignored_folders(store: GalaxyStore) -> List[str]:
    raw = store.get_setting(IGNORED_FOLDERS_KEY, [])
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if isinstance(p, str) and p.strip()]


def add_ignored_folder(store: GalaxyStore, path: object) -> bool:
    """Adds ``path`` to the ignore list; False if it was already there (or empty)."""
    norm = normalize_folder(path)
    if not norm:
        return False
    current = ignored_folders(store)
    if any(_folder_key(p) == _folder_key(norm) for p in current):
        return False
    store.set_setting(IGNORED_FOLDERS_KEY, current + [norm])
    return True


def remove_ignored_folder(store: GalaxyStore, path: object) -> bool:
    key = _folder_key(str(path or ""))
    current = ignored_folders(store)
    kept = [p for p in current if _folder_key(p) != key]
    if len(kept) == len(current):
        return False
    store.set_setting(IGNORED_FOLDERS_KEY, kept)
    return True


def _covering_entries(store: GalaxyStore, path: object) -> List[str]:
    """Ignore-list entries equal to ``path`` or containing it."""
    key = _folder_key(str(path or ""))
    if not key:
        return []
    out = []
    for entry in ignored_folders(store):
        base = _folder_key(entry)
        if base and (key == base or key.startswith(base + os.sep)):
            out.append(entry)
    return out


def folder_is_ignored(store: GalaxyStore, path: object) -> bool:
    """Uses the importer's ``is_ignored_folder`` when it exists, plus the setting itself."""
    fn = getattr(codebase_importer_module, "is_ignored_folder", None)
    if callable(fn):
        try:
            if fn(store, path):
                return True
        except Exception:
            pass
    return bool(_covering_entries(store, path))
