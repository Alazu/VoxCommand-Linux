"""Command line entry point."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from importlib import resources
from pathlib import Path

from . import __version__
from .config import DEFAULT_PROFILE, ProfileError, load_profile
from .engine import App, listen


def log(msg: str):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def resolve_profile(arg: str | None) -> Path:
    path = Path(arg).expanduser() if arg else DEFAULT_PROFILE
    if not path.exists():
        hint = "run `voxcommand init` to create it" if not arg else "file not found"
        sys.exit(f"profile {path}: {hint}")
    return path


def cmd_run(args):
    listen(App(resolve_profile(args.profile), dry_run=args.dry_run, log=log), verbose=args.verbose)


def cmd_listen(args):
    """Transcribe only - handy for finding out how Whisper spells your phrases."""
    if not args.profile and not DEFAULT_PROFILE.exists():
        write_example(DEFAULT_PROFILE)
    listen(App(resolve_profile(args.profile), dry_run=True, log=print), transcribe_only=True)


def cmd_gui(args):
    try:
        from .gui import main as gui_main
    except ImportError as e:
        sys.exit(f"the GUI needs PySide6 ({e}); install with: uv tool install '.[gui]'")
    gui_main(args.profile, hidden=args.hidden, listen=args.listen)


def cmd_simulate(args):
    """Pretend the given text was spoken."""
    app = App(resolve_profile(args.profile), dry_run=not args.execute, log=log)
    app.handle(" ".join(args.text))
    app.executor.wait()


def cmd_transcribe(args):
    """Transcribe WAV files and show which command each would run (nothing is executed)."""
    from .audio import read_wav
    from .engine import make_transcriber

    app = App(resolve_profile(args.profile), dry_run=True, log=log)
    hotwords = ", ".join(dict.fromkeys(app.matcher.vocabulary()))[:800] or None
    transcriber = make_transcriber(app.settings, log, hotwords)
    unmatched = 0
    for file in args.files:
        heard = transcriber.transcribe(read_wav(file))
        m = app.matcher.match(heard)
        unmatched += m is None
        print(f"{file}: {heard!r} -> {m.command.name if m else 'no match'}" + (f" ({m.score:.0f}%)" if m else ""))
    sys.exit(1 if unmatched else 0)


def cmd_say(args):
    """Speak text with the profile's voice, or another one; --wav saves Piper speech to a file instead."""
    from dataclasses import replace

    from . import voices
    from .engine import voice_settings

    path = Path(args.profile).expanduser() if args.profile else DEFAULT_PROFILE
    voice = voice_settings(load_profile(path).settings) if path.exists() else voices.VoiceSettings()
    if args.engine:
        voice = replace(voice, engine=args.engine)
    if args.voice:
        voice = replace(voice, voice=args.voice)
    text = " ".join(args.text)
    if args.wav:
        if voice.engine != "piper":
            if sys.platform != "win32":
                sys.exit("--wav needs a Piper voice (--engine piper) on Linux")
            voices._say_sapi(text, voice, args.wav)
            print(f"wrote {args.wav}")
            return
        result = voices.synthesize_piper(text, voice)
        voices.save_wav(args.wav, *result)
        print(f"wrote {args.wav} ({len(result[0]) / result[1]:.1f}s)")
    else:
        voices.say(text, voice)


def cmd_check(args):
    path = resolve_profile(args.profile)
    profile = load_profile(path)
    from .actions import parse_combo
    from .matcher import expand

    problems = 0
    for cmd in profile.commands:
        variants = [v for p in cmd.phrases for v in expand(p)]
        print(f"{cmd.name}: {len(variants)} phrase(s), {len(cmd.actions)} action(s)")
        for v in variants:
            print(f"    “{v}”")
        for action in cmd.actions:
            for kind in ("key", "press", "release"):
                if kind in action:
                    try:
                        parse_combo(action[kind])
                    except ValueError as e:
                        problems += 1
                        print(f"    ! {e}")
    print(f"\n{path}: {len(profile.commands)} commands, {problems} problem(s)")
    sys.exit(1 if problems else 0)


def write_example(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(resources.files("voxcommand") / "example_profile.yaml") as src:
        shutil.copyfile(src, target)


def cmd_gpu(args):
    from . import gpu

    if args.action == "status":
        print("installed" if gpu.is_installed() else "not installed", f"({gpu.install_dir()})")
        print("NVIDIA driver:", "found" if gpu.has_nvidia_driver() else "not found")
    elif args.action == "remove":
        gpu.uninstall()
        print("removed")
    else:
        if not gpu.has_nvidia_driver():
            print("warning: no NVIDIA driver detected; the libraries will not be usable without one")
        last = [-1]

        def progress(done, total, label):
            pct = done * 100 // max(total, 1)
            if pct != last[0]:
                last[0] = pct
                print(f"\r{label}: {pct}% of {total / 1e9:.1f} GB", end="", flush=True)

        gpu.install(progress)
        print(f"\ninstalled to {gpu.install_dir()}; set device to auto or cuda")


def cmd_init(args):
    target = Path(args.profile).expanduser() if args.profile else DEFAULT_PROFILE
    if target.exists() and not args.force:
        sys.exit(f"{target} already exists (use --force to overwrite)")
    write_example(target)
    print(f"wrote {target}\nedit it, then run: voxcommand run")


def cmd_devices(args):
    from .audio import sounddevice

    sd = sounddevice()

    default = sd.default.device[0]
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"{'*' if i == default else ' '} {i:3d}  {dev['name']}")
    print("\n* = default. Set `microphone:` in the profile to an index or part of a name.")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="voxcommand", description=__doc__ or "Voice command macros powered by Whisper")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    def add(name, func, help):
        p = sub.add_parser(name, help=help)
        p.add_argument("-p", "--profile", help=f"profile file (default: {DEFAULT_PROFILE})")
        p.set_defaults(func=func)
        return p

    p = add("run", cmd_run, "listen and run voice commands")
    p.add_argument("--dry-run", action="store_true", help="print actions instead of performing them")
    p.add_argument("-v", "--verbose", action="store_true", help="show timing")
    add("listen", cmd_listen, "just print what Whisper hears")
    p = add("simulate", cmd_simulate, "match typed text as if it were spoken (dry run)")
    p.add_argument("text", nargs="+")
    p.add_argument("--execute", action="store_true", help="really perform the actions")
    add("check", cmd_check, "validate a profile and list its phrases")
    p = add("transcribe", cmd_transcribe, "transcribe WAV files and show the command each matches")
    p.add_argument("files", nargs="+")
    p = add("say", cmd_say, "speak text with the profile's voice")
    p.add_argument("text", nargs="+")
    p.add_argument("--engine", choices=["system", "piper"])
    p.add_argument("--voice", help="voice name, Piper voice key or .onnx file")
    p.add_argument("--wav", help="save the speech to this WAV file instead of playing it (Piper; on Windows also system voices)")
    p = add("init", cmd_init, "create an example profile")
    p.add_argument("--force", action="store_true")
    add("devices", cmd_devices, "list microphones")
    p = add("gui", cmd_gui, "open the graphical editor")
    p.add_argument("--hidden", action="store_true", help="start in the system tray (used by autostart)")
    p.add_argument("--listen", action="store_true", help="start listening right away")
    p = sub.add_parser("gpu", help="download/remove NVIDIA GPU acceleration (1.4 GB download)")
    p.add_argument("action", choices=["install", "remove", "status"])
    p.set_defaults(func=cmd_gpu)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return
    try:
        args.func(args)
    except KeyboardInterrupt:
        print()
    except (ProfileError, PermissionError, ValueError, RuntimeError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
