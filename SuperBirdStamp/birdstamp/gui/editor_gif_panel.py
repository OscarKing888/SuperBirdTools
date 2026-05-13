from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from birdstamp.gui import editor_options

GIF_SCALE_OPTIONS = editor_options.GIF_SCALE_OPTIONS
DEFAULT_GIF_FPS = editor_options.DEFAULT_GIF_FPS
DEFAULT_GIF_LOOP = editor_options.DEFAULT_GIF_LOOP


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
        self.auto_fps_button = QPushButton("Auto")
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

        scale_widget = QWidget()
        scale_layout = QHBoxLayout(scale_widget)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(10)
        for label, scale in GIF_SCALE_OPTIONS:
            check = QCheckBox(label)
            check.toggled.connect(self.optionsChanged.emit)
            scale_layout.addWidget(check)
            self._scale_checks.append((float(scale), check))
        scale_layout.addStretch(1)
        form.addRow("缩小版本", scale_widget)

        self.keep_frames_check = QCheckBox("保留单帧图片")
        self.keep_frames_check.setChecked(True)
        self.keep_frames_check.setToolTip("勾选后会保留 GIF 合成前的 PNG 单帧序列。")
        self.keep_frames_check.toggled.connect(self.optionsChanged.emit)
        form.addRow("帧序列", self.keep_frames_check)

        root.addLayout(form)

        hint_label = QLabel("按当前照片列表顺序合成 GIF；主 GIF 生成后会按勾选项继续生成缩小版本。")
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
