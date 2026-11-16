# PyInstaller build recipe for Helios.exe (run: pyinstaller --noconfirm helios.spec).
# build_exe.bat and .github/workflows/release.yml both use this file.
#
# One-folder build: dist/Helios/Helios.exe next to an _internal/ folder.
# Zip the whole dist/Helios folder to share it. User data is not stored here
# (see core/paths.py: %APPDATA%\Helios on Windows).

import sys
from pathlib import Path

ROOT = Path(SPECPATH)
IS_WINDOWS = sys.platform.startswith("win")

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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Helios",
    icon=str(ROOT / "assets" / "helios.ico"),
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
