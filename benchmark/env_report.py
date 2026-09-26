# -*- coding: utf-8 -*-
"""基准运行环境记录（M0-1）。

只读取环境信息，不安装、不修改任何东西。缺失的可选依赖记为 "unavailable"。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

_PACKAGES = (
    "Pillow",
    "PyQt6",
    "PyQt6-Qt6",
    "numpy",
    "rawpy",
    "pillow-heif",
    "piexif",
    "ExifRead",
    "psutil",
)

_THREAD_ENV_PREFIXES = ("SuperViewer_", "BIRDSTAMP_", "OMP_", "EXIFTOOL_")


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in _PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    return versions


def _native_library_versions() -> dict[str, str]:
    info: dict[str, str] = {}
    try:
        from PIL import features

        for key in ("jpg", "libjpeg_turbo", "zlib", "libtiff", "webp"):
            try:
                info[f"pil.{key}"] = str(features.version(key))
            except Exception:
                pass
    except Exception:
        pass
    try:
        import rawpy

        info["libraw"] = str(getattr(rawpy, "libraw_version", "unknown"))
    except Exception:
        info["libraw"] = "unavailable"
    try:
        import pillow_heif

        info["libheif"] = str(pillow_heif.libheif_version())
    except Exception:
        info["libheif"] = "unavailable"
    try:
        from PyQt6.QtCore import QT_VERSION_STR

        info["qt"] = QT_VERSION_STR
    except Exception:
        info["qt"] = "unavailable"
    return info


def _memory_info() -> dict[str, Any]:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            "total_bytes": int(vm.total),
            "available_bytes": int(vm.available),
            "physical_cpus": psutil.cpu_count(logical=False),
        }
    except Exception:
        return {"psutil": "unavailable"}


def _exiftool_version() -> dict[str, str]:
    try:
        from app_common.exif_io.exiftool_path import get_exiftool_executable_path

        exe = get_exiftool_executable_path()
    except Exception as exc:  # pragma: no cover - diagnostic only
        return {"path": "", "version": f"lookup failed: {exc}"}
    if not exe:
        return {"path": "", "version": "unavailable"}
    try:
        proc = subprocess.run([exe, "-ver"], capture_output=True, text=True, timeout=30)
        return {"path": str(exe), "version": proc.stdout.strip()}
    except Exception as exc:
        return {"path": str(exe), "version": f"failed: {exc}"}


def _cpu_model() -> str:
    model = platform.processor() or ""
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5
            )
            if proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
    return model or "unknown"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_environment() -> dict[str, Any]:
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "cpu_model": _cpu_model(),
            "logical_cpus": os.cpu_count(),
        },
        "memory": _memory_info(),
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
        },
        "packages": _package_versions(),
        "native_libraries": _native_library_versions(),
        "exiftool": _exiftool_version(),
        "thread_env": {
            key: value for key, value in sorted(os.environ.items()) if key.startswith(_THREAD_ENV_PREFIXES)
        },
    }


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    for extra in (repo_root, repo_root / "SuperBirdStamp"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    print(json.dumps(collect_environment(), ensure_ascii=False, indent=2))
