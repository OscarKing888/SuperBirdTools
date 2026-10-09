"""Select the system MSVC runtime before Qt can load its bundled copy."""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path
import sys


# Keep DLL handles alive for the process, including repeated startup imports.
_RUNTIME_HANDLES: dict[str, object] = {}


def preload_windows_runtime() -> None:
    """Allow lazy Torch loading after Qt without importing Torch at startup.

    Qt's bundled MSVC runtime can make Torch's c10.dll fail with WinError 1114.
    As in the build-only PyInstaller bootstrap, prefer the installed system
    runtime. Do not change PATH or any installed DLLs; missing runtimes leave
    ordinary viewing available and the model loader reports its own failure.
    """
    if sys.platform != "win32":
        return
    system_root = os.environ.get("SystemRoot", "").strip()
    if not system_root:
        return
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        path = Path(system_root) / "System32" / name
        if name in _RUNTIME_HANDLES or not path.is_file():
            continue
        try:
            _RUNTIME_HANDLES[name] = ctypes.WinDLL(str(path))
        except OSError as exc:
            logging.getLogger(__name__).warning("[Torch runtime] Cannot preload %s: %s", path, exc)
