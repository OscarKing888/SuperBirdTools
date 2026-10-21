"""Apply a captured color recipe through owned sessions, one saved photo at a time."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

try:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
except ImportError:
    from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from app_common.image_processing_types import check_cancelled, ImageProcessingCancelled
from app_common.photo_edit.assets import copy_recipe_assets, recipe_assets
from .photo_edit_controller import source_key


class PhotoEditBatch(QObject):
    busy_changed = pyqtSignal(bool)
    progress = pyqtSignal(int, int, str)
    item_finished = pyqtSignal(str, str)  # source, outcome/error
    finished = pyqtSignal(str)

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.busy = False
        self._stems = set()
        self._keys = set()
        self._worker = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        controller.state_changed.connect(self.wake)

    def wake(self, *_):
        if self.busy:
            self._timer.start(0)

    def locks(self, source):
        return self.busy and source_key(source) in self._keys

    def retains(self, source):
        return self.busy and source_key(source) == source_key(self._current)

    def affects_files(self, paths):
        return self.busy and any(str(Path(source_key(path)).with_suffix("")) in self._stems
                                 for path in paths)

    def can_start(self, source):
        owner = self.controller
        session = owner.session(source)
        return bool(source and session and not session.error and not session.conflict
                    and not self.busy and not owner._closing and not owner._suspended
                    and not owner._file_busy and not owner._operations.get(source_key(source)))

    def start(self, source, paths):
        if not self.can_start(source):
            return False
        unique = {}
        for path in paths:
            if path and Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                unique.setdefault(source_key(path), str(path))
        if not unique:
            return False
        session = self.controller.session(source)
        session.finish_gesture()
        self.source = str(source)
        self._recipe = session.state.current.recipe.validated()
        self.paths = tuple(unique.values())
        self._keys = set(unique) | {source_key(source)}
        self._stems = {str(Path(key).with_suffix("")) for key in self._keys}
        self.done = self.applied = self.unchanged = self.failed = 0
        self._current = ""
        self._phase = "next"
        self._cancelled = False
        self.busy = True
        self.busy_changed.emit(True)
        self.wake()
        return True

    def stop(self):
        if self.busy:
            self._cancelled = True
            if self._worker is not None:
                self._worker.stop()
            self.wake()

    def _advance(self):
        if not self.busy or self._phase == "prepare":
            return
        owner = self.controller
        if self._phase == "save":
            if source_key(self._current) in owner._saving:
                return
            error = self._session.error if self._session.dirty else ""
            if self._session.dirty and not error:
                error = "参数尚未保存，请在该照片上重试保存。"
            self._record(error)
            return
        if self._cancelled or owner._closing or self.done == len(self.paths):
            self._finish()
            return
        if owner._suspended or owner._file_busy:
            return
        if self._phase == "ready":
            key = source_key(self._current)
            if key in owner._saving:
                return
            if owner.session(self._current) is not self._session or self._session.serial != self._serial:
                self._record("操作期间参数已变化，未覆盖新调整，请重试。")
            elif self._session.error or self._session.conflict:
                self._record(self._session.error or "编辑参数存在冲突，请先处理该照片的草稿。")
            else:
                self._session.edit(self._prepared_recipe)
                self._save_or_record()
            return
        if not self._current:
            self._current = self.paths[self.done]
            self.progress.emit(self.done, len(self.paths), self._current)
        key = source_key(self._current)
        session = owner.session(self._current)
        if session is None:
            if key in owner._load_errors:
                self._record(owner._load_errors[key])
            else:
                owner.ensure_loaded(self._current)
            return
        if key in owner._saving or owner._operations.get(key):
            return
        if session.error or session.conflict:
            self._record(session.error or "编辑参数存在冲突，请先处理该照片的草稿。")
            return
        session.finish_gesture()
        self._session = session
        recipe = session.state.current.recipe.validated()
        recipe.steps = deepcopy(self._recipe.steps)
        self._changed = recipe != session.state.current.recipe
        if not self._changed and not recipe_assets(recipe):
            self._save_or_record()
            return
        self._serial = session.serial
        self._phase = "prepare"

        def prepare(cancelled):
            check_cancelled(cancelled)
            copy_recipe_assets(self.source, session.state.source, recipe)
            check_cancelled(cancelled)
            return recipe

        def prepared(value, error):
            self._worker = None
            if self._cancelled or owner._closing or isinstance(error, ImageProcessingCancelled):
                self._finish()
            elif error:
                self._record(str(error))
            else:
                self._prepared_recipe = value
                self._phase = "ready"
                self.wake()

        self._worker = owner._launch(prepare, prepared)

    def _save_or_record(self):
        if self._session.dirty:
            self._phase = "save"
            self.controller.flush()
            self.controller.state_changed.emit(self._current)
        else:
            self._record("")

    def _record(self, error):
        self.done += 1
        if error:
            self.failed += 1
            outcome = "失败：" + error
        elif self._changed:
            self.applied += 1
            outcome = "已应用并保存到 XMP"
        else:
            self.unchanged += 1
            outcome = "调色参数相同，已保留"
        self.item_finished.emit(self._current, outcome)
        self.progress.emit(self.done, len(self.paths), self._current)
        self._current = ""
        self._phase = "next"
        self.controller._trim_sessions()
        self.wake()

    def _finish(self):
        self._timer.stop()
        self.busy = False
        self._keys.clear()
        self._stems.clear()
        self.busy_changed.emit(False)
        state = "已停止" if self._cancelled or self.controller._closing else "应用完成"
        self.finished.emit(f"{state}：已应用 {self.applied} 张，参数相同 {self.unchanged} 张，"
                           f"失败 {self.failed} 张，未处理 {len(self.paths) - self.done} 张。")
