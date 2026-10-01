"""有界的检测/关键点缓存，只保存小型数值结果，不持有照片或 Tensor。"""
from collections import OrderedDict
from copy import deepcopy
import threading
from pathlib import Path
from .region_tracking_result import image_file_signature

_cache=OrderedDict()
_sizes={}
MAX_BYTES=32*1024*1024
_lock=threading.RLock()
MAX_ENTRIES=256  # 每项最多少量鸟框或 23 个关键点；无像素数组。


def detector_signature():
    from birdstamp.gui import editor_core as core
    return (core._BIRD_DETECT_CONFIDENCE,core._BIRD_DETECT_MAX_LONG_EDGE,
            tuple(image_file_signature(core.resolve_bundled_path('models',name)) for name in core._BIRD_MODEL_CANDIDATES))


def cached_observation(path, kind, parameters, compute, cancelled):
    signature=image_file_signature(Path(path))
    key=(kind,signature,parameters)
    if cancelled():raise InterruptedError('已取消识别')
    with _lock:
        if signature and key in _cache:
            _cache.move_to_end(key)
            return deepcopy(_cache[key])
    value=compute()
    if cancelled():raise InterruptedError('已取消识别')
    if signature and signature==image_file_signature(Path(path)):
        with _lock:
            size=4096+8*len(repr((key,value)).encode('utf-8'))
            if size<=MAX_BYTES:
                _cache[key]=deepcopy(value);_cache.move_to_end(key);_sizes[key]=size
                while len(_cache)>MAX_ENTRIES or sum(_sizes.values())>MAX_BYTES:
                    old,_=_cache.popitem(last=False);_sizes.pop(old,None)
    return value


def detect_cached(path,image,*,cancelled=lambda:False):
    from .bird_candidates import detect_bird_candidates
    return cached_observation(path,'yolo-all-v1',detector_signature(),
        lambda:detect_bird_candidates(image,cancelled=cancelled),cancelled)


def pose_cached(path,image,target,*,cancelled=lambda:False):
    from .bird_parts.pose import predict_parts
    from .bird_parts.model_store import MODEL_ID,model_path
    return cached_observation(path,'bird-parts-v1',(MODEL_ID,image_file_signature(model_path()),tuple(target)),
        lambda:predict_parts(image,target,cancelled=cancelled),cancelled)
