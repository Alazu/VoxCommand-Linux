"""Start VoxCommand at login: an XDG autostart entry on Linux (KDE, GNOME and others),
the per-user Run registry key on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def entry_path() -> Path | str:
    if WINDOWS:
        return "HKEY_CURRENT_USER\\" + RUN_KEY + "\\VoxCommand"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "voxcommand.desktop"


def launch_command() -> list[str]:
    """How to start this same VoxCommand again: the AppImage if we run from one, else this Python."""
    appimage = os.environ.get("APPIMAGE")  # set by the AppImage runtime
    if appimage:
        return [appimage]
    if getattr(sys, "frozen", False):  # Windows build: the windowed VoxCommand.exe next to us
        return [str(Path(sys.executable).with_name("VoxCommand.exe"))]
    python = sys.executable
    if WINDOWS and Path(python).with_name("pythonw.exe").exists():
        python = str(Path(python).with_name("pythonw.exe"))  # no console window at login
    return [python, "-m", "voxcommand", "gui"]


def _quote(arg: str) -> str:
    # Desktop Entry spec: quote args with reserved characters; escape " ` $ \ inside quotes.
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def _registry_value() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, "VoxCommand")[0]
    except OSError:
        return None


def is_enabled() -> bool:
    if WINDOWS:
        return _registry_value() is not None
    return entry_path().exists()


def enable(listen: bool = True, profile: str | None = None):
    args = launch_command()
    if args[-1] != "gui":
        args.append("gui")
    args += ["--hidden"] + (["--listen"] if listen else []) + (["-p", profile] if profile else [])
    if WINDOWS:
        import subprocess
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, "VoxCommand", 0, winreg.REG_SZ, subprocess.list2cmdline(args))
        return
    path = entry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=VoxCommand\n"
        "Comment=Voice command macros powered by Whisper\n"
        f"Exec={' '.join(_quote(a).replace('%', '%%') for a in args)}\n"
        "Icon=audio-input-microphone\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-KDE-autostart-after=panel\n"
    )


def disable():
    if WINDOWS:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "VoxCommand")
        except FileNotFoundError:
            pass
        return
    entry_path().unlink(missing_ok=True)


def listens_on_start() -> bool:
    if WINDOWS:
        return "--listen" in (_registry_value() or "")
    try:
        return "--listen" in entry_path().read_text()
    except OSError:
        return False
