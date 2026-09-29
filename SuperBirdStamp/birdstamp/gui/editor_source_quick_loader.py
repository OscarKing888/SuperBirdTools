"""有界预览 action 调度；播放列表的每张图都写入应用缓存。"""
from collections import deque
from concurrent.futures import FIRST_COMPLETED, wait
import hashlib
import io
import logging
import os
from pathlib import Path
import struct
from threading import Condition, Semaphore
import tempfile

from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._work_policy import WorkKind
from birdstamp.config import get_user_data_dir
from birdstamp.decoders.image_decoder import decode_image_for_preview, read_decoded_image_size
from .editor_preview_decode_worker import cached_preview_image


_log = logging.getLogger(__name__)
_CACHE_EDGE = 512
_CACHE_SOFT_LIMIT = 512 * 1024 * 1024
_CACHE_VERSION = 2  # RAW 源尺寸改为实际内嵌 JPEG 的尺寸。


class SourceQuickAction(WorkerAction):
    """一张源图的小预览；磁盘缓存包含原图尺寸，不依赖 GUI 状态。"""

    def __init__(self, signature, path, cache_dir, *, cancelled):
        super().__init__(cancelled=cancelled)
        self.signature = signature
        self.path = Path(path)
        cache_key = f'{_CACHE_VERSION}:{signature}'
        self.cache_path = Path(cache_dir) / (hashlib.sha256(cache_key.encode('utf-8')).hexdigest() + '.bin')

    def _read_cache(self):
        try:
            with self.cache_path.open('rb') as stream:
                full_size = struct.unpack('>II', stream.read(8))
                with Image.open(io.BytesIO(stream.read())) as cached:
                    image = cached.convert('RGB')
            if min(full_size) <= 0 or max(image.size) > _CACHE_EDGE:
                image.close()
                return None
            return image, full_size
        except (OSError, ValueError, struct.error):
            return None

    def _write_cache(self, image, full_size):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.preview-', dir=self.cache_path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(struct.pack('>II', *full_size))
                image.save(stream, 'JPEG', quality=85)
            if not self.is_cancelled():
                os.replace(temporary, self.cache_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def execute(self):
        if self.is_cancelled():
            return None
        cached = self._read_cache()
        if cached is not None:
            return cached
        image = None
        try:
            try:
                image = cached_preview_image(self.path, _CACHE_EDGE)
            except Exception:
                image = None
            full_size = None
            if image is not None:
                try:
                    full_size = read_decoded_image_size(self.path)
                except Exception:
                    image.close()
                    image = None
            if image is None:
                image = decode_image_for_preview(self.path, max_long_edge=_CACHE_EDGE, decoder='auto')
                properties = image.info.get('birdstamp_source_properties') or {}
                full_size = properties.get('size') or image.size
            if self.is_cancelled():
                return None
            if image.mode != 'RGB':
                converted = image.convert('RGB')
                image.close()
                image = converted
            full_size = (int(full_size[0]), int(full_size[1]))
            self._write_cache(image, full_size)
            if self.is_cancelled():
                return None
            result, image = image, None
            return result, full_size
        finally:
            if image is not None:
                image.close()


class SourceQuickLoader(QThread):
    ready = pyqtSignal(str, str, object, object)
    failed = pyqtSignal(str, str, str)

    def __init__(self, pool, parent=None):
        super().__init__(parent)
        self._pool = pool
        self._cache_dir = get_user_data_dir() / 'config' / 'cache' / 'source_preview'
        self._condition = Condition()
        self._pending = deque()
        self._queued = set()
        self._inflight = {}
        self._generation = 0
        self._pruned_generation = -1
        self._entries = ()
        self._ready_slots = Semaphore(8)

    def reset(self, entries):
        """列表身份变化时取消旧代；同签名磁盘文件仍可直接复用。"""
        entries = tuple(dict((signature, (signature, Path(path))) for signature, path in entries).values())
        with self._condition:
            self._generation += 1
            self._entries = tuple(entries)
            for future in self._inflight:
                self._pool.cancel(future)
            self._pending = deque((signature, Path(path)) for signature, path in entries)
            self._queued = {signature for signature, _ in entries}
            self._condition.notify_all()

    def enqueue(self, entries):
        """把当前位置附近的缺失位图放到全列表任务前。"""
        with self._condition:
            inflight = {entry[0] for entry in self._inflight.values()}
            for signature, path in reversed(tuple(entries)):
                if signature in inflight:
                    continue
                if signature in self._queued:
                    self._pending = deque(entry for entry in self._pending if entry[0] != signature)
                self._queued.add(signature)
                self._pending.appendleft((signature, Path(path)))
            self._condition.notify_all()

    def stop(self):
        self.requestInterruption()
        with self._condition:
            self._generation += 1
            for future in self._inflight:
                self._pool.cancel(future)
            self._condition.notify_all()

    def release_ready(self):
        self._ready_slots.release()

    def _prune_cache(self, entries):
        """软上限只淘汰其他列表；当前列表的完整预取不被清理破坏。"""
        protected = {hashlib.sha256(signature.encode('utf-8')).hexdigest() + '.bin'
                     for signature, _ in entries}
        try:
            files = [(path, path.stat()) for path in self._cache_dir.glob('*.bin')]
        except OSError:
            return
        total = sum(stat.st_size for _, stat in files)
        for path, stat in sorted(files, key=lambda entry: entry[1].st_mtime_ns):
            if total <= _CACHE_SOFT_LIMIT:
                break
            if path.name in protected:
                continue
            try:
                path.unlink()
                total -= stat.st_size
            except OSError:
                pass

    def run(self):
        while not self.isInterruptionRequested():
            prune_entries = None
            with self._condition:
                generation = self._generation
                while self._pending and len(self._inflight) < 4:
                    signature, path = self._pending.popleft()
                    self._queued.discard(signature)
                    action = SourceQuickAction(
                        signature, path, self._cache_dir,
                        cancelled=lambda g=generation: self.isInterruptionRequested() or g != self._generation,
                    )
                    try:
                        future = self._pool.submit_action(action, kind=WorkKind.THUMBNAIL)
                    except RuntimeError:
                        return  # 窗口关闭时共享池已停止接收新任务。
                    self._inflight[future] = (signature, path, generation)
                futures = tuple(self._inflight)
                if not futures:
                    if generation != self._pruned_generation and not self._pending:
                        self._pruned_generation = generation
                        prune_entries = self._entries
                    else:
                        self._condition.wait(0.1)
            if prune_entries is not None:
                self._prune_cache(prune_entries)
                continue
            if not futures:
                continue
            completed, _ = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in completed:
                with self._condition:
                    entry = self._inflight.pop(future, None)
                if entry is None:
                    continue
                signature, path, task_generation = entry
                image = None
                try:
                    result = future.result()
                    if result is None:
                        continue
                    image, full_size = result
                    if self.isInterruptionRequested() or task_generation != self._generation:
                        continue
                    while not self._ready_slots.acquire(timeout=0.05):
                        if self.isInterruptionRequested() or task_generation != self._generation:
                            break
                    else:
                        if self.isInterruptionRequested() or task_generation != self._generation:
                            self._ready_slots.release()
                            continue
                        self.ready.emit(signature, str(path), image, full_size)
                        image = None
                        continue
                except Exception as exc:
                    if not self.isInterruptionRequested() and task_generation == self._generation:
                        _log.warning('[SourceQuickLoader] failed path=%s: %s', path, exc)
                        self.failed.emit(signature, str(path), str(exc))
                finally:
                    if image is not None:
                        image.close()
