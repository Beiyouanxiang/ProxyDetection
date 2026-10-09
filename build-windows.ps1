$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m venv .build-venv
if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python build environment.' }
& .\.build-venv\Scripts\python.exe -m pip install 'pyinstaller>=6,<7'
if ($LASTEXITCODE -ne 0) { throw 'Unable to install PyInstaller.' }
& .\.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name ProxyWatch proxywatch.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Write-Output 'Build complete: dist\ProxyWatch.exe'
