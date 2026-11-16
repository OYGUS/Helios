"""External libraries, the library table (`TECH_FINGERPRINTS`: capabilities for purpose
evidence + tech detection) and dependency manifests.

   **External Libraries** — one extra module (`kind="external"`, layer `integration`)
   when the project imports third-party packages: one `kind="package"` sub-component per
   package actually imported (Python: top-level module names that are neither standard
   library — `sys.stdlib_module_names`, small fallback list — nor project modules;
   JS/TS: bare specifiers reduced to the package name incl. `@scope/pkg`, Node built-ins
   and `node:`/other `scheme:` specifiers excluded; PHP: Composer `require` packages from
   `composer.json` plus `use`d namespaces mapped to a package through
   `vendor/composer/autoload_psr4.php`). Each lists the importing files and whether and
   where it is declared (requirements*.txt, pyproject.toml, Pipfile, setup.py
   `install_requires`, package.json, composer.json) with the version spec. Python import
   names are matched to distribution names by normalisation plus a small alias table
   (`yaml` = PyYAML, `cv2` = opencv-python, ...). No links are drawn to this module.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

try:  # Python 3.11+
    import tomllib as _tomllib  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - Python 3.10 fallback
    _tomllib = None

from connectors.codebase.constants import MAX_PARSE_BYTES, _JS_EXT_SET, _STDLIB_MODULES
from connectors.codebase.fileinfo import _FileInfo
from connectors.codebase.walk import _is_backup_dir, _should_skip_dir


# ---------------------------------------------------------------------------
# External libraries
# ---------------------------------------------------------------------------
@dataclass
class _External:
    name: str
    ecosystem: str  # "Python", "npm", "Composer"
    importers: Set[str] = field(default_factory=set)
    declared: str = ""  # manifest rel path
    spec: str = ""  # declared version spec
    note: str = ""


#: Python import name -> distribution names (only to decide whether it is declared).
_PY_IMPORT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "yaml": ("pyyaml",), "cv2": ("opencv-python", "opencv-python-headless", "opencv-contrib-python"),
    "pil": ("pillow",), "sklearn": ("scikit-learn",), "bs4": ("beautifulsoup4",), "dotenv": ("python-dotenv",),
    "whisper": ("openai-whisper",), "skimage": ("scikit-image",), "dateutil": ("python-dateutil",),
    "jwt": ("pyjwt",), "serial": ("pyserial",), "usb": ("pyusb",), "magic": ("python-magic",),
    "telegram": ("python-telegram-bot",), "docx": ("python-docx",), "pptx": ("python-pptx",),
    "speech_recognition": ("speechrecognition",), "win32api": ("pywin32",), "win32con": ("pywin32",),
    "pythoncom": ("pywin32",), "fitz": ("pymupdf",), "google": ("google-api-python-client", "protobuf"),
    "attr": ("attrs",), "crypto": ("pycryptodome",), "git": ("gitpython",), "zmq": ("pyzmq",),
}


def _collect_externals(files: List[_FileInfo], manifests: "_Manifests", php_unresolved: Dict[str, Set[str]]) -> List[_External]:
    found: Dict[Tuple[str, str], _External] = {}
    project_names: Set[str] = set()
    for f in files:
        if f.ext == ".py":
            parts = f.rel.split("/")
            project_names.update(parts[:-1])
            project_names.add(posixpath.splitext(parts[-1])[0])
    for f in files:
        if f.ext == ".py":
            for n in f.external_imports:
                if n in _STDLIB_MODULES or n in project_names or not n.isidentifier():
                    continue
                ext = found.setdefault(("py", n), _External(n, "Python"))
                ext.importers.add(f.rel)
        elif f.ext in _JS_EXT_SET:
            for n in f.external_imports:
                ext = found.setdefault(("npm", n), _External(n, "npm"))
                ext.importers.add(f.rel)
    for ext in found.values():
        if ext.ecosystem == "Python":
            keys = [_norm_dep(ext.name)] + [_norm_dep(a) for a in _PY_IMPORT_ALIASES.get(ext.name.lower(), ())]
            for k in keys:
                if k in manifests.py_specs:
                    ext.spec, ext.declared = manifests.py_specs[k]
                    break
        else:
            if ext.name in manifests.npm_specs:
                ext.spec, ext.declared = manifests.npm_specs[ext.name]
    # PHP / Composer
    for name, (spec, where) in manifests.composer.items():
        found[("composer", name)] = _External(name, "Composer", declared=where, spec=spec)
    for use, users in php_unresolved.items():
        pkg = ""
        for prefix, package in manifests.psr4:
            if use.lower().startswith(prefix.lower()):
                pkg = package
                break
        if pkg:
            ext = found.setdefault(("composer", pkg), _External(pkg, "Composer"))
            ext.importers.update(users)
        elif manifests.composer:
            root_ns = use.split("\\")[0] + "\\"
            ext = found.setdefault(("composer-ns", root_ns), _External(root_ns, "Composer"))
            ext.importers.update(users)
            ext.note = "namespace used in `use` statements; package not identified (no vendor/composer/autoload_psr4.php)"
    return sorted(found.values(), key=lambda e: (-len(e.importers), e.name.lower()))


# ---------------------------------------------------------------------------
# Library table: capabilities (purpose evidence) + tech detection (dependency names)
# ---------------------------------------------------------------------------
class _Tech(NamedTuple):
    """One library family. `capability` is the plain phrase used in the purpose evidence
    ("speech-to-text"); `arch_type` / `description` (may be empty) feed the core plan's
    "Type" / "Technologies" lines. `exact` are package or import names (normalised with
    `_norm_dep` when matched), `prefixes` match scoped / family names."""

    capability: str
    arch_type: str
    description: str
    exact: Tuple[str, ...]
    prefixes: Tuple[str, ...] = ()


#: The single library table. Order = order of the capability line.
TECH_FINGERPRINTS: Dict[str, _Tech] = {
    "speech_to_text": _Tech("speech-to-text", "", "", ("faster-whisper", "openai-whisper", "whisper", "whisperx", "vosk", "speechrecognition", "speech-recognition", "pywhispercpp", "deepgram-sdk", "assemblyai")),
    "audio_input": _Tech("microphone/audio input", "", "", ("sounddevice", "pyaudio", "soundcard", "pvrecorder", "pvporcupine", "node-record-lpcm16", "mic")),
    "text_to_speech": _Tech("text-to-speech", "", "", ("pyttsx3", "edge-tts", "gtts", "elevenlabs", "say")),
    "ollama": _Tech("LLM / AI model", "Local LLM Integration", "Ollama local AI inference", ("ollama",)),
    "langchain": _Tech("LLM / AI model", "LLM & Agent Workflow", "LangChain language model chains and autonomous agents", ("langchain",), ("langchain-", "@langchain/")),
    "llm": _Tech("LLM / AI model", "", "", ("openai", "anthropic", "transformers", "llama-cpp", "llama-cpp-python", "google-generativeai", "google-genai", "groq", "mistralai", "cohere", "@anthropic-ai/sdk", "@google/generative-ai", "@google/genai", "litellm", "gpt4all")),
    "keyboard": _Tech("keyboard/mouse automation", "", "", ("keyboard", "pynput", "pyautogui", "mouse", "robotjs", "@nut-tree/nut-js", "pydirectinput", "autoit")),
    "system": _Tech("system/process monitoring", "", "", ("psutil", "systeminformation", "gputil", "wmi", "pywin32", "win32api", "win32con")),
    "discord": _Tech("Discord bot", "Discord Bot Service", "Discord API bot and community automation", ("discord", "discord-py", "discord.js", "py-cord", "nextcord", "discord.js-selfbot-v13", "disnake", "eris")),
    "telethon": _Tech("Telegram bot", "Telegram Bot & Automation", "Telethon MTProto API Telegram automation", ("telethon",)),
    "aiogram": _Tech("Telegram bot", "Telegram Bot Service", "Aiogram asynchronous bot framework", ("aiogram",)),
    "telegram": _Tech("Telegram bot", "", "", ("python-telegram-bot", "telegram", "telegraf", "node-telegram-bot-api", "grammy", "pytelegrambotapi", "telebot")),
    "whatsapp": _Tech("WhatsApp bot", "", "", ("whatsapp-web.js", "@whiskeysockets/baileys", "baileys", "venom-bot", "@wppconnect-team/wppconnect")),
    "fastapi": _Tech("web server / API", "High-Performance REST API", "FastAPI asynchronous microservice / API backend", ("fastapi",)),
    "flask": _Tech("web server / API", "Web Service / REST API", "Flask micro web framework service", ("flask",)),
    "django": _Tech("web server / API", "Full-Featured Web Platform", "Django database-driven web framework", ("django",)),
    "express": _Tech("web server / API", "Node.js Backend & API Service", "Express.js RESTful API infrastructure", ("express",)),
    "nestjs": _Tech("web server / API", "Enterprise TypeScript Backend", "NestJS modular backend service", (), ("@nestjs/",)),
    "spring": _Tech("web server / API", "Enterprise Java Service", "Spring Boot backend service", (), ("org.springframework",)),
    "web_server": _Tech("web server / API", "", "", ("koa", "fastify", "@hapi/hapi", "hapi", "sanic", "tornado", "bottle", "starlette", "laravel/framework", "slim/slim", "symfony/framework-bundle")),
    "realtime": _Tech("real-time communication", "", "", ("socket.io", "socket.io-client", "websockets", "ws", "python-socketio", "pusher", "pusher-js")),
    "sqlalchemy": _Tech("database", "Database ORM & Model Layer", "SQLAlchemy relational database abstraction", ("sqlalchemy",)),
    "prisma": _Tech("database", "Database ORM Layer", "Prisma type-safe database client", ("prisma",), ("@prisma/",)),
    "mongoose": _Tech("database", "NoSQL / MongoDB Data Layer", "Mongoose document object modeling", ("mongoose",)),
    "sqlite3": _Tech("database", "Embedded Database Store", "SQLite local file-based storage", ("sqlite3", "better-sqlite3")),
    "database": _Tech("database", "", "", ("mysql2", "mysql", "pg", "psycopg2", "psycopg2-binary", "psycopg", "pymysql", "mysql-connector-python", "mongodb", "pymongo", "redis", "sequelize", "typeorm", "knex", "peewee", "aiosqlite", "firebase-admin", "@supabase/supabase-js", "supabase", "PDO", "mysqli")),
    "react": _Tech("web frontend", "Web Frontend & Component UI", "React-based web interface", ("react", "react-dom")),
    "next": _Tech("web frontend", "Full-Stack Web Portal", "Next.js web application", ("next",)),
    "vue": _Tech("web frontend", "Web Frontend UI", "Vue.js single-page application", ("vue", "nuxt")),
    "svelte": _Tech("web frontend", "Reactive Web UI", "Svelte compiler-based web interface", ("svelte",), ("@sveltejs/",)),
    "frontend": _Tech("web frontend", "", "", ("preact", "solid-js", "@angular/core", "jquery", "alpinejs")),
    "tailwindcss": _Tech("", "Styling & Design System", "TailwindCSS utility-first styling", ("tailwindcss",)),
    "pyside6": _Tech("desktop GUI", "Desktop Graphics & UI Application", "PySide6 (Qt6) desktop graphical user interface", ("pyside6", "pyside6-essentials", "pyside6-addons")),
    "pyqt6": _Tech("desktop GUI", "Desktop Graphics & UI Application", "PyQt6 desktop windowing system", ("pyqt6",)),
    "pyqt5": _Tech("desktop GUI", "Desktop GUI Application", "PyQt5 desktop windowing system", ("pyqt5",)),
    "tkinter": _Tech("desktop GUI", "Desktop GUI Application", "Tkinter interface library", ("tkinter",)),
    "electron": _Tech("desktop GUI", "Cross-Platform Desktop Application", "Electron-based desktop system", ("electron",)),
    "tauri": _Tech("desktop GUI", "Lightweight Desktop Application", "Rust & Webview Tauri application", ("tauri",), ("@tauri-apps/",)),
    "desktop": _Tech("desktop GUI", "", "", ("customtkinter", "kivy", "wx", "wxpython", "pyqt4", "pyside2", "flet", "dearpygui", "eel", "pywebview")),
    "game": _Tech("game / multimedia", "", "", ("pygame", "pyglet", "arcade", "phaser", "pixi.js", "ursina", "panda3d")),
    "graphics3d": _Tech("3D graphics", "", "", ("three", "babylonjs", "@babylonjs/core")),
    "http_client": _Tech("HTTP client", "", "", ("requests", "axios", "httpx", "node-fetch", "aiohttp", "got", "urllib3", "undici", "guzzlehttp/guzzle")),
    "selenium": _Tech("browser automation / scraping", "Web Automation & Testing", "Selenium browser automation and scraping", ("selenium",)),
    "playwright": _Tech("browser automation / scraping", "End-to-End Web Automation", "Playwright browser automation", ("playwright",), ("@playwright/",)),
    "scrapy": _Tech("browser automation / scraping", "Web Crawling Engine", "Scrapy data extraction framework", ("scrapy",)),
    "scraping": _Tech("browser automation / scraping", "", "", ("puppeteer", "puppeteer-core", "beautifulsoup4", "bs4", "cheerio", "undetected-chromedriver", "pyppeteer", "mechanize")),
    "pandas": _Tech("data processing / spreadsheets", "Data Analysis & Processing Engine", "Pandas tabular data analytics", ("pandas",)),
    "scipy": _Tech("data processing / spreadsheets", "Scientific Computing Engine", "SciPy numerical analysis", ("scipy",)),
    "data": _Tech("data processing / spreadsheets", "", "", ("numpy", "openpyxl", "xlsxwriter", "polars", "xlrd", "exceljs", "xlsx", "gspread")),
    "charts": _Tech("charts / plotting", "", "", ("matplotlib", "plotly", "seaborn", "chart.js", "recharts", "d3")),
    "torch": _Tech("machine learning", "Deep Learning & AI", "PyTorch neural network training and inference", ("torch",)),
    "tensorflow": _Tech("machine learning", "AI & Machine Learning Model", "TensorFlow machine learning system", ("tensorflow", "tensorflow-gpu")),
    "ml": _Tech("machine learning", "", "", ("onnxruntime", "scikit-learn", "sklearn", "keras", "xgboost", "lightgbm", "@tensorflow/tfjs")),
    "vision": _Tech("computer vision / camera", "", "", ("opencv-python", "opencv-python-headless", "cv2", "mediapipe", "ultralytics", "face-recognition", "face_recognition")),
    "images": _Tech("image processing", "", "", ("pillow", "pil", "sharp", "jimp", "imageio", "wand")),
    "audio": _Tech("audio processing / playback", "", "", ("pydub", "playsound", "simpleaudio", "librosa", "soundfile", "pygame.mixer", "howler")),
    "pdf": _Tech("PDF handling", "", "", ("pypdf", "pypdf2", "reportlab", "pdfkit", "fpdf", "fpdf2", "pdf-lib", "pymupdf", "fitz", "pdfplumber", "pdfkit", "tcpdf", "dompdf/dompdf")),
    "documents": _Tech("Word/PowerPoint documents", "", "", ("python-docx", "docx", "python-pptx", "pptx", "docxtpl")),
    "email": _Tech("sending email", "", "", ("smtplib", "nodemailer", "yagmail", "phpmailer/phpmailer", "sendgrid", "@sendgrid/mail")),
    "payments": _Tech("payments", "", "", ("stripe", "iyzipay", "paypalrestsdk", "@paypal/checkout-server-sdk", "iyzico/iyzipay-php")),
    "scheduling": _Tech("scheduled tasks", "", "", ("schedule", "apscheduler", "node-cron", "cron", "celery")),
    "cli": _Tech("command-line interface", "", "", ("argparse", "click", "typer", "commander", "yargs", "inquirer", "rich", "fire")),
    "crypto_trading": _Tech("crypto / exchange trading", "", "", ("ccxt", "python-binance", "binance", "web3", "ethers")),
    "maps": _Tech("maps / geolocation", "", "", ("leaflet", "geopy", "folium", "mapbox-gl", "@react-google-maps/api")),
    "notifications": _Tech("desktop notifications", "", "", ("plyer", "win10toast", "node-notifier")),
    "translation": _Tech("translation", "", "", ("googletrans", "deep-translator", "deepl", "argostranslate")),
}
#: Standard-library entries: only seen as imports (never declared as dependencies).
_IMPORT_ONLY_TECH = {"tkinter", "sqlite3"}
_STDLIB_CAPABILITY_NAMES = {"tkinter", "sqlite3", "argparse", "smtplib"}


def _norm_dep(name: str) -> str:
    n = name.strip().lower()
    if n.startswith("@") or n.startswith("org."):
        return n
    return re.sub(r"[_.]+", "-", n) if n not in {"discord.js"} else n


_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _parse_requirement_names(lines: Iterable[str]) -> List[str]:
    names = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http:", "https:", "file:")):
            continue
        m = _REQ_NAME_RE.match(line)
        if m:
            names.append(m.group(1))
    return names


def _toml_load(text: str) -> Optional[dict]:
    if _tomllib is None:
        return None
    try:
        return _tomllib.loads(text)
    except Exception:
        return None


def _toml_section_keys(text: str, section_re: str) -> List[str]:
    """Regex fallback: keys of TOML tables whose header matches `section_re`."""
    keys: List[str] = []
    current = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            current = s.strip("[] ")
            continue
        if current and re.fullmatch(section_re, current):
            m = re.match(r"^\"?([A-Za-z0-9][A-Za-z0-9._-]*)\"?\s*=", s)
            if m:
                keys.append(m.group(1))
    return keys


def _toml_array_strings(text: str, key: str) -> List[str]:
    """Regex fallback: string items of `key = [ ... ]` arrays (multi-line)."""
    out: List[str] = []
    for m in re.finditer(rf"(?m)^\s*{re.escape(key)}\s*=\s*\[(.*?)\]", text, flags=re.DOTALL):
        out.extend(re.findall(r"[\"']([^\"']+)[\"']", m.group(1)))
    return out


def _pyproject_dependency_names(text: str) -> List[str]:
    names: List[str] = []
    data = _toml_load(text)
    if data is not None:
        project = data.get("project") or {}
        reqs = list(project.get("dependencies") or [])
        for group in (project.get("optional-dependencies") or {}).values():
            reqs.extend(group or [])
        for group in (data.get("dependency-groups") or {}).values():
            reqs.extend(x for x in (group or []) if isinstance(x, str))
        names.extend(_parse_requirement_names(str(r) for r in reqs))
        poetry = (data.get("tool") or {}).get("poetry") or {}
        tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
        for grp in (poetry.get("group") or {}).values():
            tables.append((grp or {}).get("dependencies") or {})
        for tbl in tables:
            names.extend(k for k in tbl.keys() if k.lower() != "python")
        return names
    # Regex fallback (Python 3.10 without tomllib)
    names.extend(_parse_requirement_names(_toml_array_strings(text, "dependencies")))
    for m in re.finditer(r"(?ms)^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)", text):
        names.extend(_parse_requirement_names(re.findall(r"[\"']([^\"']+)[\"']", m.group(1))))
    for k in _toml_section_keys(text, r"tool\.poetry\.(?:dev-)?dependencies|tool\.poetry\.group\.[\w-]+\.dependencies"):
        if k.lower() != "python":
            names.append(k)
    return names


def _pipfile_dependency_names(text: str) -> List[str]:
    data = _toml_load(text)
    if data is not None:
        return list((data.get("packages") or {}).keys()) + list((data.get("dev-packages") or {}).keys())
    return _toml_section_keys(text, r"packages|dev-packages")

_REQ_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;#]*)")


def _parse_requirement_specs(lines: Iterable[str]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http:", "https:", "file:")):
            continue
        m = _REQ_SPEC_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


@dataclass
class _Manifests:
    """Declared dependencies of a project (manifests in the root and its direct
    sub-directories)."""

    names: Set[str] = field(default_factory=set)  # normalised names of every ecosystem
    description: str = ""
    py_specs: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # norm name -> (spec, manifest rel)
    npm_specs: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # name -> (spec, manifest rel)
    composer: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # package -> (spec, manifest rel)
    psr4: List[Tuple[str, str]] = field(default_factory=list)  # (namespace prefix, package) from vendor autoload
    packages: List[Tuple[str, dict]] = field(default_factory=list)  # (dir rel, parsed package.json)
    #: (manifest rel, description) of the project root, in priority order: package.json,
    #: pyproject.toml, composer.json, Cargo.toml, setup.py, setup.cfg
    own_descriptions: List[Tuple[str, str]] = field(default_factory=list)


def _collect_manifests(folder: Path, extra_package_dirs: Iterable[str] = ()) -> _Manifests:
    """Reads package.json, requirements*.txt, pyproject.toml, Pipfile, setup.py,
    composer.json (+ vendor/composer/autoload_psr4.php), pom.xml and build.gradle in
    `folder` and its direct (non-ignored) sub-directories, plus package.json files in
    `extra_package_dirs` (relative paths found by the walk)."""
    man = _Manifests()
    dirs: List[Tuple[str, Path]] = [("", folder)]
    try:
        for child in sorted(folder.iterdir()):
            if child.is_dir() and not child.is_symlink() and not _should_skip_dir(child.name, []) and not _is_backup_dir(child.name):
                dirs.append((child.name, child))
    except OSError:
        pass
    known = {d for d, _ in dirs}
    for rel_dir in sorted(set(extra_package_dirs) - known):
        dirs.append((rel_dir, folder / rel_dir))

    def read(p: Path) -> str:
        try:
            if p.is_file() and p.stat().st_size <= MAX_PARSE_BYTES:
                return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        return ""

    def rel_of(d: str, name: str) -> str:
        return f"{d}/{name}" if d else name

    def add_py(name: str, spec: str, where: str) -> None:
        man.names.add(_norm_dep(name))
        man.py_specs.setdefault(_norm_dep(name), (spec, where))

    root_desc: Dict[str, str] = {}
    for d, path in dirs:
        top_level = "/" not in d
        pkg_text = read(path / "package.json")
        if pkg_text:
            try:
                pkg = json.loads(pkg_text)
            except Exception:
                pkg = None
            if isinstance(pkg, dict):
                man.packages.append((d, pkg))
                if d == "" and str(pkg.get("description", "")).strip():
                    man.description = str(pkg["description"]).strip()
                    root_desc["package.json"] = man.description
                for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    deps = pkg.get(key) or {}
                    if isinstance(deps, dict):
                        for n, v in deps.items():
                            man.names.add(_norm_dep(n))
                            man.npm_specs.setdefault(n, (str(v), rel_of(d, "package.json")))
        if not top_level:
            continue
        try:
            req_files = sorted(path.glob("requirements*.txt"))
        except OSError:
            req_files = []
        for rf in req_files:
            for n, spec in _parse_requirement_specs(read(rf).splitlines()):
                add_py(n, spec, rel_of(d, rf.name))
        py_text = read(path / "pyproject.toml")
        if py_text:
            data = _toml_load(py_text)
            if d == "":
                desc = ""
                if data is not None:
                    desc = str((data.get("project") or {}).get("description") or ((data.get("tool") or {}).get("poetry") or {}).get("description") or "")
                else:
                    m = re.search(r"^description\s*=\s*([\"'])(.+?)\1\s*$", py_text, re.MULTILINE)
                    desc = m.group(2) if m else ""
                if desc.strip():
                    root_desc["pyproject.toml"] = desc.strip()
            if data is not None:
                project = data.get("project") or {}
                reqs = list(project.get("dependencies") or [])
                for group in (project.get("optional-dependencies") or {}).values():
                    reqs.extend(group or [])
                for n, spec in _parse_requirement_specs(str(r) for r in reqs):
                    add_py(n, spec, rel_of(d, "pyproject.toml"))
                poetry = (data.get("tool") or {}).get("poetry") or {}
                tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
                for grp in (poetry.get("group") or {}).values():
                    tables.append((grp or {}).get("dependencies") or {})
                for tbl in tables:
                    for k, v in tbl.items():
                        if k.lower() != "python":
                            add_py(k, v if isinstance(v, str) else str((v or {}).get("version", "")) if isinstance(v, dict) else "", rel_of(d, "pyproject.toml"))
            for n in _pyproject_dependency_names(py_text):
                if _norm_dep(n) not in man.py_specs:
                    add_py(n, "", rel_of(d, "pyproject.toml"))
        pip_text = read(path / "Pipfile")
        if pip_text:
            for n in _pipfile_dependency_names(pip_text):
                add_py(n, "", rel_of(d, "Pipfile"))
        setup_text = read(path / "setup.py")
        if setup_text and d == "":
            m = re.search(r"(?<![\w])description\s*=\s*([\"'])(.+?)\1", setup_text)
            if m and m.group(2).strip():
                root_desc["setup.py"] = m.group(2).strip()
        if d == "":
            cfg_text = read(path / "setup.cfg")
            m = re.search(r"^description\s*=\s*(.+?)\s*$", cfg_text, re.MULTILINE) if cfg_text else None
            if m and m.group(1).strip():
                root_desc["setup.cfg"] = m.group(1).strip()
            cargo_text = read(path / "Cargo.toml")
            m = re.search(r"^description\s*=\s*([\"'])(.+?)\1\s*$", cargo_text, re.MULTILINE) if cargo_text else None
            if m and m.group(2).strip():
                root_desc["Cargo.toml"] = m.group(2).strip()
        if setup_text:
            for n, spec in _parse_requirement_specs(_toml_array_strings(setup_text, "install_requires")):
                add_py(n, spec, rel_of(d, "setup.py"))
        comp_text = read(path / "composer.json")
        if comp_text:
            try:
                comp = json.loads(comp_text)
            except Exception:
                comp = None
            if isinstance(comp, dict):
                if d == "" and str(comp.get("description") or "").strip():
                    root_desc["composer.json"] = str(comp["description"]).strip()
                for key in ("require", "require-dev"):
                    req = comp.get(key) or {}
                    if isinstance(req, dict):
                        for n, v in req.items():
                            if n.lower() == "php" or n.lower().startswith(("ext-", "lib-")):
                                continue
                            man.names.add(_norm_dep(n))
                            man.composer.setdefault(n, (str(v), rel_of(d, "composer.json")))
            psr4 = read(path / "vendor" / "composer" / "autoload_psr4.php")
            for m in re.finditer(r"'((?:[^'\\]|\\\\)+)'\s*=>\s*array\(\s*\$vendorDir\s*\.\s*'/([^/']+/[^/']+)", psr4):
                man.psr4.append((m.group(1).replace("\\\\", "\\"), m.group(2)))
        pom = read(path / "pom.xml")
        if pom:
            man.names.update(_norm_dep(n) for n in re.findall(r"<groupId>\s*([\w.\-]+)\s*</groupId>", pom))
        for gradle in ("build.gradle", "build.gradle.kts"):
            g_text = read(path / gradle)
            if g_text:
                man.names.update(
                    _norm_dep(m.split(":")[0]) for m in re.findall(r"[\"']([\w.\-]+:[\w.\-]+(?::[^\"']*)?)[\"']", g_text)
                )
    man.psr4.sort(key=lambda x: -len(x[0]))
    order = ("package.json", "pyproject.toml", "composer.json", "Cargo.toml", "setup.py", "setup.cfg")
    man.own_descriptions = [(k, root_desc[k]) for k in order if k in root_desc]
    return man


def _collect_dependency_names(folder: Path) -> Tuple[Set[str], str]:
    """Declared dependency names (normalised) and the package description."""
    man = _collect_manifests(folder)
    return {n for n in man.names if n}, man.description


def _match_fingerprints(dep_names: Set[str], imported: Set[str]) -> List[Tuple[str, str, str]]:
    """Returns (key, matched name, how) for table entries with a technology description,
    matched exactly or by prefix against declared dependency names (stdlib-only entries
    such as tkinter / sqlite3 also by import)."""
    hits: List[Tuple[str, str, str]] = []
    for key, tech in TECH_FINGERPRINTS.items():
        if not tech.description:
            continue
        exact_n = {_norm_dep(e) for e in tech.exact}
        matched = sorted(dep_names & exact_n)
        if not matched and tech.prefixes:
            matched = sorted(n for n in dep_names if any(n.startswith(p) for p in tech.prefixes))
        if matched:
            hits.append((key, matched[0], "declared dependency"))
            continue
        if key in _IMPORT_ONLY_TECH and key in imported:
            hits.append((key, key, "imported in source"))
    return hits


def _detect_project_technologies(folder: Path, imported: Optional[Set[str]] = None, manifests: Optional[_Manifests] = None) -> Tuple[List[str], List[str], str]:
    """Detects technologies from declared dependency NAMES (package.json, requirements*.txt,
    pyproject.toml, Pipfile, setup.py, composer.json, pom.xml, build.gradle) plus
    stdlib-only imports. Returns (architectural types, tech stack lines, package description)."""
    man = manifests if manifests is not None else _collect_manifests(folder)
    dep_names = {n for n in man.names if n}
    detected_types: List[str] = []
    tech_stack: List[str] = []
    for key, matched, how in _match_fingerprints(dep_names, imported or set()):
        tech = TECH_FINGERPRINTS[key]
        t_type, t_desc = tech.arch_type, tech.description
        if t_type not in detected_types:
            detected_types.append(t_type)
        tech_stack.append(f"{t_desc} (`{matched}`, {how})")
    if (folder / "Cargo.toml").exists():
        detected_types.append("Rust Systems & Performance Application")
        tech_stack.append("Cargo package manager and Rust compiler (`Cargo.toml`)")
    if (folder / "go.mod").exists():
        detected_types.append("Go Microservice & Concurrent System")
        tech_stack.append("Go runtime and module system (`go.mod`)")
    return detected_types, tech_stack, man.description
