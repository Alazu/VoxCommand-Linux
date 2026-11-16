"""Profile loading: settings plus the list of voice commands."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import yaml

DEFAULT_PROFILE = Path.home() / ".config" / "voxcommand" / "profile.yaml"

ACTION_TYPES = ("key", "press", "release", "type", "pause", "run", "mouse", "scroll", "say", "sound", "listening")


class ProfileError(ValueError):
    pass


@dataclass
class Settings:
    model: str = "small.en"
    device: str = "auto"  # auto | cuda | cpu
    compute_type: str = "auto"
    language: str | None = "en"
    microphone: str | int | None = None  # name substring or index, see `voxcommand devices`
    wake_word: str | None = None
    match_threshold: float = 82.0  # 0-100, how close speech must be to a phrase
    push_to_talk: str | None = None  # e.g. "rightctrl" or "BTN_SIDE"; None = always listening
    silence_ms: int = 450  # trailing silence that ends an utterance
    energy_threshold: float | None = None  # RMS 0-1; None = adapt to background noise
    max_utterance_s: float = 8.0
    key_press_ms: int = 50  # how long a tapped key stays down
    start_listening: bool = True
    voice_engine: str = "system"  # system (speech-dispatcher) | piper (neural voices)
    voice: str | None = None  # system voice name, Piper voice name, or path to a Piper .onnx file
    voice_rate: int = 0  # -100 slow .. 100 fast
    voice_pitch: int = 0  # -100 .. 100 (system voices)
    voice_volume: int = 0  # -100 .. 100


@dataclass
class Command:
    phrases: list[str]
    actions: list[dict]
    name: str = ""
    repeat: int = 1

    @property
    def controls_listening(self) -> bool:
        return any("listening" in a for a in self.actions)


@dataclass
class Profile:
    settings: Settings = field(default_factory=Settings)
    commands: list[Command] = field(default_factory=list)


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: top level must be a mapping")

    raw_settings = data.get("settings") or {}
    known = {f.name for f in fields(Settings)}
    unknown = set(raw_settings) - known
    if unknown:
        raise ProfileError(f"{path}: unknown settings: {', '.join(sorted(unknown))}")
    settings = Settings(**raw_settings)

    commands = []
    for i, raw in enumerate(data.get("commands") or [], 1):
        where = f"{path}: command #{i}"
        if not isinstance(raw, dict):
            raise ProfileError(f"{where}: must be a mapping")
        phrases = raw.get("phrases", raw.get("phrase"))
        if isinstance(phrases, str):
            phrases = [phrases]
        if not phrases:
            raise ProfileError(f"{where}: needs 'phrases'")
        actions = raw.get("actions") or []
        if isinstance(actions, dict):
            actions = [actions]
        for a in actions:
            if not isinstance(a, dict) or not any(t in a for t in ACTION_TYPES):
                raise ProfileError(f"{where}: bad action {a!r} (expected one of: {', '.join(ACTION_TYPES)})")
        commands.append(
            Command(
                phrases=[str(p) for p in phrases],
                actions=actions,
                name=str(raw.get("name") or phrases[0]),
                repeat=int(raw.get("repeat", 1)),
            )
        )
    return Profile(settings=settings, commands=commands)


def save_profile(path: str | Path, settings: Settings | dict, commands: list[dict]):
    """Write a profile (used by the GUI; comments in a hand-written file are not kept)."""
    path = Path(path)
    data = {
        "settings": asdict(settings) if isinstance(settings, Settings) else dict(settings),
        "commands": [
            {k: v for k, v in c.items() if not (k == "repeat" and v == 1)} for c in commands
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
    tmp.replace(path)
