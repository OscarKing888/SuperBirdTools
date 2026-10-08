"""用户命名安全区的持久化与运行时快照；无 Qt，不在渲染热路径读磁盘。"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import tempfile

from birdstamp import config
from .safe_area import normalize_options

_log = logging.getLogger(__name__)


def options_path():
    return config.get_config_path().parent / 'user_options.json'


def default_options():
    from birdstamp.gui.editor_options import PLATFORM_SAFE_AREA
    return deepcopy(PLATFORM_SAFE_AREA)


@lru_cache(maxsize=8)
def _read_options(path):
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or 'platform_safe_area' not in raw:
            return default_options()
        return normalize_options(raw['platform_safe_area'], strict=True)
    except FileNotFoundError:
        return default_options()
    except (OSError, ValueError) as exc:
        _log.warning('Cannot load safe area user options %s: %s', path, exc)
        return default_options()


def current_options():
    """只读快照；文件只在首次使用或显式 reload 时读取。"""
    return _read_options(options_path())


def reload_options():
    _read_options.cache_clear()
    return current_options()


def save_options(options):
    normalized = normalize_options(options, strict=True)
    path = options_path()
    raw = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError(f'用户选项必须为 JSON 对象：{path}')
    raw['platform_safe_area'] = normalized
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(raw, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return reload_options()


def resolve_choice(value):
    """CLI 可使用稳定标识或当前显示组名，重命名不改变照片里的标识。"""
    value = str(value or 'off').strip()
    labels = current_options()['labels']
    if value in labels:
        return value
    if value.lower() in labels:
        return value.lower()
    return next((key for key, name in labels.items() if name == value), None)


def preset_for(value):
    return current_options()['presets'].get(value)
