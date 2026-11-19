"""Macro actions: keyboard and mouse through a virtual input device, plus shell/speech helpers.

On Linux that is a uinput device, which works below the display server, so it behaves the same on
Wayland, X11 and inside games. On Windows it is SendInput with hardware scan codes (winput.py).
"""

from __future__ import annotations

import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
import urllib.parse

from .config import Command

WINDOWS = sys.platform == "win32"

KEY_ALIASES = {
    "ctrl": "LEFTCTRL", "control": "LEFTCTRL", "lctrl": "LEFTCTRL", "rctrl": "RIGHTCTRL",
    "shift": "LEFTSHIFT", "lshift": "LEFTSHIFT", "rshift": "RIGHTSHIFT",
    "alt": "LEFTALT", "lalt": "LEFTALT", "ralt": "RIGHTALT", "altgr": "RIGHTALT",
    "super": "LEFTMETA", "win": "LEFTMETA", "meta": "LEFTMETA", "cmd": "LEFTMETA",
    "return": "ENTER", "escape": "ESC", "del": "DELETE", "ins": "INSERT",
    "pgup": "PAGEUP", "pgdn": "PAGEDOWN", "pagedn": "PAGEDOWN",
    "period": "DOT", "tilde": "GRAVE", "backtick": "GRAVE", "quote": "APOSTROPHE",
    "lbracket": "LEFTBRACE", "rbracket": "RIGHTBRACE", "caps": "CAPSLOCK",
    "-": "MINUS", "=": "EQUAL", ",": "COMMA", ".": "DOT", "/": "SLASH", ";": "SEMICOLON",
    "'": "APOSTROPHE", "[": "LEFTBRACE", "]": "RIGHTBRACE", "\\": "BACKSLASH", "`": "GRAVE",
    "volumeup": "VOLUMEUP", "volumedown": "VOLUMEDOWN", "play": "PLAYPAUSE", "next": "NEXTSONG",
    "previous": "PREVIOUSSONG",
    "mouse_left": "BTN_LEFT", "mouse_right": "BTN_RIGHT", "mouse_middle": "BTN_MIDDLE",
    "mouse4": "BTN_SIDE", "mouse5": "BTN_EXTRA",
}
MOUSE_BUTTONS = {"left": "BTN_LEFT", "right": "BTN_RIGHT", "middle": "BTN_MIDDLE", "back": "BTN_SIDE", "forward": "BTN_EXTRA"}

# Characters typed with shift on a US layout, mapped to their unshifted key.
_SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./"))
_PLAIN_CHARS = {" ": "SPACE", "\n": "ENTER", "\t": "TAB"}


def key_code(name: str) -> int:
    """Resolve "f5", "ctrl", "KEY_KP1", "BTN_SIDE", "mouse4"... to a key code for this platform."""
    if WINDOWS:
        from .winput import KEYS as table
    else:
        from evdev import ecodes

        table = ecodes.ecodes
    raw = str(name).strip()
    resolved = KEY_ALIASES.get(raw.lower(), raw).upper()
    for candidate in (resolved, "KEY_" + resolved, "BTN_" + resolved):
        code = table.get(candidate)
        if code is not None and candidate.startswith(("KEY_", "BTN_")):
            return code
    raise ValueError(f"unknown key {name!r}" + (" (not available on Windows)" if WINDOWS else ""))


def shell_quote(text: str) -> str:
    """Quote spoken (untrusted) text for the shell that `run` uses: sh on Linux, cmd.exe on Windows."""
    if not WINDOWS:
        return shlex.quote(text)
    # cmd.exe has no reliable escaping inside quotes, so drop the characters it treats specially.
    return '"' + re.sub(r'["%^&|<>!\r\n]', "", text) + '"'


def open_target(target: str):
    """Open a file, folder, URL or shortcut with its default application (no shell involved)."""
    if WINDOWS:
        os.startfile(target)  # noqa: S606 - this is what the action is for
    else:
        opener = shutil.which("xdg-open") or "xdg-open"
        subprocess.Popen([opener, target], start_new_session=True)


class _UInputDevice:
    """Linux: a uinput virtual keyboard + mouse."""

    def __init__(self):
        from evdev import UInput, ecodes as e

        keys = [c for n, c in e.ecodes.items() if n.startswith("KEY_") and c < 0x230]
        buttons = [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA]
        try:
            self.ui = UInput(
                {e.EV_KEY: keys + buttons, e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]}, name="voxcommand virtual input"
            )
        except (PermissionError, OSError) as err:
            raise PermissionError(f"cannot open /dev/uinput ({err}). See 'Permissions' in the README.") from err
        time.sleep(0.3)  # give the compositor a moment to notice the new device

    def keys(self, codes, down: bool):
        from evdev import ecodes as e

        for code in codes:
            self.ui.write(e.EV_KEY, code, 1 if down else 0)
        self.ui.syn()

    def scroll(self, clicks: int):
        from evdev import ecodes as e

        self.ui.write(e.EV_REL, e.REL_WHEEL, clicks)
        self.ui.syn()

    def close(self):
        self.ui.close()


def parse_combo(combo: str) -> list[int]:
    """ "ctrl+shift+f1" -> key codes in press order. A bare "+" key is written "plus"."""
    return [key_code("KPPLUS" if part == "plus" else part) for part in str(combo).split("+") if part.strip()]


def fill_clock(value: str, now=None) -> str:
    """Replace {time}, {time24}, {date} and {day} with the current local time, in words a voice reads well."""
    if "{" not in value:
        return value
    now = now or datetime.now()
    hour = now.hour % 12 or 12
    spoken_time = f"{hour} o'clock" if now.minute == 0 else f"{hour}:{now.minute:02d}"
    return (value.replace("{time}", f"{spoken_time} {'AM' if now.hour < 12 else 'PM'}")
            .replace("{time24}", now.strftime("%H:%M"))
            .replace("{date}", f"{now:%B} {now.day}, {now.year}")
            .replace("{day}", f"{now:%A}"))


class Executor:
    """Runs command macros one at a time on a worker thread so listening never stalls."""

    def __init__(self, dry_run=False, key_press_ms=50, on_listening=None, log=print):
        self.dry_run = dry_run
        self.log = log
        from .voices import VoiceSettings

        self.voice = VoiceSettings()
        self.key_press_s = key_press_ms / 1000
        self.on_listening = on_listening or (lambda state: None)
        self._ui = None
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="macros").start()

    # --- device -----------------------------------------------------------
    @property
    def ui(self):
        if self._ui is None:
            if WINDOWS:
                from .winput import SendInputDevice

                self._ui = SendInputDevice()
            else:
                self._ui = _UInputDevice()
        return self._ui

    def _emit(self, codes, value):
        self.ui.keys(codes, bool(value))

    def tap(self, codes, hold=None):
        self._emit(codes, 1)
        time.sleep(self.key_press_s if hold is None else hold)
        self._emit(list(reversed(codes)), 0)
        time.sleep(0.01)

    def type_text(self, text):
        if hasattr(self.ui, "type_text"):  # Windows: Unicode input, independent of the keyboard layout
            self.ui.type_text(text)
            return
        shift = [key_code("shift")]
        for ch in text:
            shifted = ch in _SHIFTED or ch.isupper()
            base = _SHIFTED.get(ch, ch.lower())
            try:
                code = key_code(_PLAIN_CHARS.get(base, base))
            except ValueError:
                self.log(f"    (cannot type {ch!r}: not on a US keyboard layout)")
                continue
            if shifted:
                self._emit(shift, 1)
            self._emit([code], 1)
            time.sleep(0.008)
            self._emit([code], 0)
            if shifted:
                self._emit(shift, 0)
            time.sleep(0.008)

    # --- running ----------------------------------------------------------
    def submit(self, command: Command, text: str = ""):
        self._queue.put((command, text))

    def wait(self):
        self._queue.join()

    def _worker(self):
        while True:
            command, text = self._queue.get()
            try:
                for _ in range(max(1, command.repeat)):
                    for action in command.actions:
                        self.run_action(action, text)
            except Exception as err:  # noqa: BLE001 - a broken macro must not kill the listener
                self.log(f"    ! {command.name}: {err}")
            finally:
                self._queue.task_done()

    def run_action(self, action: dict, text: str = ""):
        def fill(value):
            return fill_clock(str(value)).replace("{text}", text)

        times = int(action.get("times", 1))
        if self.dry_run:
            self.log(f"    [dry-run] {action}" + (f"  (text={text!r})" if "{text}" in str(action) else ""))
            if "listening" not in action:
                return

        if "key" in action:
            codes = parse_combo(action["key"])
            for _ in range(times):
                self.tap(codes, action.get("hold"))
        elif "press" in action:
            self._emit(parse_combo(action["press"]), 1)
        elif "release" in action:
            self._emit(list(reversed(parse_combo(action["release"]))), 0)
        elif "type" in action:
            self.type_text(fill(action["type"]))
        elif "pause" in action:
            time.sleep(float(action["pause"]))
        elif "mouse" in action:
            codes = [key_code(MOUSE_BUTTONS.get(str(action["mouse"]).lower(), action["mouse"]))]
            for _ in range(times):
                self.tap(codes, action.get("hold"))
        elif "scroll" in action:
            self.ui.scroll(int(action["scroll"]))
        elif "run" in action:
            # Spoken text is untrusted input: always quote it before it reaches the shell.
            # {query} is the same text URL-encoded, for search URLs.
            cmd = (fill_clock(str(action["run"])).replace("{text}", shell_quote(text))
                   .replace("{query}", shell_quote(urllib.parse.quote_plus(text))))
            if WINDOWS:  # no console window flashing up for cmd.exe
                proc = subprocess.Popen(cmd, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                proc = subprocess.Popen(cmd, shell=True, start_new_session=True)
            if action.get("wait"):
                proc.wait()
        elif "open" in action:
            # No shell here, so {text} needs no quoting; {query} is URL-encoded for search URLs.
            open_target(fill_clock(str(action["open"])).replace("{text}", text)
                        .replace("{query}", urllib.parse.quote_plus(text)))
        elif "say" in action:
            from dataclasses import replace

            from .voices import say

            voice = self.voice
            if action.get("voice"):  # per-action override, e.g. a different character's voice
                voice = replace(voice, voice=str(action["voice"]))
                if str(action["voice"]).endswith(".onnx") or action.get("engine") == "piper":
                    voice = replace(voice, engine="piper")
                elif action.get("engine"):
                    voice = replace(voice, engine=str(action["engine"]))
            say(fill(action["say"]), voice)
        elif "sound" in action:
            from .audio import play_sound, sound_path

            player = None if WINDOWS else shutil.which("pw-play") or shutil.which("paplay")
            path = sound_path(fill(action["sound"]))
            if player and os.path.isfile(path):
                subprocess.Popen([player, path])
            else:  # Windows, or no PipeWire/PulseAudio player: decode it ourselves
                play_sound(path, self.log)
        elif "listening" in action:
            self.on_listening(action["listening"])

    def close(self):
        if self._ui is not None:
            self._ui.close()
