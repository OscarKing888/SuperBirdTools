"""原图与成片播放；只调度缓存画面，不在 GUI 线程解码照片。"""
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from . import editor_options
from .editor_fps_combo import FpsComboBox
from .editor_utils import path_key
from .editor_photo_list import PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE
from .editor_source_quick_loader import SourceQuickLoader
from .editor_shared_thumb_cache import SharedThumbnailScope
from .editor_sequence_preview_worker import pil_qimage
from .editor_media_icons import media_icon


_SOURCE_QUICK_CACHE_BYTES = 64 * 1024 * 1024


class SequenceTransport(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.paths = []
        self.mode = None
        self.direction = 1
        self.key = None
        self._ordinary_first_step = False
        self.selecting = False
        self._strip_kind = None
        self._strip_paths = ()
        self._source_strip_items = {}
        self._source_cache = OrderedDict()
        self._source_loader = None
        self.shared_scope = SharedThumbnailScope()
        self._source_shutdown = False
        self._source_entries = ()
        self._source_signatures = set()
        self._source_ready = set()
        self._source_failed = {}
        self._result_frames = {}
        self._play_visual_state = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.panel = QWidget()
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.play = QToolButton()
        self.play.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.play.setIconSize(QSize(20, 20))
        self.play.setMinimumSize(36, 32)
        self._update_play_button()
        self.play.clicked.connect(self.toggle)
        self.previous = QToolButton()
        self.previous.clicked.connect(lambda: self.step(-1))
        self.next = QToolButton()
        self.next.clicked.connect(lambda: self.step(1))
        for button, label in ((self.previous, '上一张'), (self.next, '下一张')):
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(self.play.iconSize())
            button.setMinimumSize(self.play.minimumSize())
            button.setToolTip(label)
            button.setAccessibleName(label)
        self.refresh_media_icons()
        self.position = QLabel('0 / 0')
        self.preparation = QLabel('')
        self.fps = FpsComboBox(1, 30, editor_options.DEJITTER_PLAYBACK_FPS)
        self.fps.setToolTip('播放和长按方向键的预览速度')
        self.fps_unit = QLabel('帧/秒')
        self.fps.valueChanged.connect(lambda value: self.timer.setInterval(round(1000 / value)))
        self.timer.setInterval(round(1000 / self.fps.value()))
        self.auto_fps_button = QPushButton('自动')
        self.auto_fps_button.setToolTip('根据当前照片列表的拍摄时间自动计算回放 FPS。')
        self.auto_fps_button.clicked.connect(editor._on_dejitter_auto_fps_requested)
        self.loop = QCheckBox('循环')
        self.loop.setChecked(True)
        for widget in (self.play, self.previous, self.next, self.position, self.preparation):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(self.fps)
        row.addWidget(self.fps_unit)
        row.addWidget(self.auto_fps_button)
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
        # 画布点击后接收方向键，不能继续把按键交给此前有焦点的照片列表或输入框。
        canvas = editor.preview_label.canvas
        self._ordinary_canvas_focus_policy = canvas.focusPolicy()
        self.panel.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        editor.preview_label.setFocusProxy(canvas)
        for button in (self.play, self.previous, self.next):
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._navigation_surfaces = (editor.photo_list._tree_widget,
                                     editor.photo_list._tree_widget.viewport(), canvas, editor.ab_preview.preview.canvas,
                                     self.strip, self.strip.viewport())
        self._result_surfaces = (self.panel, editor.preview_label, editor.dejitter_view_tabs,
                                 self.play, self.previous, self.next, self.loop)
        # 窗口只监听失活；避免子控件未处理的按键冒泡后误触发 B 图导航（尤其 A 图）。
        for surface in (editor, *self._navigation_surfaces, *self._result_surfaces):
            surface.installEventFilter(self)
        self._source_scan_timer = QTimer(self)
        self._source_scan_timer.setSingleShot(True)
        self._source_scan_timer.timeout.connect(self._scan_source_list)
        model = editor.photo_list._tree_widget.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset,
                       model.layoutChanged, model.dataChanged):
            signal.connect(lambda *args: self._source_scan_timer.start(100))
        self._source_scan_timer.start(0)

    @property
    def active(self):
        return self.mode is not None

    def set_frames(self, sequence, frames):
        self.stop(commit=False)
        self.paths = [job.path for job in sequence.jobs.values()] if sequence else []
        self._result_frames = dict(frames)
        self._strip_kind = None
        self.sync()

    def _rebuild_result_strip(self, sequence):
        self._strip_kind = 'result'
        self._strip_paths = tuple(self.paths)
        self._source_strip_items = {}
        self.strip.blockSignals(True)
        self.strip.clear()
        has_fallback = sequence and any(a.status == 'fallback' for a in sequence.alignments.values())
        self.strip.setGridSize(QSize(156 if has_fallback else 104, 84))
        reference = self.editor._dejitter_reference_source
        for index, path in enumerate(self.paths):
            key = path_key(path)
            result = sequence.tracking[key]
            reference_frame = reference and key == path_key(Path(reference))
            partial = result.matched_count < len(result.boxes)
            label = f'{index + 1}' + (' · 参考' if reference_frame else '') + (' · 待检查' if partial else '')
            item = QListWidgetItem(label)
            alignment = sequence.alignments.get(key)
            if alignment and alignment.status == 'fallback':
                item.setText(f'{index + 1} · 未纠正旋转')
            frame = self._result_frames.get(key)
            if frame:
                item.setIcon(QIcon(QPixmap.fromImage(frame.image).scaled(
                    frame.image.size().boundedTo(self.strip.iconSize()), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            item.setToolTip(path.name + ('：部分选区失配，请检查对齐结果' if partial else ''))
            self.strip.addItem(item)
            if alignment:
                item.setToolTip(item.toolTip() + '\n' + alignment.description())
        self.strip.blockSignals(False)

    def _active_paths(self):
        if self.result_mode():
            return self.paths
        paths = self.editor._list_photo_paths()
        if self.mode == 'source_play':
            return [path for path in paths if self.editor._source_signature(path) not in self._source_failed]
        return paths

    def _scan_source_list(self):
        if self._source_shutdown:
            return
        paths = self.editor._list_photo_paths()
        entries = tuple((self.editor._source_signature(path), path) for path in paths)
        if entries == self._source_entries:
            return
        if self.mode in ('source_play', 'source_keys'):
            self.stop(commit=False)
        self._source_entries = entries
        self._source_signatures = {signature for signature, _ in entries}
        self._source_ready.clear()
        self._source_failed.clear()
        for signature in list(self._source_cache):
            if signature not in self._source_signatures:
                image, _ = self._source_cache.pop(signature)
                image.close()
        if self._source_loader is None and entries:
            loader = SourceQuickLoader(self.editor._preview_action_pool, self)
            loader.ready.connect(self._on_source_ready)
            loader.failed.connect(self._on_source_failed)
            self._source_loader = loader
            loader.reset(entries)
            loader.start()
        elif self._source_loader is not None:
            self._source_loader.reset(entries)
        self._update_source_preparation()

    def _update_source_preparation(self):
        total = len(self._source_signatures)
        done = len(self._source_ready) + len(self._source_failed)
        skipped = len(self._source_failed)
        self.preparation.setText(
            f'预览 {done}/{total}' + (f' · 跳过 {skipped}' if skipped else '') if total else '')

    def _source_entry(self, path):
        signature = self.editor._source_signature(Path(path))
        entry = self._source_cache.get(signature)
        if entry is None:
            return None
        self._source_cache.move_to_end(signature)
        image, full_size = entry
        return image.copy(), full_size

    def source_preview(self, path):
        return self._source_entry(path) if path is not None else None

    def _request_source_frames(self):
        if self._source_shutdown:
            return
        paths = self._active_paths()
        if not paths:
            return
        index = self.index()
        if index < 0:
            index = 0
        near = [paths[(index + offset) % len(paths)] for offset in range(min(8, len(paths)))]
        entries = [(self.editor._source_signature(path), path) for path in near]
        entries = [(signature, path) for signature, path in entries
                   if signature not in self._source_cache and signature not in self._source_failed]
        if not entries:
            return
        if self._source_loader is not None:
            self._source_loader.enqueue(entries)

    def _source_icon(self, image):
        pixmap = QPixmap.fromImage(pil_qimage(image))
        return QIcon(pixmap.scaled(self.strip.iconSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))

    def _on_source_ready(self, signature, path_text, image, full_size):
        if self._source_loader is not None:
            self._source_loader.release_ready()
        if (self._source_shutdown or signature != self.editor._source_signature(Path(path_text))
                or signature not in self._source_signatures):
            image.close()
            return
        self._source_ready.add(signature)
        self._source_failed.pop(signature, None)
        old = self._source_cache.pop(signature, None)
        if old is not None:
            old[0].close()
        self._source_cache[signature] = (image, tuple(full_size))
        total = sum(frame.width * frame.height * 4 for frame, _ in self._source_cache.values())
        while len(self._source_cache) > 1 and total > _SOURCE_QUICK_CACHE_BYTES:
            old_signature, (evicted, _) = self._source_cache.popitem(last=False)
            total -= evicted.width * evicted.height * 4
            evicted.close()
            old_item = self._source_strip_items.get(old_signature)
            if old_item is not None and path_key(old_item[1]) not in self.editor._sequence_quick_frames:
                old_item[0].setIcon(QIcon())
        if self._strip_kind == 'source':
            item = self._source_strip_items.get(signature)
            if item is not None:
                item[0].setIcon(self._source_icon(image))
        selected = self.editor.ab_preview.selected_path()
        if (self.mode in ('source_play', 'source_keys') and selected is not None
                and path_key(selected) == path_key(Path(path_text))):
            ab = self.editor.ab_preview
            if ab.enabled.isChecked() and ab.active_side == 'a':
                ab.sync(force=True)
            elif self.editor._dejitter_tab_active() or ab.enabled.isChecked():
                self.editor._refresh_preview_label(preserve_view=True)
            elif self.editor.current_source_image is None or not getattr(self.editor, '_preview_is_quick', False):
                self.editor._on_quick_preview_ready(
                    self.editor._preview_decode_token, path_text, image.copy(), tuple(full_size))
        self._update_source_preparation()

    def _on_source_failed(self, signature, path_text, message):
        if (self._source_shutdown or signature != self.editor._source_signature(Path(path_text))
                or signature not in self._source_signatures):
            return
        self._source_failed[signature] = message
        self._update_source_preparation()

    def _rebuild_source_strip(self, paths):
        self._strip_kind = 'source'
        self._strip_paths = tuple(paths)
        self._source_strip_items = {}
        self.strip.blockSignals(True)
        self.strip.clear()
        self.strip.setGridSize(QSize(104, 84))
        for index, path in enumerate(paths):
            item = QListWidgetItem(str(index + 1))
            item.setToolTip(path.name)
            signature = self.editor._source_signature(path)
            self._source_strip_items[signature] = (item, path)
            frame = self.editor._sequence_quick_frames.get(path_key(path))
            if frame is not None:
                item.setIcon(QIcon(QPixmap.fromImage(frame.source_image).scaled(
                    self.strip.iconSize(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            else:
                entry = self._source_entry(path)
                if entry is not None:
                    image, _ = entry
                    item.setIcon(self._source_icon(image))
                    image.close()
            self.strip.addItem(item)
        self.strip.blockSignals(False)

    def shutdown(self):
        self._source_shutdown = True
        self._source_scan_timer.stop()
        self.stop(commit=False)
        if self._source_loader is not None:
            self._source_loader.stop()
            if self._source_loader.isRunning():
                return False
        for image, _ in self._source_cache.values():
            image.close()
        self._source_cache.clear()
        return True

    def index(self):
        current = self.editor.ab_preview.selected_path()
        return next((i for i, path in enumerate(self._active_paths()) if path == current), -1)

    def sync(self):
        if self._source_shutdown:
            return
        self.editor.preview_label.canvas.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus if self.editor._dejitter_tab_active() else self._ordinary_canvas_focus_policy)
        source_mode = not self.result_mode()
        paths = self._active_paths()
        if source_mode and (self._strip_kind != 'source' or self._strip_paths != tuple(paths)):
            self._rebuild_source_strip(paths)
        elif not source_mode and (self._strip_kind != 'result' or self._strip_paths != tuple(paths)):
            self._rebuild_result_strip(self.editor._sequence_preview)
        index = self.index()
        self.strip.blockSignals(True)
        self.strip.setCurrentRow(index)
        if index >= 0:
            self.strip.scrollToItem(self.strip.item(index))
        self.strip.blockSignals(False)
        self.position.setText(f'{index + 1} / {len(paths)}')
        sequence = self.editor._sequence_preview
        if not source_mode and sequence is not None and 0 <= index < len(paths):
            result = sequence.tracking.get(path_key(paths[index]))
            if result:
                detail = f'当前第 {index + 1} 张：{result.matched_count}/{len(result.boxes)} 个选区匹配'
                key = path_key(paths[index])
                width, height = sequence.source_sizes[key]
                output_width, output_height = sequence.output_size
                padded = sequence.jobs[key].settings.get('dejitter_pad_to_union', False) is True
                if padded:
                    detail += ' · 补边保留完整画面'
                else:
                    retained = output_width*output_height/(width*height)*100
                    detail += f' · 保留原图 {retained:.1f}%'
                    if retained < 50:
                        detail += '，共同范围较小，可开启补边后再裁切'
                if result.matched_count < len(result.boxes):
                    detail += '，请检查成片'
                alignment = sequence.alignments.get(key)
                if alignment:
                    detail += '\n' + alignment.description()
                self.editor.dejitter_tracking_status.setText(self.editor._sequence_message + '\n' + detail)
                self.editor.dejitter_tracking_status.setToolTip(result.error)
        ready = bool(paths) and not self.editor._sequence_exporting
        self.play.setEnabled(ready and len(paths) > 1)
        self.auto_fps_button.setEnabled(ready and len(paths) > 1)
        self.previous.setEnabled(ready and index > 0)
        self.next.setEnabled(ready and index < len(paths) - 1)
        self.strip.setEnabled(ready)
        self.panel.setVisible(ready)
        ab = self.editor.ab_preview
        source_ready = len(self.editor._list_photo_paths()) > 1 and not self.editor._sequence_exporting
        result_ready = len(self.paths) > 1 and not self.editor._sequence_exporting
        ab.a_panel.play.setEnabled(ab.enabled.isChecked() and
                                   (result_ready if ab.mode.currentIndex() == 1 else source_ready))
        ab.b_panel.play.setEnabled(result_ready if self.editor._sequence_result_mode() else source_ready)
        self._update_play_button()

    def result_mode(self):
        ab = self.editor.ab_preview
        return (ab.mode.currentIndex() == 1 if ab.enabled.isChecked() and ab.active_side == 'a'
                else self.editor._sequence_result_mode())

    def _select(self, index):
        paths = self._active_paths()
        if not 0 <= index < len(paths):
            return
        self.selecting = True
        try:
            path = paths[index]
            item = self.editor._find_photo_item_by_path(path)
            if item is not None:
                if item is self.editor.photo_list.currentItem():
                    self.editor.ab_preview.route_photo_selection(path)
                else:
                    self.editor.photo_list.setCurrentItem(item)
                self.editor.photo_list._tree_widget.scrollToItem(item)
            else:
                if not self.editor.ab_preview.route_photo_selection(path):
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
        paths = self._active_paths()
        self._select(max(0, min(len(paths) - 1, self.index() + direction)))

    def toggle(self):
        if self.active:
            self.stop()
            return
        self._scan_source_list()
        paths = self._active_paths()
        if len(paths) < 2 or (self.result_mode() and not self.editor._validate_sequence_preview()):
            return
        if self.index() == len(paths) - 1:
            self._select(0)
        if self.result_mode():
            self.start('play', 1)
        else:
            seen_directories = set()
            for path in paths:
                if path.parent not in seen_directories:
                    seen_directories.add(path.parent)
                    self.shared_scope.ensure(path, self.editor)
            self.start('source_play', 1)

    def start(self, mode, direction):
        if mode in ('source_play', 'source_keys'):
            self._scan_source_list()
        self.mode, self.direction = mode, direction
        ab = self.editor.ab_preview
        if mode in ('source_play', 'source_keys'):
            if ab.enabled.isChecked() and ab.active_side == 'a':
                ab._cancel()
            else:
                self.editor._cancel_preview_decode()
            self._request_source_frames()
            self._update_play_button()
            if ab.enabled.isChecked() and ab.active_side == 'a':
                ab.sync(force=True)
            elif mode == 'source_play':
                selected = ab.selected_path()
                entry = self.source_preview(selected) if selected is not None else None
                if entry is not None:
                    self.editor._on_quick_preview_ready(
                        self.editor._preview_decode_token, str(selected), *entry)
            self.sync()
            self.timer.start()
            return
        self.editor._sequence_upgrade_timer.stop()
        worker = self.editor._sequence_worker
        # 分析/导出有自己的生命周期；这里只取消上一张的清晰预览任务。
        if worker is not None and self.editor._sequence_preview is not None and not self.editor._sequence_exporting:
            worker.cancel()
        self.editor._sequence_pending_path = None
        self._update_play_button()
        if ab.enabled.isChecked() and ab.active_side == 'a':
            ab.sync(force=True)
        elif self.editor._sequence_result_mode():
            self.editor._show_sequence_preview_result(preserve_view=True)
        self.timer.start()

    def refresh_media_icons(self, color: QColor | None = None):
        color = color or self.editor.palette().color(QPalette.ColorRole.ButtonText)
        self._media_icon_color = QColor(color)
        self.previous.setIcon(media_icon(self.previous, QStyle.StandardPixmap.SP_MediaSkipBackward, color))
        self.next.setIcon(media_icon(self.next, QStyle.StandardPixmap.SP_MediaSkipForward, color))
        self._update_play_button(force=True, color=color)

    def _update_play_button(self, *, force=False, color: QColor | None = None):
        playing = self.mode in ('play', 'source_play')
        ab = self.editor.ab_preview
        state = (playing, ab.active_side)
        if state == self._play_visual_state and not force:
            return
        self._play_visual_state = state
        color = color or getattr(self, '_media_icon_color', None) or self.editor.palette().color(QPalette.ColorRole.ButtonText)
        label = '暂停' if playing else '播放序列'
        icon = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self.play.setIcon(media_icon(self.play, icon, color))
        self.play.setToolTip(label)
        self.play.setAccessibleName(label)
        for side, panel in (('a', ab.a_panel), ('b', ab.b_panel)):
            side_playing = playing and ab.active_side == side
            side_label = f'暂停 {side.upper()} 侧播放' if side_playing else f'播放 {side.upper()} 侧照片序列'
            side_icon = QStyle.StandardPixmap.SP_MediaPause if side_playing else QStyle.StandardPixmap.SP_MediaPlay
            panel.play.setIcon(media_icon(panel.play, side_icon, color))
            panel.play.setToolTip(side_label)
            panel.play.setAccessibleName(side_label)

    def stop(self, *, commit=True):
        was_active = self.active
        ordinary_keys = self.mode == 'ordinary_keys'
        self.timer.stop()
        self.mode, self.key = None, None
        self._ordinary_first_step = False
        self._update_play_button()
        if was_active and commit and not self.editor._sequence_shutdown:
            item = self.editor.photo_list.currentItem()
            if item is not None:
                self.editor._on_photo_selected(item, None)
            elif not ordinary_keys:
                self.editor._refresh_preview_label(preserve_view=True)
            ab = self.editor.ab_preview
            if ab.enabled.isChecked() and ab.active_side == 'a':
                ab.sync(force=True)

    def _tick(self):
        if self.mode == 'ordinary_keys':
            tree = self.editor.photo_list._tree_widget
            current = tree.indexOfTopLevelItem(tree.currentItem())
            target = current + self.direction
            if 0 <= target < tree.topLevelItemCount():
                item = tree.topLevelItem(target)
                raw = item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
                path = Path(raw) if isinstance(raw, str) else None
                if path is not None and self.editor._source_signature(path) in self._source_cache:
                    self._select(target)
                else:
                    self._request_source_frames()
            else:
                self.timer.stop()
            return
        paths = self._active_paths()
        index = self.index() + self.direction
        if not 0 <= index < len(paths):
            if self.mode in ('play', 'source_play') and self.loop.isChecked() and paths:
                index %= len(paths)
            else:
                if self.mode in ('keys', 'source_keys'):
                    self.timer.stop()  # 到边界仍等物理松键，自动重复事件不能反复提交。
                else:
                    self.stop()
                return
        if self.mode == 'source_play':
            target = paths[index]
            if self.editor._source_signature(target) not in self._source_signatures:
                self._scan_source_list()
                return
            if (self.editor._source_signature(target) not in self._source_cache
                    and path_key(target) not in self.editor._sequence_quick_frames):
                self._request_source_frames()
                return
        self._select(index)
        if self.mode in ('source_play', 'source_keys'):
            self._request_source_frames()

    def eventFilter(self, watched, event):
        if self._source_shutdown:
            return False
        kind = event.type()
        if kind in (QEvent.Type.WindowDeactivate, QEvent.Type.FocusOut, QEvent.Type.Hide):
            if self.active:
                self.stop()
            return False
        if (not self.editor._dejitter_tab_active()
                and kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)):
            tree = self.editor.photo_list._tree_widget
            try:
                tree_surface = watched is tree or watched is tree.viewport()
                strip_surface = watched is self.strip or watched is self.strip.viewport()
            except RuntimeError:
                return False
            if not tree_surface and not strip_surface:
                return False
            directions = {Qt.Key.Key_Left: -1, Qt.Key.Key_Up: -1,
                          Qt.Key.Key_Right: 1, Qt.Key.Key_Down: 1}
            key = event.key()
            if key not in directions:
                return False
            if kind == QEvent.Type.KeyRelease:
                if event.isAutoRepeat():
                    return self.mode == 'ordinary_keys'
                if self.mode == 'ordinary_keys' and self.key == key:
                    self.stop()
                    return True
                return False
            if event.modifiers() != Qt.KeyboardModifier.NoModifier:
                if self.mode == 'ordinary_keys':
                    self.stop(commit=False)
                return False
            if not event.isAutoRepeat():
                self.stop(commit=False)
                self.mode = 'ordinary_keys'
                self.key = key
                self.direction = directions[key]
                self._ordinary_first_step = True
                # 树列表上下键保留原生选择；左右键及播放列表统一按照片顺序切图。
                # 避免依赖树节点展开或图标列表的空间布局来决定前后照片。
                if tree_surface and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                    return False
                paths = self._active_paths()
                self._select(max(0, min(len(paths) - 1, self.index() + self.direction)))
                return True
            if self.mode != 'ordinary_keys' or self.key != key:
                self.stop(commit=False)
                self.mode = 'ordinary_keys'
                self.key = key
                self.direction = directions[key]
            self._ordinary_first_step = False
            if not self.timer.isActive():
                self._tick()
                self.timer.start()
            return True
        if not self.editor._dejitter_tab_active() or not self._active_paths() or self.editor._sequence_exporting:
            return False
        if kind not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if watched not in self._navigation_surfaces:
            if watched not in self._result_surfaces or not self.result_mode():
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
            key_mode = 'keys' if self.result_mode() else 'source_keys'
            if self.mode != key_mode or self.key != key:
                self.start(key_mode, directions[key])
                self.key = key
                self._tick()
            return True
        self.step(directions[key])
        return True
