# -*- coding: utf-8 -*-
"""鸟体预览调度：快切只读内存，稳定选中才提交共享池 ANALYSIS action。"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import CancelledError
from dataclasses import dataclass
import os
import threading
import weakref

try:
    from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
except ImportError:
    from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal

from app_common.file_browser._work_policy import WorkKind
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from app_common.log import get_logger


_log = get_logger("superviewer.bird_body")


def _source_key(path: str) -> str:
    # 只做字符串规范化；长按路径上禁止 exists/stat/resolve 和 XMP 读取。
    return os.path.normcase(os.path.abspath(os.path.normpath(path))) if path else ""


@dataclass
class _Viewport:
    panel: weakref.ReferenceType
    source: str = ""
    path: str = ""
    committed: bool = False
    needs_request: bool = False
    enabled: bool = False


@dataclass(eq=False)
class _Request:
    source: str
    path: str
    cancel_event: threading.Event
    pool: object
    future: object = None


class BirdBodyController(QObject):
    """Own a bounded source cache and at most two pending/running viewport actions.

    ``show_source`` must receive the original photo identity after pixels have
    changed, including when the panel displays a cache JPEG. A/B viewports share
    source results but never copy one viewport's geometry or source identity.
    """

    _completed = pyqtSignal(object, object, object)
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None, *, pool_provider, debounce_ms=180,
                 max_cache_entries=2048, action_factory=None):
        super().__init__(parent)
        self._pool_provider = pool_provider
        self._action_factory = action_factory
        self._max_cache_entries = max(1, int(max_cache_entries))
        self._cache = OrderedDict()
        self._views: dict[int, _Viewport] = {}
        self._requests: list[_Request] = []
        self._enabled = False
        self._playback_active = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(debounce_ms)))
        self._timer.timeout.connect(self._submit_pending)
        connection = getattr(Qt, "ConnectionType", Qt).QueuedConnection
        self._completed.connect(self._on_completed, connection)

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled) and not self._closed
        changed = self._enabled != enabled
        self._enabled = enabled
        for view in self._views.values():
            view.enabled = enabled
            panel = view.panel()
            if panel is not None:
                panel._bird_body_enabled = enabled
                panel.set_show_bird_box(enabled)
                self._paint(view)
            if changed and enabled and view.committed:
                view.needs_request = True
        if not enabled:
            self._timer.stop()
            self._cancel_obsolete()
        else:
            self._schedule()

    def set_panel_enabled(self, panel, enabled: bool) -> None:
        view = self._views.setdefault(id(panel), _Viewport(weakref.ref(panel)))
        view.enabled = bool(enabled)
        panel._bird_body_enabled = view.enabled
        self._enabled = any(item.enabled for item in self._views.values())
        panel.set_show_bird_box(view.enabled)
        self._paint(view)
        view.needs_request = view.enabled and view.committed
        self._cancel_obsolete()
        self._schedule()

    def show_source(self, panel, source_path: str, *, committed: bool = True) -> None:
        if self._closed:
            return
        source = _source_key(source_path)
        if not source or os.path.splitext(source)[1].lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            self.set_playback_active(not committed)
            self.clear_panel(panel)
            return
        panel._bird_body_source_path = str(source_path)
        view = self._views.get(id(panel))
        if view is None:
            view = _Viewport(weakref.ref(panel), enabled=getattr(panel, "_bird_body_enabled", self._enabled))
            self._views[id(panel)] = view
        self._enabled = self._enabled or view.enabled
        changed = view.source != source
        resumed = bool(committed) and not view.committed
        view.source, view.path = source, str(source_path)
        view.committed = bool(committed)
        if changed or resumed:
            view.needs_request = bool(committed)
        panel.set_show_bird_box(view.enabled)
        self._paint(view)
        # MainWindow 只在最终提交时传 committed=True；完整图的异步升级在
        # 长按期间也必须传 False，避免 A/B 迟到结果重新启动后台检测。
        self.set_playback_active(not committed)
        self._cancel_obsolete()
        if changed or resumed:
            self._schedule(restart=True)
        else:
            self._schedule()

    def set_playback_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._playback_active:
            return
        self._playback_active = active
        if active:
            self._timer.stop()
            self._cancel_obsolete()
        else:
            self._schedule(restart=True)

    def clear_panel(self, panel) -> None:
        self._views.pop(id(panel), None)
        panel._bird_body_source_path = ""
        panel.set_bird_box(None)
        self._cancel_obsolete()
        if not self._views:
            self._playback_active = False
            self._timer.stop()

    def invalidate_paths(self, paths) -> None:
        """Called after a source replacement or explicit derived-cache reset."""
        keys = {_source_key(str(path)) for path in paths}
        for source in keys:
            self._cache.pop(source, None)
        for request in self._requests:
            if request.source in keys:
                self._cancel(request)
        for view in self._views.values():
            if view.source in keys:
                view.needs_request = view.committed
                self._paint(view)
        self._schedule(restart=True)

    def shutdown(self) -> None:
        self._closed = True
        self._timer.stop()
        self._cancel_obsolete()
        self._views.clear()
        self._cache.clear()

    request_shutdown = shutdown

    def is_shutdown_done(self) -> bool:
        # Future 的完成信号仍需由 GUI 消费，不能逻辑取消后就销毁 QObject。
        return not self._requests

    def _paint(self, view: _Viewport) -> None:
        panel = view.panel()
        if panel is None:
            return
        result = self._cache.get(view.source)
        if result is not None:
            self._cache.move_to_end(view.source)
        overlay = getattr(result, "overlay", getattr(result, "box", None)) if result is not None else None
        panel.set_bird_box(overlay if view.enabled else None)

    def _wanted_sources(self):
        return {view.source for view in self._views.values()
                if view.enabled and view.committed and view.panel() is not None}

    def _cancel(self, request: _Request) -> None:
        if request.cancel_event.is_set():
            return
        request.cancel_event.set()
        for view in self._views.values():
            if view.source == request.source and view.committed:
                view.needs_request = True
        if request.future is not None:
            request.pool.cancel(request.future)

    def _cancel_obsolete(self) -> None:
        wanted = self._wanted_sources() if self._enabled and not (
            self._closed or self._playback_active) else set()
        for request in self._requests:
            if request.source not in wanted:
                self._cancel(request)

    def _schedule(self, *, restart=False) -> None:
        if self._closed or not self._enabled or self._playback_active:
            return
        if not any(view.enabled and view.needs_request and view.committed and view.panel() is not None
                   for view in self._views.values()):
            return
        if restart or not self._timer.isActive():
            self._timer.start()

    def _make_action(self, path, cancelled):
        if self._action_factory is not None:
            return self._action_factory(path, cancelled=cancelled)
        from .bird_body_worker import BirdBodyAction
        return BirdBodyAction(path, cancelled=cancelled)

    def _submit_pending(self) -> None:
        if self._closed or not self._enabled or self._playback_active:
            return
        try:
            pool = self._pool_provider()
        except RuntimeError:
            return
        if pool is None:
            return
        active = {request.source for request in self._requests}
        for view in tuple(self._views.values()):
            if len(self._requests) >= 2:
                break
            if not view.enabled or not view.committed or not view.needs_request or view.panel() is None:
                continue
            if view.source in active:
                continue
            request = _Request(view.source, view.path, threading.Event(), pool)
            try:
                action = self._make_action(view.path, request.cancel_event.is_set)
                # 一次只提交最多 A/B 两项，提交中无 I/O/推理；无需保持生产者租约。
                request.future = pool.submit_action(action, kind=WorkKind.ANALYSIS)
            except (RuntimeError, ValueError) as exc:
                _log.warning("[bird_body] submit failed path=%s error=%s", view.path, exc)
                self.status_changed.emit(f"鸟体识别暂不可用：{exc}")
                continue
            self._requests.append(request)
            active.add(view.source)
            for same_source in self._views.values():
                if same_source.source == view.source:
                    same_source.needs_request = False
            request.future.add_done_callback(lambda future, request=request: self._done(request, future))
            self.status_changed.emit("正在识别鸟体…")

    def _done(self, request, future) -> None:
        outcome, error = None, None
        try:
            outcome = future.result()
        except CancelledError:
            pass
        except Exception as exc:
            error = str(exc)
        self._completed.emit(request, outcome, error)

    def _on_completed(self, request, outcome, error) -> None:
        if request not in self._requests:
            return
        self._requests.remove(request)
        if not self._closed and not request.cancel_event.is_set():
            error = error or getattr(outcome, "error", "")
            result = getattr(outcome, "result", None)
            if error:
                _log.warning("[bird_body] analysis failed path=%s error=%s", request.path, error)
                self.status_changed.emit(f"鸟体识别：{error}")
            if result is not None and not getattr(outcome, "cancelled", False):
                self._cache[request.source] = result
                self._cache.move_to_end(request.source)
                while len(self._cache) > self._max_cache_entries:
                    self._cache.popitem(last=False)
                for view in self._views.values():
                    if view.source == request.source:
                        self._paint(view)
                if not error:
                    count = len(getattr(result, "boxes", ()) or ())
                    self.status_changed.emit(("鸟体框已显示" + (f"（{count} 只）" if count > 1 else ""))
                                             if result.box is not None else "未检测到鸟体")
        self._schedule()
