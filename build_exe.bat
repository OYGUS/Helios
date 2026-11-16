@echo off
rem Builds Helios.exe on Windows: double-click this file or run build_exe.bat.
rem Needs Python 3.10 or newer. The result is dist\Helios\Helios.exe and
rem dist\Helios-windows.zip (the zip is what you share with other people).
setlocal
cd /d "%~dp0"

rem 1) Create a separate build environment in .venv-build (only the first time).
if not exist ".venv-build\Scripts\python.exe" (
    echo Creating the build environment in .venv-build ...
    py -3 -m venv .venv-build 2>nul || python -m venv .venv-build || goto :fail
)
set "PY=.venv-build\Scripts\python.exe"

rem 2) Install Helios's requirements and PyInstaller into it.
"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install -r requirements-build.txt || goto :fail

rem 3) Build dist\Helios\Helios.exe from helios.spec.
"%PY%" -m PyInstaller --noconfirm --clean helios.spec || goto :fail

rem 4) Zip the dist\Helios folder.
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Helios' -DestinationPath 'dist\Helios-windows.zip' -Force" || goto :fail

echo.
echo Done. Program: dist\Helios\Helios.exe   To share: dist\Helios-windows.zip
pause
exit /b 0

:fail
echo.
echo The build failed. Read the messages above to see why.
pause
exit /b 1
