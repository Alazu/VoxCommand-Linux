"""Optional download of the NVIDIA CUDA libraries Whisper needs for GPU acceleration.

They are over 1 GB, so the AppImage / Windows build leaves them out and fetches them on request from
PyPI (NVIDIA's official wheels), checking each download's SHA-256 before unpacking it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# Versions tested with the bundled CTranslate2 (4.x, CUDA 12 / cuDNN 9).
PACKAGES = {
    "nvidia-cublas-cu12": "12.9.1.4" if sys.platform == "win32" else "12.9.3.13",  # newest with a Windows wheel
    "nvidia-cudnn-cu12": "9.28.1.4",
    "nvidia-cuda-nvrtc-cu12": "12.9.86",
}


WINDOWS = sys.platform == "win32"
LIB_PATTERN = "*.dll" if WINDOWS else "*.so*"


def install_dir() -> Path:
    from .config import data_dir

    return data_dir() / "cuda"


def lib_dir() -> Path:
    return install_dir() / "lib"


def is_installed() -> bool:
    marker = install_dir() / "installed.json"
    try:
        return json.loads(marker.read_text()) == PACKAGES and any(lib_dir().glob("cublas64_*.dll" if WINDOWS else "libcublas.so*"))
    except (OSError, ValueError):
        return False


def has_nvidia_driver() -> bool:
    if WINDOWS:
        return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvcuda.dll").exists()
    return Path("/proc/driver/nvidia/version").exists()


def _wheel_info(package: str, version: str) -> dict:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    tag = "win_amd64" if WINDOWS else "manylinux"
    arch = "" if WINDOWS else platform.machine()
    for f in files:
        if f["packagetype"] == "bdist_wheel" and tag in f["filename"] and arch in f["filename"]:
            return f
    raise RuntimeError(f"no {'Windows' if WINDOWS else 'Linux ' + arch} wheel for {package} {version}")


def install(progress=None, cancelled=lambda: False):
    """Download and unpack the libraries. progress(done_bytes, total_bytes, label)."""
    progress = progress or (lambda done, total, label: None)
    if platform.machine().lower() not in ("x86_64", "amd64"):
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
                    # nvidia/<component>/lib/<libfile> (Linux) or nvidia/<component>/bin/<dll> (Windows)
                    if WINDOWS:
                        wanted = parts[2:3] == ["bin"] and parts[-1].lower().endswith(".dll")
                    else:
                        wanted = parts[2:3] == ["lib"] and ".so" in parts[-1]
                    if len(parts) == 4 and parts[0] == "nvidia" and wanted:
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
