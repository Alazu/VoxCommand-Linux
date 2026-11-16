"""Macro actions: keyboard and mouse through a uinput virtual device, plus shell/speech helpers.

uinput works below the display server, so it behaves the same on Wayland, X11 and inside games.
"""

from __future__ import annotations

import queue
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime
import urllib.parse

from .config import Command

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
    """Resolve "f5", "ctrl", "KEY_KP1", "BTN_SIDE", "mouse4"... to an evdev code."""
    from evdev import ecodes

    raw = str(name).strip()
    resolved = KEY_ALIASES.get(raw.lower(), raw).upper()
    for candidate in (resolved, "KEY_" + resolved, "BTN_" + resolved):
        code = ecodes.ecodes.get(candidate)
        if code is not None and candidate.startswith(("KEY_", "BTN_")):
            return code
    raise ValueError(f"unknown key {name!r}")


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
            from evdev import UInput, ecodes as e

            keys = [c for n, c in e.ecodes.items() if n.startswith("KEY_") and c < 0x230]
            buttons = [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA]
            try:
                self._ui = UInput(
                    {e.EV_KEY: keys + buttons, e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]}, name="voxcommand virtual input"
                )
            except (PermissionError, OSError) as err:
                raise PermissionError(
                    f"cannot open /dev/uinput ({err}). See 'Permissions' in the README."
                ) from err
            time.sleep(0.3)  # give the compositor a moment to notice the new device
        return self._ui

    def _emit(self, codes, value):
        from evdev import ecodes as e

        for code in codes:
            self.ui.write(e.EV_KEY, code, value)
        self.ui.syn()

    def tap(self, codes, hold=None):
        self._emit(codes, 1)
        time.sleep(self.key_press_s if hold is None else hold)
        self._emit(list(reversed(codes)), 0)
        time.sleep(0.01)

    def type_text(self, text):
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
            from evdev import ecodes as e

            self.ui.write(e.EV_REL, e.REL_WHEEL, int(action["scroll"]))
            self.ui.syn()
        elif "run" in action:
            # Spoken text is untrusted input: always quote it before it reaches the shell.
            # {query} is the same text URL-encoded, for search URLs.
            cmd = (fill_clock(str(action["run"])).replace("{text}", shlex.quote(text))
                   .replace("{query}", shlex.quote(urllib.parse.quote_plus(text))))
            proc = subprocess.Popen(cmd, shell=True, start_new_session=True)
            if action.get("wait"):
                proc.wait()
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
            player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("aplay")
            if player:
                subprocess.Popen([player, fill(action["sound"])])
        elif "listening" in action:
            self.on_listening(action["listening"])

    def close(self):
        if self._ui is not None:
            self._ui.close()
