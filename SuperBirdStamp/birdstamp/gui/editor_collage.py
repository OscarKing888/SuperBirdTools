"""独立拼图页：参数与素材顺序、后台渲染、共用 Overlay 编辑画布。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from threading import Event
import uuid

from PyQt6.QtCore import QObject, QSignalBlocker, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from app_common.collapsible_section import CollapsibleSection
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from app_common.preview_canvas import PreviewWithStatusBar
from birdstamp.collage import CollageCancelled, CollageOptions, PRESETS, MAX_EDGE, MAX_IMAGES, export_collage, render_collage
from birdstamp.overlays.model import document
from birdstamp.overlays.render import build_scene, compose_scene
from birdstamp.workspace import resolve_workspace_path, serialize_workspace_path
from .color_editor import ColorEditor
from .editor_preview_canvas import EditorPreviewCanvas
from .editor_utils import pil_to_qpixmap
from .overlay_edit import OverlaySession
from .overlay_editor_dock import OverlayEditorDock
from .overlay_panel import OverlayPanel


ASPECT_RATIOS = {
    '3:4': '3:4 · 小红书竖版', '1:1': '1:1 · 方形', '4:3': '4:3 · 横版',
    '9:16': '9:16 · 竖屏', '16:9': '16:9 · 横屏', '2:3': '2:3 · 竖版',
    '3:2': '3:2 · 横版', 'custom': '自由比例',
}
RESOLUTIONS = ((1080, '标准'), (1440, '高清'), (2160, '超清'))


class CollageWorker(QThread):
    progress = pyqtSignal(int, int)

    def __init__(self, paths, options, generation, *, output=None, overlays=None, parent=None):
        super().__init__(parent)
        self.paths, self.options, self.generation = tuple(paths), options, generation
        self.output, self.overlays = output, deepcopy(overlays)
        self.cancelled = Event()
        self.result = None
        self.error = None

    def run(self):
        try:
            kwargs = dict(cancel=self.cancelled.is_set, progress=self.progress.emit)
            if self.output is None:
                self.result = render_collage(self.paths, self.options, preview_edge=1600, **kwargs)
            else:
                self.result = export_collage(self.paths, self.options, self.output, overlays=self.overlays, **kwargs)
        except CollageCancelled:
            pass
        except Exception as exc:
            self.error = str(exc)


class CollageEditor(QObject):
    changed = pyqtSignal()

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.paths = []
        self.worker = None
        self.generation = 0
        self.pending = False
        self.closed = False
        self.restoring = False
        self._legacy_cell_size = None
        self.base = self.plan = None
        self.last_output = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self._start_preview)
        self._build_page()
        self._build_preview()
        self._build_actions()
        self.changed.connect(editor._schedule_workspace_autosave)
        self._update_actions()

    @staticmethod
    def _button(label, slot, layout, *position):
        button = QPushButton(label)
        button.clicked.connect(slot)
        layout.addWidget(button, *position)
        return button

    def _build_page(self):
        self.page = QWidget()
        root = QVBoxLayout(self.page)
        root.setContentsMargins(0, 6, 0, 0)
        self.layout_group = CollapsibleSection('拼图排版')
        form = QFormLayout(self.layout_group.body)
        self.layout_form = form
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.preset = QComboBox()
        for key, (label, _, _) in PRESETS.items():
            self.preset.addItem(label, key)
        form.addRow('排版', self.preset)
        self.columns, self.rows = QSpinBox(), QSpinBox()
        for label, widget, suffix in [('横向列数', self.columns, ' 列'), ('纵向行数', self.rows, ' 行')]:
            widget.setRange(1, MAX_IMAGES)
            widget.setValue(2)
            widget.setSuffix(suffix)
            widget.setKeyboardTracking(False)
            widget.setToolTip(f'按列 × 行均分画布，最多 {MAX_IMAGES} 格；照片从左到右、从上到下排列。')
            form.addRow(label, widget)
        self._sync_grid_controls()
        self.aspect_ratio = QComboBox()
        for key, label in ASPECT_RATIOS.items():
            self.aspect_ratio.addItem(label, key)
        form.addRow('成图比例', self.aspect_ratio)
        self.resolution = QComboBox()
        form.addRow('分辨率', self.resolution)
        self.canvas_width, self.canvas_height = QSpinBox(), QSpinBox()
        for label, widget, value in [('宽度', self.canvas_width, 1080), ('高度', self.canvas_height, 1440)]:
            widget.setRange(1, MAX_EDGE)
            widget.setValue(value)
            widget.setSuffix(' px')
            widget.setKeyboardTracking(False)
            widget.setToolTip('最终导出尺寸。固定比例时自动联动另一边；自由比例可分别修改宽高。')
            form.addRow(label, widget)
        self._refresh_resolutions()
        self.cell_summary = QLabel('单格尺寸自动计算')
        self.cell_summary.setWordWrap(True)
        form.addRow('单元格', self.cell_summary)
        self.gap = QSpinBox()
        self.margin = QSpinBox()
        for label, widget in [('间距', self.gap), ('外边距', self.margin)]:
            widget.setRange(0, 256)
            widget.setValue(12)
            widget.setSuffix(' px')
            form.addRow(label, widget)
        self.fit = QComboBox()
        self.fit.addItem('完整显示（留边）', 'contain')
        self.fit.addItem('居中裁满', 'cover')
        form.addRow('照片填充', self.fit)
        self.background = ColorEditor('#FFFFFF')
        form.addRow('背景', self.background)
        root.addWidget(self.layout_group)
        self.sources_group = CollapsibleSection('拼图照片')
        body = QVBoxLayout(self.sources_group.body)
        body.setContentsMargins(0, 0, 0, 0)
        row = QGridLayout()
        self._button('加入选中照片', lambda: self.add_paths(editor_paths(self.editor, False)), row, 0, 0)
        self._button('加入全部照片', lambda: self.add_paths(editor_paths(self.editor, True)), row, 0, 1)
        self._button('添加文件…', self.pick_files, row, 1, 0)
        self._button('移除选中', self.remove_selected, row, 1, 1)
        body.addLayout(row)
        self.files = QListWidget()
        self.files.setMinimumHeight(100)
        self.files.setMaximumHeight(210)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        body.addWidget(self.files)
        row = QHBoxLayout()
        self._button('上移', lambda: self.move_selected(-1), row)
        self._button('下移', lambda: self.move_selected(1), row)
        self._button('清空', self.clear, row)
        body.addLayout(row)
        hint = QLabel('按列表顺序排版，单格适配成图尺寸；宫格不足时留空。\n使用源照片，不套用逐图模板或去抖动结果。')
        hint.setWordWrap(True)
        body.addWidget(hint)
        root.addWidget(self.sources_group)
        root.addStretch(1)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.fit.currentIndexChanged.connect(self._settings_changed)
        self.background.colorChanged.connect(self._settings_changed)
        self.aspect_ratio.currentIndexChanged.connect(self._ratio_changed)
        self.resolution.currentIndexChanged.connect(self._resolution_changed)
        self.canvas_width.valueChanged.connect(lambda: self._dimension_changed(True))
        self.canvas_height.valueChanged.connect(lambda: self._dimension_changed(False))
        for widget in (self.gap, self.margin, self.columns, self.rows):
            widget.valueChanged.connect(self._settings_changed)

    def _sync_grid_controls(self):
        custom = self.preset.currentData() == 'custom'
        for widget in (self.columns, self.rows):
            self.layout_form.setRowVisible(widget, custom)

    def _preset_changed(self):
        self._sync_grid_controls()
        self._settings_changed()

    def _ratio_units(self):
        key = self.aspect_ratio.currentData()
        if key == 'custom':
            return self.canvas_width.value(), self.canvas_height.value()
        return tuple(map(int, key.split(':')))

    def _resolution_size(self, short_edge):
        width, height = self._ratio_units()
        factor = min(short_edge / min(width, height), MAX_EDGE / max(width, height))
        return max(1, round(width * factor)), max(1, round(height * factor))

    def _set_canvas_size(self, width, height):
        with QSignalBlocker(self.canvas_width), QSignalBlocker(self.canvas_height):
            self.canvas_width.setValue(width)
            self.canvas_height.setValue(height)

    def _refresh_resolutions(self):
        with QSignalBlocker(self.resolution):
            self.resolution.clear()
            current = (self.canvas_width.value(), self.canvas_height.value())
            selected = len(RESOLUTIONS)
            for index, (edge, label) in enumerate(RESOLUTIONS):
                size = self._resolution_size(edge)
                self.resolution.addItem(f'{label} · {size[0]} × {size[1]}', edge)
                if size == current:
                    selected = index
            self.resolution.addItem('自定义', None)
            self.resolution.setCurrentIndex(selected)

    def _ratio_changed(self):
        if self.restoring:
            return
        edge = self.resolution.currentData() or min(self.canvas_width.value(), self.canvas_height.value())
        self._set_canvas_size(*self._resolution_size(edge))
        self._refresh_resolutions()
        self._settings_changed()

    def _resolution_changed(self):
        if self.restoring or self.resolution.currentData() is None:
            return
        self._set_canvas_size(*self._resolution_size(self.resolution.currentData()))
        self._settings_changed()

    def _dimension_changed(self, width_changed):
        if self.restoring:
            return
        width, height = self.canvas_width.value(), self.canvas_height.value()
        if self.aspect_ratio.currentData() != 'custom':
            rw, rh = self._ratio_units()
            if width_changed:
                height = max(1, min(MAX_EDGE, round(width * rh / rw)))
                width = max(1, round(height * rw / rh))
            else:
                width = max(1, min(MAX_EDGE, round(height * rw / rh)))
                height = max(1, round(width * rh / rw))
            self._set_canvas_size(width, height)
        self._refresh_resolutions()
        self._settings_changed()

    def _build_preview(self):
        preview = QWidget()
        root = QVBoxLayout(preview)
        self.summary = QLabel('添加照片开始拼图')
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        row = QHBoxLayout()
        self.add_overlay_button = self._button('添加图像 overlay…', self.add_image_overlay, row)
        self.edit_overlay_button = self._button('编辑叠加层…', self.activate_overlay, row)
        self._button('适应窗口', lambda: self.preview.canvas.fit_to_window(), row)
        row.addStretch(1)
        root.addLayout(row)
        self.preview = PreviewWithStatusBar(canvas=EditorPreviewCanvas())
        root.addWidget(self.preview, 1)
        self.panel = OverlayPanel()
        self.panel.set_document({'overlays': []}, 'collage:' + uuid.uuid4().hex)
        self.session = OverlaySession(self.preview.canvas, self.panel)
        self.panel.changed.connect(self._overlay_changed)
        self.panel.activateRequested.connect(self.activate_overlay)
        self.dock = OverlayEditorDock(self.editor, self.panel, QHBoxLayout())
        self.dock.setObjectName('BirdStampCollageOverlayDock')
        self.dock.context_label.setText('拼图叠加层 · 拖动移动，角手柄缩放，顶部手柄旋转')
        self.preview_host = self.dock.create_host(preview)
        self.preview_host.setObjectName('BirdStampCollagePreviewHost')

    def _build_actions(self):
        self.actions = QWidget()
        layout = QVBoxLayout(self.actions)
        self.status = QLabel('请选择照片并加入拼图。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.export_button = self._button('导出拼图…', self.pick_export, row)
        self.cancel_button = self._button('取消', self.cancel, row)
        layout.addLayout(row)

    def options(self):
        return CollageOptions(preset=self.preset.currentData(), cell_size=self._legacy_cell_size,
                              gap=self.gap.value(), margin=self.margin.value(), background=self.background.value(),
                              fit=self.fit.currentData(), width=self.canvas_width.value(), height=self.canvas_height.value(),
                              columns=self.columns.value(), rows=self.rows.value())

    def _refresh_files(self, selected=()):
        self.files.clear()
        for index, path in enumerate(self.paths):
            item = QListWidgetItem(f'{index + 1}. {path.name}')
            item.setToolTip(str(path))
            self.files.addItem(item)
            item.setSelected(index in selected)
        self.sources_group.setTitle(f'拼图照片 · {len(self.paths)} 张')

    def add_paths(self, paths):
        # 允许同一照片多次加入，作为独立格子；一次加入有明确总量边界。
        paths = [Path(p) for p in paths]
        if len(self.paths) + len(paths) > MAX_IMAGES:
            QMessageBox.warning(self.page, '照片过多', f'一张拼图最多使用 {MAX_IMAGES} 张照片。')
            return
        if not paths:
            self.status.setText('请先在照片列表中选中照片，或使用“添加文件”。')
            return
        self.paths.extend(paths)
        self._refresh_files()
        self._settings_changed()

    def pick_files(self):
        pattern = ' '.join('*' + ext for ext in sorted(SUPPORTED_IMAGE_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self.page, '添加拼图照片', '', f'图像 ({pattern})')
        if paths:
            self.add_paths(paths)

    def remove_selected(self):
        selected = {self.files.row(i) for i in self.files.selectedItems()}
        self.paths = [p for i, p in enumerate(self.paths) if i not in selected]
        self._refresh_files()
        self._settings_changed()

    def move_selected(self, direction):
        from birdstamp.photo_order import moved_photo_rows
        selected = {self.files.row(i) for i in self.files.selectedItems()}
        order = moved_photo_rows(len(self.paths), selected, direction)
        old = self.paths
        self.paths = [old[i] for i in order]
        self._refresh_files({i for i, original in enumerate(order) if original in selected})
        self._settings_changed()

    def clear(self):
        self.paths = []
        self._refresh_files()
        self._settings_changed()

    def _settings_changed(self, *_args, preserve_legacy=False):
        if self.restoring or self.closed:
            return
        self.generation += 1
        self.pending = True
        if not preserve_legacy:
            self._legacy_cell_size = None
        self.cell_summary.setText('正在计算…' if self.paths else '单格尺寸自动计算')
        self.session.cancel()
        self.session.clear()
        if self.base is not None:
            self.base.close()
        self.base = self.plan = None
        if self.worker is not None:
            self.worker.cancelled.set()
        self.timer.start()
        self.status.setText('正在更新拼图…' if self.paths else '请选择照片并加入拼图。')
        self.summary.setText('正在更新拼图…' if self.paths else '添加照片开始拼图')
        self._update_actions()
        self.changed.emit()

    def _start_preview(self):
        if self.closed or self.worker is not None:
            return
        self.pending = False
        if not self.paths:
            from PyQt6.QtGui import QPixmap
            self.preview.set_source_pixmap(QPixmap())
            self.summary.setText('添加照片开始拼图')
            self.preview.set_original_size(None, None)
            self._update_actions()
            return
        self._start_worker(CollageWorker(self.paths, self.options(), self.generation, parent=self))

    def _start_worker(self, worker):
        self.worker = worker
        worker.progress.connect(self._progress)
        worker.finished.connect(self._finished)
        self.progress.setRange(0, len(worker.paths))
        self.progress.setValue(0)
        self.progress.show()
        self.page.setEnabled(worker.output is None)
        self._update_actions()
        worker.start()

    def _progress(self, done, total):
        if self.sender() is self.worker and self.worker.generation == self.generation and not self.closed:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _finished(self):
        worker = self.sender()
        if worker is not self.worker:
            return
        self.worker = None
        current = not self.closed and worker.generation == self.generation
        result = worker.result
        worker.result = None
        if worker.output is None and result is not None:
            if current and not worker.cancelled.is_set():
                if self._legacy_cell_size is not None:
                    # 旧工作区在后台算出原画布后迁移，归一化 Overlay 坐标随画布保留。
                    self._legacy_cell_size = None
                    with QSignalBlocker(self.aspect_ratio):
                        self.aspect_ratio.setCurrentIndex(self.aspect_ratio.findData('custom'))
                    self._set_canvas_size(*result[1].size)
                    self._refresh_resolutions()
                    result[0].close()
                    self._settings_changed()
                else:
                    self.base, self.plan = result
                    sizes = {(right - x, bottom - y) for x, y, right, bottom in self.plan.boxes}
                    if len(sizes) == 1:
                        width, height = next(iter(sizes))
                        self.cell_summary.setText(f'{width} × {height} px（自动）')
                    else:
                        widths, heights = zip(*sizes)
                        self.cell_summary.setText(f'{min(widths)}–{max(widths)} × {min(heights)}–{max(heights)} px（自动）')
                    self._render_overlays(fit=True)
                    label = PRESETS[worker.options.preset][0]
                    if worker.options.preset == 'custom':
                        label += f' · {worker.options.columns} 列 × {worker.options.rows} 行'
                    self.summary.setText(f'{label} · {len(worker.paths)} 张 · {self.plan.size[0]} × {self.plan.size[1]} px')
                    self.status.setText('拼图已更新，可添加 overlay 或导出。')
            else:
                result[0].close()
        elif current and result is not None:
            self.last_output = Path(result)
            self.status.setText(f'已导出：{result}')
        elif current and worker.error:
            self.status.setText(f'拼图失败：{worker.error}')
            if worker.output is None:
                self.summary.setText('拼图未生成，请检查照片与排版参数')
                self.cell_summary.setText('无法排版，请调整参数')
        elif current and not self.pending:
            self.status.setText('已取消。调整参数可重新生成拼图。')
        self.progress.hide()
        self.page.setEnabled(True)
        worker.deleteLater()
        self._update_actions()
        if self.pending and not self.closed:
            self.timer.start(0)

    def _render_overlays(self, *, fit=False):
        if self.base is None or self.closed:
            return
        try:
            scene = build_scene(self.panel.doc, self.plan.size)
            try:
                rendered = compose_scene(self.base, scene)
            except BaseException:
                scene.close()
                raise
            self.session.capture(self.base, scene)
            try:
                self.preview.set_source_pixmap(pil_to_qpixmap(rendered), preserve_view=not fit, preserve_scale=not fit)
            finally:
                rendered.close()
            self.preview.set_original_size(*self.plan.size)
            if fit:
                self.preview.canvas.fit_to_window()
        except Exception as exc:
            self.status.setText(f'叠加层预览失败：{exc}')

    def _overlay_changed(self, *_args):
        if self.closed or self.restoring:
            return
        self._render_overlays()
        self.changed.emit()

    def activate_overlay(self):
        if self.closed:
            return
        self.dock.reveal()
        self.preview.canvas.set_edit_mode('overlay')
        self.preview.canvas.setFocus()

    def add_image_overlay(self):
        self.activate_overlay()
        self.panel.import_file()

    def _update_actions(self):
        exporting = self.worker is not None and self.worker.output is not None
        ready = self.base is not None and not self.closed
        self.export_button.setEnabled(ready and self.worker is None and not self.pending)
        self.cancel_button.setEnabled(self.worker is not None or self.pending)
        self.add_overlay_button.setEnabled(ready and not exporting)
        self.edit_overlay_button.setEnabled(ready and not exporting)
        self.panel.setEnabled(ready and not exporting)
        self.preview.canvas.setEnabled(not exporting and not self.closed)

    def pick_export(self):
        if self.worker is not None or self.pending or self.base is None or self.closed:
            return
        self.panel.flush_text()
        path, selected_filter = QFileDialog.getSaveFileName(self.page, '导出拼图', str(self.last_output or Path('拼图.png')),
                                                          'PNG (*.png);;JPEG (*.jpg *.jpeg)')
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix('.jpg' if selected_filter.startswith('JPEG') else '.png')
            if target.exists() and QMessageBox.question(self.page, '覆盖文件', f'{target.name} 已存在，是否覆盖？') != QMessageBox.StandardButton.Yes:
                return
        self.start_export(target)

    def start_export(self, target):
        if self.worker is not None or self.pending or self.base is None or self.closed:
            return
        self.panel.flush_text()
        self.session.cancel()
        self.status.setText('正在导出拼图…')
        self._start_worker(CollageWorker(self.paths, self.options(), self.generation,
                                        output=Path(target), overlays=self.panel.doc, parent=self))

    def cancel(self):
        self.timer.stop()
        self.pending = False
        if self.worker is not None:
            self.worker.cancelled.set()
        self._update_actions()

    def state(self, workspace_path):
        self.panel.flush_text()
        return dict(options=asdict(self.options()),
                    aspect_ratio=self.aspect_ratio.currentData(),
                    paths=[serialize_workspace_path(p, workspace_path=workspace_path) for p in self.paths],
                    overlays=deepcopy(self.panel.doc))

    def restore(self, state, workspace_path):
        state = state if isinstance(state, dict) else {}
        self.restoring = True
        try:
            try:
                raw = state.get('options') or {}
                options = CollageOptions(**{k: v for k, v in raw.items() if k in asdict(CollageOptions())})
            except (TypeError, ValueError, AttributeError):
                options = CollageOptions()
            self.preset.setCurrentIndex(self.preset.findData(options.preset))
            self.columns.setValue(options.columns)
            self.rows.setValue(options.rows)
            self._legacy_cell_size = options.cell_size
            ratio = state.get('aspect_ratio', '3:4' if options.width * 4 == options.height * 3 else 'custom')
            ratio = ratio if isinstance(ratio, str) and ratio in ASPECT_RATIOS else 'custom'
            self.aspect_ratio.setCurrentIndex(self.aspect_ratio.findData(ratio))
            self._set_canvas_size(options.width, options.height)
            self._refresh_resolutions()
            self.gap.setValue(options.gap)
            self.margin.setValue(options.margin)
            self.background.set_value(options.background)
            fit = 'contain' if options.cell_size is not None and options.preset in ('vertical', 'horizontal') else options.fit
            self.fit.setCurrentIndex(self.fit.findData(fit))
            self.paths = [p for raw in (state.get('paths') or [])[:MAX_IMAGES]
                          if (p := resolve_workspace_path(raw, workspace_path=workspace_path)) is not None]
            self._refresh_files()
            self.panel.set_document(document(state.get('overlays') or {'overlays': []}), 'collage:' + uuid.uuid4().hex)
        finally:
            self.restoring = False
        self._settings_changed(preserve_legacy=True)

    def shutdown(self):
        if not self.closed:
            self.panel.flush_text()
            self.closed = True
            self.generation += 1
            self.panel._context_generation += 1
            self.cancel()
            self.session.cancel()
            self.session.clear()
            if self.base is not None:
                self.base.close()
            self.base = self.plan = None
            self.dock.hide()
        # 全局池素材导入也须交付完毕；窗口保留接收者直到实际任务完成。
        return self.worker is None and not self.panel._jobs


def editor_paths(editor, all_photos):
    return editor._list_photo_paths() if all_photos else editor._selected_photo_paths()
