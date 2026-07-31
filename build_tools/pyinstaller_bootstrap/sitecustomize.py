"""Build-only Windows runtime bootstrap for PyInstaller subprocesses.

PyQt6 bundles an older MSVC runtime next to Qt. If an isolated PyInstaller
worker imports PyQt6 before Torch, Windows can keep that older runtime loaded
and a later Torch import may crash in ``msvcp140.dll``. Preloading the current
system runtime makes the import order deterministic without changing the
application runtime or files in the virtual environment.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


_PRELOADED_MSVC_RUNTIME: list[object] = []


def _preload_windows_msvc_runtime() -> None:
    if sys.platform != "win32":
        return

    system_root = str(os.environ.get("SystemRoot", "") or "").strip()
    if not system_root:
        return

    system32 = Path(system_root) / "System32"
    for filename in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        dll_path = system32 / filename
        if not dll_path.is_file():
            continue
        try:
            _PRELOADED_MSVC_RUNTIME.append(ctypes.WinDLL(str(dll_path)))
        except OSError:
            # Keep startup compatible with machines whose VC runtime is
            # incomplete; PyInstaller will report the underlying import error.
            continue


_preload_windows_msvc_runtime()
