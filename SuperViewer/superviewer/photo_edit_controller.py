"""Owned background editing sessions; no metadata or decoding on selection."""
from __future__ import annotations

from collections import OrderedDict
import os
from pathlib import Path
import threading
import traceback

try:
    from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
except ImportError:
    from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal

from app_common.log import get_logger
from app_common.image_processing_types import ImageProcessingCancelled, check_cancelled
from app_common.image_output_paths import ImageOutputOptions
from app_common.photo_edit.cache import ImageCache
from app_common.photo_edit.session import EditSession
from app_common.photo_edit.storage import RecipeStore, RecoveryStore, EditConflict
from app_common.photo_edit.pipeline import render_recipe, resolve_input
from app_common.superviewer_user_options import get_runtime_user_options, get_user_state_dir

_log = get_logger("superviewer.photo_edit")


def source_key(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


class EditWorker(QThread):
    """Results are consumed only from real finished, retaining this QThread."""
    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.stop_event = threading.Event()
        self.value = None
        self.error = None

    def stop(self):
        self.stop_event.set()
        self.requestInterruption()

    def run(self):
        try:
            self.value = self.function(self.stop_event.is_set)
        except ImageProcessingCancelled as exc:
            self.error = exc
        except Exception as exc:
            self.error = exc
            _log.error("Photo edit worker failed: %s",traceback.format_exc())


class _SaveFailure(RuntimeError):
    def __init__(self, cause, *, draft_saved):
        super().__init__(str(cause))
        self.cause = cause
        self.draft_saved = draft_saved


class PhotoEditController(QObject):
    state_changed = pyqtSignal(str)
    render_ready = pyqtSignal(str, object, int, object)  # source, consumer, request id, RenderResult
    render_failed = pyqtSignal(str, object, int, str)
    operation_finished = pyqtSignal(str, str, object)  # source, operation, result
    error_changed = pyqtSignal(str, str)

    def __init__(self, parent=None, *, directory=None, store=None, renderer=None):
        super().__init__(parent)
        options = get_runtime_user_options()
        directory = Path(directory or get_user_state_dir())/"photo_edit"
        self.cache = ImageCache(directory/"previews",memory_bytes=options["photo_edit_memory_mb"]*1024**2,
                                disk_bytes=options["photo_edit_disk_mb"]*1024**2)
        self.store = store or RecipeStore()
        self.recovery = RecoveryStore(directory/"recovery")
        self.renderer = renderer or render_recipe
        self.sessions = OrderedDict()
        self.current_source = ""
        self._workers = {}
        self._loading = set()
        self._load_pending = OrderedDict()
        self._saving = set()
        self._load_errors = {}
        self._render_pending = OrderedDict()
        self._render_active = None
        self._render_versions = {}
        self._next_request = 0
        self._suspended = False
        self._closing = False
        self._file_busy = 0
        self._operations = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.flush)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self._start_render)

    def session(self, source=None):
        return self.sessions.get(source_key(source or self.current_source))

    def error(self, source=None):
        key = source_key(source or self.current_source)
        session = self.sessions.get(key)
        return session.error if session else self._load_errors.get(key,"")

    def _launch(self, function, done):
        worker = EditWorker(function,self)
        self._workers[worker] = done
        worker.finished.connect(lambda worker=worker:self._finished(worker))
        worker.start()
        return worker

    def _finished(self, worker):
        done = self._workers.pop(worker,None)
        if done is not None:
            try:
                done(worker.value,worker.error)
            except Exception:
                _log.error("Photo edit completion failed: %s",traceback.format_exc())
        worker.deleteLater()

    def select(self, source):
        if self._closing:
            return
        previous = self.session()
        if previous:
            previous.finish_gesture()
            if previous.dirty:
                self._save_timer.start()
        self.current_source = os.path.abspath(source) if source else ""
        if not source:
            self.state_changed.emit("")
            return
        self.ensure_loaded(source)
        if self.session(source):
            self.state_changed.emit(str(source))

    def ensure_loaded(self, source):
        if self._closing or self._suspended or self._file_busy or not source:
            return
        key = source_key(source)
        if key in self.sessions:
            self.sessions.move_to_end(key)
            return
        if key in self._loading or key in self._load_errors:
            return
        if self._loading:
            self._load_pending[key] = str(source)
            self._load_pending.move_to_end(key)
            while len(self._load_pending)>4:
                self._load_pending.popitem(last=False)
            return
        self._loading.add(key)
        def load(cancelled):
            check_cancelled(cancelled)
            state = self.store.load(source)
            draft = self.recovery.load(source)
            check_cancelled(cancelled)
            session = EditSession(state)
            if draft and draft.to_dict()!=state.to_dict():
                session.state = draft
                session.serial = 1
                session.recovery_serial = 1
                if draft.fingerprint!=state.fingerprint or draft.revision!=state.revision:
                    session.conflict = True
                    session.error = "已保留恢复草稿，但原图或外部参数已变化。请复制参数或重新载入后处理。"
            return session
        def done(session,error):
            self._loading.discard(key)
            self._drain_loads()
            if isinstance(error,ImageProcessingCancelled):
                return
            if error:
                self._load_errors[key] = str(error)
                while len(self._load_errors)>32:
                    self._load_errors.pop(next(iter(self._load_errors)))
                self.error_changed.emit(str(source),str(error))
                # Fail pending previews instead of leaving a loading state forever.
                for consumer,request in list(self._render_pending.items()):
                    if source_key(request[0])==key:
                        self._render_pending.pop(consumer)
                        self.render_failed.emit(str(source),consumer,request[1],str(error))
            else:
                self.sessions[key] = session
                self._trim_sessions()
                if session.dirty and not session.conflict:
                    self._save_timer.start()
            self.state_changed.emit(str(source))
            self._start_render()
        self._launch(load,done)

    def _drain_loads(self):
        while self._load_pending and not self._loading and not self._suspended and not self._closing:
            _,source = self._load_pending.popitem(last=True)
            self.ensure_loaded(source)

    def _trim_sessions(self):
        for key,session in list(self.sessions.items()):
            if len(self.sessions)<=24:
                break
            if key!=source_key(self.current_source) and key not in self._saving and not session.dirty:
                del self.sessions[key]

    def edit(self, recipe, *, commit=True):
        session = self.session()
        if not session or self._closing or self._suspended or self._file_busy:
            return False
        changed = session.edit(recipe,commit=commit)
        if changed or commit:
            self._changed(session,save=commit)
        return changed

    def finish_gesture(self):
        session = self.session()
        if session:
            session.finish_gesture()
            if session.dirty:
                self._save_timer.start()
            self.state_changed.emit(session.state.source)

    def _changed(self, session, *, save=True):
        if save and not self._suspended:
            self._save_timer.start()
        self.state_changed.emit(session.state.source)

    def undo(self):
        session = self.session()
        if session and not self._closing and not self._suspended and not self._file_busy and session.undo():
            self._changed(session)

    def redo(self):
        session = self.session()
        if session and not self._closing and not self._suspended and not self._file_busy and session.redo():
            self._changed(session)

    def select_input(self, key):
        session = self.session()
        if session and not self._closing and not self._suspended and not self._file_busy and session.select_input(key):
            self._changed(session)

    def flush(self):
        if self._suspended and not self._closing:
            return
        self._save_timer.stop()
        for key,session in list(self.sessions.items()):
            if (not session.dirty or key in self._saving or
                    (session.conflict and session.recovery_serial==session.serial)):
                continue
            snapshot,serial = session.snapshot(),session.serial
            journal_only = session.conflict
            self._saving.add(key)
            def save(_cancelled,snapshot=snapshot,journal_only=journal_only):
                try:
                    self.recovery.save(snapshot)
                except Exception as exc:
                    raise _SaveFailure(exc,draft_saved=False) from exc
                if journal_only:
                    return None
                try:
                    return self.store.save(snapshot)
                except Exception as exc:
                    raise _SaveFailure(exc,draft_saved=True) from exc
            def saved(revision,error,key=key,session=session,serial=serial,snapshot=snapshot):
                self._saving.discard(key)
                if error is None or isinstance(error,_SaveFailure) and error.draft_saved:
                    session.recovery_serial = serial
                if error:
                    preserved = ("草稿已保留" if isinstance(error,_SaveFailure) and error.draft_saved else
                                 "本地恢复草稿也未写入，编辑仍保留在当前窗口")
                    session.error = f"参数未写入 XMP，{preserved}：{error}"
                    session.conflict = isinstance(getattr(error,"cause",error),EditConflict)
                    self.error_changed.emit(session.state.source,session.error)
                elif revision is not None:
                    session.saved(serial,revision)
                    if session.dirty:
                        if self._closing:
                            self.flush()
                        else:
                            self._save_timer.start()
                    else:
                        # Remove only the draft we just saved, in a worker.
                        self._launch(lambda _cancelled,snapshot=snapshot:self.recovery.remove_if_matches(snapshot),
                                     lambda _value,_error:None)
                self.state_changed.emit(session.state.source)
                self._trim_sessions()
            self._launch(save,saved)

    def retry_save(self):
        session = self.session()
        if session and not session.conflict:
            self.flush()

    def reload(self, source=None):
        """Explicit user action discards this session's unsaved draft."""
        source = source or self.current_source
        key = source_key(source)
        if key in self._saving or key in self._loading or self._closing:
            return False
        self.sessions.pop(key,None)
        self._load_errors.pop(key,None)
        def done(_value,error):
            if error:
                self._load_errors[key] = str(error)
                self.error_changed.emit(source,str(error))
            else:
                self.ensure_loaded(source)
        self._launch(lambda _cancelled:self.recovery.remove(source),done)
        return True

    def request_render(self, source, consumer, *, before=False, full=False):
        self._next_request += 1
        request_id = self._next_request
        if self._closing or self._suspended or self._file_busy:
            return request_id
        self._render_versions[consumer] = request_id
        self._render_pending[consumer] = (str(source),request_id,before,full)
        if self._render_active and self._render_active[0]==consumer:
            self._render_active[1].stop()
        if not self._closing and not self._suspended:
            self.ensure_loaded(source)
            if source_key(source) in self._load_errors:
                self._render_pending.pop(consumer,None)
                error = self._load_errors[source_key(source)]
                QTimer.singleShot(0,lambda:self.render_failed.emit(str(source),consumer,request_id,error))
            self._render_timer.start()
        return request_id

    def cancel_render(self, consumer):
        self._render_pending.pop(consumer,None)
        self._render_versions.pop(consumer,None)
        if self._render_active and self._render_active[0]==consumer:
            self._render_active[1].stop()

    def _start_render(self):
        if self._closing or self._suspended or self._file_busy or self._render_active:
            return
        for consumer,request in list(self._render_pending.items()):
            source,request_id,before,full = request
            session = self.session(source)
            if session is None:
                continue
            del self._render_pending[consumer]
            snapshot = session.snapshot()
            def render(cancelled):
                return self.renderer(snapshot,proxy=not full,before=before,cache=self.cache,cancelled=cancelled)
            def done(result,error,consumer=consumer,request_id=request_id,source=source):
                self._render_active = None
                if (not self._closing and not self._suspended and not self._file_busy and
                        self._render_versions.get(consumer)==request_id):
                    if error and not isinstance(error,ImageProcessingCancelled):
                        self.render_failed.emit(source,consumer,request_id,str(error))
                    elif error is None:
                        self.render_ready.emit(source,consumer,request_id,result)
                self._start_render()
            worker = self._launch(render,done)
            worker.source = source
            self._render_active = (consumer,worker)
            break

    def set_suspended(self, value):
        self._suspended = bool(value)
        if value:
            self._load_pending.clear()
            self._save_timer.stop()
            self._render_timer.stop()
            if self._render_active:
                self._render_active[1].stop()
            self._render_pending.clear()
        elif not self._closing:
            # The caller commits the final navigation selection. Do not read
            # the previously selected photo merely because playback stopped.
            if any(session.dirty for session in self.sessions.values()):
                self._save_timer.start()

    def run_operation(self, operation, function, *, source=None, apply=None):
        """Run imports/input validation/exports against a captured photograph."""
        source = source or self.current_source
        session = self.session(source)
        if not session or self._closing or self._suspended or self._file_busy:
            return False
        snapshot = session.snapshot()
        serial = session.serial
        key = source_key(source)
        self._operations[key] = self._operations.get(key,0)+1
        self.state_changed.emit(source)
        def done(value,error):
            self._operations[key] -= 1
            if not self._operations[key]:
                del self._operations[key]
            self.state_changed.emit(source)
            if self._closing:
                return
            if error:
                if not isinstance(error,ImageProcessingCancelled):
                    self.error_changed.emit(source,str(error))
            else:
                if apply:
                    current = self.session(source)
                    if current is not session or session.serial!=serial:
                        self.error_changed.emit(source,"操作期间参数已变化，未覆盖新调整，请重试。")
                        return
                    apply(session,value)
                    self._changed(session)
                self.operation_finished.emit(source,operation,value)
        self._launch(lambda cancelled:function(snapshot,cancelled),done)
        return True

    def choose_denoised(self, path=None):
        def locate(snapshot,cancelled):
            target = path
            if not target:
                from image_denoise.preview import find_denoised_preview, denoise_preview_history_path
                from .denoise_controller import current_denoise_options
                found = find_denoised_preview(snapshot.source,current_denoise_options(),cancelled=cancelled,
                                              history_path=denoise_preview_history_path())
                if not found:
                    raise ValueError("未找到匹配的降噪成片，请先降噪或选择已有版本。")
                target = found.path
            snapshot.use_denoised(target)
            resolve_input(snapshot)
            return str(target)
        return self.run_operation("denoised",locate,apply=lambda session,path:session.add_denoised(path))

    def auto_adjust(self, mode="tone", *, source=None):
        from app_common.photo_edit.auto_adjust import prepare_auto_adjustment, can_auto_adjust
        source = source or self.current_source
        session = self.session(source)
        if (not session or self._closing or self._suspended or self._file_busy or
                self._operations.get(source_key(source)) or
                not can_auto_adjust(session.state.current.recipe)):
            return False
        session.finish_gesture()
        return self.run_operation("auto-adjust", lambda snapshot, cancelled:
            prepare_auto_adjustment(snapshot, mode, cache=self.cache, cancelled=cancelled),
            source=source, apply=lambda current, result: current.edit(result.recipe))

    def export(self, *, directory=None, destination=None):
        from app_common.photo_edit.export import export_recipe
        values = get_runtime_user_options()
        options = ImageOutputOptions(**{key:values["photo_edit_"+key] for key in
                                       ("output_mode","subdir","output_directory","format")})
        if directory:
            from dataclasses import replace
            options = replace(options,output_directory=directory)
        return self.run_operation("export",lambda snapshot,cancelled:export_recipe(snapshot,options,
            destination=destination,cache=self.cache,cancelled=cancelled))

    def clear_cache(self):
        if self._render_active:
            self._render_active[1].stop()
        self._render_pending.clear()
        # Serialise with render/decode so an in-flight result cannot recreate it.
        def clear(cancelled):
            from app_common.photo_edit.pipeline import full_resolution_slot
            with full_resolution_slot(cancelled):
                self.cache.clear(disk=True)
        self._launch(clear,lambda _value,error:self.operation_finished.emit("","clear-cache",str(error) if error else ""))

    def request_shutdown(self):
        if self._closing:
            return
        self._closing = True
        self._save_timer.stop()
        self._render_timer.stop()
        self._render_pending.clear()
        self._load_pending.clear()
        for worker in list(self._workers):
            worker.stop()
        for session in self.sessions.values():
            session.finish_gesture()
        self.flush()

    def shutdown(self, *, wait_timeout_ms=0):
        self.request_shutdown()
        if wait_timeout_ms:
            for worker in list(self._workers):
                worker.wait(wait_timeout_ms)
        return not self._workers

    def is_shutdown_done(self):
        return self._closing and not self._workers

    def unsafe_drafts(self):
        """A close flow must keep the window alive if even recovery writing failed."""
        return [s.state.source for s in self.sessions.values() if s.dirty and s.recovery_serial<s.serial]

    def file_operation_reason(self, paths, *, prepare=False):
        """Keep same-stem XMP saves and edits out of file transfers."""
        if self._file_busy:
            return "文件操作正在进行，完成后可继续编辑或移动。"
        stems = {str(Path(source_key(path)).with_suffix('')) for path in paths}
        affected = lambda key:str(Path(key).with_suffix('')) in stems
        if self._render_active and affected(source_key(self._render_active[1].source)):
            if prepare:
                self._render_active[1].stop()
                for consumer,request in list(self._render_pending.items()):
                    if affected(source_key(request[0])):
                        self._render_pending.pop(consumer,None)
            return "正在结束照片编辑预览读取，请稍后重试文件操作。"
        if any(affected(key) for key in self._loading | self._operations.keys()):
            return "正在读取或处理这些照片的编辑参数，请完成后重试文件操作。"
        pending = [s for key,s in self.sessions.items() if affected(key) and (s.dirty or key in self._saving)]
        if pending:
            if prepare:
                for session in pending:
                    session.finish_gesture()
                self.flush()
            return next((s.error for s in pending if s.error),
                        "正在保存最新编辑参数，请保存完成后重试文件操作。")
        return ""

    def begin_file_operation(self):
        self._file_busy += 1
        for session in self.sessions.values():
            self.state_changed.emit(session.state.source)

    def end_file_operation(self, paths=()):
        self._file_busy = max(0,self._file_busy-1)
        # Copy/move/rename can replace an existing path. Re-read its real XMP.
        for path in paths:
            key = source_key(path)
            session = self.sessions.get(key)
            if session and not session.dirty:
                self.sessions.pop(key,None)
            self._load_errors.pop(key,None)
        for path in set(paths):
            self.state_changed.emit(str(path))
        for session in self.sessions.values():
            self.state_changed.emit(session.state.source)
        self._start_render()
