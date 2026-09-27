# -*- coding: utf-8 -*-
"""A seek slider with an audio envelope and one shared time-to-pixel mapping."""
from __future__ import annotations

import math

from .qt_compat import (
    QSlider, QPainter, QColor, QPen, QPalette, Qt, pyqtSignal,
    _Horizontal, _LeftButton, _AlignCenter,
)


class WaveformSlider(QSlider):
    position_selected = pyqtSignal(int)
    _MARGIN = 8

    def __init__(self, parent=None):
        super().__init__(_Horizontal, parent)
        self.setRange(0, 10000)
        self.setFixedHeight(56)
        self.setMouseTracking(True)
        self.setAccessibleName('视频音频波形时间轴')
        self._peaks = ()
        self._columns = ()
        self._columns_width = 0
        self._status = '音频波形'
        self._duration_ms = 0
        self._dragging = False
        self.sliderReleased.connect(lambda: self.position_selected.emit(self.value()))

    def set_duration(self, milliseconds):
        self._duration_ms = max(0, int(milliseconds))

    def set_waveform(self, peaks=(), status=''):
        self._peaks = tuple(peaks)
        self._columns_width = 0
        self._status = status
        self.setToolTip((status or '音频波形（首条音轨）') + ' · 点击或拖动定位视频')
        self.update()

    def cancel_drag(self):
        # 切文件/关闭时丢弃尚未提交的拖动，不把旧位置提交给新文件。
        self._dragging = False
        blocked = self.blockSignals(True)
        self.setSliderDown(False)
        self.blockSignals(blocked)

    def _span(self):
        return max(1, self.width() - 2 * self._MARGIN - 1)

    def _value_at(self, x):
        fraction = max(0.0, min(1.0, (x - self._MARGIN) / self._span()))
        return round(self.minimum() + fraction * (self.maximum() - self.minimum()))

    def _event_x(self, event):
        return event.position().x() if hasattr(event, 'position') else event.x()

    def mousePressEvent(self, event):
        if event.button() == _LeftButton and self.isEnabled():
            self.setFocus()
            self._dragging = True
            self.setSliderDown(True)
            self.setValue(self._value_at(self._event_x(event)))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        x = self._event_x(event)
        if self._dragging:
            self.setValue(self._value_at(x))
        elif self._duration_ms:
            milliseconds = round(self._value_at(x) / max(1, self.maximum()) * self._duration_ms)
            seconds, fraction = divmod(milliseconds, 1000)
            minutes, seconds = divmod(seconds, 60)
            self.setToolTip(f'{minutes:02d}:{seconds:02d}.{fraction:03d} · 点击或拖动定位视频')
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == _LeftButton and self._dragging:
            self.setValue(self._value_at(self._event_x(event)))
            self._dragging = False
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        before = self.value()
        super().keyPressEvent(event)
        if self.value() != before:
            self.position_selected.emit(self.value())

    def wheelEvent(self, event):
        before = self.value()
        super().wheelEvent(event)
        if self.value() != before:
            self.position_selected.emit(self.value())

    def paintEvent(self, event):
        painter = QPainter(self)
        palette = self.palette()
        roles = getattr(QPalette, 'ColorRole', QPalette)
        background = palette.color(roles.Base)
        foreground = palette.color(roles.Text)
        accent = palette.color(roles.Highlight)
        painter.fillRect(self.rect(), background)
        width = self._span() + 1
        center = self.height() // 2
        playhead = self._MARGIN + round((self.sliderPosition() - self.minimum()) /
                                      max(1, self.maximum() - self.minimum()) * self._span())
        played = QColor(accent)
        played.setAlpha(35)
        painter.fillRect(self._MARGIN, 3, max(0, playhead - self._MARGIN), self.height() - 6, played)
        if self._peaks:
            if self._columns_width != width:
                count = len(self._peaks)
                maximum = max(self._peaks) or 1.0
                # 峰值压缩到屏幕像素，窄窗口也保留瞬态；只在数据/宽度变化时计算。
                self._columns = tuple(math.sqrt(max(self._peaks[i * count // width:
                    max(i * count // width + 1, (i + 1) * count // width)]) / maximum)
                    for i in range(width))
                self._columns_width = width
            pending = QColor(foreground)
            pending.setAlpha(125 if self.isEnabled() else 65)
            for i, peak in enumerate(self._columns):
                x = self._MARGIN + i
                height = round(peak * (center - 7))
                painter.setPen(accent if x <= playhead else pending)
                painter.drawLine(x, center - height, x, center + height)
        else:
            faint = QColor(foreground)
            faint.setAlpha(45)
            painter.setPen(faint)
            painter.drawLine(self._MARGIN, center, self._MARGIN + self._span(), center)
            painter.setPen(foreground)
            painter.drawText(self.rect(), _AlignCenter, self._status)
        painter.setPen(QPen(accent, 2))
        painter.drawLine(playhead, 2, playhead, self.height() - 3)
        painter.setBrush(accent)
        painter.drawEllipse(playhead - 3, self.height() - 8, 6, 6)
        if self.hasFocus():
            painter.setPen(QPen(accent, 1))
            painter.setBrush(getattr(Qt, 'BrushStyle', Qt).NoBrush)
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
