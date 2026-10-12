"""Windows bootstrap for PyInstaller subprocesses and frozen runtime hooks.

PyQt6 bundles an older MSVC runtime next to Qt. If an isolated PyInstaller
worker imports PyQt6 before Torch, Windows can keep that older runtime loaded
and a later Torch import may crash in ``msvcp140.dll``. Preloading the current
system runtime makes the import order deterministic without changing files in
the virtual environment. Windows specs also run this file before frozen app
imports, so startup/model diagnostics use the same MSVC runtime as the GUI.

Python 3.12+ also queries WMI from platform.win32_ver()/machine()/processor().
An unresponsive WMI provider can block PyInstaller before its first log line.
Use platform's own local Windows API/registry/environment fallbacks, including
in isolated workers, without changing the system service.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


_PRELOADED_MSVC_RUNTIME: list[object] = []


def _use_local_windows_platform_info() -> None:
    if sys.platform != "win32":
        return

    import platform

    # CPython's optional WMI backend raises OSError when unavailable; its public
    # platform functions then use their existing local fallbacks. Older Python
    # versions without this backend already follow the local path.
    if hasattr(platform, "_wmi"):
        platform._wmi = None


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


_use_local_windows_platform_info()
_preload_windows_msvc_runtime()
