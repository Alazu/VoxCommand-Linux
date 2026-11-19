# Build the Windows release zip. Run from the repository root in PowerShell, with Python 3.12 on PATH:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
$ErrorActionPreference = "Stop"
$version = (Select-String -Path voxcommand\__init__.py -Pattern '__version__ = "(.*)"').Matches[0].Groups[1].Value
python -m pip install --upgrade pip
python -m pip install ".[gui]" pyinstaller
python packaging\windows\make_icon.py packaging\windows\voxcommand.ico
python -m PyInstaller --noconfirm --distpath dist --workpath build\pyinstaller packaging\windows\voxcommand.spec
Copy-Item README.md, LICENSE dist\VoxCommand\
$zip = "dist\VoxCommand-$version-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\VoxCommand -DestinationPath $zip
Write-Host "built $zip"
