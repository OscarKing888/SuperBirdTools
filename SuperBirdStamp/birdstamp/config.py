from __future__ import annotations

import copy
import os
import platform
import sys
from pathlib import Path
from typing import Any

import yaml

from birdstamp.constants import DEFAULT_SHOW_FIELDS


def default_jobs() -> int:
    cpu_count = os.cpu_count() or 2
    return max(1, cpu_count - 1)


DEFAULT_CONFIG: dict[str, Any] = {
    "template": "default",
    "theme": "gray",
    "banner_height": 260,
    "lang": "zh",
    "show": sorted(DEFAULT_SHOW_FIELDS),
    "bird_from": ["arg", "meta", "filename"],
    "bird_regex": r"(?P<bird>[^_]+)_",
    "mode": "keep",
    "frame_style": "crop",
    "max_long_edge": 2048,
    "name_template": "{stem}__banner.{ext}",
    "output_format": "jpeg",
    "quality": 92,
    "use_exiftool": "auto",
    "decoder": "auto",
    "skip_existing": True,
    "jobs": default_jobs(),
    "show_eq_focal": True,
    "time_format": "%Y-%m-%d %H:%M",
}


def get_app_dir() -> Path:
    """Return the application root directory.

    - Frozen (PyInstaller): directory containing the executable.
    - Development: project root (two levels up from this file).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # birdstamp/config.py → birdstamp/ → project_root/
    return Path(__file__).resolve().parent.parent


def get_app_resource_dir() -> Path:
    """返回打包资源根目录。

    开发环境下返回工程根目录；
    Windows onedir 优先返回 ``_internal``；
    macOS .app 优先返回 ``Contents/Resources``。
    """
    if not getattr(sys, "frozen", False):
        return get_app_dir()

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


def resolve_bundled_path(*parts: str) -> Path:
    """基于资源根目录拼接内置文件路径。"""
    return get_app_resource_dir().joinpath(*parts)


def get_user_data_dir() -> Path:
    """返回用户可写的数据目录，打包后避免写入 app bundle 内部。"""
    if not getattr(sys, "frozen", False):
        return get_app_dir()

    system_name = platform.system().lower()
    if system_name == "windows":
        base = (
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or str(Path.home() / "AppData" / "Roaming")
        )
        return Path(base) / "BirdStamp"
    if system_name == "darwin":
        return Path.home() / "Library" / "Application Support" / "BirdStamp"

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "BirdStamp"
    return Path.home() / ".config" / "BirdStamp"


def _legacy_frozen_config_path() -> Path | None:
    """兼容旧版本：曾将配置写到可执行文件旁边。"""
    if not getattr(sys, "frozen", False):
        return None
    legacy_path = get_app_dir() / "Config" / "config.yaml"
    if legacy_path.exists():
        return legacy_path
    return None


def get_config_path() -> Path:
    return get_user_data_dir() / "Config" / "config.yaml"


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or get_config_path()
    if path is None and not cfg_path.exists():
        legacy_path = _legacy_frozen_config_path()
        if legacy_path is not None:
            cfg_path = legacy_path
    if not cfg_path.exists():
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["jobs"] = default_jobs()
        return cfg

    text = cfg_path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        loaded = {}
    cfg = _deep_merge(DEFAULT_CONFIG, loaded)
    if not cfg.get("jobs"):
        cfg["jobs"] = default_jobs()
    return cfg


def write_default_config(path: Path | None = None, force: bool = False) -> Path:
    cfg_path = path or get_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists() and not force:
        return cfg_path
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["jobs"] = default_jobs()
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return cfg_path
