"""Optional download of the NVIDIA CUDA libraries Whisper needs for GPU acceleration.

They are over 1 GB, so the AppImage leaves them out and fetches them on request from PyPI
(NVIDIA's official wheels), checking each download's SHA-256 before unpacking it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# Versions tested with the bundled CTranslate2 (4.x, CUDA 12 / cuDNN 9).
PACKAGES = {
    "nvidia-cublas-cu12": "12.9.3.13",
    "nvidia-cudnn-cu12": "9.28.1.4",
    "nvidia-cuda-nvrtc-cu12": "12.9.86",
}


def install_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "voxcommand" / "cuda"


def lib_dir() -> Path:
    return install_dir() / "lib"


def is_installed() -> bool:
    marker = install_dir() / "installed.json"
    try:
        return json.loads(marker.read_text()) == PACKAGES and any(lib_dir().glob("libcublas.so*"))
    except (OSError, ValueError):
        return False


def has_nvidia_driver() -> bool:
    return Path("/proc/driver/nvidia/version").exists()


def _wheel_info(package: str, version: str) -> dict:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    arch = platform.machine()
    for f in files:
        if f["packagetype"] == "bdist_wheel" and "manylinux" in f["filename"] and arch in f["filename"]:
            return f
    raise RuntimeError(f"no Linux {arch} wheel for {package} {version}")


def install(progress=None, cancelled=lambda: False):
    """Download and unpack the libraries. progress(done_bytes, total_bytes, label)."""
    progress = progress or (lambda done, total, label: None)
    if platform.machine() != "x86_64":
        raise RuntimeError("GPU acceleration is only available for x86_64")
    wheels = [(pkg, _wheel_info(pkg, ver)) for pkg, ver in PACKAGES.items()]
    total = sum(w["size"] for _, w in wheels)
    target = install_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="cuda-", dir=target.parent))
    try:
        (staging / "lib").mkdir()
        done = 0
        for pkg, wheel in wheels:
            file = staging / wheel["filename"]
            digest = hashlib.sha256()
            with urllib.request.urlopen(wheel["url"], timeout=60) as r, open(file, "wb") as out:
                while chunk := r.read(1 << 20):
                    if cancelled():
                        raise InterruptedError("cancelled")
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    progress(done, total, f"Downloading {pkg}")
            if digest.hexdigest() != wheel["digests"]["sha256"]:
                raise RuntimeError(f"{wheel['filename']}: checksum mismatch, download corrupted")
            progress(done, total, f"Unpacking {pkg}")
            with zipfile.ZipFile(file) as z:
                for name in z.namelist():
                    parts = name.split("/")
                    # nvidia/<component>/lib/<libfile>
                    if len(parts) == 4 and parts[0] == "nvidia" and parts[2] == "lib" and ".so" in parts[3]:
                        with z.open(name) as src, open(staging / "lib" / parts[3], "wb") as dst:
                            shutil.copyfileobj(src, dst)
            file.unlink()
        (staging / "installed.json").write_text(json.dumps(PACKAGES))
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def uninstall():
    shutil.rmtree(install_dir(), ignore_errors=True)
