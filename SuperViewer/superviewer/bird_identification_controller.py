# -*- coding: utf-8 -*-
"""SuperViewer 识鸟菜单、参数、进度与后台线程生命周期。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import traceback
from pathlib import Path

from app_common.log import get_logger
from .bird_identification import BirdIDClient, BirdIDOptions, collect_paths, identify_file, adopt_candidate
from .qt_compat import QThread, pyqtSignal

try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
        QFormLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout)
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject
    from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
        QFormLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QTextEdit, QVBoxLayout)

_log = get_logger("birdid.viewer")


@dataclass(frozen=True)
class BirdIDJob:
    inputs: tuple[str, ...]
    recursive: bool = False
    display_paths: tuple[tuple[str, str], ...] = ()


class BirdIDSettingsDialog(QDialog):
    def __init__(self, parent, options):
        super().__init__(parent)
        self.setWindowTitle("SuperPicky 识鸟")
        layout = QVBoxLayout(self)
        label = QLabel("请先启动本机 SuperPicky 的 BirdID 服务。\n达到阈值的首选鸟种写入鸟名和标题；低于阈值写入待确定候选。\n完整候选及定位信息存入同名 XMP；已有说明、标签和评级保留。", self)
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        self.url = QLineEdit(options.url, self)
        self.threshold = QDoubleSpinBox(self)
        self.threshold.setRange(0, 100)
        self.threshold.setSuffix(" %")
        self.threshold.setValue(options.threshold)
        self.skip = QCheckBox("跳过已有鸟种或识别记录的照片", self)
        self.skip.setChecked(options.skip_existing)
        form.addRow("本机服务地址", self.url)
        form.addRow("鸟种确认阈值", self.threshold)
        form.addRow(self.skip)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始识鸟")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self):
        return BirdIDOptions(self.url.text().strip(), self.threshold.value(), self.skip.isChecked())


class BirdIDWorker(QThread):
    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, job, options):
        super().__init__()
        self.job = job
        self.client = BirdIDClient(options)

    def stop(self):
        self.client.cancel()

    def run(self):
        try:
            self.status_changed.emit("正在连接 SuperPicky…")
            self.client.health()
            self.status_changed.emit("正在扫描照片…")
            paths = collect_paths(self.job.inputs, recursive=self.job.recursive, cancelled=self.client.cancelled.is_set)
            if not paths and not self.client.cancelled.is_set():
                self.failed.emit("没有找到可识别的照片。")
            self.progress_changed.emit(0, len(paths))
            for index, path in enumerate(paths, 1):
                if self.client.cancelled.is_set():
                    break
                self.status_changed.emit(f"正在识别：{Path(path).name}")
                result = identify_file(path, self.client)
                self.result_ready.emit(result)
                self.progress_changed.emit(index, len(paths))
        except Exception as exc:
            if not self.client.cancelled.is_set():
                _log.error("[BirdID] batch failed: %s", traceback.format_exc())
                self.failed.emit(str(exc))
        finally:
            self.client.cancel()


class BirdIDAdoptWorker(QThread):
    def __init__(self, entry, index, job):
        super().__init__()
        self.entry, self.index, self.job = entry, index, job
        self.previous = entry.result
        self.result = None

    def run(self):
        self.result = adopt_candidate(self.previous, self.index, cancelled=self.isInterruptionRequested)

    def stop(self):
        self.requestInterruption()


class BirdIDProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    def __init__(self, parent, *, results_table=False):
        super().__init__(parent)
        self.setWindowTitle("SuperPicky 识鸟进度")
        self.setMinimumSize(600, 380)
        self.running = True
        self.label = QLabel("准备识鸟…", self)
        self.label.setWordWrap(True)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        self.summary = QLabel("", self)
        if results_table:
            from .bird_identification_table import BirdIDResultsTable
            self.details = BirdIDResultsTable(self)
            self.resize(1180, 540)
        else:
            self.details = QTextEdit(self)
            self.details.setReadOnly(True)
            self.details.document().setMaximumBlockCount(1000)
        self.button = QPushButton("停止", self)
        self.button.clicked.connect(self._clicked)
        layout = QVBoxLayout(self)
        for widget in (self.label, self.bar, self.summary, self.details, self.button):
            layout.addWidget(widget)

    def _clicked(self):
        if self.running:
            self.cancel_requested.emit()
            self.button.setText("正在停止…")
            self.button.setEnabled(False)
        else:
            self.close()

    def closeEvent(self, event):
        if self.running:
            self.cancel_requested.emit()
        super().closeEvent(event)

    def finish(self, text):
        self.running = False
        self.label.setText(text)
        self.button.setText("关闭")
        self.button.setEnabled(True)


class BirdIDController(QObject):
    def __init__(self, window, file_list, dir_browser=None):
        super().__init__(window)
        self._main, self._file_list = window, file_list
        self._worker = self._dialog = self._adopt_worker = None
        self._shutdown_requested = False
        self._options = BirdIDOptions()
        self._counts = Counter()
        self._failure = ""
        self._stopped = False
        file_list.add_file_context_menu_extender(self.extend_file_menu)
        if dir_browser is not None:
            dir_browser.add_context_menu_extender(self.extend_directory_menu)

    @property
    def busy(self):
        return self._worker is not None or self._adopt_worker is not None

    def show_progress(self):
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()

    def _busy_menu(self, menu):
        menu.addAction("查看识鸟进度", self.show_progress)
        menu.addAction("停止识鸟", self.stop)

    def extend_file_menu(self, menu, paths):
        if self.busy:
            self._busy_menu(menu)
        elif paths:
            label = "识别鸟种…" if len(paths) == 1 else f"批量识别鸟种…（{len(paths)} 张）"
            menu.addAction(label, lambda: self.start_for_paths(list(paths)))

    def extend_directory_menu(self, menu, directory):
        sub = menu.addMenu("识别鸟种（SuperPicky）")
        if self.busy:
            self._busy_menu(sub)
        else:
            for label, recursive in (("识别当前目录…", False), ("识别目录及子目录…", True)):
                sub.addAction(label, lambda _checked=False, r=recursive: self.start(BirdIDJob((directory,), r)))

    def start_for_paths(self, paths, *, options=None):
        resolve = getattr(self._file_list, "_resolve_source_path_for_action", None)
        sources = []
        try:
            for path in paths:
                source = resolve(path) if callable(resolve) else path
                if not source:
                    raise ValueError(f"找不到照片原文件：{path}")
                sources.append(str(source))
        except Exception as exc:
            QMessageBox.information(self._main, "识别鸟种", str(exc))
            return False
        return self.start(BirdIDJob(tuple(sources), display_paths=tuple(zip(paths, sources))), options=options)

    def start(self, job, *, options=None):
        if self._shutdown_requested:
            return False
        if self.busy:
            self.show_progress()
            return False
        if options is None:
            settings = BirdIDSettingsDialog(self._main, self._options)
            accepted = settings.exec() == QDialog.DialogCode.Accepted
            options = settings.options()
            settings.deleteLater()
            if not accepted:
                return False
        try:
            options.validate()
        except ValueError as exc:
            QMessageBox.information(self._main, "识别鸟种", str(exc))
            return False
        self._options = options
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._counts, self._failure, self._stopped = Counter(), "", False
        self._job = job
        worker = self._worker = BirdIDWorker(job, options)
        dialog = self._dialog = BirdIDProgressDialog(self._main, results_table=True)
        dialog.details.adopt_requested.connect(lambda entry, index, d=dialog: self._adopt(d, entry, index))
        dialog.cancel_requested.connect(self.stop)
        worker.status_changed.connect(lambda text, w=worker: self._status(w, text))
        worker.progress_changed.connect(lambda n, total, w=worker: self._progress(w, n, total))
        worker.result_ready.connect(lambda result, w=worker: self._result(w, result))
        worker.failed.connect(lambda text, w=worker: self._failed(w, text))
        worker.finished.connect(lambda w=worker: self._finished(w))
        dialog.show()
        worker.start()
        return True

    def _active(self, worker):
        return worker is self._worker and not self._shutdown_requested

    def _status(self, worker, text):
        if self._active(worker):
            self._dialog.label.setText(text)

    def _progress(self, worker, done, total):
        if self._active(worker):
            self._dialog.bar.setRange(0, max(1, total))
            self._dialog.bar.setValue(done)

    def _result(self, worker, result):
        if not self._active(worker):
            return
        self._counts[result.status] += 1
        _log.info("[BirdID] %s %r: %s", result.status, result.source, result.message)
        self._dialog.details.append_result(result)
        self._update_summary()
        if result.updates:
            self._refresh_rows(result, worker.job)

    def _update_summary(self):
        labels = (("success", "已确认"), ("candidate", "待确定"), ("skipped", "跳过"), ("failed", "失败"), ("cancelled", "取消"))
        self._dialog.summary.setText("，".join(f"{label} {self._counts[key]}" for key, label in labels))

    def _adopt(self, dialog, entry, index):
        if (self._shutdown_requested or dialog is not self._dialog or self._adopt_worker is not None
                or not 0 <= index < entry.row_count):
            return
        model = dialog.details.results
        row = entry.first_row + index
        if row >= len(model.rows) or model.rows[row][0] is not entry or not model.can_adopt(row):
            return
        worker = self._adopt_worker = BirdIDAdoptWorker(entry, index, self._job)
        entry.pending_index = index
        model.set_pending(True)
        dialog.running = True
        dialog.button.setText("停止")
        dialog.button.setEnabled(True)
        if self._worker is None:
            dialog.label.setText("正在保存采纳结果…")
        worker.finished.connect(lambda w=worker: self._adopt_finished(w))
        worker.start()

    def _adopt_finished(self, worker):
        if worker is not self._adopt_worker:
            return
        self._adopt_worker = None
        if not self._shutdown_requested:
            result, entry = worker.result, worker.entry
            entry.pending_index = None
            if result.status == "success":
                self._counts[entry.result.status] -= 1
                self._counts[result.status] += 1
                entry.result, entry.error = result, ""
                self._refresh_rows(result, worker.job)
                self._update_summary()
            else:
                entry.error = result.message
                entry.stale = result.status == "skipped"
            _log.info("[BirdID] adopt %r: %s", result.source, result.message)
            self._dialog.details.results.refresh_entry(entry)
            self._dialog.details.results.set_pending(False)
        self._finish_dialog()
        if not self._shutdown_requested and self._worker is None:
            self._dialog.label.setText(worker.result.message)
        worker.deleteLater()

    def _refresh_rows(self, result, job):
        if not hasattr(self, "_metadata_sync"):
            from .metadata_result_sync import MetadataResultSync
            self._metadata_sync = MetadataResultSync(self._file_list)
        self._metadata_sync.sync([result], job.display_paths)

    def _failed(self, worker, text):
        if self._active(worker):
            self._failure = text

    def _finished(self, worker):
        if worker is not self._worker:
            return
        self._worker = None
        worker.deleteLater()
        self._finish_dialog()

    def _finish_dialog(self):
        if self.busy:
            return
        if self._shutdown_requested:
            self._dialog.finish("已停止")
            self._dialog.close()
        elif self._failure:
            self._dialog.finish(f"识鸟未完成：{self._failure}")
        elif self._stopped:
            self._dialog.finish("识鸟已停止，已保存的结果保留。")
        else:
            self._dialog.finish("识鸟完成。" if not self._counts["failed"] else "识鸟结束，部分照片失败，请查看详情。")

    def stop(self):
        self._stopped = True
        if self._worker is not None:
            self._worker.stop()
        if self._adopt_worker is not None:
            self._adopt_worker.stop()

    def request_shutdown(self):
        self._shutdown_requested = True
        self.stop()

    def is_shutdown_done(self):
        return not self.busy
