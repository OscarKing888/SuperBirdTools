# -*- coding: utf-8 -*-
"""拍摄地点编辑、剪贴板和后台批量保存。"""
from __future__ import annotations

from pathlib import Path
import os

from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from app_common.file_browser._work_action import WorkerAction
from app_common.file_browser._metadata_actions import MetadataResult as LocationResult, LocationCopyAction
from app_common.file_browser._metadata_tasks import MetadataBatchController, MetadataCopyController
from app_common.shooting_location import shooting_location, write_shooting_location
from .file_context_menu import file_menu_group
from .bird_identification import _fingerprint
from .metadata_result_sync import MetadataResultSync
from .qt_compat import QApplication, QMessageBox
try:
    from PyQt6.QtWidgets import QInputDialog
except ImportError:  # pragma: no cover
    from PyQt5.QtWidgets import QInputDialog


def save_location(path, text):
    source = os.path.normpath(os.path.abspath(path))
    with xmp_sidecar_write_lock(source):
        updates = write_shooting_location(source, text)
        stamp = _fingerprint(PhotoMetaDataXMP().sidecar_path_for(source))
    return LocationResult(source, updates=updates, saved_fingerprint=stamp)


class LocationWriteAction(WorkerAction):
    def __init__(self, path, text, *, cancelled=lambda: False):
        super().__init__(cancelled=cancelled)
        self.path, self.text = path, text

    def execute(self):
        with xmp_sidecar_write_lock(self.path):
            if self.is_cancelled():
                return None
            return save_location(self.path, self.text)


class ShootingLocationController(MetadataBatchController):
    def __init__(self, window, files):
        super().__init__(files, window)
        self._main = window
        self._sync = MetadataResultSync(files)
        self._copy = MetadataCopyController(files, self)
        self._pending_paste = None
        files.add_file_context_menu_extender(self.extend_file_menu)

    @file_menu_group("capture")
    def extend_file_menu(self, menu, paths):
        photos = [p for p in paths if Path(p).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS]
        if not photos:
            return
        edit = menu.addAction("修改拍摄地点…", lambda: self.edit(photos))
        copy = menu.addAction("复制拍摄地点", lambda: self.copy(photos[0]))
        paste = menu.addAction("粘贴拍摄地点", lambda: self.paste(photos))
        copy.setEnabled(len(photos) == 1)
        edit.setEnabled(self._worker is None)
        paste.setEnabled(self._worker is None and (self._copy.pending or bool(QApplication.clipboard().text().strip())))

    def _source(self, path):
        source = self._files._resolve_source_path_for_action(path)
        if not source:
            raise FileNotFoundError(f"找不到照片原文件：{path}")
        return source

    def copy(self, path):
        if self._shutdown_requested:
            return
        try:
            source = self._source(path)
        except Exception as exc:
            QMessageBox.warning(self._main, "复制拍摄地点失败", str(exc))
            return
        self._pending_paste = None
        self._copy.start(
            lambda cancelled: LocationCopyAction(source, cancelled=cancelled),
            self._location_copied,
            lambda exc: QMessageBox.warning(self._main, "复制拍摄地点失败", str(exc)),
        )

    def _location_copied(self, text):
        QApplication.clipboard().setText(text)
        pending, self._pending_paste = self._pending_paste, None
        if pending:
            self.start(pending, text)

    def paste(self, paths):
        if self._copy.pending:
            self._pending_paste = list(paths)
            return
        self.start(paths, QApplication.clipboard().text())

    def edit(self, paths):
        current = shooting_location(self._files.cached_photo_metadata_for_path(paths[0])) if len(paths) == 1 else ""
        text, accepted = QInputDialog.getText(
            self._main, "修改拍摄地点", f"为 {len(paths)} 张照片设置拍摄地点（留空可清除）：", text=current,
        )
        if accepted:
            self.start(paths, text)

    def save_one(self, path, text):
        if self._shutdown_requested or self._worker is not None:
            raise RuntimeError("正在保存批量拍摄地点，请稍后再试。")
        source = self._source(path)
        result = save_location(source, text)
        self._sync.sync([result], [(path, source)])
        return True

    def start(self, paths, text):
        if self._shutdown_requested or self._worker is not None:
            return False
        if not self._files._file_writes_allowed("修改拍摄地点", warn=True):
            return False
        try:
            aliases = [(path, self._source(path)) for path in paths]
        except Exception as exc:
            QMessageBox.warning(self._main, "拍摄地点", str(exc))
            return False
        return self.start_batch(
            aliases,
            lambda path, cancelled: LocationWriteAction(path, text, cancelled=cancelled),
            "保存拍摄地点",
        )

    def request_shutdown(self):
        self._pending_paste = None
        self._copy.request_shutdown()
        super().request_shutdown()

    def is_shutdown_done(self):
        return super().is_shutdown_done() and not self._copy.busy
