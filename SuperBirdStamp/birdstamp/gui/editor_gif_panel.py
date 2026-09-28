from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRect, QSize, pyqtSignal
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from birdstamp.gui import editor_options

GIF_SCALE_OPTIONS = editor_options.GIF_SCALE_OPTIONS
DEFAULT_GIF_FPS = editor_options.DEFAULT_GIF_FPS
DEFAULT_GIF_LOOP = editor_options.DEFAULT_GIF_LOOP


class _ScaleOptionsLayout(QLayout):
    """Keep all configured scale choices visible as the export sidebar narrows."""

    _TEXT_CLEARANCE = 16

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(10)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout_items(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout_items(rect, apply=True)

    def sizeHint(self) -> QSize:
        width = sum(self._item_size(item).width() for item in self._items)
        width += max(0, len(self._items) - 1) * self.spacing()
        return QSize(width, self.heightForWidth(width))

    def minimumSize(self) -> QSize:
        width = max((self._item_size(item).width() for item in self._items), default=0)
        return QSize(width, self.heightForWidth(width))

    def _item_size(self, item: QLayoutItem) -> QSize:
        size = item.sizeHint()
        # Native checkbox styles can paint the final glyph beyond Qt's size hint.
        return QSize(size.width() + self._TEXT_CLEARANCE, size.height())

    def _layout_items(self, rect: QRect, *, apply: bool) -> int:
        x = rect.x()
        y = rect.y()
        row_height = 0
        for item in self._items:
            size = self._item_size(item)
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x = rect.x()
                y += row_height + self.spacing()
                row_height = 0
            if apply:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y - rect.y() + row_height


class _ScaleOptionsWidget(QWidget):
    """Tell QFormLayout when wrapping needs a taller field row."""

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        layout = self.layout()
        if layout is not None:
            height = layout.heightForWidth(event.size().width())
            if self.minimumHeight() != height:
                self.setMinimumHeight(height)


@dataclass(slots=True)
class GifExportRequest:
    fps: float
    loop: int
    keep_frame_images: bool
    scale_factors: list[float]


class GifExportPanel(QGroupBox):
    """GIF 导出参数面板。"""

    optionsChanged = pyqtSignal()
    autoFpsRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("GIF 选项", parent)
        self._scale_checks: list[tuple[float, QCheckBox]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setSingleStep(1)
        self.fps_spin.setValue(max(1, int(round(float(DEFAULT_GIF_FPS)))))
        self.fps_spin.valueChanged.connect(lambda _value: self.optionsChanged.emit())
        self.auto_fps_button = QPushButton("自动")
        self.auto_fps_button.setToolTip("根据当前照片列表的拍摄时间自动计算 FPS。")
        self.auto_fps_button.clicked.connect(self.autoFpsRequested.emit)
        fps_widget = QWidget()
        fps_layout = QHBoxLayout(fps_widget)
        fps_layout.setContentsMargins(0, 0, 0, 0)
        fps_layout.setSpacing(8)
        fps_layout.addWidget(self.fps_spin)
        fps_layout.addWidget(self.auto_fps_button)
        fps_layout.addStretch(1)
        form.addRow("FPS", fps_widget)

        self.loop_spin = QSpinBox()
        self.loop_spin.setRange(0, 9999)
        self.loop_spin.setValue(DEFAULT_GIF_LOOP)
        self.loop_spin.setToolTip("0 表示无限循环，1 表示播放 1 次。")
        self.loop_spin.valueChanged.connect(self.optionsChanged.emit)
        form.addRow("循环次数", self.loop_spin)

        scale_widget = _ScaleOptionsWidget()
        scale_layout = _ScaleOptionsLayout(scale_widget)
        for label, scale in GIF_SCALE_OPTIONS:
            check = QCheckBox(label)
            check.toggled.connect(self.optionsChanged.emit)
            scale_layout.addWidget(check)
            self._scale_checks.append((float(scale), check))
        form.addRow("缩小版本", scale_widget)

        self.keep_frames_check = QCheckBox("保留单帧图片")
        self.keep_frames_check.setChecked(True)
        self.keep_frames_check.setToolTip("勾选后会保留 GIF 合成前的 PNG 单帧序列。")
        self.keep_frames_check.toggled.connect(self.optionsChanged.emit)
        form.addRow("帧序列", self.keep_frames_check)

        root.addLayout(form)

        hint_label = QLabel(
            "按当前照片列表顺序合成 GIF，并生成勾选的缩小版本。"
            "GIF 以 10 毫秒计时；高于 100 FPS 时按原总时长采样到 100 FPS，部分输入帧不会写入 GIF。"
        )
        hint_label.setStyleSheet("color: #7A7A7A; font-size: 11px;")
        hint_label.setWordWrap(True)
        root.addWidget(hint_label)

    def current_request(self) -> GifExportRequest:
        fps = float(max(1, int(self.fps_spin.value())))

        scales: list[float] = []
        for scale, check in self._scale_checks:
            if check.isChecked():
                scales.append(float(scale))

        return GifExportRequest(
            fps=fps,
            loop=max(0, int(self.loop_spin.value())),
            keep_frame_images=bool(self.keep_frames_check.isChecked()),
            scale_factors=scales,
        )

    def set_state(
        self,
        *,
        fps: float | None = None,
        loop: int | None = None,
        keep_frame_images: bool | None = None,
        scale_factors: list[float] | tuple[float, ...] | None = None,
    ) -> None:
        self.fps_spin.blockSignals(True)
        self.loop_spin.blockSignals(True)
        self.keep_frames_check.blockSignals(True)
        for _scale, check in self._scale_checks:
            check.blockSignals(True)
        try:
            if fps is not None:
                self.fps_spin.setValue(max(1, min(240, int(round(float(fps))))))
            if loop is not None:
                self.loop_spin.setValue(max(0, int(loop)))
            if keep_frame_images is not None:
                self.keep_frames_check.setChecked(bool(keep_frame_images))
            if scale_factors is not None:
                selected = {round(float(scale), 6) for scale in scale_factors if float(scale) > 0}
                for scale, check in self._scale_checks:
                    check.setChecked(round(float(scale), 6) in selected)
        finally:
            for _scale, check in reversed(self._scale_checks):
                check.blockSignals(False)
            self.keep_frames_check.blockSignals(False)
            self.loop_spin.blockSignals(False)
            self.fps_spin.blockSignals(False)
