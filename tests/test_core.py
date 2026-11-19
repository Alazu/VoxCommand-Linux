import sys

import numpy as np
import pytest

from voxcommand.audio import BLOCK_MS, RATE, Segmenter
from voxcommand.config import Command, ProfileError, load_profile
from voxcommand.matcher import Matcher, expand, normalize, strip_wake_word

N = RATE * BLOCK_MS // 1000


def cmd(*phrases):
    return Command(phrases=list(phrases), actions=[{"key": "a"}], name=phrases[0])


def test_normalize():
    assert normalize("Gear 2, please!") == "gear two please"
    assert normalize("Don't  STOP") == "dont stop"
    assert normalize("42") == "forty two"


def test_expand():
    assert expand("[open|show] [the|] map") == ["open the map", "open map", "show the map", "show map"]
    assert expand("plain") == ["plain"]


def test_match_fuzzy_and_reject():
    m = Matcher([cmd("landing gear"), cmd("fire missiles"), cmd("power to shields")])
    assert m.match("Landing gear.").command.name == "landing gear"
    assert m.match("landing gears").command.name == "landing gear"
    assert m.match("Fire missile!").command.name == "fire missiles"
    assert m.match("what a lovely day") is None
    assert m.match("") is None


def test_best_match_wins():
    m = Matcher([cmd("gear up"), cmd("gear down")])
    assert m.match("gear down").command.name == "gear down"


def test_wildcard():
    m = Matcher([cmd("type *"), cmd("type faster")])
    hit = m.match("Type hello there, world")
    assert hit.command.name == "type *" and hit.text == "hello there, world"
    assert m.match("type faster").command.name == "type faster"
    assert m.match("type") is None


def test_wake_word():
    assert strip_wake_word("Computer, landing gear", "computer") == "landing gear"
    assert strip_wake_word("landing gear", "computer") is None
    assert strip_wake_word("Hey Jarvis fire", "hey jarvis") == "fire"


def test_segmenter_cuts_utterance():
    rng = np.random.default_rng(0)
    seg = Segmenter(silence_ms=300)
    quiet = lambda: (rng.standard_normal(N) * 0.001).astype(np.float32)
    loud = lambda: (rng.standard_normal(N) * 0.1).astype(np.float32)
    out = [seg.feed(b) for b in [quiet() for _ in range(30)] + [loud() for _ in range(20)] + [quiet() for _ in range(15)]]
    clips = [o for o in out if o is not None]
    assert len(clips) == 1
    assert 20 * N <= len(clips[0]) <= 45 * N


def test_segmenter_ignores_clicks_and_recovers_from_noise():
    rng = np.random.default_rng(1)
    seg = Segmenter(silence_ms=300, max_s=1.0)
    quiet = (rng.standard_normal(N) * 0.001).astype(np.float32)
    loud = (rng.standard_normal(N) * 0.1).astype(np.float32)
    assert all(seg.feed(b) is None for b in [quiet] * 20 + [loud] * 2 + [quiet] * 30)
    for _ in range(200):  # constant loud noise must stop triggering once the floor adapts
        seg.feed(loud)
    assert not seg.active or seg.floor > 0.01


def test_profile_validation(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("commands:\n  - phrases: hi\n    actions:\n      - key: a\n")
    prof = load_profile(p)
    assert prof.commands[0].phrases == ["hi"]
    p.write_text("commands:\n  - phrases: hi\n    actions:\n      - bogus: a\n")
    with pytest.raises(ProfileError):
        load_profile(p)
    p.write_text("settings:\n  nope: 1\n")
    with pytest.raises(ProfileError):
        load_profile(p)


def test_example_profile_is_valid():
    from importlib import resources

    from voxcommand.actions import parse_combo

    with resources.as_file(resources.files("voxcommand") / "example_profile.yaml") as path:
        prof = load_profile(path)
    assert len(prof.commands) > 5
    for c in prof.commands:
        for a in c.actions:
            for kind in ("key", "press", "release"):
                if kind in a:
                    assert parse_combo(a[kind])
    assert Matcher(prof.commands).match("Gear up").command.name == "landing gear"


def test_desktop_apps(tmp_path):
    from voxcommand.apps import exec_to_command, list_desktop_apps as list_apps, suggested_phrase

    assert exec_to_command('firefox %u') == "firefox"
    assert exec_to_command('/usr/bin/flatpak run --command=steam com.valvesoftware.Steam @@u %U @@') == \
        "/usr/bin/flatpak run --command=steam com.valvesoftware.Steam @@u @@"
    assert exec_to_command('"/opt/My App/app" --x=100%%') == "'/opt/My App/app' --x=100%"
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "sub").mkdir(parents=True); b.mkdir()
    (a / "sub" / "game.desktop").write_text("[Desktop Entry]\nType=Application\nName=Game\nExec=game %f\n")
    (a / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hid\nExec=hid\nNoDisplay=true\n")
    (b / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hid\nExec=hid\n")
    (b / "zed.desktop").write_text("[Desktop Entry]\nType=Application\nName=Zed\nExec=zed\n[Desktop Action x]\nExec=y\n")
    (b / "broken.desktop").write_text("garbage")
    apps = list_apps([a, b])
    assert [(x.name, x.command) for x in apps] == [("Game", "game"), ("Zed", "zed")]
    assert suggested_phrase(apps[0]) == "[open|launch|start] Game"


def test_specific_wildcard_beats_generic():
    m = Matcher([cmd("[search|look up] [for|] *"), cmd("[search|look up] google [for|] *")])
    hit = m.match("search google for linux voice control")
    assert hit.command.name.endswith("google [for|] *") and hit.text == "linux voice control"
    assert m.match("search for fedora").text == "fedora"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
def test_run_query_placeholder(monkeypatch):
    import subprocess

    from voxcommand import actions

    ran = []
    run = subprocess.run
    monkeypatch.setattr(actions.subprocess, "Popen", lambda cmd, **kw: ran.append(cmd))
    actions.Executor().run_action({"run": "echo 'https://x.test/?a=1&q='{query}'&b=2' {text}"}, "cats & dogs; rm -rf ~")
    monkeypatch.undo()
    out = run(["bash", "-c", ran[0]], capture_output=True, text=True).stdout
    assert out == "https://x.test/?a=1&q=cats+%26+dogs%3B+rm+-rf+~&b=2 cats & dogs; rm -rf ~\n"


@pytest.mark.skipif(sys.platform == "win32", reason="XDG autostart")
def test_autostart_entry(tmp_path, monkeypatch):
    import shlex

    from voxcommand import autostart

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPIMAGE", "/home/me/My Apps/VoxCommand 100%.AppImage")
    assert not autostart.is_enabled()
    autostart.enable(listen=True)
    text = autostart.entry_path().read_text()
    exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))[5:]
    assert shlex.split(exec_line.replace("%%", "%")) == [
        "/home/me/My Apps/VoxCommand 100%.AppImage", "gui", "--hidden", "--listen"]
    assert autostart.is_enabled() and autostart.listens_on_start()
    autostart.enable(listen=False)
    assert not autostart.listens_on_start()
    autostart.disable()
    assert not autostart.is_enabled()


def test_clock_placeholders():
    from datetime import datetime

    from voxcommand.actions import fill_clock

    t = datetime(2026, 11, 16, 15, 7)
    assert fill_clock("It's {time} on {day}, {date} ({time24})", t) == \
        "It's 3:07 PM on Monday, November 16, 2026 (15:07)"
    assert fill_clock("{time}", datetime(2026, 1, 1, 0, 0)) == "12 o'clock AM"
    assert fill_clock("no braces") == "no braces"


def test_wildcard_tolerates_split_words():
    m = Matcher([cmd("[search|look up] [for|] *"), cmd("[search|look up] duckduckgo [for|] *"),
                 cmd("[search|look up] startpage [for|] *"), cmd("[search|look up] google [for|] *")])
    for said, name, text in [
        ("Search duck duck go for fedora linux.", "duckduckgo", "fedora linux"),
        ("Look up start page for voice control.", "startpage", "voice control"),
        ("search DuckDuckGo cats", "duckduckgo", "cats"),
        ("search google for linux voice control", "google", "linux voice control"),
        ("search for fedora", "[for|] *", "fedora"),
    ]:
        hit = m.match(said)
        assert name in hit.command.name and hit.text == text, (said, hit)


def test_say_uses_profile_voice_and_override(monkeypatch):
    from voxcommand import actions, voices

    heard = []
    monkeypatch.setattr(voices, "say", lambda text, v: heard.append((text, v.engine, v.voice, v.rate)))
    e = actions.Executor()
    e.voice = voices.VoiceSettings("system", "English (America)+Andrea", rate=30)
    e.run_action({"say": "hi"})
    e.run_action({"say": "yo", "voice": "/v/custom.onnx"})
    e.run_action({"say": "hey", "voice": "en_GB-alan-medium", "engine": "piper"})
    assert heard == [("hi", "system", "English (America)+Andrea", 30), ("yo", "piper", "/v/custom.onnx", 30),
                     ("hey", "piper", "en_GB-alan-medium", 30)]


def test_wildcard_words_can_follow_in_next_utterance(tmp_path):
    from voxcommand import engine

    p = tmp_path / "p.yaml"
    p.write_text("commands:\n  - name: dictate\n    phrases: ['type *']\n    actions:\n      - type: '{text}'\n"
                 "  - name: gear\n    phrases: ['gear up']\n    actions:\n      - key: g\n")
    app = engine.App(p, dry_run=True, log=lambda *a: None)
    sent = []
    app.executor.submit = lambda cmd, text="": sent.append((cmd.name, text))
    assert app.handle("Type.")          # just the trigger word
    assert app.handle("Hello world.")   # the next utterance is the text
    assert app.handle("type in one go")
    assert not app.handle("hello again")  # no pending trigger any more
    assert app.handle("Gear up")
    assert sent == [("dictate", "Hello world"), ("dictate", "in one go"), ("gear", "")]
    app.handle("type"); app.pending = (app.pending[0], 0)  # expired
    assert not app.handle("too late")


def test_windows_key_table():
    from voxcommand import winput as w

    assert w.KEYS["KEY_A"] == 0x1E and w.KEYS["KEY_F10"] == 0x44 and w.KEYS["KEY_F12"] == 0x58
    assert w.KEYS["KEY_UP"] == w.EXT | 0x48 and w.KEYS["KEY_RIGHTCTRL"] == w.EXT | 0x1D
    assert w.KEYS["KEY_VOLUMEUP"] == w.VK | 0xAF and w.KEYS["BTN_SIDE"] == w.MOUSE | 4
    assert "KEY_NUMLOCK" in w.KEYS and w.KEYS["KEY_SCROLLLOCK"] == 0x46


def test_key_names_resolve_on_windows_table(monkeypatch):
    from voxcommand import actions, winput

    monkeypatch.setattr(actions, "WINDOWS", True)
    assert actions.parse_combo("ctrl+shift+f5") == [0x1D, 0x2A, 0x3F]
    assert actions.key_code("mouse4") == winput.MOUSE | 4
    assert actions.key_code("rctrl") == winput.EXT | 0x1D
    with pytest.raises(ValueError, match="Windows"):
        actions.key_code("KEY_BRIGHTNESSUP")


def test_windows_shell_quote_drops_cmd_specials(monkeypatch):
    from voxcommand import actions

    monkeypatch.setattr(actions, "WINDOWS", True)
    assert actions.shell_quote('cats & "dogs" | %PATH% ^ <x> !y') == '"cats  dogs  PATH  x y"'


def test_open_action_fills_placeholders(monkeypatch):
    from voxcommand import actions

    opened = []
    monkeypatch.setattr(actions, "open_target", opened.append)
    actions.Executor().run_action({"open": "https://x.test/?q={query}"}, "cats & dogs")
    assert opened == ["https://x.test/?q=cats+%26+dogs"]


def test_start_menu_apps(tmp_path):
    from voxcommand.apps import list_windows_apps

    a, b = tmp_path / "a", tmp_path / "b"
    (a / "Games").mkdir(parents=True); b.mkdir()
    (a / "Games" / "Elite.lnk").write_text("")
    (a / "Uninstall Elite.lnk").write_text("")
    (b / "elite.lnk").write_text("")  # same name in the second folder: first one wins
    (b / "Notes.txt").write_text("")
    (b / "Zed.url").write_text("")
    apps = list_windows_apps([a, b])
    assert [(x.name, x.comment) for x in apps] == [("Elite", "Games"), ("Zed", "")]
    assert apps[0].command == f'start "" "{a / "Games" / "Elite.lnk"}"'


windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


@windows
def test_sendinput_presses_real_keys():
    import ctypes
    import time

    from voxcommand import winput as w

    assert ctypes.sizeof(w.INPUT) == (40 if sys.maxsize > 2**32 else 28)
    assert w.virtual_key(w.KEYS["KEY_A"]) == 0x41
    assert w.virtual_key(w.KEYS["KEY_RIGHTCTRL"]) == 0xA3
    dev = w.SendInputDevice()
    f24 = w.KEYS["KEY_F24"]
    held = w.watch_key(f24)
    dev.keys([f24], True)
    time.sleep(0.1)
    assert held.is_set()
    dev.keys([f24], False)
    time.sleep(0.1)
    assert not held.is_set()


@windows
def test_windows_autostart_registry():
    from voxcommand import autostart

    before = autostart._registry_value()
    try:
        autostart.enable(listen=True)
        assert autostart.is_enabled() and autostart.listens_on_start()
        assert "gui --hidden --listen" in autostart._registry_value()
        autostart.enable(listen=False)
        assert not autostart.listens_on_start()
        autostart.disable()
        assert not autostart.is_enabled()
    finally:
        if before:
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
                winreg.SetValueEx(key, "VoxCommand", 0, winreg.REG_SZ, before)


@windows
def test_sapi_voices():
    from voxcommand import voices

    assert voices.system_voices(), "Windows should have at least one SAPI voice"
