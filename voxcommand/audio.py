"""Microphone capture and splitting the stream into utterances."""

from __future__ import annotations

import os
import queue
import sys
import threading
from collections import deque

import numpy as np

RATE = 16000  # what Whisper expects
BLOCK_MS = 30


def sounddevice():
    """Import sounddevice, pointing it at a bundled PortAudio when packaged (AppImage)."""
    bundled = os.environ.get("VOXCOMMAND_PORTAUDIO")
    if bundled and os.path.exists(bundled) and "sounddevice" not in sys.modules:
        import ctypes.util

        original = ctypes.util.find_library
        ctypes.util.find_library = lambda name: bundled if name == "portaudio" else original(name)
        try:
            import sounddevice as sd
        finally:
            ctypes.util.find_library = original
        return sd
    import sounddevice as sd

    return sd


class Segmenter:
    """Energy-based voice activity detector: feed fixed-size blocks, get whole utterances back.

    It only has to be good enough to cut the stream; Whisper's own VAD rejects non-speech later.
    """

    def __init__(self, silence_ms=450, preroll_ms=300, max_s=8.0, threshold=None, min_speech_ms=150):
        self.fixed = threshold
        self.floor = 0.002  # running estimate of background RMS
        self.silence_blocks = max(1, silence_ms // BLOCK_MS)
        self.max_blocks = int(max_s * 1000 / BLOCK_MS)
        self.min_voiced = max(1, min_speech_ms // BLOCK_MS)
        self.pre: deque[np.ndarray] = deque(maxlen=max(1, preroll_ms // BLOCK_MS))
        self._reset()

    def _reset(self):
        self.active = False
        self.buf: list[np.ndarray] = []
        self.levels: list[float] = []
        self.run = self.silence = self.voiced = 0

    @property
    def threshold(self) -> float:
        return self.fixed if self.fixed is not None else max(self.floor * 3.0, 0.006)

    def feed(self, block: np.ndarray) -> np.ndarray | None:
        rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
        loud = rms > self.threshold
        if not self.active:
            self.pre.append(block)
            if loud:
                self.run += 1
            else:
                self.run = 0
                self.floor = 0.95 * self.floor + 0.05 * rms
            if self.run >= 3:
                self.active = True
                self.buf = list(self.pre)
                self.voiced = self.run
                self.pre.clear()
            return None

        self.buf.append(block)
        self.levels.append(rms)
        if loud:
            self.silence = 0
            self.voiced += 1
        else:
            self.silence += 1
        too_long = len(self.buf) >= self.max_blocks
        if self.silence < self.silence_blocks and not too_long:
            return None
        audio = np.concatenate(self.buf)
        voiced = self.voiced
        if too_long:
            # Never went quiet: the room got louder, so lift the noise floor to recover.
            self.floor = max(self.floor, float(np.percentile(self.levels, 20)))
        self._reset()
        return audio if voiced >= self.min_voiced else None


class Microphone:
    """Yields 30 ms float32 mono blocks at 16 kHz (None on timeouts so callers stay responsive)."""

    def __init__(self, device: str | int | None = None):
        self.device = device
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.stream = None
        self.native_rate = RATE

    def __enter__(self):
        sd = sounddevice()

        def callback(indata, frames, time, status):
            self.q.put(indata[:, 0].copy())

        try:
            self.stream = sd.InputStream(
                samplerate=RATE, blocksize=RATE * BLOCK_MS // 1000, channels=1, dtype="float32",
                device=self.device, callback=callback,
            )
        except sd.PortAudioError:
            # Raw hardware devices may refuse 16 kHz; capture at their rate and resample.
            self.native_rate = int(sd.query_devices(self.device, "input")["default_samplerate"])
            self.stream = sd.InputStream(
                samplerate=self.native_rate, blocksize=self.native_rate * BLOCK_MS // 1000, channels=1,
                dtype="float32", device=self.device, callback=callback,
            )
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()

    def blocks(self, stop: threading.Event | None = None):
        n = RATE * BLOCK_MS // 1000
        while not (stop and stop.is_set()):
            try:
                block = self.q.get(timeout=0.25)
            except queue.Empty:
                yield None
                continue
            if self.native_rate != RATE:
                block = np.interp(
                    np.linspace(0, len(block), n, endpoint=False), np.arange(len(block)), block
                ).astype(np.float32)
            yield block


class PushToTalk:
    """Same interface as Segmenter: record while `held` is set, return the clip on release."""

    def __init__(self, held: threading.Event, max_s: float = 30.0):
        self.held = held
        self.max_blocks = int(max_s * 1000 / BLOCK_MS)
        self.buf: list[np.ndarray] = []
        self.tail = 0  # keep a little audio after release so the last word isn't clipped

    def feed(self, block: np.ndarray) -> np.ndarray | None:
        if self.held.is_set():
            self.buf.append(block)
            self.tail = 200 // BLOCK_MS
            return None
        if not self.buf:
            return None
        if self.tail > 0:
            self.buf.append(block)
            self.tail -= 1
            return None
        buf, self.buf = self.buf[-self.max_blocks :], []
        return np.concatenate(buf) if len(buf) * BLOCK_MS >= 250 else None


def clips(mic: Microphone, detector, stop: threading.Event | None = None, on_level=None):
    """Yield utterances cut out of the microphone stream by a Segmenter or PushToTalk."""
    for block in mic.blocks(stop):
        if block is None:
            continue
        if on_level:
            on_level(float(np.sqrt(np.mean(np.square(block)))))
        audio = detector.feed(block)
        if audio is not None:
            yield audio


def watch_key(key_code: int) -> threading.Event:
    """Track a key/button on every input device that has it. Needs read access to /dev/input."""
    import selectors

    import evdev

    devices = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if key_code in dev.capabilities().get(evdev.ecodes.EV_KEY, []) and "voxcommand" not in dev.name:
            devices.append(dev)
        else:
            dev.close()
    if not devices:
        raise PermissionError(
            "push-to-talk: no readable input device has that key. "
            "Grant access with: sudo usermod -aG input $USER  (then log out and back in)"
        )

    held = threading.Event()

    def loop():
        sel = selectors.DefaultSelector()
        for dev in devices:
            sel.register(dev, selectors.EVENT_READ)
        while sel.get_map():
            for key, _ in sel.select():
                try:
                    for ev in key.fileobj.read():
                        if ev.type == evdev.ecodes.EV_KEY and ev.code == key_code:
                            held.set() if ev.value else held.clear()
                except OSError:  # device unplugged
                    sel.unregister(key.fileobj)

    threading.Thread(target=loop, daemon=True, name="push-to-talk").start()
    return held
