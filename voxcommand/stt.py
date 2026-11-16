"""Speech-to-text with Whisper (faster-whisper / CTranslate2)."""

from __future__ import annotations

import ctypes
import glob
import importlib.util
import os

import numpy as np


def _preload_cuda_libs():
    """Make the NVIDIA runtime visible to CTranslate2: downloaded (see gpu.py) or pip-installed (`gpu` extra)."""
    from . import gpu

    if gpu.is_installed():
        _load_dir(str(gpu.lib_dir()))
    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(pkg)
        except ModuleNotFoundError:
            continue
        if spec is None or not spec.submodule_search_locations:
            continue
        _load_dir(os.path.join(list(spec.submodule_search_locations)[0], "lib"))


def _load_dir(path: str):
    # Load order matters (cuDNN needs cuBLAS); retry until nothing more loads.
    pending = sorted(glob.glob(os.path.join(path, "*.so*")))
    while pending:
        failed = []
        for lib in pending:
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                failed.append(lib)
        if len(failed) == len(pending):
            break
        pending = failed


class Transcriber:
    def __init__(self, model="small.en", device="auto", compute_type="auto", language="en", hotwords=None, log=print):
        from faster_whisper import WhisperModel

        self.language = language or None
        self.hotwords = hotwords
        attempts = [("cuda", "float16"), ("cpu", "int8")] if device == "auto" else [(device, None)]
        if device != "cpu":
            _preload_cuda_libs()
        last_error = None
        for dev, default_ct in attempts:
            ct = compute_type if compute_type != "auto" else (default_ct or "default")
            try:
                self.model = WhisperModel(model, device=dev, compute_type=ct)
                # A missing CUDA library only shows up on first use, so run one real inference now.
                self._run(np.zeros(8000, dtype=np.float32), vad_filter=False)
                self.device = dev
                return
            except Exception as e:  # noqa: BLE001 - CTranslate2 raises plain RuntimeError/ValueError
                last_error = e
                if len(attempts) > 1 and dev == "cuda":
                    log(f"  GPU unavailable ({str(e).splitlines()[0]}), using CPU")
        raise RuntimeError(f"could not load Whisper model {model!r}: {last_error}")

    def _run(self, audio, vad_filter=True):
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=5,
            vad_filter=vad_filter,
            condition_on_previous_text=False,
            without_timestamps=True,
            hotwords=self.hotwords,
        )
        return list(segments)

    def transcribe(self, audio: np.ndarray) -> str:
        parts = [
            s.text.strip()
            for s in self._run(audio)
            if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)  # likely hallucination
        ]
        return " ".join(p for p in parts if p)
