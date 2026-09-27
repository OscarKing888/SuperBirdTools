"""独立 A/B 对照：A 自选/钉住照片，B 保持编辑器的选图与编辑上下文。"""
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget

from app_common.preview_canvas import PreviewWithStatusBar
from .editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState
from .editor_preview_decode_worker import EditorPreviewDecodeWorker
from .editor_sequence_preview_worker import EditorSequencePreviewWorker, pil_qimage
from .editor_tracking_overlay import tracking_overlays
from .editor_utils import path_key
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from . import editor_core, editor_options
from birdstamp.image_dejitter.sequence_geometry import source_normalized_crop


class ABPreview(QObject):
    def __init__(self, editor, layout):
        super().__init__(editor)
        self.editor = editor
        self.path = None
        self.paths = ()
        self.worker = None
        self.token = 0
        self.request = None
        self.pending = False
        self.stopping = False
        self.frame = None
        self.image = None
        self.size = None
        self.upgrade = QTimer(self)
        self.upgrade.setSingleShot(True)
        self.upgrade.setInterval(120)
        self.upgrade.timeout.connect(self._start)
        bar = QHBoxLayout()
        self.enabled = QCheckBox('A/B 对照')
        self.enabled.setChecked(editor_options.PREVIEW_AB_ENABLED)
        self.enabled.setToolTip('A 可独立选图并钉住；B 随照片列表切换。两侧可独立缩放和拖动。')
        bar.addWidget(self.enabled)
        bar.addStretch()
        layout.addLayout(bar)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.a_panel = QWidget()
        a = QVBoxLayout(self.a_panel)
        a.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel('A'))
        self.photos = QComboBox()
        self.photos.setMinimumWidth(110)
        self.photos.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        head.addWidget(self.photos, 1)
        self.pin = QCheckBox('钉住')
        self.pin.setChecked(True)
        self.pin.setToolTip('保持 A 图不随 B 图切换；仍可用 A 的下拉框主动选图。')
        head.addWidget(self.pin)
        self.mode = QComboBox()
        self.mode.addItems(['原图', '去抖动成片'])
        head.addWidget(self.mode)
        a.addLayout(head)
        tools = QHBoxLayout()
        self.center = QCheckBox('自动焦点居中')
        self.center.setChecked(editor_options.PREVIEW_AUTO_FOCUS_CENTER)
        tools.addWidget(self.center)
        fit = QPushButton('适应窗口')
        tools.addWidget(fit)
        tools.addStretch()
        a.addLayout(tools)
        self.preview = PreviewWithStatusBar(canvas=EditorPreviewCanvas())
        a.addWidget(self.preview, 1)
        b_panel = QWidget()
        b = QVBoxLayout(b_panel)
        b.setContentsMargins(0, 0, 0, 0)
        self.b_header = QWidget()
        b_head = QVBoxLayout(self.b_header)
        b_head.setContentsMargins(0, 0, 0, 0)
        b_row = QHBoxLayout()
        b_row.addWidget(QLabel('B'))
        self.b_photos = QComboBox()
        self.b_photos.setMinimumWidth(110)
        self.b_photos.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.b_photos.setToolTip('选择当前编辑照片，与照片列表同步。')
        b_row.addWidget(self.b_photos, 1)
        b_head.addLayout(b_row)
        b_tools = QHBoxLayout()
        b_fit = QPushButton('适应窗口')
        b_tools.addWidget(b_fit)
        b_tools.addStretch()
        b_head.addLayout(b_tools)
        b.addWidget(self.b_header)
        b.addWidget(editor.preview_label, 1)
        self.splitter.addWidget(self.a_panel)
        self.splitter.addWidget(b_panel)
        self.splitter.setSizes([500, 500])
        layout.addWidget(self.splitter, 1)
        self.a_panel.setVisible(self.enabled.isChecked())
        self.b_header.setVisible(self.enabled.isChecked())
        self.enabled.toggled.connect(self._toggle)
        self.photos.currentIndexChanged.connect(self._choose)
        self.b_photos.currentIndexChanged.connect(self._choose_b)
        b_fit.clicked.connect(lambda: editor._refresh_preview_label(reset_view=True, force_fit=True))
        self.mode.currentIndexChanged.connect(lambda: self.sync(force=True))
        self.pin.toggled.connect(lambda: self.sync())
        self.center.toggled.connect(self.preview.canvas.set_auto_focus_center)
        self.preview.canvas.set_auto_focus_center(self.center.isChecked())
        fit.clicked.connect(lambda: self.preview.set_source_pixmap(
            QPixmap.fromImage(self.image) if self.image is not None else None, reset_view=True))

    def _toggle(self, enabled):
        self.a_panel.setVisible(enabled)
        self.b_header.setVisible(enabled)
        if enabled:
            self.splitter.setSizes([500, 500])
            self.sync(force=True)
        else:
            self._cancel()
            self.request = None

    def _choose(self):
        value = self.photos.currentData()
        if value:
            self.path = Path(value)
            # 主动选 A 不改变 B，也不受“钉住”限制。
            self.sync(force=True, follow=False)

    def _choose_b(self):
        value = self.b_photos.currentData()
        item = self.editor._find_photo_item_by_path(Path(value)) if value else None
        if item is not None:
            self.editor.photo_list.setCurrentItem(item)

    def sync(self, *, force=False, follow=True):
        if not self.enabled.isChecked() or self.stopping:
            return
        editor = self.editor
        paths = tuple(editor._list_photo_paths())
        if paths != self.paths:
            self.paths = paths
            self.photos.blockSignals(True)
            self.photos.clear()
            self.b_photos.blockSignals(True)
            self.b_photos.clear()
            for path in paths:
                self.photos.addItem(path.name, str(path))
                self.b_photos.addItem(path.name, str(path))
            self.photos.blockSignals(False)
            self.b_photos.blockSignals(False)
        if self.path not in paths:
            reference = getattr(editor, '_dejitter_reference_source', None)
            self.path = Path(reference) if reference and Path(reference) in paths else next(iter(paths), None)
        if follow and not self.pin.isChecked() and editor.current_path in paths:
            self.path = editor.current_path
        self.photos.blockSignals(True)
        self.photos.setCurrentIndex(self.photos.findData(str(self.path)))
        self.photos.blockSignals(False)
        current = editor.current_path
        self.b_photos.blockSignals(True)
        self.b_photos.setCurrentIndex(self.b_photos.findData(str(current)))
        self.b_photos.blockSignals(False)
        sequence = editor._sequence_preview
        result = self.mode.currentIndex() == 1
        request = (self.path, result, id(sequence) if result else None,
                   image_file_signature(self.path) if self.path else None)
        if not force and request == self.request:
            self._display()
            return
        self._cancel()
        self.request = request
        self.image = self.frame = self.size = None
        self.preview.set_source_pixmap(None)
        self.preview.set_original_size(None, None)
        self.preview.set_cropped_size(None, None)
        self.preview.set_source_mode('A · 正在读取…' if self.path else 'A · 未选择')
        if self.path is None:
            return
        key = path_key(self.path)
        frame = editor._sequence_quick_frames.get(key)
        if frame is not None:
            self.image = frame.image if result else frame.source_image
            self.size = frame.source_size
            self.frame = frame if result else None
        if result and (sequence is None or key not in sequence.jobs):
            self.preview.set_source_mode('成片待分析 · 可切回原图对照')
            return
        self._display()
        self.pending = True
        self.upgrade.start()

    def _cancel(self):
        self.token += 1
        self.pending = False
        self.upgrade.stop()
        if self.worker is not None:
            if hasattr(self.worker, 'cancel'):
                self.worker.cancel()
            else:
                self.worker.requestInterruption()

    def _start(self):
        if not self.pending or self.worker is not None or self.stopping:
            return
        if self.editor._sequence_fast_preview_active():
            self.upgrade.start()
            return
        path, result, *_ = self.request
        self.pending = False
        if result:
            sequence = self.editor._sequence_preview
            if sequence is None:
                return
            worker = EditorSequencePreviewWorker(token=self.token, path=path, sequence=sequence, parent=self)
            worker.ready.connect(self._aligned)
            worker.failed.connect(self._failed)
        else:
            worker = EditorPreviewDecodeWorker(self.token, path,
                                               max_long_edge=self.editor._preview_decode_max_long_edge(), parent=self)
            worker.quick_decoded.connect(self._decoded)
            worker.decoded.connect(self._decoded)
            worker.failed.connect(lambda token, _path, message: self._failed(token, message))
        self.worker = worker
        worker.finished.connect(self._finished)
        worker.start()

    def _accept(self, token):
        return (token == self.token and self.enabled.isChecked() and not self.stopping
                and self.request is not None and self.path is not None
                and self.request[3] == image_file_signature(self.path))

    def _decoded(self, token, path, image, size):
        try:
            if not self._accept(token) or path_key(Path(path)) != path_key(self.path):
                return
            with image.convert('RGB') as rgb:
                self.image = pil_qimage(rgb)
            self.size = tuple(size)
            self.frame = None
            self._display()
        finally:
            image.close()

    def _aligned(self, token, sequence, frame):
        if not self._accept(token) or sequence is not self.editor._sequence_preview:
            return
        if not sequence.files_current():
            self.editor._invalidate_sequence_preview()
            return
        self.frame, self.image, self.size = frame, frame.image, frame.source_size
        self._display()

    def _failed(self, token, message):
        if self._accept(token):
            self.preview.set_source_mode(f'A 预览失败：{message}')

    def _finished(self):
        worker = self.sender()
        if worker is not self.worker:
            return
        self.worker = None
        worker.deleteLater()
        if self.pending and not self.stopping:
            self.upgrade.start()

    def _display(self):
        if self.image is None or self.size is None:
            return
        editor = self.editor
        key = path_key(self.path)
        sequence = editor._sequence_preview
        metadata = dict(editor.raw_metadata_cache.get(key) or editor.photo_list_metadata_cache.get(key) or {})
        if sequence and key in sequence.jobs:
            metadata.update(sequence.jobs[key].raw_metadata)
        crop, pad = self.frame.crop_plan if self.frame else (None, (0,0,0,0))
        width, height = self.size
        state = EditorPreviewOverlayState(crop_effect_box=(0, 0, 1, 1))
        state.focus_box = editor_core.resolve_focus_box_after_processing(
            metadata, source_width=width, source_height=height, crop_box=crop,
            outer_pad=pad, apply_ratio_crop=crop is not None,
            camera_type=editor_core.resolve_focus_camera_type_from_metadata(metadata))
        bird = editor._bird_box_cache.get(editor._source_signature(self.path))
        state.bird_box = editor_core.transform_source_box_after_crop_padding(
            bird, crop_box=crop, source_width=width, source_height=height, pt=pad[0], pb=pad[1], pl=pad[2], pr=pad[3]) if crop else bird
        regions = editor._dejitter_reference_regions
        tracking = editor._reference_tracking_results.get(key) if editor._reference_tracking_input() == editor._reference_tracking_definition else None
        if tracking and (tracking.signature != image_file_signature(self.path) or
                         image_file_signature(Path(editor._dejitter_reference_source)) != editor._reference_tracking_signature):
            tracking = None
        source_crop = source_normalized_crop(self.size, sequence.pixel_boxes[key]) if self.frame and sequence else None
        state.reference_diagnostics = tracking_overlays(regions, tracking, source_crop)
        if not crop and editor._dejitter_reference_source and path_key(Path(editor._dejitter_reference_source)) == key:
            state.reference_regions = regions
            state.reference_diagnostics = ()
        options = editor._build_preview_overlay_options()
        options.show_reference_regions = True
        options.show_crop_effect = False
        if not self.frame and sequence and key in sequence.pixel_boxes and not editor.dejitter_pad_to_union_check.isChecked():
            state.crop_effect_box = source_normalized_crop(self.size, sequence.pixel_boxes[key])
            state.alignment_crop_box = state.crop_effect_box
            options.show_crop_effect = editor.show_crop_effect_check.isChecked()
        self.preview.apply_overlay_options(options)
        self.preview.apply_overlay_state(state)
        self.preview.set_original_size(width, height)
        self.preview.set_cropped_size(*(self.frame.output_size if self.frame else (None, None)))
        self.preview.set_source_mode('A · 去抖动成片' if self.frame else 'A · 原图')
        first = self.preview.canvas._source_pixmap is None
        self.preview.set_source_pixmap(QPixmap.fromImage(self.image), reset_view=first, preserve_view=not first,
                                       preserve_scale=not first)

    def shutdown(self):
        self.stopping = True
        self._cancel()
        return self.worker is None
