#!/usr/bin/env bash
# Builds Helios on macOS or Linux: run `bash build_app.sh` in this folder.
# Needs Python 3.10 or newer (set PYTHON=/path/to/python to pick one).
# The result is
#   macOS: dist/Helios.app      and dist/Helios-macos.zip
#   Linux: dist/Helios/Helios   and dist/Helios-linux.tar.gz
# The archive is what you share with other people. On Windows use build_exe.bat.
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo; echo "The build failed. Read the messages above to see why." >&2' ERR

case "$(uname -s)" in
  Darwin) os=macos ;;
  Linux) os=linux ;;
  *) echo "build_app.sh is for macOS and Linux. On Windows use build_exe.bat." >&2; exit 1 ;;
esac

# 1) Create a separate build environment in .venv-build (only the first time).
if [ ! -x .venv-build/bin/python ]; then
  echo "Creating the build environment in .venv-build ..."
  "${PYTHON:-python3}" -m venv .venv-build
fi
PY=.venv-build/bin/python
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Helios needs Python 3.10 or newer.")'

# 2) Install Helios's requirements and PyInstaller into it.
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements-build.txt

# 3) Build from helios.spec (dist/Helios.app on macOS, dist/Helios/ on Linux).
"$PY" -m PyInstaller --noconfirm --clean helios.spec

# 4) Pack it the same way the release workflow does.
if [ "$os" = macos ]; then
  # ditto keeps the symlinks inside the Qt frameworks; a plain zip would break them.
  rm -f dist/Helios-macos.zip
  ditto -c -k --keepParent dist/Helios.app dist/Helios-macos.zip
  echo
  echo "Done. Program: dist/Helios.app   To share: dist/Helios-macos.zip"
else
  # tar keeps the executable bits and symlinks.
  tar -C dist -czf dist/Helios-linux.tar.gz Helios
  echo
  echo "Done. Program: dist/Helios/Helios   To share: dist/Helios-linux.tar.gz"
fi
