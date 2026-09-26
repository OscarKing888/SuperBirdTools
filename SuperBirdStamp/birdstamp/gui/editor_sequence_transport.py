"""成片播放与方向键节拍；只调度缓存画面，不在 GUI 线程读取照片。"""
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import editor_options
from .editor_utils import path_key


class SequenceTransport(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.paths = []
        self.mode = None
        self.direction = 1
        self.key = None
        self.selecting = False
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.panel = QWidget()
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.play = QPushButton('播放序列')
        self.play.clicked.connect(self.toggle)
        self.previous = QPushButton('上一张')
        self.previous.clicked.connect(lambda: self.step(-1))
        self.next = QPushButton('下一张')
        self.next.clicked.connect(lambda: self.step(1))
        self.position = QLabel('0 / 0')
        self.fps = QSpinBox()
        self.fps.setRange(1, 30)
        self.fps.setValue(editor_options.DEJITTER_PLAYBACK_FPS)
        self.fps.setSuffix(' 帧/秒')
        self.fps.setToolTip('播放和长按方向键的预览速度')
        self.fps.valueChanged.connect(lambda value: self.timer.setInterval(round(1000 / value)))
        self.timer.setInterval(round(1000 / self.fps.value()))
        self.loop = QCheckBox('循环')
        self.loop.setChecked(True)
        for widget in (self.play, self.previous, self.next, self.position):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(self.fps)
        row.addWidget(self.loop)
        layout.addLayout(row)
        self.strip = QListWidget()
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setMovement(QListWidget.Movement.Static)
        self.strip.setIconSize(QSize(84, 56))
        self.strip.setGridSize(QSize(104, 84))
        self.strip.setFixedHeight(106)
        self.strip.setHorizontalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.strip.currentRowChanged.connect(self._seek)
        layout.addWidget(self.strip)
        self.panel.hide()
        for surface in (editor, editor.photo_list._tree_widget,
                        editor.photo_list._tree_widget.viewport(), editor.preview_label.canvas,
                        self.strip, self.strip.viewport()):
            surface.installEventFilter(self)

    @property
    def active(self):
        return self.mode is not None

    def set_frames(self, sequence, frames):
        self.stop(commit=False)
        self.paths = [job.path for job in sequence.jobs.values()] if sequence else []
        self.strip.blockSignals(True)
        self.strip.clear()
        reference = self.editor._dejitter_reference_source
        for index, path in enumerate(self.paths):
            key = path_key(path)
            result = sequence.tracking[key]
            reference_frame = reference and key == path_key(Path(reference))
            partial = result.matched_count < len(result.boxes)
            label = f'{index + 1}' + (' · 参考' if reference_frame else '') + (' · 待检查' if partial else '')
            item = QListWidgetItem(label)
            frame = frames.get(key)
            if frame:
                item.setIcon(QIcon(QPixmap.fromImage(frame.image).scaled(
                    frame.image.size().boundedTo(self.strip.iconSize()), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            item.setToolTip(path.name + ('：部分选区失配，请检查对齐结果' if partial else ''))
            self.strip.addItem(item)
        self.strip.blockSignals(False)
        self.sync()

    def index(self):
        current = self.editor.current_path
        return next((i for i, path in enumerate(self.paths) if path == current), -1)

    def sync(self):
        index = self.index()
        self.strip.blockSignals(True)
        self.strip.setCurrentRow(index)
        if index >= 0:
            self.strip.scrollToItem(self.strip.item(index))
        self.strip.blockSignals(False)
        self.position.setText(f'{index + 1} / {len(self.paths)}')
        sequence = self.editor._sequence_preview
        if sequence is not None and 0 <= index < len(self.paths):
            result = sequence.tracking.get(path_key(self.paths[index]))
            if result:
                detail = f'当前第 {index + 1} 张：{result.matched_count}/{len(result.boxes)} 个选区匹配'
                if result.matched_count < len(result.boxes):
                    detail += '，请检查成片'
                self.editor.dejitter_tracking_status.setText(self.editor._sequence_message + '\n' + detail)
        ready = bool(self.paths) and not self.editor._sequence_exporting
        self.play.setEnabled(ready and len(self.paths) > 1)
        self.previous.setEnabled(ready and index > 0)
        self.next.setEnabled(ready and index < len(self.paths) - 1)
        self.strip.setEnabled(ready)
        self.panel.setVisible(self.editor._sequence_result_mode())

    def _select(self, index):
        if not 0 <= index < len(self.paths):
            return
        self.selecting = True
        try:
            path = self.paths[index]
            item = self.editor._find_photo_item_by_path(path)
            if item is not None:
                self.editor.photo_list.setCurrentItem(item)
                self.editor.photo_list._tree_widget.scrollToItem(item)
            else:
                self.editor.current_path = path
                self.editor._refresh_preview_label(preserve_view=True)
            self.sync()
        finally:
            self.selecting = False

    def _seek(self, index):
        self.stop(commit=False)
        self._select(index)
        self.editor._refresh_preview_label(preserve_view=True)

    def step(self, direction):
        self.stop(commit=False)
        self._select(max(0, min(len(self.paths) - 1, self.index() + direction)))

    def toggle(self):
        if self.active:
            self.stop()
            return
        if len(self.paths) < 2 or not self.editor._validate_sequence_preview():
            return
        if self.index() == len(self.paths) - 1:
            self._select(0)
        self.start('play', 1)

    def start(self, mode, direction):
        self.mode, self.direction = mode, direction
        self.editor._sequence_upgrade_timer.stop()
        worker = self.editor._sequence_worker
        # 分析/导出有自己的生命周期；这里只取消上一张的清晰预览任务。
        if worker is not None and self.editor._sequence_preview is not None and not self.editor._sequence_exporting:
            worker.cancel()
        self.editor._sequence_pending_path = None
        self.play.setText('暂停' if mode == 'play' else '播放序列')
        self.timer.start()

    def stop(self, *, commit=True):
        was_active = self.active
        self.timer.stop()
        self.mode, self.key = None, None
        self.play.setText('播放序列')
        if was_active and commit and not self.editor._sequence_shutdown:
            item = self.editor.photo_list.currentItem()
            if item is not None:
                self.editor._on_photo_selected(item, None)
            else:
                self.editor._refresh_preview_label(preserve_view=True)

    def _tick(self):
        index = self.index() + self.direction
        if not 0 <= index < len(self.paths):
            if self.mode == 'play' and self.loop.isChecked() and self.paths:
                index %= len(self.paths)
            else:
                if self.mode == 'keys':
                    self.timer.stop()  # 到边界仍等物理松键，自动重复事件不能反复提交。
                else:
                    self.stop()
                return
        self._select(index)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.WindowDeactivate, QEvent.Type.FocusOut, QEvent.Type.Hide):
            if self.active:
                self.stop()
            return False
        if not self.editor._dejitter_tab_active() or not self.paths or self.editor._sequence_exporting:
            return False
        if kind not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        key = event.key()
        directions = {Qt.Key.Key_Left: -1, Qt.Key.Key_Up: -1,
                      Qt.Key.Key_Right: 1, Qt.Key.Key_Down: 1}
        if key not in directions:
            return False
        if kind == QEvent.Type.KeyRelease:
            if event.isAutoRepeat():
                return True
            if self.key == key:
                self.stop()
            return True
        if event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        if event.isAutoRepeat():
            if self.mode != 'keys' or self.key != key:
                self.start('keys', directions[key])
                self.key = key
                self._tick()
            return True
        self.step(directions[key])
        return True
