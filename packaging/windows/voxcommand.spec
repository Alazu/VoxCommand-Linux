# PyInstaller spec for the Windows build: pyinstaller packaging/windows/voxcommand.spec
# Produces dist/VoxCommand/ with VoxCommand.exe (GUI, no console) and voxcommand-cli.exe (console).
import os

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

here = SPECPATH  # noqa: F821 - defined by PyInstaller
icon = os.path.join(here, "voxcommand.ico")

datas, binaries, hiddenimports = [], [], ["comtypes.stream"]
for package in ("voxcommand", "faster_whisper", "ctranslate2", "piper"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
binaries += collect_dynamic_libs("av")

a = Analysis(
    [os.path.join(here, "entry.py")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "evdev", "pytest", "matplotlib", "IPython"],
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VoxCommand", console=False,
          icon=icon)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="voxcommand-cli", console=True, icon=icon)
COLLECT(gui, cli, a.binaries, a.datas, name="VoxCommand")
