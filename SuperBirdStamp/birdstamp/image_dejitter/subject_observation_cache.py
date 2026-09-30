"""只缓存无像素的不可变局部观测；强度/输出编码不改变观测签名。"""
from collections import OrderedDict
import threading

_lock = threading.Lock()
_cache = OrderedDict()
_bytes = 0
BUDGET = 32 * 1024 * 1024


def get(key):
    with _lock:
        item = _cache.get(key)
        if item is not None:
            _cache.move_to_end(key)
            return item[0]


def put(key, result):
    global _bytes
    observation = result.observation
    # Python 对象有额外开销，按每点 512 字节保守计量；另限制条目数。
    size = 4096 + len(result.boxes)*512 + (len(observation.points)*512 if observation else 0)
    if size > BUDGET:
        return
    with _lock:
        old = _cache.pop(key,None)
        if old:
            _bytes -= old[1]
        _cache[key] = (result,size)
        _bytes += size
        while _bytes > BUDGET or len(_cache) > 512:
            _,(_,cost) = _cache.popitem(last=False)
            _bytes -= cost
