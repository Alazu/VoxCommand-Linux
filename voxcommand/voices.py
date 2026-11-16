"""Spoken replies (the `say` action): system voices via speech-dispatcher, or Piper neural voices.

Piper voices are natural-sounding, run offline, and come either from the public catalogue
(downloaded on request) or from any Piper .onnx voice file you have, including ones you trained.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json?download=true"
FILE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}?download=true"


@dataclass
class VoiceSettings:
    engine: str = "system"  # system | piper
    voice: str | None = None  # system: speech-dispatcher voice name; piper: catalogue key or path to .onnx
    rate: int = 0  # -100 (slow) .. 100 (fast)
    pitch: int = 0  # -100 .. 100 (system voices only)
    volume: int = 0  # -100 .. 100


def voices_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "voxcommand" / "voices"


# ----------------------------------------------------------------- system voices
def system_voices() -> list[str]:
    """speech-dispatcher voice names for its default module (espeak-ng on Fedora)."""
    spd = shutil.which("spd-say")
    if not spd:
        return []
    try:
        out = subprocess.run([spd, "-L"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    names = []
    for line in out.splitlines()[1:]:  # header: NAME LANGUAGE VARIANT
        parts = line.split()
        if len(parts) >= 3:
            names.append(" ".join(parts[:-2]))
    return names


def _say_system(text: str, s: VoiceSettings):
    spd = shutil.which("spd-say")
    if spd:
        args = [spd, "-w", "-N", "voxcommand", "-r", str(s.rate), "-p", str(s.pitch), "-i", str(s.volume)]
        if s.voice:
            args += ["-y", s.voice]
        subprocess.run(args + ["--", text], check=False, timeout=120)
        return
    espeak = shutil.which("espeak-ng")
    if not espeak:
        raise RuntimeError("no speech engine: install speech-dispatcher or espeak-ng")
    args = [espeak, "-s", str(int(175 * 2 ** (s.rate / 100))), "-p", str(50 + s.pitch // 2),
            "-a", str(max(0, min(200, 100 + s.volume)))]
    if s.voice:
        args += ["-v", s.voice]
    subprocess.run(args + ["--", text], check=False, timeout=120)


# ------------------------------------------------------------------ piper voices
_catalog: dict | None = None
_loaded: dict[str, object] = {}
_lock = threading.Lock()


def catalog(refresh: bool = False) -> dict:
    """The Piper voice catalogue (cached on disk for a week)."""
    global _catalog
    cache = voices_dir() / "voices.json"
    if _catalog is not None and not refresh:
        return _catalog
    if refresh or not cache.exists() or time.time() - cache.stat().st_mtime > 7 * 86400:
        try:
            with urllib.request.urlopen(CATALOG_URL, timeout=30) as r:
                data = r.read()
            json.loads(data)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        except Exception:  # noqa: BLE001 - offline: fall back to a stale cache if we have one
            if not cache.exists():
                raise
    _catalog = json.loads(cache.read_text())
    return _catalog


def installed_voices() -> list[str]:
    """Catalogue keys of downloaded voices."""
    return sorted(p.name[: -len(".onnx")] for p in voices_dir().glob("*.onnx") if Path(str(p) + ".json").exists())


def describe(key: str, info: dict) -> str:
    lang = info["language"]
    speakers = f", {info['num_speakers']} speakers" if info.get("num_speakers", 1) > 1 else ""
    return f"{lang['name_english']} ({lang['country_english']}) - {info['name']}, {info['quality']}{speakers}"


def download_voice(key: str, progress=None):
    """Download a catalogue voice (.onnx + .onnx.json), verifying the MD5s the catalogue lists."""
    info = catalog()[key]
    files = [(path, meta) for path, meta in info["files"].items() if path.endswith((".onnx", ".onnx.json"))]
    total = sum(meta["size_bytes"] for _, meta in files)
    done = 0
    target = voices_dir()
    target.mkdir(parents=True, exist_ok=True)
    for path, meta in sorted(files, key=lambda f: f[0].endswith(".onnx")):  # small .json first
        dest = target / Path(path).name
        tmp = dest.with_suffix(dest.suffix + ".part")
        digest = hashlib.md5()
        with urllib.request.urlopen(FILE_URL.format(path=path), timeout=60) as r, open(tmp, "wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if digest.hexdigest() != meta["md5_digest"]:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{dest.name}: checksum mismatch, download corrupted")
        tmp.replace(dest)


def remove_voice(key: str):
    for suffix in (".onnx", ".onnx.json"):
        (voices_dir() / f"{key}{suffix}").unlink(missing_ok=True)
    _loaded.pop(str(voices_dir() / f"{key}.onnx"), None)


def piper_model_path(voice: str | None) -> Path:
    if not voice:
        installed = installed_voices()
        if not installed:
            raise RuntimeError("no Piper voice installed: pick one in Settings > Voice")
        voice = installed[0]
    path = Path(voice).expanduser()
    if path.suffix != ".onnx":
        path = voices_dir() / f"{voice}.onnx"
    if not path.exists():
        raise RuntimeError(f"Piper voice not found: {path}")
    if not Path(str(path) + ".json").exists():
        raise RuntimeError(f"{path.name} needs its config file next to it: {path.name}.json")
    return path


def _say_piper(text: str, s: VoiceSettings):
    import numpy as np
    from piper import PiperVoice, SynthesisConfig

    from .audio import sounddevice

    path = str(piper_model_path(s.voice))
    with _lock:
        voice = _loaded.get(path)
        if voice is None:
            voice = _loaded[path] = PiperVoice.load(path)
    config = SynthesisConfig(length_scale=2 ** (-s.rate / 100), volume=max(0.0, 1 + s.volume / 100))
    chunks = list(voice.synthesize(text, syn_config=config))
    if not chunks:
        return
    audio = np.concatenate([c.audio_float_array for c in chunks])
    sd = sounddevice()
    sd.play(audio, chunks[0].sample_rate, blocking=True)


def say(text: str, settings: VoiceSettings):
    if settings.engine == "piper":
        _say_piper(text, settings)
    else:
        _say_system(text, settings)
