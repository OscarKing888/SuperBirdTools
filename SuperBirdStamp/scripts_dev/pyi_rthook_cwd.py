"""PyInstaller runtime hook: set working directory to the bundled resource directory.

This ensures that relative paths like ``models/yolo11n.pt`` or ``config/...``
resolve correctly when the app is launched from a desktop shortcut or macOS
Finder (where the default CWD is often / or the user home).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_RESOURCE_ROOT_SENTINELS: tuple[tuple[str, ...], ...] = (
    ("config", "editor_options.json"),
    ("config", "templates", "default.json"),
    ("images", "default.jpg"),
    ("scripts_dev", "install_ffmpeg_tool.py"),
)


def _iter_resource_dirs() -> list[Path]:
    executable_dir = Path(sys.executable).resolve().parent
    raw_candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        raw_candidates.append(Path(meipass))

    if sys.platform == "darwin":
        raw_candidates.append(executable_dir.parent / "Resources")

    raw_candidates.append(executable_dir / "_internal")
    raw_candidates.append(executable_dir)

    candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        normalized = candidate.resolve(strict=False)
        key = str(normalized)
        if key in seen:
            continue
        seen.add(key)
        if normalized.is_dir():
            candidates.append(normalized)
    return candidates


def _looks_like_resource_dir(path: Path) -> bool:
    for sentinel_parts in _RESOURCE_ROOT_SENTINELS:
        try:
            if path.joinpath(*sentinel_parts).exists():
                return True
        except OSError:
            continue
    return False


def _resource_dir() -> Path:
    candidates = _iter_resource_dirs()
    for candidate in candidates:
        if _looks_like_resource_dir(candidate):
            return candidate

    if candidates:
        return candidates[0]
    return Path(sys.executable).resolve().parent


def _configure_windows_exiftool() -> None:
    """Use the stable installed tool, not MERGE's temporary extraction tree."""
    if not sys.platform.startswith("win"):
        return
    if any(os.environ.get(name, "").strip() for name in ("EXIFTOOL_EXE", "EXIFTOOL_PATH")):
        return
    tool = (Path(sys.executable).resolve().parent / "_internal" / "app_common"
            / "exif_io" / "exiftools_win" / "exiftool.exe")
    if tool.is_file():
        os.environ["EXIFTOOL_EXE"] = str(tool)


if getattr(sys, "frozen", False):
    os.chdir(_resource_dir())
    _configure_windows_exiftool()
