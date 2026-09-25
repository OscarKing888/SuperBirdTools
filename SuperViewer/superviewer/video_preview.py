# -*- coding: utf-8 -*-
"""Video view and owned probe worker; photo policy stays in PreviewPanel."""
from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime

from app_common.video import format_duration, is_video, probe_video, video_thumbnail_rgb
from app_common.log import get_logger
from .preview_panel import PreviewPanel, _qimage_rgb888_format
from .qt_compat import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QComboBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QThread, QImage, QPixmap,
    pyqtSignal, Qt, _Horizontal, _AlignCenter, _KeepAspectRatio, _SmoothTransformation,
    _NoEditTriggers,
)

_log = get_logger('superviewer.video')


class VideoInfoPanel(QWidget):
    """Read-only technical information; never routes through photo EXIF/focus."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ''
        layout = QVBoxLayout(self)
        title = QLabel('视频信息')
        title.setStyleSheet('font-size: 16px; font-weight: bold; padding: 8px 0;')
        layout.addWidget(title)
        self.status = QLabel('选择视频以查看信息')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['属性', '内容'])
        self.table.setEditTriggers(_NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 90)
        layout.addWidget(self.table)

    def set_path(self, path):
        self.path = os.path.normpath(path) if path else ''
        self.status.setText('正在读取视频信息…' if path else '选择视频以查看信息')
        self._set_rows([('文件名', Path(path).name), ('位置', str(path))] if path else [])

    def update_info(self, path, info, error):
        if os.path.normpath(path) != self.path:
            return
        def value(key):
            return info.get(key) or '—'
        width, height = info.get('width'), info.get('height')
        rows = [('文件名', Path(path).name), ('格式', value('container')),
                ('时长', format_duration(info.get('duration'))),
                ('分辨率', f'{width} × {height}' if width and height else '—'),
                ('帧率', f"{info['fps']:g} fps" if info.get('fps') else '—'),
                ('视频编码', value('video_codec')),
                ('总码率', f"{info['bit_rate'] / 1_000_000:.2f} Mbps" if info.get('bit_rate') else '—'),
                ('音轨', value('audio')), ('音轨数量', str(info.get('audio_tracks', '—'))),
                ('文件大小', f"{info['size'] / 1048576:.2f} MB" if 'size' in info else '—'),
                ('修改时间', datetime.fromtimestamp(info['modified']).strftime('%Y-%m-%d %H:%M:%S') if info.get('modified') else '—'),
                ('位置', path)]
        self._set_rows(rows)
        self.status.setText(error or '原始视频 · 只读信息')

    def _set_rows(self, rows):
        self.table.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            for col, text in enumerate((label, str(value))):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()


class _VideoProbe(QThread):
    result = pyqtSignal(int, str, object, object, str)

    def __init__(self, token, path, need_poster, parent):
        super().__init__(parent)
        self.token, self.path, self.need_poster = token, path, need_poster

    def run(self):
        info, image, errors = {}, None, []
        try:
            info = probe_video(self.path, cancelled=self.isInterruptionRequested)
        except Exception as exc:
            errors.append(str(exc))
        if self.need_poster and not self.isInterruptionRequested():
            try:
                data, w, h = video_thumbnail_rgb(self.path, 1024, cancelled=self.isInterruptionRequested)
                image = QImage(data, w, h, w * 3, _qimage_rgb888_format()).copy()
            except Exception as exc:
                errors.append(str(exc))
        if not self.isInterruptionRequested():
            self.result.emit(self.token, self.path, info, image, '\n'.join(errors))


class VideoPlayerView(QWidget):
    """Paused poster until explicit play. Qt owns A/V decoding and synchronization."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ''
        self.player = None
        self.audio = None
        self._poster = None
        self._duration = 0
        self._position = 0
        self._source_set = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.poster = QLabel('正在加载视频封面…')
        self.poster.setAlignment(_AlignCenter)
        self.poster.setMinimumSize(1, 1)
        self.poster.setStyleSheet('background: #15171a; color: #ddd;')
        self.stack.addWidget(self.poster)
        layout.addWidget(self.stack, 1)
        self.message = QLabel('点击播放视频')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        timeline = QHBoxLayout()
        self.seek = QSlider(_Horizontal)
        self.seek.setRange(0, 10000)
        self.seek.setEnabled(False)
        self.seek.setToolTip('拖动定位视频')
        self.seek.sliderReleased.connect(self._seek)
        self.time_label = QLabel('00:00 / —')
        self.time_label.setMinimumWidth(125)
        timeline.addWidget(self.seek, 1)
        timeline.addWidget(self.time_label)
        layout.addLayout(timeline)
        controls = QHBoxLayout()
        self.play = QPushButton('▶ 播放')
        self.play.clicked.connect(self.toggle_play)
        controls.addWidget(self.play)
        self.restart = QPushButton('↺ 重播')
        self.restart.clicked.connect(self._restart)
        controls.addWidget(self.restart)
        controls.addStretch(1)
        self.speed = QComboBox()
        for speed in (0.25, 0.5, 1, 1.5, 2):
            self.speed.addItem(f'{speed:g}×', speed)
        self.speed.setCurrentIndex(2)
        self.speed.setToolTip('播放速度')
        self.speed.currentIndexChanged.connect(self._set_speed)
        controls.addWidget(self.speed)
        self.mute = QPushButton('静音')
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self._set_audio)
        controls.addWidget(self.mute)
        self.volume = QSlider(_Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setMaximumWidth(90)
        self.volume.setToolTip('音量')
        self.volume.valueChanged.connect(self._set_audio)
        controls.addWidget(self.volume)
        layout.addLayout(controls)

    def set_path(self, path):
        self.stop()
        self.path = path
        self._poster = None
        self.poster.clear()
        self.poster.setText('正在加载视频封面…')
        self.message.setText('点击播放视频')
        self.play.setEnabled(True)
        self.restart.setEnabled(True)
        self._duration = self._position = 0
        self.seek.setValue(0)
        self._update_time()

    def set_poster(self, pixmap):
        self._poster = pixmap
        self._resize_poster()

    def _resize_poster(self):
        if self._poster is not None and not self._poster.isNull():
            self.poster.setPixmap(self._poster.scaled(self.poster.size(), _KeepAspectRatio, _SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_poster()

    def _ensure_player(self):
        if self.player is not None:
            return True
        try:
            from PyQt6.QtCore import QUrl, QMetaObject, Q_ARG
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtMultimediaWidgets import QVideoWidget
            self._url = QUrl
            self._invoke = QMetaObject.invokeMethod
            self._arg = Q_ARG
            self.video = QVideoWidget(self)
            self.stack.addWidget(self.video)
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.video)
            self.player.positionChanged.connect(self._position_changed)
            self.player.durationChanged.connect(self._duration_changed)
            self.player.playbackStateChanged.connect(self._state_changed)
            self.player.mediaStatusChanged.connect(self._media_status)
            self.player.seekableChanged.connect(self.seek.setEnabled)
            self.player.errorOccurred.connect(self._error)
            self._set_audio()
            self._set_speed()
            return True
        except (ImportError, RuntimeError) as exc:
            self.message.setText(f'无法启动播放器：{exc}')
            return False

    def toggle_play(self):
        if not self.path or not self._ensure_player():
            return
        if not self._source_set:
            self._source_set = True
            self.player.setSource(self._url.fromLocalFile(os.path.abspath(self.path)))
        if self.player.playbackState() == self.player.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == self.player.MediaStatus.EndOfMedia:
                self.player.setPosition(0)
            self.stack.setCurrentWidget(self.video)
            self.message.setText('正在播放')
            self.player.play()

    def _restart(self):
        if self.player is not None and self._source_set:
            self.player.setPosition(0)
            self.stack.setCurrentWidget(self.video)
            self.player.play()
        else:
            self.toggle_play()

    def _state_changed(self, state):
        playing = state == self.player.PlaybackState.PlayingState
        self.play.setText('Ⅱ 暂停' if playing else '▶ 播放')
        if self._source_set:
            self.message.setText('正在播放' if playing else '已暂停')

    def _media_status(self, status):
        if not self._source_set:
            return
        if status == self.player.MediaStatus.EndOfMedia:
            self.message.setText('播放结束 · 点击播放或重播')
        elif status == self.player.MediaStatus.LoadingMedia:
            self.message.setText('正在加载视频…')
        elif status == self.player.MediaStatus.BufferingMedia:
            self.message.setText('正在缓冲…')

    def _error(self, *_args):
        if self._source_set:
            detail = self.player.errorString() or '当前系统无法解码此视频'
            self.message.setText('无法播放：' + detail)
            self.stack.setCurrentWidget(self.poster)
            _log.warning('Video playback failed path=%r: %s', self.path, detail)

    def _set_audio(self, *_args):
        if self.audio is not None:
            self.audio.setVolume(self.volume.value() / 100.0)
            self.audio.setMuted(self.mute.isChecked())

    def _set_speed(self, *_args):
        if self.player is not None:
            self.player.setPlaybackRate(float(self.speed.currentData()))

    def _seek(self):
        if self.player is not None and self._source_set:
            self.player.setPosition(round(self.seek.value() / 10000 * self._duration))

    def _position_changed(self, value):
        if self._source_set:
            self._position = value
            if not self.seek.isSliderDown():
                self.seek.setValue(round(value / max(1, self._duration) * 10000))
            self._update_time()

    def _duration_changed(self, value):
        if self._source_set and value > 0:
            self._duration = value
            self._update_time()

    def _update_time(self):
        self.time_label.setText(f'{format_duration(self._position / 1000)} / {format_duration(self._duration / 1000) if self._duration else "—"}')

    def stop(self):
        had_source = self._source_set
        self._source_set = False
        if self.player is not None and had_source:
            # 通过 Qt 元调用释放 Python GIL；FFmpeg 音频线程销毁连接时
            # 会回调 sipQAudioOutput，直接持有 GIL 调用 stop 可导致互等。
            self._invoke(self.player, 'stop', Qt.ConnectionType.DirectConnection)
            self._invoke(self.player, 'setSource', Qt.ConnectionType.DirectConnection,
                         self._arg(self._url, self._url()))
        self.seek.setEnabled(False)
        self.play.setText('▶ 播放')
        self.stack.setCurrentWidget(self.poster)


class MediaPreviewPanel(PreviewPanel):
    """Add video at the preview boundary without rewriting photo loading policy."""
    video_info_ready = pyqtSignal(str, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_view = VideoPlayerView(self)
        self.layout().addWidget(self.video_view, 1)
        self.video_view.hide()
        self._video_token = 0
        self._video_worker = None
        self._video_pending = None
        self._video_path = ''

    def stop_video_playback(self):
        # 进入按键连续浏览时，即使下一个封面未命中也必须停止音视频和探测。
        self._cancel_video()
        self.video_view.play.setEnabled(False)
        self.video_view.restart.setEnabled(False)

    def _cancel_video(self):
        self._video_token += 1
        self._video_pending = None
        if self._video_worker is not None:
            self._video_worker.requestInterruption()
        self.video_view.stop()
        self._video_path = ''

    def _show_video(self, enabled):
        self.video_view.setVisible(enabled)
        self._canvas.setVisible(not enabled)
        self._preview_status_label.setVisible(not enabled)

    def set_image(self, path, *, load_full=True, quick_size=None):
        if self._shutdown_requested:
            return
        self._cancel_video()
        if not is_video(path):
            self._show_video(False)
            return super().set_image(path, load_full=load_full, quick_size=quick_size)
        # Even fast-only video frames never reach an image decoder or media player.
        super().clear_image()
        self._current_path = os.path.normpath(path)
        self._video_path = self._current_path
        self._show_video(True)
        self.video_view.set_path(self._video_path)
        cached = self._cached_quick_preview_pixmap(path, quick_size or 128)
        if cached is not None:
            self.video_view.set_poster(cached)
        if not load_full:
            self.video_view.message.setText('快速浏览 · 松开方向键后可播放')
            self.video_view.play.setEnabled(False)
            self.video_view.restart.setEnabled(False)
            return
        request = (self._video_token, self._video_path, cached is None or (quick_size or 128) < 1024)
        if self._video_worker is not None:
            self._video_pending = request
        else:
            self._start_video_probe(request)

    def set_quick_pixmap(self, path, pixmap, *, quick_size=None):
        if self._shutdown_requested:
            return
        self._cancel_video()
        self._show_video(False)
        super().set_quick_pixmap(path, pixmap, quick_size=quick_size)

    def _start_video_probe(self, request):
        worker = _VideoProbe(*request, self)
        self._video_worker = worker
        worker.result.connect(self._video_result)
        worker.finished.connect(lambda: self._video_finished(worker))
        worker.start()

    def _video_result(self, token, path, info, image, error):
        if self._shutdown_requested or token != self._video_token or path != self._video_path:
            return
        if image is not None:
            self.video_view.set_poster(QPixmap.fromImage(image))
        if error:
            _log.warning('Video probe failed path=%r: %s', path, error)
            self.video_view.message.setText(error + '\n可尝试点击播放')
            if self.video_view._poster is None:
                self.video_view.poster.setText('▶ 视频封面不可用')
        self.video_view._duration = round(float(info.get('duration') or 0) * 1000)
        self.video_view._update_time()
        self.video_info_ready.emit(path, info, error)

    def _video_finished(self, worker):
        if self._video_worker is not worker:
            return
        self._video_worker = None
        worker.deleteLater()
        request, self._video_pending = self._video_pending, None
        if request and not self._shutdown_requested:
            self._start_video_probe(request)

    def clear_image(self):
        self._cancel_video()
        self._show_video(False)
        super().clear_image()

    def request_shutdown(self):
        self._cancel_video()
        super().request_shutdown()

    def shutdown(self, *, wait_timeout_ms=None):
        done = super().shutdown(wait_timeout_ms=wait_timeout_ms)
        # Retain the QThread until its real finished callback, even after wait().
        worker = self._video_worker
        if worker is not None:
            worker.requestInterruption()
            worker.wait(25 if wait_timeout_ms is None else max(0, wait_timeout_ms))
        return done and self._video_worker is None
