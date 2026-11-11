"""Scans local Antigravity, OpenAI Codex, and Claude Code session histories.

Architecture cannot be derived from chat, so session logs are used for two things only:
finding which project folders were worked on, and a per-project history of the sessions.

* **The map comes from the real code.** For every project folder found in the logs that
  exists on this machine and contains analysable source files, the modules, sub-components
  and links are built with `connectors.codebase_importer.build_code_map()`, exactly as a
  codebase scan would build them (a file that no session touched still appears).
* **One "Work History" module** (`kind="history"`, layer `docs`) is added. Its
  sub-components are sessions, most recent first (at most `HISTORY_SESSION_CAP`), titled
  `YYYY-MM-DD — <files touched>` (date from log timestamps, else the log file's
  modification time, UTC; label built from the files touched by tool calls, never from
  chat text). The content names the tool and quotes the user's requests of that session
  (at most `REQUESTS_PER_SESSION`, truncated), the files changed/read and the tools used.
  Antigravity plan/design artifacts (`*.md` in the session folder) may be added as extra
  sub-components titled by their H1 (at most `HISTORY_ARTIFACT_CAP`). Every tool's
  sessions go into the same module; each sub-component's first line names its tool.
* Chat headings, bold text and request sentences are never used as module or
  sub-component titles.

Project identity
----------------
* Codex (`session_meta.cwd`) and Claude Code (`cwd` on each entry) record the session's
  working directory; Antigravity has no cwd, so the absolute paths seen in tool-call
  arguments are used instead.
* If the directory exists on this machine, the project root is found by walking up from
  it to the nearest folder containing a root marker (`.git`, `package.json`,
  `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `go.mod`, `composer.json`,
  `*.sln`, ...), never going to or above the home directory. Otherwise the cwd itself is
  the root (Windows paths are handled as strings via `PureWindowsPath` on any OS).
* For tool-call paths, the root is the nearest marker root of a path that exists locally,
  else the deepest common ancestor of the touched paths (narrowed below generic
  containers such as home or Desktop, trailing generic sub-folders trimmed).
* Sessions without a resolvable workspace (home directory, Desktop, drive roots, agent
  data folders, no paths at all) are skipped and counted in `LAST_SCAN_STATS`. Chat text
  (e.g. a GitHub URL mentioned in conversation) is never used.
* One project per folder across all sources: the normalised folder path is the identity.
  An existing system for the folder under ANY non-manual source (codebase, another
  tool) is updated in place: the tool is added to `sources`, `source` stays as it is.
  Stale duplicates for the same folder left by older versions are consolidated
  (`codebase_importer.consolidate_folder_systems`).

Folders that do not become projects (counted per folder in `LAST_SCAN_STATS[source]`,
with up to `STATS_PATH_CAP` paths under `<key>_paths`):

* `skipped_missing_workspace` — the folder does not exist on this machine;
* `skipped_no_code` — it exists but contains no analysable source files;
* `skipped_scratch` — a tool-managed scratch workspace (e.g.
  `Documents/Codex/<YYYY-MM-DD>/<name>`, or below a `scratchpad` / `scratch` folder)
  without a project root marker DIRECTLY at its root. How much code it holds does not
  matter: such folders often hold copies, clones and backups of other projects (with
  `package.json` files only in nested sub-folders). A real project nested inside a
  scratch dir (with its own marker, e.g. `.../outputs/regbot/package.json`) is found only
  through the files a session actually touched inside it — the scratch dir itself is
  never searched for markers.
* `skipped_ignored` — the folder equals or is inside a folder of the store setting
  `ignored_folders` (see `codebase_importer.is_ignored_folder`). An existing system for
  it is left alone here (the UI removes it).

For the first three reasons an existing agent-source system that has nothing user-made
in it (`codebase_importer.has_user_made_content`; e.g. a module of an importer-only kind
such as "External Libraries" flagged `is_user_modified` by the canvas auto-layout does
not count) and was never a codebase scan is removed (`removed_stale`); a system with a
user edit, a canvas note, a user-drawn link or a purpose typed by the user is left
untouched.

Purpose evidence: the session requests of a folder are used for one thing besides the
Work History module: the first few real requests of its earliest sessions (noise
filtered, quoted, truncated) become the FIRST line of `SolarSystem.purpose_evidence`,
"From the first sessions (what the user asked for)". They never shape the map.

Trivial/noisy requests ("continue", "devam et", raw error dumps, token strings, internal
system prompts) are not quoted in the history.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from connectors.codebase_importer import (
    AGENT_SOURCES,
    HISTORY_PLANET_TITLE,
    LAST_SCAN_STATS,
    _keep_only_real_links,
    _identity_keys,
    add_source,
    build_code_map,
    compose_purpose_evidence,
    consolidate_folder_systems,
    find_systems_for_folder,
    format_session_requests,
    has_user_made_content,
    history_note,
    ignored_folder_keys,
    is_ignored_folder,
    merge_identity,
    merge_purpose,
    refresh_system_from_code,
    update_core_plan_purpose,
    workspace_key,
)
from core.models import LAYER_DEFINITIONS, SOURCE_CATEGORIES, GalaxyStore, PlanetNode, SatelliteNode, SolarSystem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_JSONL_LINES = 20_000  # lines read per session transcript
MAX_JSONL_BYTES = 64 * 1024 * 1024  # bytes read per session transcript
HISTORY_SESSION_CAP = 8  # sessions shown in the Work History module (all tools together)
HISTORY_ARTIFACT_CAP = 4  # plan/design artifacts shown in the Work History module
REQUESTS_PER_SESSION = 6  # user requests quoted per session
REQUEST_CHAR_LIMIT = 160  # characters per quoted request
STATS_PATH_CAP = 20  # folder paths listed per skip reason in LAST_SCAN_STATS
DETECTOR_WALK_CAP = 20_000  # directory entries visited when counting Codex sessions

#: Statistics of the most recent scan per source (the same dict object as
#: `codebase_importer.LAST_SCAN_STATS`), e.g.
#: LAST_SCAN_STATS["codex"] == {"sessions_found": 10, "skipped_scratch": 1,
#:                              "skipped_scratch_paths": ["C:\\...\\tablo"], ...}


def _tr_lower(text: str) -> str:
    """Lowercases text safely across ASCII and Unicode characters."""
    return text.replace("İ", "i").replace("I", "i").lower()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class Exchange:
    user: str
    ai: str = ""
    tools: List[str] = field(default_factory=list)
    files: List[Tuple[str, bool]] = field(default_factory=list)  # (path, edited)

    @property
    def used_tools(self) -> bool:
        return bool(self.tools)

    @property
    def edited_files(self) -> List[str]:
        return [p for p, e in self.files if e]


@dataclass
class SessionData:
    session_id: str
    cwd: str = ""
    exchanges: List[Exchange] = field(default_factory=list)
    artifacts: List[Path] = field(default_factory=list)
    tool_paths: List[str] = field(default_factory=list)
    mtime: float = 0.0
    title: str = ""
    log_path: str = ""
    last_ts: str = ""  # latest ISO timestamp seen in the log ("" if none)

    def note_timestamp(self, value: object) -> None:
        s = str(value or "")
        if re.match(r"^\d{4}-\d{2}-\d{2}", s) and s > self.last_ts:
            self.last_ts = s


def _note_entry_time(data: SessionData, obj: dict) -> None:
    for key in ("timestamp", "created_at", "createdAt", "time"):
        if key in obj:
            data.note_timestamp(obj.get(key))
    payload = obj.get("payload")
    if isinstance(payload, dict) and "timestamp" in payload:
        data.note_timestamp(payload.get("timestamp"))


# ---------------------------------------------------------------------------
# Path helpers (Windows paths are handled as strings on any OS)
# ---------------------------------------------------------------------------
_WIN_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)")
_WIN_PATH_IN_TEXT = re.compile(r"(?<![\w])[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n\s']+\\)*[^\\/:*?\"<>|\r\n\s']*")
_WIN_QUOTED_PATH = re.compile(r"[\"']([A-Za-z]:\\[^\"'\r\n]+)[\"']")
_POSIX_PATH_IN_TEXT = re.compile(r"(?<![\w.~:/])/(?:[\w.\-@+]+/)+[\w.\-@+]*")

ROOT_MARKERS: Tuple[str, ...] = (
    ".git",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "deno.json",
    "CMakeLists.txt",
    "pubspec.yaml",
)
_ROOT_MARKER_GLOBS: Tuple[str, ...] = ("*.sln", "*.csproj")
_ROOT_MARKER_NAMES_LOWER = {m.lower() for m in ROOT_MARKERS}

#: Folder names that hold projects rather than being one.
CONTAINER_FOLDER_NAMES = {
    "users", "home", "desktop", "documents", "downloads", "projects", "repos", "repositories",
    "source", "sources", "code", "dev", "development", "workspace", "workspaces", "github",
    "git", "tmp", "temp", "htdocs", "www", "xampp", "sites", "onedrive", "codex", "volumes",
    "mnt", "media", "root", "private", "var", "chatgpt", "scratch",
}
_DATE_OR_UUID_RE = re.compile(r"^(?:\d{4}-\d{2}(?:-\d{2})?|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
#: Path segments that mark agent data / system locations (never a project).
_EXCLUDED_SEGMENTS = {".codex", ".claude", ".gemini", "appdata", "system32", "program files", "program files (x86)", "site-packages", "node_modules"}
#: Generic project folder names that get the parent folder name as a prefix.
GENERIC_PROJECT_NAMES = {
    "src", "app", "apps", "backend", "frontend", "server", "client", "web", "api", "code",
    "source", "project", "main", "www", "public_html", "site", "website", "lib", "core",
    "service", "services", "repo",
}
#: Trailing sub-folders trimmed from a derived (non-local) common ancestor.
_TRIM_SUBFOLDERS = {
    "src", "lib", "css", "js", "styles", "components", "pages", "scripts", "static", "assets",
    "public", "tests", "test", "utils", "helpers", "img", "images", "api", "ui", "core", "models",
    "views", "routes", "controllers", "services", "templates", "docs", "config", "handlers",
}


def _clean_path_str(raw: str) -> str:
    p = str(raw or "").strip().strip("\"'").strip()
    if p.lower().startswith("file://"):
        from urllib.parse import unquote

        p = unquote(p[7:])
        if re.match(r"^/[A-Za-z]:[\\/]", p):
            p = p[1:]
    if len(p) > 3 and p.endswith(("\\", "/")):
        p = p.rstrip("\\/")
    return p


def _is_windows_path(p: str) -> bool:
    return bool(_WIN_ABS_RE.match(p))


def _is_absolute(p: str) -> bool:
    return _is_windows_path(p) or p.startswith("/")


def _pure(p: str):
    return PureWindowsPath(p) if _is_windows_path(p) else PurePosixPath(p)


def _parts_lower(p: str) -> Tuple[str, ...]:
    pure = _pure(p)
    parts = pure.parts
    if _is_windows_path(p):
        return tuple(_tr_lower(x) for x in parts)
    return tuple(parts)


def _path_key(p: str) -> str:
    """Normalised identity key for a folder path (case-insensitive for Windows paths)."""
    return workspace_key(_clean_path_str(p))


def _exists_locally(p: str) -> bool:
    if not p or (_is_windows_path(p) and os.name != "nt"):
        return False
    try:
        return Path(p).exists()
    except (OSError, ValueError):
        return False


def _local_home_keys() -> Set[str]:
    try:
        home = Path.home()
    except Exception:
        return set()
    return {_path_key(str(home)), _path_key(str(home.parent))}


def _is_container_path(p: str) -> bool:
    """True for folders that are not a project: drive/filesystem roots, home folders,
    generic containers (Desktop, Documents, ...) and agent/system data locations."""
    p = _clean_path_str(p)
    if not p or not _is_absolute(p):
        return True
    parts = _parts_lower(p)
    if len(parts) <= 1:
        return True
    low_parts = tuple(_tr_lower(x) for x in parts)
    if any(seg in _EXCLUDED_SEGMENTS for seg in low_parts):
        return True
    last = low_parts[-1]
    if last in CONTAINER_FOLDER_NAMES or last.startswith("onedrive"):
        return True
    if _DATE_OR_UUID_RE.match(last):
        return True  # dated scratch folders (e.g. Documents\Codex\2025-01-31) and session ids
    if _is_windows_path(p):
        if len(parts) == 3 and parts[1] == "users":
            return True
        if parts[1] == "windows":
            return True
    else:
        if len(parts) == 3 and parts[1] in ("home", "Users"):
            return True
        if len(parts) == 2 and parts[1] == "root":
            return True
    if _path_key(p) in _local_home_keys():
        return True
    return False


def _has_root_marker(d: Path) -> bool:
    for m in ROOT_MARKERS:
        if (d / m).exists():
            return True
    for pattern in _ROOT_MARKER_GLOBS:
        try:
            if next(d.glob(pattern), None) is not None:
                return True
        except OSError:
            pass
    return False


def _find_marker_root(start: Path) -> Optional[Path]:
    """Walks up from `start` to the nearest folder with a root marker, stopping before
    the home directory and the filesystem root."""
    try:
        cur = start if start.is_dir() else start.parent
    except OSError:
        return None
    try:
        home = Path.home().resolve()
    except Exception:
        home = None
    stop = {home, home.parent} if home else set()
    for _ in range(40):
        if cur in stop or cur == cur.parent:
            return None
        if _has_root_marker(cur):
            return cur
        cur = cur.parent
    return None


def _looks_like_file(p: str) -> bool:
    name = _pure(p).name
    return bool(name) and "." in name.strip(".")


def _dir_of(p: str) -> str:
    if _exists_locally(p):
        try:
            return p if Path(p).is_dir() else str(Path(p).parent)
        except OSError:
            return p
    return str(_pure(p).parent) if _looks_like_file(p) else p


def _relative_to_root(path: str, root: str) -> Optional[str]:
    """POSIX-style path of `path` relative to `root`, or None if outside it."""
    path = _clean_path_str(path)
    if not path or not root:
        return None
    if not _is_absolute(path):
        rel = path.replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if not rel or rel.startswith("../") or rel == "..":
            return None
        return rel
    if _is_windows_path(path) != _is_windows_path(root):
        return None
    pp, rp = _parts_lower(path), _parts_lower(root)
    if len(pp) <= len(rp) or pp[: len(rp)] != rp:
        return None
    original = _pure(path).parts
    return "/".join(original[len(rp) :])


def _join_path(base: str, rel: str) -> str:
    rel = rel.strip()
    if not rel or _is_absolute(rel) or not base:
        return rel
    if _is_windows_path(base):
        return str(PureWindowsPath(base) / PureWindowsPath(rel))
    return posixpath.normpath(posixpath.join(base, rel))


def _extract_paths_from_text(text: str) -> List[str]:
    found: List[str] = []
    for m in _WIN_QUOTED_PATH.finditer(text):
        found.append(m.group(1))
    for m in _WIN_PATH_IN_TEXT.finditer(text):
        found.append(m.group(0))
    for m in _POSIX_PATH_IN_TEXT.finditer(text):
        if m.group(0).count("/") >= 2:
            found.append(m.group(0))
    return list(dict.fromkeys(_clean_path_str(f) for f in found if f))


def _display_name(root: str) -> str:
    pure = _pure(root)
    name = pure.name or str(pure)
    parent = pure.parent.name
    if _tr_lower(name) in GENERIC_PROJECT_NAMES and parent and _tr_lower(parent) not in CONTAINER_FOLDER_NAMES:
        return f"{parent}-{name}"
    return name


def _deepest_common_ancestor(paths: List[str]) -> Optional[str]:
    if not paths:
        return None
    lowered = [_parts_lower(p) for p in paths]
    first = _pure(paths[0]).parts
    common = 0
    for i in range(min(len(x) for x in lowered)):
        if all(x[i] == lowered[0][i] for x in lowered):
            common = i + 1
        else:
            break
    if common == 0:
        return None
    flavour = PureWindowsPath if _is_windows_path(paths[0]) else PurePosixPath
    return str(flavour(*first[:common]))


def _root_from_tool_paths(paths: Iterable[str]) -> Optional[Tuple[str, bool]]:
    """Derives a project root from absolute tool-call paths. Returns (root, confirmed)."""
    abs_paths = []
    for raw in paths:
        p = _clean_path_str(raw)
        if not p or not _is_absolute(p):
            continue
        if any(_tr_lower(seg) in _EXCLUDED_SEGMENTS for seg in _parts_lower(p)):
            continue
        abs_paths.append(p)
    if not abs_paths:
        return None

    # 1. Paths existing on this machine: nearest marker root, most frequent wins.
    local_roots: Counter = Counter()
    for p in abs_paths:
        if _exists_locally(p):
            mr = _find_marker_root(Path(p))
            if mr is not None and not _is_container_path(str(mr)):
                local_roots[str(mr)] += 1
    if local_roots:
        return local_roots.most_common(1)[0][0], True

    # 2. Explicit marker files among the touched paths.
    marker_roots: Counter = Counter()
    for p in abs_paths:
        if _tr_lower(_pure(p).name) in _ROOT_MARKER_NAMES_LOWER or _pure(p).suffix.lower() in (".sln", ".csproj"):
            parent = str(_pure(p).parent)
            if not _is_container_path(parent):
                marker_roots[parent] += 1
    if marker_roots:
        return marker_roots.most_common(1)[0][0], False

    # 3. Deepest common ancestor, narrowing to the largest cluster below containers.
    win = [p for p in abs_paths if _is_windows_path(p)]
    posix = [p for p in abs_paths if not _is_windows_path(p)]
    group = [_dir_of(p) for p in (win if len(win) >= len(posix) else posix)]
    for _ in range(6):
        dca = _deepest_common_ancestor(group)
        if dca is None:
            return None
        if not _is_container_path(dca):
            cur = dca
            while _tr_lower(_pure(cur).name) in _TRIM_SUBFOLDERS and not _is_container_path(str(_pure(cur).parent)):
                cur = str(_pure(cur).parent)
            return cur, False
        depth = len(_parts_lower(dca))
        nxt = Counter(_parts_lower(d)[depth] for d in group if len(_parts_lower(d)) > depth)
        if not nxt:
            return None
        seg = nxt.most_common(1)[0][0]
        group = [d for d in group if len(_parts_lower(d)) > depth and _parts_lower(d)[depth] == seg]
    return None


def _root_from_cwd(cwd: str) -> Optional[Tuple[str, bool]]:
    cwd = _clean_path_str(cwd)
    if not cwd or not _is_absolute(cwd):
        return None
    if _exists_locally(cwd):
        p = Path(cwd)
        mr = _find_marker_root(p)
        root = mr if mr is not None else (p if p.is_dir() else p.parent)
        root_s = str(root)
        if _is_container_path(root_s):
            return None
        return root_s, True
    if _is_container_path(cwd):
        return None
    return cwd, False


def _resolve_project_root(cwd: str, tool_paths: Iterable[str] = ()) -> Optional[Tuple[str, str, str, bool]]:
    """Returns (root key, root path, display name, confirmed) or None if unresolvable."""
    res = _root_from_cwd(cwd) if cwd else None
    if res is None:
        res = _root_from_tool_paths(tool_paths)
    if res is None:
        return None
    root, confirmed = res
    if _is_container_path(root):
        return None
    return _path_key(root), root, _display_name(root), confirmed


# ---------------------------------------------------------------------------
# Message cleaning / noise filtering
# ---------------------------------------------------------------------------
def _clean_user_request(raw_text: str) -> str:
    """Extracts the clean user message inside <USER_REQUEST> tags and strips system blocks."""
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw_text, re.DOTALL)
    if match:
        return match.group(1).strip()

    cleaned = raw_text
    for tag in (
        "environment_context",
        "permissions[^>]*",
        "app-context",
        "collaboration_mode",
        "turn_aborted",
        "in-app-browser-context[^>]*",
        "send_user_message_question_reply",
        "system-reminder",
        "user_instructions",
    ):
        name = tag.split("[")[0]
        cleaned = re.sub(rf"<{tag}>.*?</{name}>", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(rf"<{tag}>", "", cleaned)
    return cleaned.strip()


_CONTINUE_RE = re.compile(
    r"^(?:run(?:\s+the)?\s+code|start\s+(?:the\s+)?(?:app|server)|continue|go\s+ahead|go\s+on|fix\s+it|looks\s+good|ok(?:ay)?|"
    r"yes|no|thanks?|thank\s+you|done|devam(?:\s+et)?|tamam|evet|hay[ıi]r|olur|te[şs]ekk[üu]rler|sa[ğg]\s*ol|[çc]al[ıi][şs]t[ıi]r|d[üu]zelt)$"
)
_NOISE_PHRASES = (
    "the following is the codex agent history",
    "treat the transcript, tool call arguments",
    ">>> transcript start",
    "<in-app-browser-context",
    "<send_user_message_question_reply",
    "# files pasted by the user",
    "# agents.md instructions",
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
    "caveat: the messages below were generated",
    "[request interrupted by user",
)


def _is_noise_or_trivial_message(user_msg: str, ai_msg: str = "", used_tools: bool = False) -> bool:
    """Filters out empty messages, trivial operational commands, stack traces, and tokens.

    Words are counted Unicode-aware. A short request ("add dark mode") is noise only if
    no tools were used AND the reply is short."""
    u = user_msg.strip()
    if not u:
        return True
    low = _tr_lower(u)
    if any(noise in low for noise in _NOISE_PHRASES):
        return True
    if re.search(r"[A-Za-z0-9_-]{22,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}", u):
        return True
    if _CONTINUE_RE.match(re.sub(r"[\s.!?,…]+$", "", low).strip()):
        return True
    words = re.findall(r"\w+", low)
    if len(words) <= 3 and len(u) < 32:
        if not used_tools and len(ai_msg.strip()) < 400:
            return True
    return False


# ---------------------------------------------------------------------------
# Scratch workspaces
# ---------------------------------------------------------------------------
#: A date / UUID folder directly below one of these is a tool-managed scratch dir
#: (e.g. `Documents\Codex\2026-09-22\tablo`).
SCRATCH_TOOL_DIR_NAMES = {"codex", "chatgpt", "claude", "claude code", "antigravity", "gemini", "cursor", "windsurf"}
#: Any folder below one of these is a scratch workspace.
SCRATCH_SEGMENT_NAMES = {"scratchpad", "scratch"}


def _is_scratch_path(p: str) -> bool:
    parts = [_tr_lower(x) for x in _pure(_clean_path_str(p)).parts]
    for i, seg in enumerate(parts):
        if i < len(parts) - 1 and seg in SCRATCH_SEGMENT_NAMES:
            return True
        if i > 0 and _DATE_OR_UUID_RE.match(seg) and parts[i - 1] in SCRATCH_TOOL_DIR_NAMES:
            return True
    return False


def _scratch_qualifies(root: str) -> bool:
    """A scratch workspace counts as a project only with a root marker directly at its
    root (the amount of code is irrelevant: copies / backups of other projects)."""
    return _has_root_marker(Path(root))


def _nested_project_root(session: SessionData, scratch_root: str) -> Optional[str]:
    """The most frequent marker root strictly inside `scratch_root` among the local
    paths the session touched (a real project nested in a scratch dir), or None."""
    hits: Counter = Counter()
    candidates = [p for ex in session.exchanges for p, _e in ex.files] + list(session.tool_paths)
    for raw in candidates:
        p = _clean_path_str(raw)
        if not p or not _is_absolute(p) or not _exists_locally(p):
            continue
        if not _relative_to_root(p, scratch_root):
            continue
        mr = _find_marker_root(Path(p))
        if mr is not None and _relative_to_root(str(mr), scratch_root):
            hits[str(mr)] += 1
    return hits.most_common(1)[0][0] if hits else None


def _first_requests_line(sessions: List[SessionData]) -> str:
    """Purpose-evidence line with the first real requests of the earliest sessions
    (oldest first; noise filtered like the Work History quotes)."""
    requests: List[str] = []
    for s in sorted(sessions, key=lambda x: (_session_when(x)[0], x.session_id)):
        for ex in s.exchanges:
            if not _is_noise_or_trivial_message(ex.user, ex.ai, ex.used_tools):
                requests.append(ex.user)
            if len(requests) >= 12:
                break
        if len(requests) >= 12:
            break
    return format_session_requests(requests)


def _is_local_dir(p: str) -> bool:
    if not _exists_locally(p):
        return False
    try:
        return Path(p).is_dir()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Work History module
# ---------------------------------------------------------------------------
_TOOL_LINE_RE = re.compile(r"^TOOL: .*\[(\w+)\]\s*$", re.MULTILINE)
_KIND_LINE_RE = re.compile(r"^KIND: (\w+)\s*$", re.MULTILINE)
_SESSION_LINE_RE = re.compile(r"^SESSION: (\S+)", re.MULTILINE)
_WHEN_LINE_RE = re.compile(r"^LAST ACTIVITY: (\S+)", re.MULTILINE)
_TOOL_COUNT_LINE_RE = re.compile(r"^• (.+) \[(\w+)\]: (.+)$", re.MULTILINE)


def _app_label(source: str) -> str:
    return str(SOURCE_CATEGORIES.get(source, {}).get("title", source))


def _session_when(s: SessionData) -> Tuple[str, str]:
    """(sortable UTC ISO time, how it was obtained)."""
    if s.last_ts:
        ts = s.last_ts.replace(" ", "T")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), "log timestamps, UTC"
        except ValueError:
            return ts[:19], "log timestamps"
    if s.mtime:
        return datetime.fromtimestamp(s.mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "log file modification time, UTC"
    return "0000-00-00T00:00:00", "unknown"


def _touched_files(s: SessionData, root: str) -> Tuple[List[str], List[str]]:
    """(changed, read-only) project-relative paths touched by tool calls, most frequent first."""
    changed: Counter = Counter()
    read: Counter = Counter()
    order: Dict[str, int] = {}
    for ex in s.exchanges:
        for p, edited in ex.files:
            rel = _relative_to_root(p, root) if _is_absolute(_clean_path_str(p)) else None
            if not rel or rel.startswith(".."):
                continue
            order.setdefault(rel, len(order))
            (changed if edited else read)[rel] += 1
    rank = lambda c: sorted(c, key=lambda r: (-c[r], order[r]))  # noqa: E731
    changed_l = rank(changed)
    read_l = [r for r in rank(read) if r not in changed]
    return changed_l, read_l


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _session_label(changed: List[str], read: List[str]) -> str:
    names = list(dict.fromkeys(posixpath.basename(r) for r in (changed or read)))
    if not names:
        return "no project files touched"
    label = ", ".join(names[:2])
    if len(names) > 2:
        label += f" (+{len(names) - 2})"
    return _clip(label, 60)


def _session_satellite(s: SessionData, source: str, root: str) -> SatelliteNode:
    when, how = _session_when(s)
    changed, read = _touched_files(s, root)
    title = f"{when[:10]} — {_session_label(changed, read)}"
    requests = [
        f'• "{_clip(ex.user, REQUEST_CHAR_LIMIT)}"'
        for ex in s.exchanges
        if not _is_noise_or_trivial_message(ex.user, ex.ai, ex.used_tools)
    ]
    tools = Counter(t.split(":")[0].strip() for ex in s.exchanges for t in ex.tools)
    lines = [
        f"TOOL: {_app_label(source)} [{source}]",
        "KIND: session",
        f"SESSION: {s.session_id}",
        f"LAST ACTIVITY: {when} (from {how})",
    ]
    if s.log_path:
        lines.append(f"LOG FILE: {s.log_path}")
    lines.append(f"\nREQUESTS IN THIS SESSION (quoted from the log, first {REQUESTS_PER_SESSION}, truncated):")
    lines.append("\n".join(requests[:REQUESTS_PER_SESSION]) or "• (only short follow-ups such as 'continue', or no user text)")
    if len(requests) > REQUESTS_PER_SESSION:
        lines.append(f"• … +{len(requests) - REQUESTS_PER_SESSION} more requests")
    lines.append(f"\nFILES CHANGED BY TOOL CALLS ({len(changed)}): " + (", ".join(changed[:12]) + (" …" if len(changed) > 12 else "") if changed else "none"))
    if read:
        lines.append(f"FILES READ ({len(read)}): " + ", ".join(read[:12]) + (" …" if len(read) > 12 else ""))
    lines.append("TOOLS USED: " + (", ".join(f"{t} ×{c}" for t, c in tools.most_common(8)) if tools else "none"))
    lines.append("\nThis is session history, not architecture. The title is built from the date and the files touched.")
    return SatelliteNode(title=title, content="\n".join(lines), visual_tag="thought", file_path=s.log_path)


def _artifact_satellites(sessions: List[SessionData], source: str) -> List[SatelliteNode]:
    out: List[SatelliteNode] = []
    seen: Set[str] = set()
    for s in sorted(sessions, key=lambda x: _session_when(x)[0], reverse=True):
        when, _how = _session_when(s)
        for art in reversed(s.artifacts):
            if len(out) >= HISTORY_ARTIFACT_CAP:
                return out
            try:
                text = art.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            h1 = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
            if not text or not h1:
                continue
            title = _clip(re.sub(r"[*_`#]+", "", h1.group(1)), 50)
            if not title or _tr_lower(title) in seen:
                continue
            seen.add(_tr_lower(title))
            content = (
                f"TOOL: {_app_label(source)} [{source}]\nKIND: artifact\nSESSION: {s.session_id}\nLAST ACTIVITY: {when}\n"
                f"ARTIFACT FILE: {art}\n\nPlan / design document written during an {_app_label(source)} session "
                f"(title = its first heading). It describes intended work, not verified architecture.\n\n"
                f"EXCERPT:\n{text[:900]}"
            )
            out.append(SatelliteNode(title=title, content=content, visual_tag="diagram", file_path=str(art)))
    return out


def _sat_meta(sat: SatelliteNode) -> Tuple[str, str, str]:
    """(tool source, kind, sortable time) parsed from a history sub-component."""
    tool = _TOOL_LINE_RE.search(sat.content or "")
    kind = _KIND_LINE_RE.search(sat.content or "")
    when = _WHEN_LINE_RE.search(sat.content or "")
    return (tool.group(1) if tool else "", kind.group(1) if kind else "", when.group(1) if when else "")


def _upsert_history_planet(system: SolarSystem, source: str, sessions: List[SessionData], root: str) -> PlanetNode:
    """Adds or refreshes the single Work History module of `system` with this tool's
    sessions, keeping other tools' entries and user-modified sub-components."""
    planet = next((p for p in system.planets if p.kind == "history"), None)
    if planet is None:
        planet = system.add_planet(
            title=HISTORY_PLANET_TITLE, summary="", color=LAYER_DEFINITIONS["docs"][1], layer="docs"
        )
        planet.kind = "history"
        planet.file_path = ""
    old_summary = planet.summary or ""

    user_sats = [s for s in planet.satellites if s.is_user_modified]
    others = [s for s in planet.satellites if not s.is_user_modified and _sat_meta(s)[0] not in ("", source)]
    # A session entry the user edited replaces the regenerated entry of that session.
    user_ids = {m.group(1) for s in user_sats for m in [_SESSION_LINE_RE.search(s.content or "")] if m}
    fresh = [_session_satellite(s, source, root) for s in sessions if s.session_id not in user_ids]
    fresh += _artifact_satellites(sessions, source)
    pool = others + fresh
    sess = sorted((s for s in pool if _sat_meta(s)[1] == "session"), key=lambda s: _sat_meta(s)[2], reverse=True)
    arts = sorted((s for s in pool if _sat_meta(s)[1] == "artifact"), key=lambda s: _sat_meta(s)[2], reverse=True)
    hidden = max(0, len(sess) - HISTORY_SESSION_CAP)
    chosen = sess[:HISTORY_SESSION_CAP] + arts[:HISTORY_ARTIFACT_CAP]

    taken = {_tr_lower(s.title.strip()) for s in user_sats}
    for sat in chosen:
        base, n = sat.title, 2
        while _tr_lower(sat.title.strip()) in taken:
            sat.title = f"{base} ({n})"
            n += 1
        taken.add(_tr_lower(sat.title.strip()))
    planet.satellites = chosen + user_sats
    planet.rebalance_satellites()

    if not planet.is_user_modified:
        per_tool: Dict[str, str] = {}
        for m in _TOOL_COUNT_LINE_RE.finditer(old_summary):
            if m.group(2) != source and m.group(2) in AGENT_SOURCES:
                per_tool[m.group(2)] = f"• {m.group(1)} [{m.group(2)}]: {m.group(3)}"
        dates = sorted(_session_when(s)[0][:10] for s in sessions)
        span = f", {dates[0]} to {dates[-1]}" if dates else ""
        per_tool[source] = f"• {_app_label(source)} [{source}]: {len(sessions)} session(s) in the latest scan{span}"
        name = system.project_name or system.name
        planet.summary = "\n".join(
            [
                "SESSION HISTORY (NOT ARCHITECTURE):",
                f"AI coding sessions recorded for {name}. This module is history only: the other modules and "
                "their links come from the source code, not from these sessions.",
                "\nSESSIONS PER TOOL (latest scan of each tool):",
                "\n".join(per_tool[k] for k in AGENT_SOURCES if k in per_tool),
                f"\nSHOWN: the {min(len(sess), HISTORY_SESSION_CAP)} most recent session(s)"
                + (f" (+{hidden} older not shown)" if hidden else "")
                + (f" and {len(arts[:HISTORY_ARTIFACT_CAP])} plan/design document(s)" if arts else "")
                + ".",
                "\nHOW THIS WAS DERIVED:\n• Dates from log timestamps (else the log file's modification time).\n"
                "• Titles from the files touched by tool calls; requests are quoted verbatim (truncated) from the logs.",
            ]
        )
    return planet


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------
def _iter_jsonl(path: Path, max_lines: int = MAX_JSONL_LINES, max_bytes: int = MAX_JSONL_BYTES) -> Iterator[dict]:
    """Yields JSON objects from a .jsonl file, streaming, capped by lines and bytes."""
    read = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                read += len(line)
                if read > max_bytes:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


_EDIT_TOOL_RE = re.compile(r"(?i)(write|edit|replace|create|patch|apply|insert|delete|rename|move|save|notebookedit)")
_PATH_ARG_RE = re.compile(r"(?i)(path|paths|file|files|dir|directory|cwd|uri|targetfile|workdir)$")
_DIR_ARG_RE = re.compile(r"(?i)(dir|directory|directorypath|cwd|searchpath|workdir|searchdirectory)$")


def _looks_like_path(v: str) -> bool:
    v = v.strip()
    return bool(v) and "\n" not in v and len(v) < 500 and (_is_absolute(v) or "/" in v or "\\" in v or "." in v)
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_PATCH_MOVE_RE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)


def _paths_from_args(args: dict) -> Tuple[List[str], List[str]]:
    """Returns (file-like path arguments, other path hints) from tool-call arguments."""
    file_like: List[str] = []
    hints: List[str] = []
    for k, v in args.items():
        if isinstance(v, str):
            if _PATH_ARG_RE.search(k):
                val = _clean_path_str(v)
                if val and _looks_like_path(val):
                    if _DIR_ARG_RE.search(k):
                        hints.append(val)
                    else:
                        file_like.append(val)
            elif re.search(r"(?i)(command|cmd|commandline)", k):
                hints.extend(_extract_paths_from_text(v))
        elif isinstance(v, list) and re.search(r"(?i)(paths|files)$", k):
            file_like.extend(_clean_path_str(x) for x in v if isinstance(x, str) and _looks_like_path(x))
    return file_like, hints


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------
def _count_codex_sessions(codex_path: Path) -> Tuple[int, bool]:
    count, visited = 0, 0
    for sub in ("sessions", "archived_sessions"):
        base = codex_path / sub
        if not base.is_dir():
            continue
        for _dirpath, dirnames, filenames in os.walk(base):
            visited += 1 + len(dirnames) + len(filenames)
            count += sum(1 for f in filenames if f.endswith(".jsonl"))
            if visited >= DETECTOR_WALK_CAP:
                return count, True
    return count, False


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
                    if d.is_dir() and (d / ".system_generated" / "logs" / "transcript.jsonl").exists()
                )
            except OSError:
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
            "description": "Finds the project folders of Antigravity sessions, maps them from their code and adds a Work History of the sessions.",
            "tags": [],
        }

        # 2. OpenAI Codex: count session rollout files (capped walk; runs on the UI thread)
        codex_path = home / ".codex"
        codex_count, capped = 0, False
        if codex_path.exists():
            try:
                codex_count, capped = _count_codex_sessions(codex_path)
            except OSError:
                codex_count = 0
        count_label = f"{codex_count}+" if capped else str(codex_count)
        results["codex"] = {
            "name": "OpenAI Codex",
            "brand": "CLI & Code Agent",
            "icon": "CX",
            "detected": codex_path.exists() and codex_count > 0,
            "count": codex_count,
            "status": f"{count_label} sessions found" if codex_count > 0 else ("No sessions found" if codex_path.exists() else "Not detected"),
            "color": "#10B981",
            "path": "~/.codex",
            "description": "Finds the project folders of Codex sessions (working directory), maps them from their code and adds a Work History.",
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
            except OSError:
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
            "description": "Finds the project folders of Claude Code sessions, maps them from their code and adds a Work History.",
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
            except OSError:
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
                f"{len(ollama_models)} models installed" if ollama_models else ("Service ready" if ollama_exists else "Not installed")
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


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------
_PATH_LIST_KEYS = ("skipped_missing_workspace", "skipped_no_code", "skipped_scratch", "skipped_ignored", "removed_stale")


def _new_stats(source: str) -> Dict[str, object]:
    stats: Dict[str, object] = {
        "sessions_found": 0,
        "sessions_parsed": 0,
        "sessions_failed": 0,
        "skipped_no_workspace": 0,  # sessions without a resolvable folder
        "skipped_no_content": 0,  # sessions without any request / tool call / artifact
        "skipped_missing_workspace": 0,  # folders (not sessions) from here on
        "skipped_no_code": 0,
        "skipped_scratch": 0,
        "skipped_ignored": 0,
        "removed_stale": 0,
        "merged_duplicates": 0,
        "projects_created": 0,
        "projects_updated": 0,
        "projects_failed": 0,
    }
    for key in _PATH_LIST_KEYS:
        stats[f"{key}_paths"] = []
    LAST_SCAN_STATS[source] = stats
    return stats


def _count(stats: Dict[str, object], key: str, path: str = "") -> None:
    stats[key] = int(stats.get(key, 0)) + 1  # type: ignore[arg-type]
    paths = stats.get(f"{key}_paths")
    if path and isinstance(paths, list) and len(paths) < STATS_PATH_CAP and path not in paths:
        paths.append(path)


class AgentHistoryImporter:
    """Scans local AI agent histories (Antigravity, Codex, Claude Code) and groups them by project."""

    @staticmethod
    def scan_all_into_store(store: GalaxyStore, max_sessions: int = 30) -> int:
        """Scans all supported local AI tools; returns the number of projects created or updated."""
        added_count = 0
        added_count += AgentHistoryImporter.scan_antigravity(store, max_sessions=max_sessions)
        added_count += AgentHistoryImporter.scan_codex(store, max_sessions=60)
        added_count += AgentHistoryImporter.scan_claude_code(store, max_sessions=max_sessions)
        store.save()
        return added_count

    @staticmethod
    def scan_selected_sources(store: GalaxyStore, sources: List[str], max_sessions: int = 25) -> int:
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

    # ---- shared grouping / store update ----------------------------------
    @staticmethod
    def _folder_systems(store: GalaxyStore, source: str, root: str, names: Iterable[str] = ()) -> List[SolarSystem]:
        """Systems for this folder under any non-manual source, plus legacy entries of
        this source (old `source:name` ids or no workspace path but the same name)."""
        found = find_systems_for_folder(store, root)
        key = _path_key(root)
        targets = _identity_keys(root)
        for s in store.systems:
            if s.source != source or s in found:
                continue
            if s.workspace_path and not (_identity_keys(s.workspace_path) & targets):
                continue
            if s.source_id == f"{source}:{key}":
                found.append(s)
                continue
            if not s.workspace_path:
                for name in names:
                    low = name.strip().lower()
                    if s.source_id == f"{source}:{_tr_lower(name)}" or low in {s.project_name.strip().lower(), s.name.strip().lower()}:
                        found.append(s)
                        break
        return found

    @staticmethod
    def _remove_stale(store: GalaxyStore, source: str, root: str, stats: Dict[str, object]) -> None:
        """Removes old agent-made systems for a folder that is no longer a project, but
        only if nothing in them is user-made and no codebase scan ever produced them."""
        for s in AgentHistoryImporter._folder_systems(store, source, root, [_display_name(root)]):
            if s.source not in AGENT_SOURCES or "codebase" in (s.sources or []) or has_user_made_content(s):
                continue
            if s in store.systems:
                store.systems.remove(s)
                _count(stats, "removed_stale", s.workspace_path or root)

    @staticmethod
    def _import_sessions(store: GalaxyStore, source: str, app_label: str, sessions: List[SessionData], stats: Dict[str, object]) -> int:
        groups: Dict[str, Dict[str, object]] = {}
        for s in sessions:
            if not s.exchanges and not s.artifacts:
                _count(stats, "skipped_no_content")
                continue
            res = _resolve_project_root(s.cwd, s.tool_paths)
            if res is None:
                _count(stats, "skipped_no_workspace")
                continue
            key, root, name, confirmed = res
            g = groups.setdefault(key, {"root": root, "names": Counter(), "sessions": [], "confirmed": confirmed})
            g["names"][name] += 1  # type: ignore[index]
            g["sessions"].append(s)  # type: ignore[union-attr]
            g["confirmed"] = bool(g["confirmed"]) or confirmed

        # Merge unconfirmed (non-local) roots into an ancestor root seen in the same scan.
        for key in sorted(groups, key=len, reverse=True):
            g = groups.get(key)
            if g is None or g["confirmed"]:
                continue
            for other in sorted(groups, key=len):
                if other != key and len(other) < len(key):
                    sep = "\\" if _is_windows_path(str(groups[other]["root"])) else "/"
                    if key.startswith(other.rstrip(sep) + sep):
                        groups[other]["sessions"].extend(g["sessions"])  # type: ignore[union-attr]
                        groups[other]["names"].update(g["names"])  # type: ignore[union-attr]
                        del groups[key]
                        break

        # Pass 1: keep only folders that exist here; re-route scratch dirs to nested projects.
        final: Dict[str, Dict[str, object]] = {}

        def add_final(root: str, ss: List[SessionData]) -> None:
            f = final.setdefault(_path_key(root), {"root": root, "sessions": []})
            f["sessions"].extend(ss)  # type: ignore[union-attr]

        ignored = ignored_folder_keys(store)
        for key, g in groups.items():
            root = str(g["root"])
            sess: List[SessionData] = list(g["sessions"])  # type: ignore[arg-type]
            if is_ignored_folder(store, root, ignored):
                _count(stats, "skipped_ignored", root)
                continue
            if not _is_local_dir(root):
                _count(stats, "skipped_missing_workspace", root)
                AgentHistoryImporter._remove_stale(store, source, root, stats)
                continue
            if _is_scratch_path(root) and not _scratch_qualifies(root):
                rerouted: Dict[str, List[SessionData]] = defaultdict(list)
                left = 0
                for s in sess:
                    nested = _nested_project_root(s, root)
                    if nested:
                        rerouted[nested].append(s)
                    else:
                        left += 1
                for nested, ss in rerouted.items():
                    add_final(nested, ss)
                if left:
                    _count(stats, "skipped_scratch", root)
                AgentHistoryImporter._remove_stale(store, source, root, stats)
                continue
            add_final(root, sess)

        # Pass 2: build each project from its code and attach the history.
        updated = 0
        for key, g in final.items():
            if is_ignored_folder(store, str(g["root"]), ignored):  # a nested project inside an ignored folder
                _count(stats, "skipped_ignored", str(g["root"]))
                continue
            try:
                updated += AgentHistoryImporter._update_project(store, source, str(g["root"]), list(g["sessions"]), stats)  # type: ignore[arg-type]
            except Exception:
                _count(stats, "projects_failed")
                logger.exception("Failed to import %s project %s", source, key)
        return updated

    @staticmethod
    def _update_project(store: GalaxyStore, source: str, root: str, sessions: List[SessionData], stats: Dict[str, object]) -> int:
        folder = Path(root)
        name = _display_name(root)
        key = _path_key(root)
        fresh = SolarSystem(
            name=name,
            project_name=name,
            sun_title=name,
            core_plan="",
            source=source,
            source_id=f"{source}:{key}",
            workspace_path=root,
        )
        code = build_code_map(folder, fresh)
        if code.code_files == 0:
            _count(stats, "skipped_no_code", root)
            AgentHistoryImporter._remove_stale(store, source, root, stats)
            return 0
        fresh.purpose_evidence = compose_purpose_evidence(code.purpose_items, _first_requests_line(sessions))

        matches = AgentHistoryImporter._folder_systems(store, source, root, [name])
        existing, removed = consolidate_folder_systems(store, matches)
        stats["merged_duplicates"] = int(stats.get("merged_duplicates", 0)) + removed  # type: ignore[arg-type]

        if existing is None:
            excluded = {id(s) for s in matches}
            clash = any(
                id(s) not in excluded and s.source != "manual" and name.lower() in {s.project_name.strip().lower(), s.name.strip().lower()}
                for s in store.systems
            )
            if clash:
                parent = _pure(root).parent.name
                if parent:
                    fresh.name = fresh.project_name = fresh.sun_title = f"{name} ({parent})"
            _upsert_history_planet(fresh, source, sessions, root)
            fresh.core_plan = code.core_plan + history_note(fresh)
            real_links = list(fresh.links)
            added = store.add_system(fresh)
            if added.links != real_links:
                added.links = real_links
                store.save()
            _count(stats, "projects_created")
            return 1

        add_source(existing, source)
        _upsert_history_planet(existing, source, sessions, root)
        has_ua = any((folder / d / "knowledge-graph.json").exists() for d in (".ua", ".understand-anything"))
        if existing.source == "codebase" and has_ua:
            _keep_only_real_links(existing)  # keep the imported .ua graph; only the history changes
            merge_purpose(existing, fresh)
            merge_identity(existing, fresh)
            update_core_plan_purpose(existing)
        else:
            refresh_system_from_code(existing, fresh, code.core_plan)
        if not existing.workspace_path:
            existing.workspace_path = root
        _count(stats, "projects_updated")
        return 1

    # ---- Antigravity ----------------------------------------------------
    @staticmethod
    def scan_antigravity(store: GalaxyStore, max_sessions: int = 25) -> int:
        """Scans local Antigravity history (~/.gemini/antigravity/brain) and merges sessions by project root."""
        stats = _new_stats("antigravity")
        brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
        if not brain_dir.exists():
            return 0
        try:
            session_dirs = [d for d in brain_dir.iterdir() if d.is_dir()]
        except OSError:
            return 0
        session_dirs.sort(key=_safe_mtime, reverse=True)

        sessions: List[SessionData] = []
        for s_dir in session_dirs[:max_sessions]:
            transcript_path = s_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if not transcript_path.exists():
                continue
            stats["sessions_found"] += 1
            try:
                data = AgentHistoryImporter._extract_antigravity_data(s_dir, transcript_path)
            except Exception:
                stats["sessions_failed"] += 1
                logger.exception("Failed to parse Antigravity session %s", s_dir)
                continue
            stats["sessions_parsed"] += 1
            sessions.append(data)
        return AgentHistoryImporter._import_sessions(store, "antigravity", "Antigravity", sessions, stats)

    @staticmethod
    def _extract_antigravity_data(session_dir: Path, transcript_path: Path) -> SessionData:
        data = SessionData(session_id=session_dir.name, mtime=_safe_mtime(transcript_path), log_path=str(transcript_path))
        current: Optional[Exchange] = None

        for step in _iter_jsonl(transcript_path):
            _note_entry_time(data, step)
            step_type = step.get("type", "")
            content = step.get("content", "") or ""
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            if step_type == "USER_INPUT":
                cleaned = _clean_user_request(content)
                if cleaned:
                    if current is not None:
                        data.exchanges.append(current)
                    current = Exchange(user=cleaned)
            elif step_type == "PLANNER_RESPONSE":
                if current is None:
                    current = Exchange(user="")
                if content.strip():
                    current.ai = (current.ai + "\n\n" + content.strip()).strip() if current.ai else content.strip()
                for tc in step.get("tool_calls", []) or []:
                    if not isinstance(tc, dict):
                        continue
                    t_name = str(tc.get("name", "tool"))
                    args = tc.get("args", {}) or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    file_like, hints = _paths_from_args(args)
                    edited = bool(_EDIT_TOOL_RE.search(t_name))
                    for p in file_like:
                        current.files.append((p, edited))
                    data.tool_paths.extend(file_like + hints)
                    summary = str(args.get("toolSummary", "")).strip('"') or (file_like[0] if file_like else "") or str(args.get("query", "")).strip('"')
                    current.tools.append(f"{t_name}: {summary}".strip(": "))
        if current is not None and (current.user or current.tools):
            data.exchanges.append(current)
        data.exchanges = [ex for ex in data.exchanges if ex.user or ex.tools]
        try:
            data.artifacts = sorted(f for f in session_dir.glob("*.md") if f.is_file() and not f.name.startswith("."))
        except OSError:
            data.artifacts = []
        return data

    # ---- Codex ----------------------------------------------------------
    @staticmethod
    def scan_codex(store: GalaxyStore, max_sessions: int = 60) -> int:
        """Scans local OpenAI Codex sessions (~/.codex/sessions and archived_sessions)."""
        stats = _new_stats("codex")
        codex_dir = Path.home() / ".codex"
        if not codex_dir.exists():
            return 0

        thread_names: Dict[str, str] = {}
        index_path = codex_dir / "session_index.jsonl"
        if index_path.exists():
            for item in _iter_jsonl(index_path):
                sid = str(item.get("id", ""))
                tname = str(item.get("thread_name", "")).strip()
                if sid and tname:
                    thread_names[sid] = tname

        rollout_files: List[Path] = []
        for subdir_name in ("sessions", "archived_sessions"):
            s_dir = codex_dir / subdir_name
            if s_dir.exists():
                try:
                    rollout_files.extend(p for p in s_dir.rglob("*.jsonl") if p.is_file())
                except OSError:
                    pass
        rollout_files.sort(key=_safe_mtime, reverse=True)

        sessions: List[SessionData] = []
        for r_file in rollout_files[:max_sessions]:
            stats["sessions_found"] += 1
            try:
                data = AgentHistoryImporter._parse_codex_rollout(r_file)
            except Exception:
                stats["sessions_failed"] += 1
                logger.exception("Failed to parse Codex rollout %s", r_file)
                continue
            data.title = thread_names.get(data.session_id, "")
            stats["sessions_parsed"] += 1
            sessions.append(data)
        return AgentHistoryImporter._import_sessions(store, "codex", "OpenAI Codex", sessions, stats)

    @staticmethod
    def _codex_tool_files(name: str, raw_args: object, cwd: str) -> Tuple[List[Tuple[str, bool]], List[str]]:
        files: List[Tuple[str, bool]] = []
        hints: List[str] = []
        args: object = raw_args
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except ValueError:
                args = raw_args
        patch_texts: List[str] = []
        workdir = cwd
        if isinstance(args, str):
            if "*** Begin Patch" in args:
                patch_texts.append(args)
        elif isinstance(args, dict):
            workdir = str(args.get("workdir") or args.get("working_directory") or args.get("cwd") or cwd)
            if workdir and workdir != cwd:
                hints.append(workdir)
            cmd = args.get("command") or args.get("cmd")
            cmd_text = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd or "")
            if "*** Begin Patch" in cmd_text:
                patch_texts.append(cmd_text)
            elif cmd_text:
                hints.extend(_extract_paths_from_text(cmd_text))
            for key in ("input", "patch"):
                if isinstance(args.get(key), str) and "*** Begin Patch" in args[key]:
                    patch_texts.append(args[key])
            file_like, more = _paths_from_args(
                {k: v for k, v in args.items() if k not in ("command", "cmd", "workdir", "working_directory", "cwd")}
            )
            edited = bool(_EDIT_TOOL_RE.search(name))
            files.extend((_join_path(workdir, p), edited) for p in file_like)
            hints.extend(more)
        for text in patch_texts:
            for m in list(_PATCH_FILE_RE.finditer(text)) + list(_PATCH_MOVE_RE.finditer(text)):
                files.append((_join_path(workdir, m.group(1).strip()), True))
        return files, hints

    @staticmethod
    def _parse_codex_rollout(file_path: Path) -> SessionData:
        data = SessionData(session_id=file_path.stem, mtime=_safe_mtime(file_path), log_path=str(file_path))
        current: Optional[Exchange] = None

        for obj in _iter_jsonl(file_path):
            _note_entry_time(data, obj)
            obj_type = obj.get("type", "")
            payload = obj.get("payload", {}) or {}
            if not isinstance(payload, dict):
                continue
            if obj_type == "session_meta":
                data.session_id = str(payload.get("session_id") or payload.get("id") or data.session_id)
                data.cwd = data.cwd or str(payload.get("cwd") or "")
                roots = payload.get("runtime_workspace_roots") or []
                if roots and not data.cwd:
                    data.cwd = str(roots[0])
            elif obj_type == "turn_context":
                if not data.cwd and payload.get("cwd"):
                    data.cwd = str(payload["cwd"])
            elif obj_type == "response_item":
                p_type = payload.get("type", "")
                if p_type == "message":
                    role = payload.get("role", "")
                    text_parts = [str(c.get("text", "")) for c in (payload.get("content") or []) if isinstance(c, dict) and c.get("text")]
                    full_text = "\n".join(text_parts).strip()
                    if not full_text or "The following is the Codex agent history" in full_text:
                        continue
                    if role == "user":
                        cleaned = _clean_user_request(full_text)
                        if cleaned and not cleaned.startswith("<") and not cleaned.lower().startswith("# agents.md instructions"):
                            if current is not None:
                                data.exchanges.append(current)
                            current = Exchange(user=cleaned)
                    elif role == "assistant":
                        if current is None:
                            current = Exchange(user="")
                        current.ai = (current.ai + "\n\n" + full_text).strip() if current.ai else full_text
                elif p_type in {"function_call", "custom_tool_call", "local_shell_call"}:
                    if current is None:
                        current = Exchange(user="")
                    fn_name = str(payload.get("name") or p_type)
                    raw = payload.get("arguments")
                    if raw is None:
                        raw = payload.get("input")
                    if raw is None and isinstance(payload.get("action"), dict):
                        raw = payload["action"]
                    files, hints = AgentHistoryImporter._codex_tool_files(fn_name, raw, data.cwd)
                    current.tools.append(fn_name)
                    current.files.extend(files)
                    data.tool_paths.extend([p for p, _ in files] + hints)
        if current is not None:
            data.exchanges.append(current)
        data.exchanges = [ex for ex in data.exchanges if ex.user or ex.tools]
        return data

    # ---- Claude Code ----------------------------------------------------
    @staticmethod
    def _is_subagent_file(path: Path) -> bool:
        return path.name.startswith("agent-") or "subagents" in path.parts

    @staticmethod
    def scan_claude_code(store: GalaxyStore, max_sessions: int = 12) -> int:
        """Scans local Claude Code project histories (~/.claude/projects) and merges by project root."""
        stats = _new_stats("claude_code")
        claude_dir = Path.home() / ".claude" / "projects"
        if not claude_dir.exists():
            return 0
        try:
            jsonl_files = [p for p in claude_dir.rglob("*.jsonl") if p.is_file() and not AgentHistoryImporter._is_subagent_file(p)]
        except OSError:
            jsonl_files = []
        jsonl_files.sort(key=_safe_mtime, reverse=True)

        sessions: List[SessionData] = []
        for j_file in jsonl_files[:max_sessions]:
            stats["sessions_found"] += 1
            try:
                data = AgentHistoryImporter._parse_claude_session(j_file)
            except Exception:
                stats["sessions_failed"] += 1
                logger.exception("Failed to parse Claude Code session %s", j_file)
                continue
            if not data.cwd:
                data.cwd = AgentHistoryImporter._decode_claude_project_dir(j_file.parent.name)
            stats["sessions_parsed"] += 1
            sessions.append(data)
        return AgentHistoryImporter._import_sessions(store, "claude_code", "Claude Code", sessions, stats)

    @staticmethod
    def _decode_claude_project_dir(dir_name: str) -> str:
        """Best-effort decoding of a Claude Code project folder name (cwd with separators
        replaced by dashes). Only used when no `cwd` field exists, and only accepted if
        the decoded path exists on this machine (the encoding is lossy)."""
        m = re.match(r"^([A-Za-z])--(.*)$", dir_name)
        if m:
            cand = f"{m.group(1)}:\\" + m.group(2).replace("-", "\\")
        elif dir_name.startswith("-"):
            cand = "/" + dir_name[1:].replace("-", "/")
        else:
            return ""
        return cand if _exists_locally(cand) else ""

    @staticmethod
    def _parse_claude_session(path: Path) -> SessionData:
        data = SessionData(session_id=path.stem, mtime=_safe_mtime(path), log_path=str(path))
        current: Optional[Exchange] = None
        cwd_counter: Counter = Counter()

        for item in _iter_jsonl(path):
            if item.get("isSidechain") is True:
                continue
            _note_entry_time(data, item)
            etype = item.get("type")
            if etype == "summary":
                if item.get("summary") and not data.title:
                    data.title = str(item["summary"]).strip()[:120]
                continue
            if etype not in ("user", "assistant"):
                continue
            if item.get("cwd"):
                cwd_counter[str(item["cwd"])] += 1
            if item.get("isMeta") is True:
                continue
            msg = item.get("message") or {}
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or etype
            content = msg.get("content")

            if role == "user":
                texts: List[str] = []
                if isinstance(content, str):
                    texts = [content]
                elif isinstance(content, list):
                    if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                        continue  # tool output turn, not a user request
                    texts = [str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("type") == "text"]
                text = "\n".join(t for t in texts if t).strip()
                if not text or text.startswith(("<command-", "<local-command", "Caveat:", "[Request interrupted")):
                    continue
                cleaned = _clean_user_request(text)
                if not cleaned:
                    continue
                if current is not None:
                    data.exchanges.append(current)
                current = Exchange(user=cleaned)
            elif role == "assistant":
                if current is None:
                    current = Exchange(user="")
                blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text"):
                        txt = str(b["text"]).strip()
                        if len(current.ai) < 20_000:
                            current.ai = (current.ai + "\n\n" + txt).strip() if current.ai else txt
                    elif b.get("type") == "tool_use":
                        t_name = str(b.get("name", "tool"))
                        inp = b.get("input") or {}
                        current.tools.append(t_name)
                        if not isinstance(inp, dict):
                            continue
                        edited = bool(_EDIT_TOOL_RE.search(t_name))
                        for k in ("file_path", "notebook_path"):
                            if isinstance(inp.get(k), str) and inp[k].strip():
                                p = _clean_path_str(inp[k])
                                current.files.append((p, edited))
                                data.tool_paths.append(p)
                        if isinstance(inp.get("path"), str) and inp["path"].strip():
                            p = _clean_path_str(inp["path"])
                            data.tool_paths.append(p)
                            if edited:
                                current.files.append((p, True))
        if current is not None:
            data.exchanges.append(current)
        data.exchanges = [ex for ex in data.exchanges if ex.user or ex.tools]
        if cwd_counter:
            data.cwd = cwd_counter.most_common(1)[0][0]
        return data
