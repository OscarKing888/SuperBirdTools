"""整组分析的逐照片跟踪缓存：输入没变的照片直接复用上次结果。

添加接力、修改接力选区或手动修正某张后重新分析，只有使用新定义或被修正的照片需要重新解码和跟踪，
接力参考图之前已对齐的照片不再从头计算。键包含照片与定义源图的文件签名、选区、匹配参数和该照片的
手动位置，任一项变化都会自然失配；共同画幅仍按整组重新计算。只在进程内保存数值结果和有界小图，不写盘。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY, valid_manual_boxes
from birdstamp.image_dejitter.matching_options import MatchingOptions
from birdstamp.image_dejitter.recognition import SubjectSettings
from birdstamp.image_dejitter.region_tracking_result import image_file_signature

MAX_RESULTS = 8192
MAX_THUMBNAIL_BYTES = 96 * 1024 * 1024


def _boxes(values):
    return tuple(None if box is None else tuple(float(v) for v in box) for box in values)


def tracking_key(path, source, regions, options, manual_boxes=(), *, kind='track'):
    """一张照片对某组选区的跟踪输入；文件读不到时返回 None，不缓存。"""
    signature, source_signature = image_file_signature(Path(path)), image_file_signature(Path(source))
    if signature is None or source_signature is None:
        return None
    return (kind, signature, source_signature, _boxes(regions), options, _boxes(manual_boxes))


class SequenceTrackingCache:
    """线程安全的 LRU；结果是不可变的 RegionTrackingResult，可在分析线程间共享。"""

    def __init__(self, *, max_results=MAX_RESULTS, max_thumbnail_bytes=MAX_THUMBNAIL_BYTES):
        self._lock = threading.Lock()
        self._results = OrderedDict()
        self._thumbnails = OrderedDict()
        self._thumbnail_bytes = 0
        self._max_results = max_results
        self._max_thumbnail_bytes = max_thumbnail_bytes

    def get(self, key):
        if key is None:
            return None
        with self._lock:
            value = self._results.get(key)
            if value is not None:
                self._results.move_to_end(key)
            return value

    def __contains__(self, key):
        with self._lock:
            return key is not None and key in self._results

    def put(self, key, result, size):
        if key is None:
            return
        with self._lock:
            self._results[key] = (result, tuple(size))
            self._results.move_to_end(key)
            while len(self._results) > self._max_results:
                self._results.popitem(last=False)

    def thumbnail(self, path, edge):
        """快速预览用的原图小图副本；调用方负责关闭。"""
        key = (image_file_signature(Path(path)), int(edge))
        with self._lock:
            image = self._thumbnails.get(key)
            if image is None:
                return None
            self._thumbnails.move_to_end(key)
            return image.copy()

    def put_thumbnail(self, path, edge, image):
        key = (image_file_signature(Path(path)), int(edge))
        if key[0] is None:
            return
        copy = image.copy()
        size = copy.width * copy.height * len(copy.getbands())
        with self._lock:
            old = self._thumbnails.pop(key, None)
            if old is not None:
                self._thumbnail_bytes -= old.width * old.height * len(old.getbands())
                old.close()
            self._thumbnails[key] = copy
            self._thumbnail_bytes += size
            while self._thumbnail_bytes > self._max_thumbnail_bytes and len(self._thumbnails) > 1:
                _, evicted = self._thumbnails.popitem(last=False)
                self._thumbnail_bytes -= evicted.width * evicted.height * len(evicted.getbands())
                evicted.close()

    def clear(self):
        with self._lock:
            self._results.clear()
            for image in self._thumbnails.values():
                image.close()
            self._thumbnails.clear()
            self._thumbnail_bytes = 0


def tracking_reuse_plan(seeds, cache):
    """不解码、不跟踪，预估下一次分析能复用多少张：供按钮显示“从第几张继续”。

    返回 dict(total, reused, first_missing, relay)；first_missing 是第一张需要重新对齐的列表位置（0 起），
    relay 表示需要重算的照片里有接力段照片。局部主体方法或缺少参考区时返回 None。
    """
    seeds = tuple(seeds)
    if not seeds or cache is None:
        return None
    settings = seeds[0].settings
    reference = settings.get('dejitter_reference_source')
    regions = tuple(tuple(box) for box in settings.get('dejitter_reference_regions') or ())
    if not reference or not regions or SubjectSettings.from_settings(settings).method == 'subject_local':
        return None
    from .sequence_relay import RelayPlan
    relay = RelayPlan.resolve(seeds, reference, settings)
    owners = relay.owners if relay is not None else {}
    options = MatchingOptions.from_settings(settings)
    total = reused = 0
    first_missing, relay_missing = None, False
    for index, seed in enumerate(seeds):
        key = path_key(seed.path)
        anchor = owners.get(key)
        source, definition = (anchor.path, anchor.regions) if anchor is not None else (Path(reference), regions)
        if path_key(Path(source)) == key:
            continue  # 参考图与接力参考图本身不需要跟踪
        total += 1
        manual = valid_manual_boxes(seed.settings.get(MANUAL_MATCHES_KEY), seed.path, source, definition)
        if tracking_key(seed.path, source, definition, options, manual) in cache:
            reused += 1
        else:
            first_missing = index if first_missing is None else first_missing
            relay_missing = relay_missing or anchor is not None
    for anchor in relay.anchors if relay is not None else ():
        bridge = relay.paths[relay.keys[anchor.bridge]]
        if tracking_key(bridge, anchor.path, anchor.regions, options, kind='bridge') not in cache:
            relay_missing = True
            first_missing = anchor.index if first_missing is None else min(first_missing, anchor.index)
    return dict(total=total, reused=reused, first_missing=first_missing, relay=relay_missing)
