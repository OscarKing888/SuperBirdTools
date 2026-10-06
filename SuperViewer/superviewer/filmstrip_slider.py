# -*- coding: utf-8 -*-
"""A compact contact strip sharing the waveform's time/interaction mapping."""
from __future__ import annotations

import math

from .qt_compat import QImage, QPainter, QPalette, QPen, QRect, Qt, _AlignCenter
from .waveform_slider import WaveformSlider


class FilmstripSlider(WaveformSlider):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setAccessibleName('视频序列帧时间轴')
        self._frames = ()
        self._images = ()
        self.set_frames(status='正在生成序列帧…')

    def set_frames(self, frames=(), status=''):
        # QImage 只持有当前视频的小图；工作线程从不创建 QPixmap。
        self._frames = tuple(frames)
        fmt = getattr(QImage, 'Format', QImage).Format_RGB888
        images = [QImage(frame.rgb, frame.width, frame.height,
                         frame.width * 3, fmt).copy() if frame else None
                  for frame in self._frames]
        ready = [index for index, image in enumerate(images) if image is not None]
        # 抽帧按由粗到细交付；未到的格先借用最近已到的画面，整条立即铺满。
        self._images = tuple(
            image if image is not None or not ready else
            images[min(ready, key=lambda i: (abs(i - index), i))]
            for index, image in enumerate(images))
        self._status = status
        self.setToolTip((status or '视频序列帧') + ' · 点击或拖动定位视频')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        roles = getattr(QPalette, 'ColorRole', QPalette)
        palette = self.palette()
        accent = palette.color(roles.Highlight)
        painter.fillRect(self.rect(), palette.color(roles.Base))
        area = QRect(self._MARGIN, 3, self._span() + 1, self.height() - 6)
        available = next((frame for frame in self._frames if frame), None)
        if available:
            # 随窗口宽度选择均匀分布的小图，不触发重新抽帧，也不横向拉伸画面。
            tile_width = max(24, area.height() * available.width / available.height)
            count = min(len(self._frames), max(1, math.ceil(area.width() / tile_width)))
            painter.setRenderHint(getattr(QPainter, 'RenderHint', QPainter).SmoothPixmapTransform)
            for index in range(count):
                sample = min(len(self._images) - 1, int((index + .5) * len(self._images) / count))
                image = self._images[sample]
                left = area.left() + round(index * area.width() / count)
                right = area.left() + round((index + 1) * area.width() / count)
                target = QRect(left, area.top(), right - left, area.height())
                if image is not None:
                    # 等比居中裁切每格，保留参考图连续紧密的胶片外观。
                    ratio = target.width() / target.height()
                    source = image.rect()
                    if image.width() / image.height() > ratio:
                        source.setWidth(max(1, round(image.height() * ratio)))
                    else:
                        source.setHeight(max(1, round(image.width() / ratio)))
                    source.moveCenter(image.rect().center())
                    painter.drawImage(target, image, source)
                painter.setPen(palette.color(roles.Mid))
                painter.drawLine(left, area.top(), left, area.bottom())
        else:
            painter.setPen(palette.color(roles.Text))
            painter.drawText(area, _AlignCenter, self._status)
        painter.setPen(QPen(accent if self.isEnabled() else palette.color(roles.Mid), 1))
        painter.setBrush(getattr(Qt, 'BrushStyle', Qt).NoBrush)
        painter.drawRoundedRect(area.adjusted(0, 0, -1, -1), 3, 3)
        if self._duration_ms:
            x = self._MARGIN + round(self.sliderPosition() / max(1, self.maximum()) * self._span())
            # 深色描边保证白色画面上的播放头仍清晰。
            painter.setPen(QPen(palette.color(roles.Base), 4))
            painter.drawLine(x, 1, x, self.height() - 2)
            painter.setPen(QPen(accent, 2))
            painter.drawLine(x, 1, x, self.height() - 2)
        if self.hasFocus():
            painter.setPen(QPen(accent, 1))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
