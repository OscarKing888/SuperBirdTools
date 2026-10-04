"""内嵌 PNG 素材和有界、线程安全的解码缓存。"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from collections import OrderedDict
from threading import RLock
from pathlib import Path
from PIL import Image, ImageOps

_cache = OrderedDict()
_encoded = {}
_lock = RLock()
_budget = 128 * 1024 * 1024


def import_image(path):
    # Pillow 保留 PNG/WebP 透明度；其它项目支持的静态格式使用共享解码入口。
    try:
        with Image.open(path) as source:
            if getattr(source, 'is_animated', False):
                raise ValueError('请选择静态图片，暂不支持动画素材')
            image = ImageOps.exif_transpose(source).convert('RGBA')
    except (OSError, SyntaxError):
        from birdstamp.decoders.image_decoder import decode_image
        source = decode_image(Path(path))
        try:
            image = source.convert('RGBA')
        finally:
            source.close()
    try:
        stream = BytesIO()
        image.save(stream, format='PNG')
        data = stream.getvalue()
        key = hashlib.sha256(data).hexdigest()
        return key, dict(mime='image/png', data=base64.b64encode(data).decode('ascii'),
                         name=Path(path).name, width=image.width, height=image.height)
    finally:
        image.close()


def decode_asset(key, assets):
    raw = assets.get(key)
    if not isinstance(raw, dict):
        raise ValueError(f'图像素材缺失: {key}')
    with _lock:
        if key in _cache and _encoded.get(key) is raw.get('data'):
            _cache.move_to_end(key)
            return _cache[key].copy()
    try:
        data = base64.b64decode(raw['data'], validate=True)
        if hashlib.sha256(data).hexdigest() != key:
            raise ValueError('素材校验失败')
        with _lock:
            if key in _cache:
                _cache.move_to_end(key)
                return _cache[key].copy()
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = source.convert('RGBA')
    except Exception as exc:
        raise ValueError(f'图像素材损坏: {raw.get("name", key)} ({exc})') from exc
    with _lock:
        old = _cache.pop(key, None)
        if old is not None:
            old.close()
        _cache[key] = image
        _encoded[key] = raw['data']
        result = image.copy()
        while sum(v.width*v.height*4 for v in _cache.values()) > _budget:
            removed_key, removed = _cache.popitem(last=False)
            _encoded.pop(removed_key, None)
            removed.close()
        return result
