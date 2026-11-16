"""Find installed desktop applications (.desktop files) for the GUI's application picker."""

from __future__ import annotations

import configparser
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DesktopApp:
    name: str
    command: str
    icon: str = ""
    comment: str = ""
    path: str = ""


def data_dirs() -> list[Path]:
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [home, *system.split(":"),
            str(Path.home() / ".local/share/flatpak/exports/share"), "/var/lib/flatpak/exports/share"]
    return [Path(d) / "applications" for d in dict.fromkeys(d for d in dirs if d)]


def exec_to_command(exec_line: str) -> str:
    """Turn a desktop Exec= line into a shell command: drop %u/%F-style field codes."""
    try:
        parts = shlex.split(exec_line)
    except ValueError:
        parts = exec_line.split()
    parts = [re.sub(r"%[%a-zA-Z]", lambda m: "%" if m.group() == "%%" else "", p) for p in parts]
    return shlex.join(p for p in parts if p)


def list_apps(dirs: list[Path] | None = None) -> list[DesktopApp]:
    seen: dict[str, DesktopApp] = {}  # desktop-file id -> app; earlier dirs win, as in the spec
    for base in dirs or data_dirs():
        if not base.is_dir():
            continue
        for file in sorted(base.rglob("*.desktop")):
            file_id = str(file.relative_to(base)).replace("/", "-")
            if file_id in seen:
                continue
            parser = configparser.RawConfigParser(strict=False, interpolation=None)
            parser.optionxform = str
            try:
                parser.read(file, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
                continue
            hidden = entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true"
            if hidden or entry.get("Type") != "Application" or not entry.get("Exec"):
                seen[file_id] = None  # shadows the same id in later dirs
                continue
            seen[file_id] = DesktopApp(
                name=entry.get("Name", file.stem), command=exec_to_command(entry["Exec"]),
                icon=entry.get("Icon", ""), comment=entry.get("Comment", ""), path=str(file),
            )
    return sorted((a for a in seen.values() if a), key=lambda a: a.name.lower())


def suggested_phrase(app: DesktopApp) -> str:
    return f"[open|launch|start] {app.name}"
