"""Independent carousel editor: model state, bounded previews and owned export worker."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from threading import Event

from PIL import Image, ImageOps
from PyQt6.QtCore import QObject, QSignalBlocker, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from app_common.collapsible_section import CollapsibleSection
from app_common.image_output_form import ImageOutputOptionsForm
from app_common.image_output_paths import ImageOutputOptions, output_directory
from app_common.preview_canvas import PreviewWithStatusBar
from app_common.toggle_button import ToggleToolButton
from birdstamp.decoders.image_decoder import decode_image_for_preview, read_decoded_image_size
from birdstamp.image_pipeline import ImageProcContext
from birdstamp.seamless_split import (
    SplitCancelled, SplitCanvasStage, SplitOptions, check_cancel, export_split,
    normalized_crop, preview_size, slice_boxes,
)
from birdstamp.workspace import resolve_workspace_path, serialize_workspace_path
from .color_editor import ColorEditor
from .editor_utils import pil_to_qpixmap, path_key
from .split_preview_canvas import SplitPreviewCanvas


RATIOS = ('3:4', '3:5', '1:1', '4:3', '9:16', '16:9', '2:3', '3:2', 'custom')
XHS_FULLSCREEN_RATIO = '3:5'


def source_stamp(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def load_preview(path):
    """Preserve alpha for the background preview; all I/O runs in the worker."""
    try:
        with Image.open(path) as image:
            alpha = 'A' in image.getbands() or 'transparency' in image.info
            if alpha:
                with ImageOps.exif_transpose(image) as oriented:
                    size = oriented.size
                    oriented.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                    return oriented.convert('RGBA'), size
    except (OSError, ValueError):
        pass  # RAW/HEIF use the normal decoder below.
    image = decode_image_for_preview(path, max_long_edge=2400)
    try:
        size = image.info.get('birdstamp_source_properties', {}).get('size') or read_decoded_image_size(path)
        return image, tuple(size)
    except BaseException:
        image.close()
        raise


class SplitWorker(QThread):
    progress = pyqtSignal(int, int)

    def __init__(self, path, options, crop, generation, *, source=None, source_size=None, stamp=None, output=None, page_index=None, parent=None):
        super().__init__(parent)
        self.path, self.options, self.crop, self.generation = path, options, crop, generation
        self.source, self.source_size, self.output = source, source_size, output
        self.stamp = stamp
        self.page_index = page_index
        self.cancelled = Event()
        self.result = None
        self.error = None

    def run(self):
        from app_common.exif_io.exiftool_runner import exiftool_read_request, exiftool_worker_session
        with exiftool_worker_session(), exiftool_read_request(self.cancelled.is_set, timeout=60):
            self._run()

    def _run(self):
        canvas = None
        try:
            check_cancel(self.cancelled.is_set)
            if self.output is not None:
                self.result = export_split(self.path, self.options, self.output, self.crop,
                                           page_index=self.page_index, cancel=self.cancelled.is_set, progress=self.progress.emit)
                return
            stamp = source_stamp(self.path)
            if self.source is not None and self.stamp != stamp:
                self.source.close()
                self.source = None
            if self.source is None:
                self.source, self.source_size = load_preview(self.path)
            check_cancel(self.cancelled.is_set)
            crop = normalized_crop(self.source_size, self.options, self.crop)
            context = ImageProcContext(self.source, asdict(self.options), source_size=self.source_size,
                                       crop_box=crop, precomputed={'split_preview_size': preview_size(self.options)})
            SplitCanvasStage().process(context)
            canvas = context.image
            check_cancel(self.cancelled.is_set)
            if stamp != source_stamp(self.path):
                raise RuntimeError('读取期间源图发生变化，请重新选择照片')
            self.result = (self.source, self.source_size, canvas, crop, stamp)
            self.source = canvas = None  # ownership transfers only in real finished callback
        except SplitCancelled:
            pass
        except Exception as exc:
            if not self.cancelled.is_set():
                self.error = str(exc)
        finally:
            if self.source is not None:
                self.source.close()
                self.source = None
            if canvas is not None:
                canvas.close()


class SeamlessSplitEditor(QObject):
    changed = pyqtSignal()

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.path = None
        self.source = self.canvas_image = self.source_size = None
        self.source_stamp = None
        self.crops = {}
        self.crop_paths = {}
        self.undo_stacks, self.redo_stacks = {}, {}
        self.worker = None
        self.pending = self.closed = self.restoring = False
        self.generation = 0
        self.page_index = 0
        self.drag_before = None
        self._fit_next = True
        self._valid = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self._start_preview)
        self._build_page()
        self._build_preview()
        self._build_actions()
        self.changed.connect(editor._schedule_workspace_autosave)
        self._update_actions()

    def active(self):
        return self.editor.export_tabs.currentWidget() is self.page

    def exporting(self):
        return self.worker is not None and self.worker.output is not None

    @staticmethod
    def _button(text, slot, layout):
        button = QPushButton(text)
        button.clicked.connect(slot)
        layout.addWidget(button)
        return button

    @staticmethod
    def _spin(low, high, value):
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setValue(value)
        return widget

    def _section(self, title, root):
        section = CollapsibleSection(title)
        root.addWidget(section)
        form = QFormLayout(section.body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        return section, form

    def _build_page(self):
        self.page = QWidget()
        root = QVBoxLayout(self.page)
        root.setContentsMargins(0, 6, 0, 0)
        self.layout_group, form = self._section('切分设置', root)
        self.count = self._spin(2, 20, 3)
        form.addRow('切分份数', self.count)
        self.ratio = QComboBox()
        # Let WrapLongRows move a long preset below its label instead of clipping it.
        self.ratio.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        for ratio in RATIOS:
            label = '自定义宽高比' if ratio == 'custom' else ratio
            if ratio == XHS_FULLSCREEN_RATIO:
                label = '小红书全屏连屏 3:5'
            self.ratio.addItem(label, ratio)
        form.addRow('每张比例', self.ratio)
        self.ratio_hint = QLabel('3:5 为全屏图文参考比例。发布时使用全屏图或长文入口，'
                                 '按编号排列，避免逐张裁剪；发布预览中检查接缝。')
        self.ratio_hint.setWordWrap(True)
        self.ratio_hint.hide()
        form.addRow(self.ratio_hint)
        self.resolution = QComboBox()
        for edge in (1080, 1440, 2160):
            self.resolution.addItem(f'短边 {edge} px', edge)
        self.resolution.addItem('自定义', None)
        form.addRow('每张分辨率', self.resolution)
        self.width, self.height = self._spin(64, 8192, 1080), self._spin(64, 8192, 1440)
        form.addRow('每张宽度', self.width)
        form.addRow('每张高度', self.height)
        self.dimensions = QLabel()
        self.dimensions.setWordWrap(True)
        form.addRow(self.dimensions)
        self.framing_group, form = self._section('构图设置', root)
        self.fit = QComboBox()
        self.fit.addItem('裁满画面', 'cover')
        self.fit.addItem('完整显示并补边', 'contain')
        form.addRow('构图方式', self.fit)
        self.background = ColorEditor()
        self.background.set_value('#FFFFFF')
        form.addRow('背景颜色', self.background)
        row = QHBoxLayout()
        self.reset_button = self._button('重置构图', self.reset_crop, row)
        self.undo_button = self._button('撤销', lambda: self.undo_crop(False), row)
        self.redo_button = self._button('重做', lambda: self.undo_crop(True), row)
        form.addRow(row)
        self.output_group, form = self._section('输出设置', root)
        self.output_form = ImageOutputOptionsForm({'split_subdir': '连屏切图', 'split_format': 'png',
                                                   'split_output_mode': 'ask'}, prefix='split', formats=('jpeg', 'png'))
        self.output_form.format.setItemText(0, 'JPEG')
        self.output_form.format.setItemText(1, 'PNG（8 位，无损）')
        form.addRow(self.output_form)
        self.quality = self._spin(1, 100, 95)
        self.quality.setEnabled(False)
        form.addRow('JPEG 质量', self.quality)
        hint = QLabel('使用选中源照片，不套用普通裁剪、模板或去抖动结果。\n分割线和编号仅用于预览；每次导出到新的结果目录，不生成 XMP。\n切换到逐张预览，可导出当前显示的一张。')
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch()
        self.ratio.currentIndexChanged.connect(self._ratio_changed)
        self.resolution.currentIndexChanged.connect(self._resolution_changed)
        self.width.valueChanged.connect(lambda: self._dimension_changed(True))
        self.height.valueChanged.connect(lambda: self._dimension_changed(False))
        self.count.valueChanged.connect(self._settings_changed)
        self.fit.currentIndexChanged.connect(self._settings_changed)
        self.background.colorChanged.connect(self._settings_changed)
        self.quality.valueChanged.connect(self._output_changed)
        for combo in (self.output_form.mode, self.output_form.format):
            combo.currentIndexChanged.connect(self._output_changed)
        for edit in (self.output_form.subdir, self.output_form.directory):
            edit.textChanged.connect(self._output_changed)

    def _build_preview(self):
        self.preview_host = QWidget()
        root = QVBoxLayout(self.preview_host)
        self.summary = QLabel('请从照片列表选择一张图片')
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.tools = QWidget()
        tools = QVBoxLayout(self.tools)
        tools.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.tools)
        row = QHBoxLayout()
        tools.addLayout(row)
        self.whole, self.single = ToggleToolButton('整体裁剪'), ToggleToolButton('逐张预览')
        self.view_group = QButtonGroup(self)
        for button in (self.whole, self.single):
            self.view_group.addButton(button)
            row.addWidget(button)
        self.whole.setChecked(True)
        self.crop_tool = ToggleToolButton('调整裁剪框')
        self.crop_tool.setChecked(True)
        row.addWidget(self.crop_tool)
        row.addStretch()
        row = QHBoxLayout()
        tools.addLayout(row)
        self.lines, self.numbers = ToggleToolButton('分割线'), ToggleToolButton('编号')
        for button in (self.lines, self.numbers):
            button.setChecked(True)
            row.addWidget(button)
        self._button('适应窗口', lambda: self.preview.canvas.fit_to_window(), row)
        self._button('100%', lambda: self.preview.canvas.set_display_scale_percent(100), row)
        self.zoom = self._spin(1, 3200, 100)
        self.zoom.setSuffix('%')
        self.zoom.setAccessibleName('预览缩放')
        row.addWidget(self.zoom)
        row.addStretch()
        self.preview = PreviewWithStatusBar(canvas=SplitPreviewCanvas())
        root.addWidget(self.preview, 1)
        self.pager = QWidget()
        row = QHBoxLayout(self.pager)
        self.previous_button = self._button('上一张', lambda: self.move_page(-1), row)
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.page_label)
        self.next_button = self._button('下一张', lambda: self.move_page(1), row)
        root.addWidget(self.pager)
        self.pager.hide()
        self.single.toggled.connect(self._view_changed)
        self.crop_tool.toggled.connect(self._sync_overlays)
        self.lines.toggled.connect(self._sync_overlays)
        self.numbers.toggled.connect(self._sync_overlays)
        self.zoom.valueChanged.connect(lambda value: self.preview.canvas.set_display_scale_percent(value))
        self.preview.canvas.display_scale_percent_changed.connect(self._zoom_changed)
        self.preview.canvas.crop_drag_started.connect(self._drag_started)
        self.preview.canvas.crop_drag_finished.connect(self._drag_finished)
        self.preview.canvas.crop_box_changed.connect(self._crop_changed)
        self.preview.canvas.pageRequested.connect(self.move_page)

    def _build_actions(self):
        self.actions = QWidget()
        root = QVBoxLayout(self.actions)
        self.status = QLabel('选择照片后可调整构图并导出。')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        root.addWidget(self.progress)
        self.export_current_button = self._button('导出当前张', lambda: self.pick_export(current=True), root)
        row = QHBoxLayout()
        root.addLayout(row)
        self.export_button = self._button('导出整组', self.pick_export, row)
        self.cancel_button = self._button('取消', self.cancel, row)

    def options(self):
        return SplitOptions(self.count.value(), self.width.value(), self.height.value(),
                            self.fit.currentData(), self.background.value(), self.output_form.format.currentData(), self.quality.value())

    def crop(self):
        return self.crops.get(path_key(self.path)) if self.path else None

    def _set_dimensions(self, width, height):
        with QSignalBlocker(self.width), QSignalBlocker(self.height):
            self.width.setValue(width)
            self.height.setValue(height)

    def _refresh_resolution(self):
        current = min(self.width.value(), self.height.value())
        with QSignalBlocker(self.resolution):
            self.resolution.setCurrentIndex(max(0, self.resolution.findData(current)) if current in (1080, 1440, 2160) else 3)

    def _ratio_changed(self):
        if self.restoring:
            return
        if self.ratio.currentData() != 'custom':
            a, b = map(int, self.ratio.currentData().split(':'))
            edge = min(self.width.value(), self.height.value())
            self._set_dimensions(round(a * edge / min(a, b)), round(b * edge / min(a, b)))
        self._refresh_resolution()
        self._settings_changed()

    def _resolution_changed(self):
        if self.restoring or self.resolution.currentData() is None:
            return
        a, b = self.width.value(), self.height.value()
        scale = min(self.resolution.currentData() / min(a, b), 8192 / max(a, b))
        self._set_dimensions(round(a * scale), round(b * scale))
        self._settings_changed()

    def _dimension_changed(self, width_changed):
        if self.restoring:
            return
        w, h = self.width.value(), self.height.value()
        if self.ratio.currentData() != 'custom':
            a, b = map(int, self.ratio.currentData().split(':'))
            h = max(64, min(8192, round(w * b / a))) if width_changed else h
            w = max(64, min(8192, round(h * a / b)))
            self._set_dimensions(w, h)
        self._refresh_resolution()
        self._settings_changed()

    def set_source(self, path):
        if self.closed or self.exporting():
            return
        path = Path(path) if path else None
        if path == self.path and (self.pending or (
                self.worker is not None and self.worker.generation == self.generation
                and not self.worker.cancelled.is_set())):
            return
        if path == self.path and self.source is not None:
            try:
                if self.source_stamp == source_stamp(path):
                    return
            except OSError:
                pass
        self._drag_finished()
        self.path = path
        if path is not None:
            self.crop_paths[path_key(path)] = path
        self.page_index = 0
        self._fit_next = True
        self._release_images()
        self.preview.set_source_pixmap(QPixmap())
        if path is None:
            self.summary.setText('请从照片列表选择一张图片')
            self.preview.set_original_size(None, None)
        self._settings_changed()

    def _release_images(self):
        for image in (self.source, self.canvas_image):
            if image is not None:
                image.close()
        self.source = self.canvas_image = self.source_size = None
        self.source_stamp = None

    def _settings_changed(self, *_args):
        if self.restoring or self.closed or self.exporting():
            return
        self.ratio_hint.setVisible(self.ratio.currentData() == XHS_FULLSCREEN_RATIO)
        self.generation += 1
        self._valid = False
        if self.worker is not None:
            self.worker.cancelled.set()
        try:
            options = self.options()
            self.dimensions.setText(f'总画布 {options.size[0]} × {options.size[1]} px · 共 {options.count} 张')
            if self.source_size and self.path:
                self.crops[path_key(self.path)] = normalized_crop(self.source_size, options, self.crop())
            self.pending = self.path is not None
            self.status.setText('正在更新连屏预览…' if self.path else '请从照片列表选择一张图片。')
            self.timer.start()
        except (ValueError, TypeError) as exc:
            self.pending = False
            self.timer.stop()
            self.status.setText(str(exc))
        self.quality.setEnabled(self.output_form.format.currentData() == 'jpeg')
        self._sync_overlays(save=False)
        self._update_actions()
        self.changed.emit()

    def _output_changed(self, *_args):
        if self.restoring:
            return
        self.quality.setEnabled(self.output_form.format.currentData() == 'jpeg')
        self._update_actions()
        self.changed.emit()

    def _start_preview(self):
        if self.closed or self.worker is not None or not self.pending:
            return
        self.pending = False
        if self.path is None:
            return
        worker = SplitWorker(self.path, self.options(), self.crop(), self.generation,
                             source=self.source.copy() if self.source is not None else None,
                             source_size=self.source_size, stamp=self.source_stamp, parent=self)
        self._start_worker(worker)

    def _start_worker(self, worker):
        self.worker = worker
        worker.finished.connect(self._finished)
        worker.progress.connect(self._progress)
        total = (worker.options.count if worker.page_index is None else 1) if worker.output else 0
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.show()
        self._update_actions()
        worker.start()

    def _progress(self, done, total):
        if self.sender() is self.worker and self.worker.generation == self.generation:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _finished(self):
        worker = self.sender()
        if worker is not self.worker:
            return
        self.worker = None
        current = not self.closed and worker.generation == self.generation
        result, worker.result = worker.result, None
        if worker.output is None and result is not None:
            if current and not worker.cancelled.is_set():
                self._release_images()
                self.source, self.source_size, self.canvas_image, crop, self.source_stamp = result
                self.crops[path_key(self.path)] = crop
                self._valid = True
                self._display(fit=self._fit_next)
                self._fit_next = False
                l, t, r, b = crop
                scale = (max(worker.options.size[0] / self.source_size[0], worker.options.size[1] / self.source_size[1])
                         if worker.options.fit == 'cover' else min(worker.options.size[0] / self.source_size[0], worker.options.size[1] / self.source_size[1]))
                if worker.options.fit == 'cover':
                    scale = worker.options.size[0] / ((r - l) * self.source_size[0])
                self.status.setText(f'预览就绪 · 输出将放大 {scale:.2f} 倍' if scale > 1.001 else '预览就绪，可导出整组或逐张预览中的当前张。')
            else:
                result[0].close()
                result[2].close()
        elif current and worker.output is not None and result is not None:
            label = f'已导出 {worker.options.count} 张' if worker.page_index is None else f'已导出第 {worker.page_index + 1} 张'
            self.status.setText(f'{label}：{result}')
        elif current and worker.error:
            self.status.setText(f'连屏切图失败：{worker.error}')
        elif current and not self.pending:
            self.status.setText('已取消。')
        self.progress.hide()
        worker.deleteLater()
        self._update_actions()
        if self.pending and not self.closed:
            self.timer.start(0)

    def _display(self, *, fit=False):
        if self.source is None or self.canvas_image is None:
            return
        try:
            options = self.options()
        except ValueError:
            return
        self.page_index = min(self.page_index, options.count - 1)
        single = self.single.isChecked()
        if single:
            with self.canvas_image.crop(slice_boxes(self.canvas_image.size, options.count)[self.page_index]) as tile:
                pixmap = pil_to_qpixmap(tile)
        else:
            if options.fit == 'cover' and 'A' in self.source.getbands():
                with Image.new('RGBA', self.source.size, options.background) as flattened:
                    flattened.alpha_composite(self.source)
                    pixmap = pil_to_qpixmap(flattened)
            else:
                pixmap = pil_to_qpixmap(self.source if options.fit == 'cover' else self.canvas_image)
        self.preview.set_source_pixmap(pixmap, preserve_view=not fit, preserve_scale=not fit)
        size = (options.width, options.height) if single else self.source_size if options.fit == 'cover' else options.size
        self.preview.set_original_size(*size)
        self.summary.setText(f'{self.path.name} · 每张 {options.width} × {options.height} px · 共 {options.count} 张')
        self.page_label.setText(f'{self.page_index + 1} / {options.count}')
        self.previous_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(self.page_index < options.count - 1)
        self._sync_overlays(save=False)
        self._update_actions()
        if fit:
            self.preview.canvas.fit_to_window()

    def _sync_overlays(self, *_args, save=True):
        if not hasattr(self, 'preview'):
            return
        canvas = self.preview.canvas
        cover = self.fit.currentData() == 'cover'
        single = self.single.isChecked()
        canvas.single_view = single
        canvas.split_count = self.count.value()
        canvas.show_lines, canvas.show_numbers = self.lines.isChecked(), self.numbers.isChecked()
        canvas.split_box = self.crop() if cover else (0., 0., 1., 1.)
        canvas.set_show_crop_effect(cover and not single)
        canvas.set_crop_effect_box(self.crop() if cover and not single else None)
        # The displayed proxy can have a fractionally different aspect after rounding.
        if self.source is not None and self.crop() is not None:
            l, t, r, b = self.crop()
            canvas.set_crop_ratio_constraint((r - l) * self.source.width / ((b - t) * self.source.height), False)
        canvas.set_crop_edit_mode(bool(self.source is not None and cover and not single and self.crop_tool.isChecked()))
        self.crop_tool.setEnabled(cover and not single)
        self.pager.setVisible(single)
        canvas.update()
        if save and not self.restoring:
            self.changed.emit()

    def _view_changed(self):
        if self.restoring:
            return
        self._drag_finished()
        self._display(fit=True)
        self._sync_overlays()
        self._update_actions()

    def _zoom_changed(self, value):
        if value is None:
            return
        with QSignalBlocker(self.zoom):
            self.zoom.setValue(round(value))

    def move_page(self, delta):
        if not self.single.isChecked() or not self._valid:
            return
        self.page_index = max(0, min(self.count.value() - 1, self.page_index + delta))
        self._display()
        self.changed.emit()

    def _drag_started(self):
        self.drag_before = self.crop()

    def _crop_changed(self, box):
        if self.path is None or self.source_size is None or self.restoring:
            return
        crop = normalized_crop(self.source_size, self.options(), box)
        self.crops[path_key(self.path)] = crop
        self.preview.canvas.set_crop_effect_box(crop)
        if self.drag_before is None:
            self._settings_changed()

    def _drag_finished(self):
        if self.drag_before is None:
            return
        previous, self.drag_before = self.drag_before, None
        if previous != self.crop():
            self._remember_crop(previous)
            self._settings_changed()

    def _remember_crop(self, previous):
        key = path_key(self.path)
        history = self.undo_stacks.setdefault(key, [])
        history.append(previous)
        del history[:-100]
        self.redo_stacks[key] = []

    def reset_crop(self):
        if self.source_size is None or self.exporting():
            return
        self._remember_crop(self.crop())
        self.crops[path_key(self.path)] = normalized_crop(self.source_size, self.options())
        self._settings_changed()

    def undo_crop(self, redo=False):
        if not self.path or self.exporting():
            return
        key = path_key(self.path)
        source, destination = (self.redo_stacks, self.undo_stacks) if redo else (self.undo_stacks, self.redo_stacks)
        history = source.get(key, [])
        if history:
            destination.setdefault(key, []).append(self.crop())
            self.crops[key] = history.pop()
            self._settings_changed()

    def _update_actions(self):
        exporting = self.exporting()
        ready = self._valid and self.path is not None and not self.closed
        self.page.setEnabled(not exporting and not self.closed)
        self.tools.setEnabled(not exporting and not self.closed)
        self.pager.setEnabled(not exporting and not self.closed)
        self.preview.canvas.setEnabled(ready and not exporting and not self.closed)
        error = self.output_form.validation_error()
        self.export_button.setEnabled(ready and not self.pending and self.worker is None and not error)
        self.export_button.setToolTip(error or '按从左到右顺序导出整组图片，不生成 XMP')
        self.export_current_button.setEnabled(self.export_button.isEnabled() and self.single.isChecked())
        self.export_current_button.setText(f'导出当前张（{self.page_index + 1}/{self.count.value()}）' if self.single.isChecked() else '导出当前张')
        self.export_current_button.setToolTip(error or ('导出逐张预览中显示的切片，不生成 XMP' if self.single.isChecked() else '请先切换到逐张预览，选择要导出的切片'))
        self.cancel_button.setEnabled(not self.closed and (self.worker is not None or self.pending))
        key = path_key(self.path) if self.path else ''
        self.undo_button.setEnabled(bool(self.undo_stacks.get(key)) and not exporting)
        self.redo_button.setEnabled(bool(self.redo_stacks.get(key)) and not exporting)
        self.reset_button.setEnabled(ready and not exporting)

    def pick_export(self, *, current=False):
        button = self.export_current_button if current else self.export_button
        if not button.isEnabled():
            return
        page_index = self.page_index if current else None
        raw = self.output_form.selected_options()
        options = ImageOutputOptions(**{key.removeprefix('split_'): value for key, value in raw.items()})
        if options.output_mode == 'ask':
            directory = QFileDialog.getExistingDirectory(self.page, '选择连屏输出目录', str(self.path.parent))
            if not directory:
                return
        else:
            try:
                directory = output_directory(self.path, options)
            except ValueError as exc:
                self.status.setText(str(exc))
                return
        self.start_export(Path(directory), page_index=page_index)

    def start_export(self, directory, *, page_index=None):
        if not self._valid or self.path is None or self.worker is not None or self.pending or self.closed:
            return
        try:
            if source_stamp(self.path) != self.source_stamp:
                self.set_source(self.path)
                self.status.setText('源图已更新，正在重新生成预览；完成后可重新导出。')
                return
        except OSError as exc:
            self._valid = False
            self.status.setText(f'无法读取源图：{exc}')
            self._update_actions()
            return
        self.preview.canvas._finish_crop_resolution_drag()
        if self.pending:
            return
        self.status.setText('正在导出整组图片…' if page_index is None else f'正在导出第 {page_index + 1} 张…')
        self._start_worker(SplitWorker(self.path, self.options(), self.crop(), self.generation,
                                      output=Path(directory), page_index=page_index, parent=self))

    def cancel(self):
        self.timer.stop()
        self.pending = False
        if self.worker is not None:
            self.worker.cancelled.set()
        self._update_actions()

    def state(self, workspace_path):
        # Persist even invalid/incomplete dimension combinations so drafts are not lost.
        options = dict(count=self.count.value(), width=self.width.value(), height=self.height.value(),
                       fit=self.fit.currentData(), background=self.background.value(),
                       format=self.output_form.format.currentData(), quality=self.quality.value())
        return dict(options=options, ratio=self.ratio.currentData(), output=self.output_form.selected_options(),
                    path=serialize_workspace_path(self.path, workspace_path=workspace_path) if self.path else None,
                    crops=[dict(path=serialize_workspace_path(self.crop_paths.get(key, Path(key)), workspace_path=workspace_path), box=box)
                           for key, box in self.crops.items()],
                    view='single' if self.single.isChecked() else 'whole', page=self.page_index,
                    lines=self.lines.isChecked(), numbers=self.numbers.isChecked(), crop_tool=self.crop_tool.isChecked())

    def restore(self, state, workspace_path):
        state = state if isinstance(state, dict) else {}
        self.cancel()
        self.generation += 1
        self.restoring = True
        self._valid = False
        self._release_images()
        self.preview.set_source_pixmap(QPixmap())
        try:
            raw = state.get('options')
            raw = raw if isinstance(raw, dict) else {}
            try:
                options = SplitOptions.from_dict(raw)
            except (TypeError, ValueError):
                options = SplitOptions()
            self.count.setValue(options.count)
            self._set_dimensions(options.width, options.height)
            for key in ('count', 'width', 'height'):
                value = raw.get(key)
                widget = getattr(self, key)
                if type(value) is int and widget.minimum() <= value <= widget.maximum():
                    widget.setValue(value)
            self.ratio.setCurrentIndex(max(0, self.ratio.findData(state.get('ratio', '3:4'))))
            self.fit.setCurrentIndex(self.fit.findData(options.fit))
            self.background.set_value(options.background)
            self.quality.setValue(options.quality)
            output = state.get('output')
            output = output if isinstance(output, dict) else {}
            self.output_form.mode.setCurrentIndex(max(0, self.output_form.mode.findData(output.get('split_output_mode', 'ask'))))
            self.output_form.subdir.setText(str(output.get('split_subdir', '连屏切图')))
            self.output_form.directory.setText(str(output.get('split_output_directory', '')))
            self.output_form.format.setCurrentIndex(max(0, self.output_form.format.findData(options.format)))
            self.crops = {}
            self.crop_paths = {}
            records = state.get('crops')
            for item in records if isinstance(records, list) else []:
                if not isinstance(item, dict):
                    continue
                path = resolve_workspace_path(item.get('path'), workspace_path=workspace_path)
                box = item.get('box')
                try:
                    normalized_crop((1, 1), options, box)
                    if path and box is not None:
                        self.crops[path_key(path)] = tuple(map(float, box))
                        self.crop_paths[path_key(path)] = path
                except (ValueError, TypeError):
                    continue
            self.undo_stacks, self.redo_stacks = {}, {}
            self.path = resolve_workspace_path(state.get('path'), workspace_path=workspace_path)
            if self.path is not None:
                self.crop_paths[path_key(self.path)] = self.path
            self.single.setChecked(state.get('view') == 'single')
            self.whole.setChecked(state.get('view') != 'single')
            try:
                self.page_index = max(0, min(options.count - 1, int(state.get('page', 0))))
            except (ValueError, TypeError):
                self.page_index = 0
            for key in ('lines', 'numbers', 'crop_tool'):
                getattr(self, key).setChecked(bool(state.get(key, True)))
            self._refresh_resolution()
            self._fit_next = True
        finally:
            self.restoring = False
        self._settings_changed()

    def shutdown(self):
        if not self.closed:
            self.closed = True
            self.generation += 1
            self.cancel()
            self._release_images()
        return self.worker is None
