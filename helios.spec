# PyInstaller build recipe for Helios on Windows, macOS and Linux
# (run: pyinstaller --noconfirm helios.spec on the OS you build for).
# build_exe.bat, build_app.sh and .github/workflows/release.yml all use this file.
#
# One-folder build, one per OS:
#   Windows: dist/Helios/Helios.exe next to an _internal/ folder (zip dist/Helios).
#   macOS:   dist/Helios.app (zip it with `ditto -c -k --keepParent`, which keeps
#            the framework symlinks; dist/Helios/ is only an intermediate folder).
#   Linux:   dist/Helios/Helios next to an _internal/ folder (tar.gz dist/Helios).
# User data is not stored here (see core/paths.py: %APPDATA%\Helios on Windows,
# ~/Library/Application Support/Helios on macOS, ~/.local/share/Helios on Linux).

import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

BUNDLE_ID = "io.github.oygus.helios"


def _min_macos():
    """The oldest macOS the bundled Qt runs on, read from the installed PySide6 wheel tag
    (e.g. macosx_14_0_universal2 -> "14.0"), so Finder refuses older systems cleanly."""
    from importlib import metadata

    for dist in ("PySide6-Essentials", "PySide6"):
        try:
            wheel = metadata.distribution(dist).read_text("WHEEL") or ""
        except metadata.PackageNotFoundError:
            continue
        found = re.search(r"macosx_(\d+)_(\d+)_", wheel)
        if found:
            return f"{found.group(1)}.{found.group(2)}"
    return "11.0"  # first macOS for Apple Silicon


def _project_version():
    """The version from pyproject.toml (the only place it is written)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # Python 3.10: read the [project] table by hand
        section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
        match = section and re.search(r'^version\s*=\s*"([^"]+)"', section.group(1), re.M)
        if not match:
            raise SystemExit("helios.spec: no version in the [project] table of pyproject.toml")
        return match.group(1)
    return tomllib.loads(text)["project"]["version"]


VERSION = _project_version()
MIN_MACOS = _min_macos() if IS_MAC else None

# Qt modules Helios never imports (it uses QtCore, QtGui and QtWidgets only).
# Leaving them out keeps the download smaller. (QtDBus stays: QtGui imports it on Linux.)
UNUSED_QT = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtConcurrent",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtPrintSupport",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtUiTools", "PySide6.QtStateMachine", "PySide6.QtXmlPatterns",
]
UNUSED_PY = ["tkinter", "pydoc_data", "pytest", "_pytest", "PyInstaller"]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    # main.py loads assets/helios.ico as the window icon on every OS (and the
    # smoke test fails without it), so both files are bundled everywhere.
    datas=[
        (str(ROOT / "assets" / "helios.ico"), "assets"),
        (str(ROOT / "assets" / "helios.png"), "assets"),
    ],
    hiddenimports=[],
    excludes=UNUSED_QT + UNUSED_PY,
    noarchive=False,
)
# Helios's UI is English only, so Qt's own translation files are not needed
# (PySide6/translations on Windows, PySide6/Qt/translations on Linux).
def _is_qt_translation(dest):
    dest = dest.replace("\\", "/")
    return dest.startswith("PySide6/") and "/translations/" in dest


a.datas = [d for d in a.datas if not _is_qt_translation(d[0])]

pyz = PYZ(a.pure)

# The file icon: .ico on Windows, .icns on macOS; Linux executables have none.
if IS_WINDOWS:
    EXE_ICON = str(ROOT / "assets" / "helios.ico")
elif IS_MAC:
    EXE_ICON = str(ROOT / "assets" / "helios.icns")
else:
    EXE_ICON = None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Helios",
    icon=EXE_ICON,
    console=False,  # windowed app: no black console window
    upx=False,  # UPX-packed files trigger more antivirus false positives
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="Helios",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Helios.app",
        icon=str(ROOT / "assets" / "helios.icns"),
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": "Helios",
            "CFBundleDisplayName": "Helios",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": MIN_MACOS,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "MIT License",
        },
    )
