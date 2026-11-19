"""Windows keyboard and mouse: SendInput with hardware scan codes (so DirectInput games see real keys).

Key names are the same as on Linux (evdev names such as KEY_F5 or BTN_SIDE), so profiles work on both.
A key code here is an int: a scan code (+EXT for E0-prefixed keys), VK + a virtual-key code
(media keys, Pause, Num Lock), or MOUSE + a button number.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

EXT = 0x100
VK = 0x10000
MOUSE = 0x20000

_BASIC = """ESC 1 2 3 4 5 6 7 8 9 0 MINUS EQUAL BACKSPACE TAB Q W E R T Y U I O P LEFTBRACE RIGHTBRACE ENTER
LEFTCTRL A S D F G H J K L SEMICOLON APOSTROPHE GRAVE LEFTSHIFT BACKSLASH Z X C V B N M COMMA DOT SLASH
RIGHTSHIFT KPASTERISK LEFTALT SPACE CAPSLOCK F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 - SCROLLLOCK KP7 KP8 KP9
KPMINUS KP4 KP5 KP6 KPPLUS KP1 KP2 KP3 KP0 KPDOT""".split()  # set-1 scan codes 1..83, in order

KEYS: dict[str, int] = {f"KEY_{name}": code for code, name in enumerate(_BASIC, 1) if name != "-"}
KEYS.update({
    "KEY_102ND": 0x56, "KEY_F11": 0x57, "KEY_F12": 0x58,
    **{f"KEY_F{n}": sc for n, sc in zip(range(13, 25), (0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C,
                                                        0x6D, 0x6E, 0x76))},
    "KEY_KPENTER": EXT | 0x1C, "KEY_RIGHTCTRL": EXT | 0x1D, "KEY_KPSLASH": EXT | 0x35, "KEY_SYSRQ": EXT | 0x37,
    "KEY_PRINT": EXT | 0x37, "KEY_RIGHTALT": EXT | 0x38, "KEY_HOME": EXT | 0x47, "KEY_UP": EXT | 0x48,
    "KEY_PAGEUP": EXT | 0x49, "KEY_LEFT": EXT | 0x4B, "KEY_RIGHT": EXT | 0x4D, "KEY_END": EXT | 0x4F,
    "KEY_DOWN": EXT | 0x50, "KEY_PAGEDOWN": EXT | 0x51, "KEY_INSERT": EXT | 0x52, "KEY_DELETE": EXT | 0x53,
    "KEY_LEFTMETA": EXT | 0x5B, "KEY_RIGHTMETA": EXT | 0x5C, "KEY_COMPOSE": EXT | 0x5D, "KEY_MENU": EXT | 0x5D,
    "KEY_NUMLOCK": VK | 0x90, "KEY_PAUSE": VK | 0x13, "KEY_MUTE": VK | 0xAD, "KEY_VOLUMEDOWN": VK | 0xAE,
    "KEY_VOLUMEUP": VK | 0xAF, "KEY_NEXTSONG": VK | 0xB0, "KEY_PREVIOUSSONG": VK | 0xB1, "KEY_STOPCD": VK | 0xB2,
    "KEY_PLAYPAUSE": VK | 0xB3,
    "BTN_LEFT": MOUSE | 1, "BTN_RIGHT": MOUSE | 2, "BTN_MIDDLE": MOUSE | 3, "BTN_SIDE": MOUSE | 4,
    "BTN_EXTRA": MOUSE | 5,
})

# MOUSEEVENTF_* (down, up) and mouseData for each button
_BUTTONS = {1: (0x02, 0x04, 0), 2: (0x08, 0x10, 0), 3: (0x20, 0x40, 0), 4: (0x80, 0x100, 1), 5: (0x80, 0x100, 2)}
_BUTTON_VK = {1: 0x01, 2: 0x02, 3: 0x04, 4: 0x05, 5: 0x06}

if sys.platform == "win32":
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    _user32.MapVirtualKeyW.restype = wintypes.UINT

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
MOUSEEVENTF_WHEEL = 0x800


def _key_input(code: int, down: bool):
    inp = INPUT(type=1)
    up = 0 if down else KEYEVENTF_KEYUP
    if code & VK:
        inp.ki.wVk = code & 0xFF
        inp.ki.dwFlags = up
    else:
        inp.ki.wScan = code & 0xFF
        inp.ki.dwFlags = KEYEVENTF_SCANCODE | up | (KEYEVENTF_EXTENDEDKEY if code & EXT else 0)
    return inp


def _mouse_input(flags: int, data: int = 0):
    inp = INPUT(type=0)
    inp.mi.dwFlags = flags
    inp.mi.mouseData = data & 0xFFFFFFFF
    return inp


def _send(inputs: list):
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise OSError(f"SendInput blocked (error {ctypes.get_last_error()}); "
                      "run VoxCommand as administrator to control apps that run as administrator")


class SendInputDevice:
    """Same small interface as the Linux uinput device wrapper in actions.py."""

    def keys(self, codes, down: bool):
        inputs = []
        for code in codes:
            if code & MOUSE:
                d, u, data = _BUTTONS[code & 0xFF]
                inputs.append(_mouse_input(d if down else u, data))
            else:
                inputs.append(_key_input(code, down))
        _send(inputs)

    def scroll(self, clicks: int):
        _send([_mouse_input(MOUSEEVENTF_WHEEL, 120 * clicks)])

    def type_text(self, text: str, delay: float = 0.008):
        """Type any character, whatever the keyboard layout (Unicode input)."""
        for ch in text:
            if ch == "\n":
                self.keys([KEYS["KEY_ENTER"]], True)
                self.keys([KEYS["KEY_ENTER"]], False)
            else:
                units = ch.encode("utf-16-le")
                for i in range(0, len(units), 2):
                    unit = int.from_bytes(units[i:i + 2], "little")
                    down, up = INPUT(type=1), INPUT(type=1)
                    down.ki.wScan = up.ki.wScan = unit
                    down.ki.dwFlags, up.ki.dwFlags = KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
                    _send([down, up])
            time.sleep(delay)

    def close(self):
        pass


def virtual_key(code: int) -> int:
    """Our key code -> Windows virtual-key code (for GetAsyncKeyState)."""
    if code & MOUSE:
        return _BUTTON_VK[code & 0xFF]
    if code & VK:
        return code & 0xFF
    scan = (0xE000 if code & EXT else 0) | (code & 0xFF)
    vk = _user32.MapVirtualKeyW(scan, 3)  # MAPVK_VSC_TO_VK_EX
    if not vk:
        raise ValueError(f"no Windows virtual key for scan code {scan:#x}")
    return vk


def watch_key(code: int) -> threading.Event:
    """Push-to-talk: poll the key's state (works in games; no hooks or admin rights needed)."""
    vk = virtual_key(code)
    held = threading.Event()

    def loop():
        while True:
            held.set() if _user32.GetAsyncKeyState(vk) & 0x8000 else held.clear()
            time.sleep(0.01)

    threading.Thread(target=loop, daemon=True, name="push-to-talk").start()
    return held
