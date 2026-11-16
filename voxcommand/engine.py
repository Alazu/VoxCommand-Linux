"""The listen -> transcribe -> match -> act loop, shared by the CLI and the GUI."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .config import ProfileError, load_profile
from .matcher import Matcher, strip_wake_word


FOLLOW_UP_S = 8.0  # after just "type", how long the next utterance counts as the text


class App:
    def __init__(self, profile_path: Path, dry_run=False, log=print):
        from .actions import Executor

        self.path = Path(profile_path)
        self.log = log
        self.listening = True
        self.pending = None  # (command, deadline): said "type", waiting for the words to type
        self.executor = Executor(dry_run=dry_run, on_listening=self.set_listening, log=log)
        self.load()
        self.listening = self.settings.start_listening

    def load(self):
        profile = load_profile(self.path)
        self.settings = profile.settings
        self.matcher = Matcher(profile.commands, profile.settings.match_threshold)
        self.executor.key_press_s = profile.settings.key_press_ms / 1000
        self.executor.voice = voice_settings(profile.settings)
        self.mtime = self.path.stat().st_mtime
        self.commands = profile.commands

    def reload_if_changed(self):
        try:
            if self.path.stat().st_mtime == self.mtime:
                return
            self.load()
            self.log(f"profile reloaded ({len(self.commands)} commands)")
        except (OSError, ProfileError) as e:
            self.mtime = self.path.stat().st_mtime if self.path.exists() else self.mtime
            self.log(f"! profile not reloaded: {e}")

    def set_listening(self, state):
        if state == "toggle":
            self.listening = not self.listening
        else:
            self.listening = state in (True, "on", "true", "yes")
        self.log("listening" if self.listening else "paused (say a resume command to continue)")

    def handle(self, heard: str) -> bool:
        """Act on one transcribed utterance. Returns True if a command ran."""
        if self.pending and time.monotonic() < self.pending[1]:
            # The previous utterance was just "type" (or another `*` phrase): this one is its text.
            command, self.pending = self.pending[0], None
            words = heard.strip().rstrip(".!?,;:")
            self.log(f'> "{heard}" -> {command.name}')
            self.executor.submit(command, words)
            return True
        self.pending = None
        text = heard
        if self.settings.wake_word:
            text = strip_wake_word(heard, self.settings.wake_word)
            if text is None:
                self.log(f'  heard "{heard}" (no wake word)')
                return False
        m = self.matcher.match(text)
        if m is None:
            prefix = self.matcher.match_prefix(text)
            if prefix and (self.listening or prefix.command.controls_listening):
                self.pending = (prefix.command, time.monotonic() + FOLLOW_UP_S)
                self.log(f'> "{heard}" -> {prefix.command.name}: waiting for what to use as the text…')
                return True
            self.log(f'  heard "{heard}" (no match)')
            return False
        if not self.listening and not m.command.controls_listening:
            self.log(f'  heard "{heard}" (paused)')
            return False
        self.log(f'> "{heard}" -> {m.command.name} ({m.score:.0f}%)')
        self.executor.submit(m.command, m.text)
        return True


def voice_settings(settings):
    from .voices import VoiceSettings

    return VoiceSettings(settings.voice_engine, settings.voice, settings.voice_rate, settings.voice_pitch,
                         settings.voice_volume)


def make_transcriber(settings, log=print, hotwords=None):
    from .stt import Transcriber

    log(f"loading Whisper model {settings.model!r} (the first run downloads it)...")
    t = Transcriber(settings.model, settings.device, settings.compute_type, settings.language, hotwords, log=log)
    log(f"model ready on {t.device}")
    return t


def make_detector(settings, log=print):
    from . import audio

    if settings.push_to_talk:
        from .actions import key_code

        held = audio.watch_key(key_code(settings.push_to_talk))
        log(f"push-to-talk: hold {settings.push_to_talk} and speak")
        return audio.PushToTalk(held)
    log("listening for speech")
    return audio.Segmenter(settings.silence_ms, max_s=settings.max_utterance_s, threshold=settings.energy_threshold)


def listen(app: App, stop: threading.Event | None = None, on_level=None, on_ready=None, verbose=False, transcribe_only=False):
    """Run until `stop` is set (or forever)."""
    from .audio import Microphone, clips

    s, log = app.settings, app.log
    if not app.executor.dry_run and not transcribe_only:
        app.executor.ui  # fail early if /dev/uinput is not writable
    # Bias Whisper towards the profile's vocabulary (it only reads the first ~200 tokens).
    hotwords = None if transcribe_only else ", ".join(dict.fromkeys(app.matcher.vocabulary()))[:800] or None
    transcriber = make_transcriber(s, log, hotwords)
    with Microphone(s.microphone) as mic:
        detector = make_detector(s, log)
        if on_ready:
            on_ready()
        for clip in clips(mic, detector, stop, on_level):
            app.reload_if_changed()
            started = time.monotonic()
            heard = transcriber.transcribe(clip)
            if not heard:
                continue
            if transcribe_only:
                log(heard)
                continue
            app.handle(heard)
            if verbose:
                log(f"  ({len(clip) / 16000:.1f}s audio, {time.monotonic() - started:.2f}s to transcribe)")
