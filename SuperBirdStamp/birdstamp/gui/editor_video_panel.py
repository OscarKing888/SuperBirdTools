from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from birdstamp.gui import editor_options
from birdstamp.video_export import VideoExportCancelledError, VideoExportOptions, VideoFrameJob, export_video

VIDEO_CONTAINER_OPTIONS = editor_options.VIDEO_CONTAINER_OPTIONS
VIDEO_CODEC_OPTIONS = editor_options.VIDEO_CODEC_OPTIONS
VIDEO_PRESET_OPTIONS = editor_options.VIDEO_PRESET_OPTIONS
VIDEO_ORIENTATION_OPTIONS = editor_options.VIDEO_ORIENTATION_OPTIONS
VIDEO_FRAME_SIZE_OPTIONS = editor_options.VIDEO_FRAME_SIZE_OPTIONS
VIDEO_FPS_OPTIONS = editor_options.VIDEO_FPS_OPTIONS
DEFAULT_VIDEO_CONTAINER = editor_options.DEFAULT_VIDEO_CONTAINER
DEFAULT_VIDEO_CODEC = editor_options.DEFAULT_VIDEO_CODEC
DEFAULT_VIDEO_PRESET = editor_options.DEFAULT_VIDEO_PRESET
DEFAULT_VIDEO_ORIENTATION = editor_options.DEFAULT_VIDEO_ORIENTATION
DEFAULT_VIDEO_FRAME_SIZE_MODE = editor_options.DEFAULT_VIDEO_FRAME_SIZE_MODE
DEFAULT_VIDEO_FPS = editor_options.DEFAULT_VIDEO_FPS
DEFAULT_VIDEO_CRF = editor_options.DEFAULT_VIDEO_CRF
DEFAULT_VIDEO_WIDTH = editor_options.DEFAULT_VIDEO_WIDTH
DEFAULT_VIDEO_HEIGHT = editor_options.DEFAULT_VIDEO_HEIGHT


@dataclass(slots=True)
class VideoExportRequest:
    container: str
    codec: str
    fps: float
    preset: str
    crf: int
    frame_size_mode: str
    frame_width: int
    frame_height: int
    preserve_temp_files: bool = True


class VideoExportPanel(QGroupBox):
    """视频导出参数面板。"""

    exportRequested = pyqtSignal(object)
    cancelRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("视频导出", parent)
        self._busy = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self.container_combo = QComboBox()
        for suffix, label in VIDEO_CONTAINER_OPTIONS:
            self.container_combo.addItem(label, suffix)
        container_index = self.container_combo.findData(DEFAULT_VIDEO_CONTAINER)
        self.container_combo.setCurrentIndex(max(0, container_index))
        form.addRow("容器", self.container_combo)

        self.codec_combo = QComboBox()
        for label, value in VIDEO_CODEC_OPTIONS:
            self.codec_combo.addItem(label, value)
        codec_index = self.codec_combo.findData(DEFAULT_VIDEO_CODEC)
        self.codec_combo.setCurrentIndex(max(0, codec_index))
        form.addRow("编码器", self.codec_combo)

        self.fps_combo = QComboBox()
        self.fps_combo.setEditable(True)
        for value in VIDEO_FPS_OPTIONS:
            text = f"{float(value):.3f}".rstrip("0").rstrip(".")
            self.fps_combo.addItem(text, float(value))
        self.fps_combo.setCurrentText(f"{DEFAULT_VIDEO_FPS:.3f}".rstrip("0").rstrip("."))
        form.addRow("FPS", self.fps_combo)

        self.frame_size_combo = QComboBox()
        for item in VIDEO_FRAME_SIZE_OPTIONS:
            data = {
                "mode": str(item.get("mode") or "auto").strip().lower(),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
            }
            self.frame_size_combo.addItem(str(item.get("label") or ""), data)
        frame_size_index = 0
        for idx in range(self.frame_size_combo.count()):
            data = self.frame_size_combo.itemData(idx) or {}
            if str(data.get("mode") or "").strip().lower() == DEFAULT_VIDEO_FRAME_SIZE_MODE:
                frame_size_index = idx
                break
        self.frame_size_combo.setCurrentIndex(frame_size_index)
        self.frame_size_combo.currentIndexChanged.connect(self._sync_frame_size_state)
        form.addRow("尺寸", self.frame_size_combo)

        self.orientation_combo = QComboBox()
        for label, value in VIDEO_ORIENTATION_OPTIONS:
            self.orientation_combo.addItem(label, value)
        orientation_index = self.orientation_combo.findData(DEFAULT_VIDEO_ORIENTATION)
        self.orientation_combo.setCurrentIndex(max(0, orientation_index))
        self.orientation_combo.currentIndexChanged.connect(self._sync_frame_size_state)
        form.addRow("方向", self.orientation_combo)

        custom_size_widget = QWidget()
        custom_size_layout = QHBoxLayout(custom_size_widget)
        custom_size_layout.setContentsMargins(0, 0, 0, 0)
        custom_size_layout.setSpacing(6)

        self.frame_width_spin = QSpinBox()
        self.frame_width_spin.setRange(2, 16384)
        self.frame_width_spin.setSingleStep(2)
        self.frame_width_spin.setValue(DEFAULT_VIDEO_WIDTH)
        custom_size_layout.addWidget(self.frame_width_spin)

        custom_size_layout.addWidget(QLabel("x"))

        self.frame_height_spin = QSpinBox()
        self.frame_height_spin.setRange(2, 16384)
        self.frame_height_spin.setSingleStep(2)
        self.frame_height_spin.setValue(DEFAULT_VIDEO_HEIGHT)
        custom_size_layout.addWidget(self.frame_height_spin)
        custom_size_layout.addStretch(1)
        form.addRow("自定义尺寸", custom_size_widget)

        preset_widget = QWidget()
        preset_layout = QHBoxLayout(preset_widget)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)

        self.preset_combo = QComboBox()
        for label, value in VIDEO_PRESET_OPTIONS:
            self.preset_combo.addItem(label, value)
        preset_index = self.preset_combo.findData(DEFAULT_VIDEO_PRESET)
        self.preset_combo.setCurrentIndex(max(0, preset_index))
        preset_layout.addWidget(self.preset_combo, stretch=1)

        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(DEFAULT_VIDEO_CRF)
        self.crf_spin.setToolTip("CRF 越小画质越高，体积通常也越大。")
        preset_layout.addWidget(QLabel("CRF"))
        preset_layout.addWidget(self.crf_spin)
        form.addRow("编码质量", preset_widget)

        self.preserve_temp_files_check = QCheckBox("保留临时文件")
        self.preserve_temp_files_check.setChecked(True)
        self.preserve_temp_files_check.setToolTip(
            "保留并复用导出缓存；全局导出开关会切换缓存桶，单张模板或裁切变化只重做对应帧。"
        )
        form.addRow("帧缓存", self.preserve_temp_files_check)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)

        self.export_button = QPushButton("生成视频")
        self.export_button.clicked.connect(self._emit_export_request)

        self.cancel_button = QPushButton("中断导出")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._emit_cancel_request)

        button_min_width = max(
            self.export_button.sizeHint().width(),
            self.cancel_button.sizeHint().width(),
            112,
        )
        for button in (self.export_button, self.cancel_button):
            button.setMinimumWidth(button_min_width)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button_row.addWidget(button, stretch=1)

        form.addRow("", button_row)
        root.addLayout(form)

        self.hint_label = QLabel(
            "按当前文件列表顺序生成视频，需要 ffmpeg；勾选“保留临时文件”后，FPS/编码参数会复用已有缓存，全局导出开关变化会切换缓存桶，单张模板或裁切变化只重做对应帧。"
        )
        self.hint_label.setStyleSheet("color: #7A7A7A; font-size: 11px;")
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #7A7A7A; font-size: 11px;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self._sync_frame_size_state()

    def _sync_frame_size_state(self) -> None:
        data = self.current_frame_size_data()
        mode = str(data.get("mode") or "auto").strip().lower()
        width, height = self._resolved_preset_size(data)

        is_custom = mode == "custom"
        self.orientation_combo.setEnabled(mode == "preset")
        self.frame_width_spin.setEnabled(is_custom)
        self.frame_height_spin.setEnabled(is_custom)

        if mode == "preset" and width > 0 and height > 0:
            self.frame_width_spin.setValue(width)
            self.frame_height_spin.setValue(height)

    def current_frame_size_data(self) -> dict[str, int | str]:
        data = self.frame_size_combo.currentData()
        return data if isinstance(data, dict) else {"mode": "auto", "width": 0, "height": 0}

    def current_orientation(self) -> str:
        return str(self.orientation_combo.currentData() or DEFAULT_VIDEO_ORIENTATION).strip().lower() or DEFAULT_VIDEO_ORIENTATION

    def _resolved_preset_size(self, data: dict[str, int | str] | None = None) -> tuple[int, int]:
        frame_data = data if isinstance(data, dict) else self.current_frame_size_data()
        width = int(frame_data.get("width") or 0)
        height = int(frame_data.get("height") or 0)
        if width <= 0 or height <= 0:
            return (width, height)

        orientation = self.current_orientation()
        long_edge = max(width, height)
        short_edge = min(width, height)
        if orientation == "portrait" and long_edge != short_edge:
            return (short_edge, long_edge)
        if orientation == "landscape" and long_edge != short_edge:
            return (long_edge, short_edge)
        return (width, height)

    def current_request(self) -> VideoExportRequest:
        fps_text = str(self.fps_combo.currentText() or "").strip()
        try:
            fps = float(fps_text or DEFAULT_VIDEO_FPS)
        except Exception:
            fps = float(DEFAULT_VIDEO_FPS)
        frame_data = self.current_frame_size_data()
        mode = str(frame_data.get("mode") or "auto").strip().lower() or "auto"
        width = self.frame_width_spin.value()
        height = self.frame_height_spin.value()
        if mode == "preset":
            width, height = self._resolved_preset_size(frame_data)
        elif mode == "auto":
            width = 0
            height = 0

        return VideoExportRequest(
            container=str(self.container_combo.currentData() or DEFAULT_VIDEO_CONTAINER),
            codec=str(self.codec_combo.currentData() or DEFAULT_VIDEO_CODEC),
            fps=fps,
            preset=str(self.preset_combo.currentData() or DEFAULT_VIDEO_PRESET),
            crf=int(self.crf_spin.value()),
            frame_size_mode=mode,
            frame_width=width,
            frame_height=height,
            preserve_temp_files=bool(self.preserve_temp_files_check.isChecked()),
        )

    def current_state(self) -> dict[str, int | float | str | bool]:
        frame_data = self.current_frame_size_data()
        return {
            "container": str(self.container_combo.currentData() or DEFAULT_VIDEO_CONTAINER),
            "codec": str(self.codec_combo.currentData() or DEFAULT_VIDEO_CODEC),
            "fps_text": str(self.fps_combo.currentText() or "").strip(),
            "frame_size_mode": str(frame_data.get("mode") or DEFAULT_VIDEO_FRAME_SIZE_MODE).strip().lower(),
            "frame_size_width": int(frame_data.get("width") or 0),
            "frame_size_height": int(frame_data.get("height") or 0),
            "orientation": self.current_orientation(),
            "custom_width": int(self.frame_width_spin.value()),
            "custom_height": int(self.frame_height_spin.value()),
            "preset": str(self.preset_combo.currentData() or DEFAULT_VIDEO_PRESET),
            "crf": int(self.crf_spin.value()),
            "preserve_temp_files": bool(self.preserve_temp_files_check.isChecked()),
        }

    def set_state(self, state: dict[str, object] | None) -> None:
        if not isinstance(state, dict):
            return

        widgets = (
            self.container_combo,
            self.codec_combo,
            self.fps_combo,
            self.frame_size_combo,
            self.orientation_combo,
            self.frame_width_spin,
            self.frame_height_spin,
            self.preset_combo,
            self.crf_spin,
            self.preserve_temp_files_check,
        )
        previous_blocks: list[tuple[QWidget, bool]] = []
        for widget in widgets:
            previous_blocks.append((widget, bool(widget.blockSignals(True))))
        try:
            container = str(state.get("container") or "").strip().lower()
            idx = self.container_combo.findData(container)
            if idx >= 0:
                self.container_combo.setCurrentIndex(idx)

            codec = str(state.get("codec") or "").strip().lower()
            idx = self.codec_combo.findData(codec)
            if idx >= 0:
                self.codec_combo.setCurrentIndex(idx)

            fps_text = str(state.get("fps_text") or "").strip()
            if fps_text:
                self.fps_combo.setCurrentText(fps_text)

            target_mode = str(state.get("frame_size_mode") or "").strip().lower() or DEFAULT_VIDEO_FRAME_SIZE_MODE
            try:
                target_frame_width = int(state.get("frame_size_width") or 0)
            except Exception:
                target_frame_width = 0
            try:
                target_frame_height = int(state.get("frame_size_height") or 0)
            except Exception:
                target_frame_height = 0

            frame_index = -1
            for idx in range(self.frame_size_combo.count()):
                data = self.frame_size_combo.itemData(idx) or {}
                item_mode = str(data.get("mode") or "").strip().lower()
                item_width = int(data.get("width") or 0)
                item_height = int(data.get("height") or 0)
                if item_mode != target_mode:
                    continue
                if target_mode != "preset":
                    frame_index = idx
                    break
                if item_width == target_frame_width and item_height == target_frame_height:
                    frame_index = idx
                    break
                if frame_index < 0:
                    frame_index = idx
            if frame_index >= 0:
                self.frame_size_combo.setCurrentIndex(frame_index)

            orientation = str(state.get("orientation") or "").strip().lower()
            idx = self.orientation_combo.findData(orientation)
            if idx >= 0:
                self.orientation_combo.setCurrentIndex(idx)

            self._sync_frame_size_state()

            try:
                custom_width = int(state.get("custom_width") or 0)
            except Exception:
                custom_width = 0
            try:
                custom_height = int(state.get("custom_height") or 0)
            except Exception:
                custom_height = 0
            if custom_width > 0:
                self.frame_width_spin.setValue(custom_width)
            if custom_height > 0:
                self.frame_height_spin.setValue(custom_height)

            preset = str(state.get("preset") or "").strip().lower()
            idx = self.preset_combo.findData(preset)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)

            try:
                crf = int(state.get("crf"))
            except Exception:
                crf = None
            if crf is not None:
                self.crf_spin.setValue(max(self.crf_spin.minimum(), min(self.crf_spin.maximum(), crf)))

            preserve_temp_files = state.get("preserve_temp_files")
            if preserve_temp_files is not None:
                self.preserve_temp_files_check.setChecked(bool(preserve_temp_files))
        finally:
            for widget, old_block in reversed(previous_blocks):
                widget.blockSignals(old_block)

        self._sync_frame_size_state()

    def set_busy(self, busy: bool, *, status_text: str | None = None) -> None:
        self._busy = busy
        self.container_combo.setEnabled(not busy)
        self.codec_combo.setEnabled(not busy)
        self.fps_combo.setEnabled(not busy)
        self.frame_size_combo.setEnabled(not busy)
        self.orientation_combo.setEnabled(not busy and str(self.current_frame_size_data().get("mode") or "") == "preset")
        self.frame_width_spin.setEnabled(not busy and str(self.current_frame_size_data().get("mode") or "") == "custom")
        self.frame_height_spin.setEnabled(not busy and str(self.current_frame_size_data().get("mode") or "") == "custom")
        self.preset_combo.setEnabled(not busy)
        self.crf_spin.setEnabled(not busy)
        self.preserve_temp_files_check.setEnabled(not busy)
        self.export_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.export_button.setText("正在生成..." if busy else "生成视频")
        if status_text is not None:
            self.set_status_text(status_text)

    def set_status_text(self, text: str) -> None:
        self.status_label.setText(str(text or "").strip())

    def _emit_export_request(self) -> None:
        if self._busy:
            return
        self.exportRequested.emit(self.current_request())

    def _emit_cancel_request(self) -> None:
        if not self._busy:
            return
        self.cancelRequested.emit()


class VideoExportWorker(QThread):
    """后台视频导出线程。"""

    progressTextChanged = pyqtSignal(str)
    exportSucceeded = pyqtSignal(str)
    exportCancelled = pyqtSignal(str)
    exportFailed = pyqtSignal(str)

    def __init__(
        self,
        jobs: list[VideoFrameJob],
        options: VideoExportOptions,
        template_paths: dict[str, Path] | None = None,
        dirty_path_keys: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._jobs = jobs
        self._options = options
        self._template_paths = template_paths or {}
        self._dirty_path_keys = set(dirty_path_keys or set())
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            output_path = export_video(
                self._jobs,
                self._options,
                template_paths=self._template_paths,
                dirty_path_keys=self._dirty_path_keys,
                progress_callback=lambda progress: self.progressTextChanged.emit(progress.message),
                cancel_event=self._cancel_event,
            )
        except VideoExportCancelledError as exc:
            self.exportCancelled.emit(str(exc))
            return
        except Exception as exc:
            self.exportFailed.emit(str(exc))
            return
        self.exportSucceeded.emit(str(output_path))
