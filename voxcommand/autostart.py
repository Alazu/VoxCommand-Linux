"""Start VoxCommand at login through an XDG autostart entry (works on KDE, GNOME and others)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def entry_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "voxcommand.desktop"


def launch_command() -> list[str]:
    """How to start this same VoxCommand again: the AppImage if we run from one, else this Python."""
    appimage = os.environ.get("APPIMAGE")  # set by the AppImage runtime
    if appimage:
        return [appimage]
    return [sys.executable, "-m", "voxcommand", "gui"]


def _quote(arg: str) -> str:
    # Desktop Entry spec: quote args with reserved characters; escape " ` $ \ inside quotes.
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def is_enabled() -> bool:
    return entry_path().exists()


def enable(listen: bool = True, profile: str | None = None):
    args = launch_command()
    if args[-1] != "gui":
        args.append("gui")
    args += ["--hidden"] + (["--listen"] if listen else []) + (["-p", profile] if profile else [])
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
    entry_path().unlink(missing_ok=True)


def listens_on_start() -> bool:
    try:
        return "--listen" in entry_path().read_text()
    except OSError:
        return False
