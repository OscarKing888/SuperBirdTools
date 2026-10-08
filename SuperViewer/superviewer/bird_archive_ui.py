# -*- coding: utf-8 -*-
"""珍禽入册的设置、菜单与后台任务生命周期。"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

try:
    from PyQt6.QtCore import QObject, QPointF, Qt, QThread, pyqtSignal
    from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
    from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
        QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
        QTextEdit, QVBoxLayout, QWidget)
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QPointF, Qt, QThread, pyqtSignal
    from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
    from PyQt5.QtWidgets import (QAction, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
        QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
        QTextEdit, QVBoxLayout, QWidget)

from app_common.log import get_logger
from . import paths_settings
from .bird_archive import ArchiveOptions, archive_photos

_log = get_logger("bird_archive")


def archive_icon() -> QIcon:
    """金色圆章里的飞鸟和书册，矢量绘制保证两种系统无需 emoji 字体。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#c28a27"))
    painter.drawEllipse(2, 2, 60, 60)
    painter.setBrush(QColor("#fff9e8"))
    bird = QPainterPath(QPointF(13, 21))
    bird.cubicTo(22, 23, 27, 27, 31, 28)
    bird.cubicTo(29, 20, 26, 15, 22, 12)
    bird.cubicTo(35, 13, 38, 21, 39, 24)
    bird.cubicTo(44, 19, 49, 23, 48, 27)
    bird.lineTo(54, 28)
    bird.lineTo(47, 31)
    bird.cubicTo(43, 40, 30, 39, 25, 32)
    bird.closeSubpath()
    painter.drawPath(bird)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#fff9e8"), 3))
    for coords in (((15, 42), (32, 47), (49, 42)), ((15, 48), (32, 53), (49, 48))):
        painter.drawLine(QPointF(*coords[0]), QPointF(*coords[1]))
        painter.drawLine(QPointF(*coords[1]), QPointF(*coords[2]))
    painter.end()
    return QIcon(pixmap)


def archive_action(parent, text="珍禽入册…"):
    action = QAction(archive_icon(), text, parent)
    font = action.font()
    font.setBold(True)
    action.setFont(font)
    action.setIconVisibleInMenu(True)
    action.setToolTip("将选中的照片与 XMP 按鸟名编入珍藏名册；自动避开同名文件。")
    return action


def settings_path() -> Path:
    return Path(paths_settings._get_user_state_dir()) / "bird_archive.json"


def load_archive_options() -> ArchiveOptions:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return ArchiveOptions(str(data.get("directory") or ""),
                              "copy" if data.get("mode") == "copy" else "move",
                              data.get("date_prefix", True) is not False)
    except (OSError, ValueError, TypeError, AttributeError):
        return ArchiveOptions()


def save_archive_options(options: ArchiveOptions) -> None:
    target = settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".bird-archive-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(options), stream, ensure_ascii=False, indent=2)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ArchiveOptionsForm(QWidget):
    """用户选项页和入册确认窗口共用的归档设置表单，不直接写配置。"""
    def __init__(self, parent=None, *, count=0, options=None):
        super().__init__(parent)
        options = options or load_archive_options()
        layout = QVBoxLayout(self)
        title = QLabel("珍禽入册 · 以鸟为目，以影成册", self)
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        intro = QLabel(f"将选中的 {count} 张照片按鸟名收藏。" if count else "设置照片名册的存放位置和入册方式。", self)
        layout.addWidget(intro)
        form = QFormLayout()
        self.directory = QLineEdit(options.directory, self)
        self.directory.setPlaceholderText("选择归档根目录")
        browse = QPushButton("选择目录…", self)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.directory, 1)
        row.addWidget(browse)
        form.addRow("归档目录", row)
        self.mode = QComboBox(self)
        self.mode.addItem("移动入册（从原目录移走）", "move")
        self.mode.addItem("复制入册（保留原照片）", "copy")
        self.mode.setCurrentIndex(self.mode.findData(options.mode))
        form.addRow("入册方式", self.mode)
        self.date_prefix = QCheckBox("在原文件名前添加拍摄日期和时间", self)
        self.date_prefix.setChecked(options.date_prefix)
        form.addRow("文件命名", self.date_prefix)
        layout.addLayout(form)
        hint = QLabel("例：白鹭 / RAW / 20261005_083015_DSC01234.ARW\n"
                      "自动分类：RAW/HIF/HEIF/HEIC → RAW；PSD → PSD；PNG/JPG/JPEG → Export。\n"
                      "其他图片格式保留在鸟名目录。\n"
                      "同名自动追加 _002、_003…，绝不覆盖已有照片。\n"
                      "XMP 随照片入册；同名 ACR 随 RAW/HIF 一起入册。\n"
                      "没有鸟名的照片跳过，拍摄日期缺失时标为“日期未知”。", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择归档根目录", self.directory.text())
        if path:
            self.directory.setText(path)

    def selected_options(self):
        return ArchiveOptions(self.directory.text().strip(), self.mode.currentData(), self.date_prefix.isChecked())


class ArchiveDialog(QDialog):
    def __init__(self, parent=None, *, count=0, options=None):
        super().__init__(parent)
        self.setWindowTitle("珍禽入册" if count else "珍禽入册 · 归档设置")
        self.setWindowIcon(archive_icon())
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self.form = ArchiveOptionsForm(self, count=count, options=options)
        layout.addWidget(self.form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始入册" if count else "保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_options(self):
        return self.form.selected_options()

    def accept(self):
        if not self.selected_options().directory:
            QMessageBox.information(self, "珍禽入册", "请先选择归档目录。")
            return
        try:
            save_archive_options(self.selected_options())
        except OSError as exc:
            QMessageBox.warning(self, "珍禽入册", f"归档设置保存失败：{exc}")
            return
        super().accept()


class ArchiveWorker(QThread):
    progress = pyqtSignal(int, int)
    result_ready = pyqtSignal(object)

    def __init__(self, paths, options, report_rows):
        super().__init__()
        self.paths, self.options, self.report_rows = paths, options, report_rows
        self.results = []
        self.error = ""

    def run(self):
        try:
            self.results = archive_photos(self.paths, self.options, report_rows=self.report_rows,
                cancelled=self.isInterruptionRequested, on_result=self.result_ready.emit,
                on_progress=self.progress.emit)
        except Exception as exc:
            self.error = str(exc)
            _log.exception("[Archive] batch failed")


class ArchiveProgress(QDialog):
    stop_requested = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("珍禽入册 · 编册进度")
        self.setWindowIcon(archive_icon())
        self.setMinimumWidth(580)
        self.running = True
        layout = QVBoxLayout(self)
        self.label = QLabel("正在读取鸟名与拍摄日期…", self)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)
        self.details = QTextEdit(self)
        self.details.setReadOnly(True)
        self.details.document().setMaximumBlockCount(2000)
        self.button = QPushButton("停止入册", self)
        self.button.clicked.connect(self._button)
        for widget in (self.label, self.bar, self.details, self.button):
            layout.addWidget(widget)

    def _button(self):
        if self.running:
            self.stop_requested.emit()
            self.button.setText("正在完成当前照片组…")
            self.button.setEnabled(False)
        else:
            self.close()

    def closeEvent(self, event):
        if self.running:
            self.stop_requested.emit()
        super().closeEvent(event)

    def finish(self, message):
        self.running = False
        self.label.setText(message)
        self.button.setEnabled(True)
        self.button.setText("关闭")


class BirdArchiveController(QObject):
    photos_moved = pyqtSignal(object)

    def __init__(self, main_window, file_list):
        super().__init__(main_window)
        self._main, self._files = main_window, file_list
        self._worker = self._dialog = None
        self._shutdown_requested = False
        self._display_paths = {}
        self._scope_keys = {}
        file_list.add_file_context_menu_extender(self.extend_file_menu)

    @property
    def busy(self):
        return self._worker is not None

    def extend_file_menu(self, menu, paths):
        if not paths:
            return
        action = archive_action(menu, f"珍禽入册…（{len(paths)} 张）")
        first = menu.actions()[0] if menu.actions() else None
        menu.insertAction(first, action)
        menu.insertSeparator(first)
        action.triggered.connect(lambda _checked=False, p=tuple(paths): self.start_for_paths(p))

    def start_selected(self):
        return self.start_for_paths(self._files._active_view_selected_paths())

    def start_for_paths(self, paths, *, options=None):
        if self._shutdown_requested:
            return False
        if self.busy:
            self._dialog.show()
            self._dialog.raise_()
            return False
        if not paths:
            QMessageBox.information(self._main, "珍禽入册", "请先选择要入册的照片。")
            return False
        if options is None:
            dialog = ArchiveDialog(self._main, count=len(paths))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            options = dialog.selected_options()
        sources, reports, displays, scopes = [], {}, {}, {}
        try:
            for path in paths:
                source = self._files._resolve_source_path_for_action(path)
                if not source:
                    raise ValueError(f"无法解析照片原文件：{path}")
                source = os.path.abspath(source)
                sources.append(source)
                displays[source] = path
                report = self._files.get_report_row_for_path(path)
                scopes[source] = self._files._report_scope_path_key(report)
                if report:
                    reports[source] = dict(report)
        except Exception as exc:
            QMessageBox.warning(self._main, "珍禽入册", str(exc))
            return False
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
        self._display_paths, self._scope_keys = displays, scopes
        self._worker = worker = ArchiveWorker(tuple(sources), options, reports)
        self._dialog = ArchiveProgress(self._main)
        self._dialog.stop_requested.connect(worker.requestInterruption)
        worker.result_ready.connect(lambda result, w=worker: self._on_result(w, result))
        worker.progress.connect(lambda done, total, w=worker: self._on_progress(w, done, total))
        worker.finished.connect(lambda w=worker: self._on_finished(w))
        self._dialog.show()
        worker.start()
        return True

    def _on_progress(self, worker, done, total):
        if worker is self._worker and not self._shutdown_requested:
            self._dialog.bar.setRange(0, total)
            self._dialog.bar.setValue(done)

    def _on_result(self, worker, result):
        if worker is not self._worker or self._shutdown_requested:
            return
        if result.status == "success" and worker.options.mode == "move":
            self.photos_moved.emit(result.sources)
        label = {"success": "已入册", "skipped": "跳过", "failed": "失败"}[result.status]
        detail = f"{label}：{', '.join(result.sources)}\n" + ("\n".join(result.destinations) or result.message)
        self._dialog.details.append("")
        self._dialog.details.insertPlainText(detail)
        _log.info("[Archive] %s", detail)

    def _on_finished(self, worker):
        if worker is not self._worker:
            return
        self._worker = None
        if self._shutdown_requested:
            self._dialog.finish("已停止")
            self._dialog.close()
        else:
            counts = Counter()
            moved, touched = [], []
            for result in worker.results:
                counts[result.status] += len(result.sources)
                if result.status == "success":
                    touched.extend((*result.sources, *result.destinations))
                    if worker.options.mode == "move":
                        moved.extend(result.sources)
            if moved:
                self._files._delete_report_rows_for_paths(
                    [self._display_paths[p] for p in moved], resolved_paths=moved,
                    scope_keys=[self._scope_keys[p] for p in moved])
            directory = self._files.get_current_dir()
            if directory and any(Path(p).is_relative_to(Path(directory)) for p in touched):
                if moved:
                    self._files.clear_tag_history()
                self._files.load_directory(directory, force_reload=True)
            title = "已停止入册" if worker.isInterruptionRequested() else "编册完成"
            text = f"{title}：入册 {counts['success']} 张，跳过 {counts['skipped']} 张，失败 {counts['failed']} 张。"
            if worker.error:
                text = "入册未完成：" + worker.error
            self._dialog.finish(text)
        worker.deleteLater()

    def request_shutdown(self):
        self._shutdown_requested = True
        if self._worker is not None:
            self._worker.requestInterruption()

    def is_shutdown_done(self):
        return self._worker is None
