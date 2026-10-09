#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
command -v curl >/dev/null || { echo "Please install curl first." >&2; exit 1; }
python3 -c 'import sys, tkinter; assert sys.version_info >= (3, 8), "Python 3.8+ required"'
python3 -m venv .build-venv
.build-venv/bin/python -m pip install 'pyinstaller>=6,<7'
.build-venv/bin/python -m PyInstaller --clean --noconfirm --onefile --name ProxyWatch \
  --add-binary "$(command -v curl):." proxywatch.py
echo "Built: $(pwd)/dist/ProxyWatch — test on your target CentOS before distributing."
