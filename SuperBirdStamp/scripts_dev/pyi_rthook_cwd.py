"""PyInstaller runtime hook: set working directory to the bundled resource directory.

This ensures that relative paths like ``models/yolo11n.pt`` or ``config/...``
resolve correctly when the app is launched from a desktop shortcut or macOS
Finder (where the default CWD is often / or the user home).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_dir() -> Path:
    executable_dir = Path(sys.executable).resolve().parent
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))

    if sys.platform == "darwin":
        candidates.append(executable_dir.parent / "Resources")

    candidates.append(executable_dir / "_internal")
    candidates.append(executable_dir)

    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    return executable_dir


if getattr(sys, "frozen", False):
    os.chdir(_resource_dir())
