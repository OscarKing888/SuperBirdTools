from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from time import monotonic

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QTabBar, QCheckBox, QComboBox, QFileDialog, QListWidget, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt, QTimer

from birdstamp.export_stage.sequence_preview import sequence_input_key
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from . import editor_core, editor_options
from .edit_modes import EDIT_MODE_NONE, EDIT_MODE_REFERENCE_REGION
from .editor_preview_canvas import EditorPreviewOverlayState
from .editor_sequence_preview_worker import EditorSequencePreviewWorker, EditorSequenceExportWorker
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from .editor_utils import pil_to_qpixmap
from .editor_utils import path_key


class _BirdStampDejitterMixin:
    """去抖动页/共享画布协调；计算与作业结果由无窗口核心持有。"""

    def _init_dejitter_preview(self):
        self._sequence_worker = None
        self._sequence_exporting = False
        self._sequence_epoch = 0
        self._sequence_shutdown = False
        self._sequence_preview = None
        self._sequence_frames = OrderedDict()
        self._sequence_quick_frames = {}
        self._sequence_validated_at = 0
        self._sequence_upgrade_timer = QTimer(self)
        self._sequence_upgrade_timer.setSingleShot(True)
        self._sequence_upgrade_timer.setInterval(120)
        self._sequence_upgrade_timer.timeout.connect(self._upgrade_sequence_frame)
        self._sequence_frame_bytes = 0
        self._sequence_pending_path = None
        self._sequence_message = '只需在一张参考图框选一次，自动匹配整组照片。'
        self._ordinary_edit_mode = EDIT_MODE_NONE
        self._dejitter_edit_mode = EDIT_MODE_REFERENCE_REGION
        self._last_dejitter_tab = False
        self._dejitter_view = 'edit'
        self._dejitter_edit_source = None
        self._dejitter_edit_pixmap = None

    def _build_dejitter_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        intro = QLabel('只在一张参考图框选 → 自动匹配整组 → 导出全部')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        reference = QGroupBox('参考区对齐')
        form = QVBoxLayout(reference)
        form.setContentsMargins(10, 24, 10, 12)
        self.dejitter_reference_check = QCheckBox('启用参考区去抖动', reference)
        self.dejitter_reference_check.setToolTip('框选后自动启用；独立处理原图，不读取模板裁切。')
        self.dejitter_reference_check.hide()
        self.dejitter_reference_status = QLabel('尚未选择参考区')
        self.dejitter_reference_status.setWordWrap(True)
        form.addWidget(self.dejitter_reference_status)
        buttons = QHBoxLayout()
        self.dejitter_edit_reference_btn = QPushButton('编辑参考图')
        self.dejitter_edit_reference_btn.clicked.connect(self._on_edit_reference_photo)
        self.dejitter_draw_btn = QPushButton('框选 / 追加选区')
        self.dejitter_draw_btn.clicked.connect(self._on_dejitter_draw)
        buttons.addWidget(self.dejitter_edit_reference_btn)
        buttons.addWidget(self.dejitter_draw_btn)
        form.addLayout(buttons)
        self.dejitter_region_list = QListWidget()
        self.dejitter_region_list.setMaximumHeight(110)
        self.dejitter_region_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        form.addWidget(self.dejitter_region_list)
        self.dejitter_delete_region_btn = QPushButton('删除选中选区')
        self.dejitter_delete_region_btn.clicked.connect(self._on_delete_dejitter_regions)
        form.addWidget(self.dejitter_delete_region_btn)
        self.dejitter_region_list.itemSelectionChanged.connect(self._update_dejitter_controls)
        strength = QHBoxLayout()
        strength.addWidget(QLabel('补偿强度'))
        self.dejitter_reference_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.dejitter_reference_strength_slider.setRange(0, 100)
        self.dejitter_reference_strength_slider.setValue(editor_options.DEJITTER_REFERENCE_STRENGTH)
        self.dejitter_reference_value_label = QLabel('100%')
        strength.addWidget(self.dejitter_reference_strength_slider, 1)
        strength.addWidget(self.dejitter_reference_value_label)
        form.addLayout(strength)
        hint = QLabel('Shift 追加；八个手柄调节大小；右键框内删除该区，也可从列表多选删除。')
        hint.setWordWrap(True)
        form.addWidget(hint)
        layout.addWidget(reference)

        self.dejitter_preprocess_btn = QPushButton('分析并预览成片')
        self.dejitter_preprocess_btn.clicked.connect(self._on_reference_preprocess_clicked)
        layout.addWidget(self.dejitter_preprocess_btn)
        self.dejitter_effective_status = QLabel()
        self.dejitter_effective_status.setWordWrap(True)
        layout.addWidget(self.dejitter_effective_status)
        self.dejitter_tracking_status = QLabel()
        self.dejitter_tracking_status.setWordWrap(True)
        layout.addWidget(self.dejitter_tracking_status)
        output = QHBoxLayout()
        self.dejitter_output_format = QComboBox()
        formats = [(suffix, label) for suffix, label in editor_options.OUTPUT_FORMAT_OPTIONS
                   if suffix in {'png', 'jpg', 'jpeg'}]
        for suffix, label in formats or [('png', 'PNG'), ('jpg', 'JPG')]:
            self.dejitter_output_format.addItem(label, 'jpg' if suffix == 'jpeg' else suffix)
        self.dejitter_export_btn = QPushButton('去抖动导出全部')
        self.dejitter_export_btn.clicked.connect(self._on_dejitter_export_all)
        output.addWidget(self.dejitter_output_format)
        output.addWidget(self.dejitter_export_btn, 1)
        layout.addLayout(output)
        note = QLabel('自动保留对齐后整组共同覆盖的最大矩形，无需选择裁切比例。独立输出原图对齐结果，不叠加模板或文字；参考线、焦点和鸟体框仅用于预览。')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self.dejitter_reference_check.toggled.connect(self._on_dejitter_options_changed)
        self.dejitter_reference_strength_slider.valueChanged.connect(self._on_dejitter_options_changed)
        return page

    def _build_dejitter_view_bar(self):
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.dejitter_view_tabs = QTabBar()
        self.dejitter_view_tabs.setExpanding(False)
        self.dejitter_view_tabs.addTab('编辑构图')
        self.dejitter_view_tabs.addTab('成片预览')
        self.dejitter_view_tabs.currentChanged.connect(
            lambda index: self._set_dejitter_view('result' if index else 'edit'))
        row.addWidget(self.dejitter_view_tabs)
        row.addStretch(1)
        bar.setVisible(False)
        self.dejitter_view_bar = bar
        return bar

    def _dejitter_tab_active(self):
        tabs = getattr(self, 'export_tabs', None)
        return tabs is not None and tabs.currentWidget() is self.dejitter_page

    def _sequence_result_mode(self):
        return self._dejitter_tab_active() and self._dejitter_view == 'result'

    def _on_export_tab_changed(self, _index):
        if not hasattr(self, 'dejitter_view_bar'):
            return
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.stop(commit=False)
        active = self._dejitter_tab_active()
        if active != self._last_dejitter_tab:
            if active:
                self._ordinary_edit_mode = self._current_edit_mode_id()
                self._set_edit_mode_button_checked(self._dejitter_edit_mode)
            else:
                self._dejitter_edit_mode = self._current_edit_mode_id()
                self._set_edit_mode_button_checked(self._ordinary_edit_mode)
        self._edit_mode_buttons.get('crop_adjust', self._edit_mode_buttons[EDIT_MODE_NONE]).setEnabled(not active)
        self._last_dejitter_tab = active
        self.dejitter_view_bar.setVisible(active)
        self._update_dejitter_controls()
        self._restore_selected_preview_source()
        self._refresh_preview_label(preserve_view=True)
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.sync()

    def _set_dejitter_view(self, view):
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.stop(commit=False)
        self._dejitter_view = view
        if view == 'result':
            self._cancel_preview_decode()
            self._cancel_async_bird_detect()
            self._preview_debounce_timer.stop()
        self.dejitter_view_tabs.blockSignals(True)
        self.dejitter_view_tabs.setCurrentIndex(1 if view == 'result' else 0)
        self.dejitter_view_tabs.blockSignals(False)
        self._restore_selected_preview_source()
        self._refresh_preview_label(reset_view=True)
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.sync()

    def _restore_selected_preview_source(self):
        if not self._sequence_result_mode() and self.current_source_image is None:
            item = self.photo_list.currentItem()
            if item is not None:
                self._on_photo_selected(item, None)

    def _sequence_fast_preview_active(self):
        return hasattr(self, 'sequence_transport') and self.sequence_transport.active

    def _select_sequence_preview(self, path):
        # 成片切图不进入模板渲染、元数据查询或识别管线。
        if not self._sequence_result_mode() and not (
                self._dejitter_tab_active() and self._sequence_fast_preview_active()):
            return False
        transport = self.sequence_transport
        if transport.active and not transport.selecting:
            transport.stop(commit=False)
        self._cancel_preview_decode()
        self._cancel_async_bird_detect()
        self._preview_debounce_timer.stop()
        item = self._find_photo_item_by_path(path)
        if not transport.active and item is not None:
            self._begin_photo_selection(path, item, preserve_preview_view=True)
        else:
            self.current_path = path
            if self.current_source_image is not None:
                self.current_source_image.close()
            self.current_source_image = None
            self.current_source_full_size = None
            self.current_raw_metadata = self._metadata_snapshot_for_selection(path)
            self.current_file_label.setText(f'当前照片: {path}')
        transport.sync()
        self._refresh_preview_label(preserve_view=True)
        return True

    def _validate_sequence_preview(self):
        sequence = self._sequence_preview
        if sequence is None:
            return False
        if (tuple(sequence.jobs) != tuple(path_key(path) for path in self._list_photo_paths())
                or not sequence.files_current()):
            self._invalidate_sequence_preview()
            return False
        self._sequence_validated_at = monotonic()
        return True

    def _upgrade_sequence_frame(self):
        if self._sequence_shutdown or self._sequence_fast_preview_active() or not self._sequence_result_mode():
            return
        key = path_key(self.current_path) if self.current_path else ''
        if not self._sequence_preview or key not in self._sequence_preview.jobs or key in self._sequence_frames:
            return
        if self._sequence_worker is None:
            self._launch_sequence_worker()
        else:
            self._sequence_pending_path = self.current_path

    def _on_dejitter_draw(self):
        self._set_dejitter_view('edit')
        if self._dejitter_reference_source and not self._reference_regions_editable():
            self._on_edit_reference_photo()
        else:
            self._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
            self._refresh_preview_label(preserve_view=True)

    def _invalidate_sequence_preview(self, *, shutdown=False):
        self._sequence_upgrade_timer.stop()
        self._sequence_quick_frames.clear()
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.set_frames(None, {})
        self._sequence_epoch += 1
        self._sequence_shutdown |= shutdown
        self._sequence_preview = None
        self._sequence_frames.clear()
        self._sequence_frame_bytes = 0
        self._sequence_pending_path = None
        if self._sequence_worker is not None:
            self._sequence_worker.cancel()
        self._sequence_message = '设置已变化，请重新分析；旧成片已失效。'
        self._update_dejitter_controls()
        if hasattr(self, 'preview_label') and self._sequence_result_mode():
            self._show_sequence_preview_result(preserve_view=True)

    def _update_dejitter_controls(self):
        if not hasattr(self, 'dejitter_effective_status'):
            return
        regions = getattr(self, '_dejitter_reference_regions', ())
        source = self._dejitter_reference_source
        self.dejitter_reference_status.setText(f'{Path(source).name} · {len(regions)} 个选区' if source and regions else '尚未选择参考区')
        self.dejitter_reference_strength_slider.setEnabled(bool(regions))
        self.dejitter_edit_reference_btn.setEnabled(bool(self._dejitter_reference_source))
        self.dejitter_effective_status.setText(
            ('强度 0%：不补偿位移，仅计算共同尺寸。' if self.dejitter_reference_strength_slider.value() == 0 else '自动计算整组公共裁切；不使用前面的裁切与模板设置。') if regions else '请先在参考图框选一个或多个区域。')
        if hasattr(self, 'dejitter_region_list'):
            labels = [f'选区 {index + 1}  ·  {round((box[2]-box[0])*100)}% × {round((box[3]-box[1])*100)}%'
                      for index, box in enumerate(regions)]
            if labels != [self.dejitter_region_list.item(i).text() for i in range(self.dejitter_region_list.count())]:
                self.dejitter_region_list.blockSignals(True)
                self.dejitter_region_list.clear()
                self.dejitter_region_list.addItems(labels)
                self.dejitter_region_list.blockSignals(False)
            self.dejitter_delete_region_btn.setEnabled(bool(self.dejitter_region_list.selectedItems()))
        if not self._dejitter_tab_active():
            return
        worker = self._sequence_worker
        stopping = worker is not None and worker.isInterruptionRequested()
        upgrading = worker is not None and self._sequence_preview is not None and not self._sequence_exporting
        self.dejitter_preprocess_btn.setText('正在停止…' if stopping else '分析并预览成片' if upgrading or not worker
                                           else '取消导出' if self._sequence_exporting else '取消分析')
        self.dejitter_preprocess_btn.setEnabled(not stopping and not upgrading and not self._sequence_shutdown
                                               and (worker is not None or (self.current_path is not None and bool(regions))))
        self.dejitter_export_btn.setEnabled(worker is None and self._sequence_preview is not None and not self._sequence_shutdown)
        self.dejitter_tracking_status.setText(self._sequence_message)
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.sync()

    def _on_dejitter_analyze(self):
        if self._sequence_shutdown:
            return
        if self._sequence_worker is not None:
            self._invalidate_sequence_preview()
            self._sequence_message = '已取消任务；导出中的本次文件会撤销，等待线程结束后可重新执行。'
            self._update_dejitter_controls()
            self._refresh_preview_label(preserve_view=True)
            return
        paths = self._list_photo_paths()
        if not paths or self.current_path is None:
            self._show_error('无法分析', '请先导入并选择照片。')
            return
        if not self._reference_tracking_input():
            self._show_error('缺少参考区', '请先框选一个或多个参考区。')
            return
        self._invalidate_sequence_preview()
        self._sequence_message = '正在准备整组分析…'
        seeds = self._build_dejitter_seeds(paths)
        self._launch_sequence_worker(seeds=seeds)
        self._set_dejitter_view('result')
        self._refresh_preview_label(preserve_view=True)

    def _launch_sequence_worker(self, *, seeds=()):
        worker = EditorSequencePreviewWorker(
            token=self._sequence_epoch, path=self.current_path, seeds=seeds,
            template_paths=self.template_paths, sequence=self._sequence_preview,
            bird_boxes=self._bird_box_cache, parent=self,
        )
        self._sequence_worker = worker
        worker.ready.connect(self._on_sequence_ready)
        worker.quick_ready.connect(self._on_sequence_quick_ready)
        worker.failed.connect(self._on_sequence_failed)
        worker.progress.connect(self._on_sequence_progress)
        worker.finished.connect(self._on_sequence_finished)
        worker.start()
        self._update_dejitter_controls()

    def _accept_sequence_signal(self, token):
        worker = self.sender()
        return (worker is not None and worker is self._sequence_worker and token == self._sequence_epoch
                and not self._sequence_shutdown and not worker.isInterruptionRequested())

    def _on_sequence_progress(self, token, message):
        if self._accept_sequence_signal(token):
            if self._sequence_preview is not None and not self._sequence_exporting:
                return  # 清晰帧升级不覆盖已经完成的整组分析摘要。
            self._sequence_message = message
            self._update_dejitter_controls()

    def _on_sequence_failed(self, token, message):
        if self._accept_sequence_signal(token):
            self._sequence_message = f'去抖动任务失败：{message}'
            self._sequence_pending_path = None
            self._update_dejitter_controls()

    def _on_sequence_quick_ready(self, token, sequence, frames):
        if not self._accept_sequence_signal(token):
            return
        seeds = self._build_dejitter_seeds(self._list_photo_paths())
        if not sequence.files_current() or sequence_input_key(seeds) != sequence.input_key:
            self._invalidate_sequence_preview()
            return
        self._sequence_preview = sequence
        self._sequence_quick_frames = frames
        self.sequence_transport.set_frames(sequence, frames)
        self._sequence_validated_at = monotonic()
        self._on_sequence_ready(token, sequence, None)

    def _on_sequence_ready(self, token, sequence, frame):
        if not self._accept_sequence_signal(token):
            return
        if not sequence.files_current():
            self._invalidate_sequence_preview()
            return
        self._sequence_preview = sequence
        # 元数据到达、用户切图可能改变顺序/设置；完整签名也要在接收时验证。
        seeds = self._build_dejitter_seeds(self._list_photo_paths())
        if sequence_input_key(seeds, self.template_paths) != sequence.input_key:
            self._invalidate_sequence_preview()
            return
        if frame is not None:
            key = path_key(frame.path)
            old = self._sequence_frames.pop(key, None)
            if old is not None:
                self._sequence_frame_bytes -= old.image.sizeInBytes()
            self._sequence_frames[key] = frame
            self._sequence_frame_bytes += frame.image.sizeInBytes()
            while len(self._sequence_frames) > 1 and self._sequence_frame_bytes > editor_options.DEJITTER_PREVIEW_CACHE_BYTES:
                _, removed = self._sequence_frames.popitem(last=False)
                self._sequence_frame_bytes -= removed.image.sizeInBytes()
        self._bird_box_cache.update(sequence.bird_boxes)
        self._reference_tracking_results = dict(sequence.tracking)
        self._reference_tracking_definition = self._reference_tracking_input()
        source = self._dejitter_reference_source
        self._reference_tracking_signature = image_file_signature(Path(source)) if source else None
        failed = sum(r.matched_count < len(r.boxes) for r in sequence.tracking.values())
        self._sequence_message = f'整组 {len(sequence.jobs)} 张已分析；统一 {sequence.output_size[0]} × {sequence.output_size[1]}；{failed} 张存在部分选区失配。'
        self._reference_tracking_message = self._sequence_message
        self._set_status(self._sequence_message)
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)

    def _on_sequence_finished(self):
        worker = self.sender()
        if worker is None or worker is not self._sequence_worker:
            return
        self._sequence_worker = None
        self._sequence_exporting = False
        worker.deleteLater()
        pending = self._sequence_pending_path
        self._sequence_pending_path = None
        self._update_dejitter_controls()
        if (not self._sequence_shutdown and pending is not None and self._sequence_preview is not None
                and self._sequence_result_mode() and self.current_path == pending
                and path_key(pending) not in self._sequence_frames and not self._sequence_fast_preview_active()):
            self._launch_sequence_worker()

    def _show_sequence_preview_result(self, *, reset_view=False, preserve_view=False, **_kwargs):
        if not self._sequence_result_mode():
            return False
        sequence = self._sequence_preview
        if sequence is not None and (not self._sequence_fast_preview_active() or monotonic() - self._sequence_validated_at > 1):
            if not self._validate_sequence_preview():
                sequence = None
        key = path_key(self.current_path) if self.current_path else ''
        fast = self._sequence_fast_preview_active()
        frame = (self._sequence_quick_frames.get(key) if fast else
                 self._sequence_frames.get(key) or self._sequence_quick_frames.get(key)) if sequence else None
        if sequence is not None and key in sequence.jobs and key not in self._sequence_frames and not fast:
            self._sequence_upgrade_timer.start()
        options = self._build_preview_overlay_options()
        options.show_reference_regions = False
        options.show_crop_effect = False
        self.preview_label.apply_overlay_options(options)
        self.preview_label.canvas.set_edit_mode(EDIT_MODE_NONE)
        state = EditorPreviewOverlayState()
        if frame is not None:
            if key in self._sequence_frames:
                self._sequence_frames.move_to_end(key)
            crop, (pt, pb, pl, pr) = frame.crop_plan
            width, height = frame.source_size
            job = sequence.jobs[key]
            focus = editor_core.resolve_focus_box_after_processing(
                job.raw_metadata, source_width=width, source_height=height, crop_box=crop,
                outer_pad=(pt, pb, pl, pr), apply_ratio_crop=True,
                camera_type=editor_core.resolve_focus_camera_type_from_metadata(job.raw_metadata),
            )
            bird = editor_core.transform_source_box_after_crop_padding(
                self._bird_box_cache.get(self._source_signature(self.current_path)), crop_box=crop,
                source_width=width, source_height=height, pt=pt, pb=pb, pl=pl, pr=pr,
            )
            state = EditorPreviewOverlayState(focus_box=focus,
                                              bird_box=bird, crop_effect_box=(0, 0, 1, 1))
            self.preview_label.set_original_size(*frame.source_size)
            self.preview_label.set_cropped_size(*frame.output_size)
            pixmap = QPixmap.fromImage(frame.image)
        else:
            pixmap = None
            self.preview_label.set_cropped_size(None, None)
            # 未分析或尚无结果时保持待更新状态；所有清晰请求由防抖定时器调度。
        self.preview_label.apply_overlay_state(state)
        self.preview_label.set_source_mode('去抖动成片' if frame else '成片待更新')
        if frame is not None and self.preview_label.canvas._source_pixmap is None:
            reset_view, preserve_view = True, False
        self.preview_label.set_source_pixmap(pixmap, reset_view=reset_view, preserve_view=preserve_view,
                                             preserve_scale=preserve_view)
        return True

    def _valid_sequence_for_export(self):
        sequence = self._sequence_preview
        if sequence is None:
            return None
        seeds = self._build_dejitter_seeds(self._list_photo_paths())
        if sequence_input_key(seeds, self.template_paths) != sequence.input_key:
            self._invalidate_sequence_preview()
            return None
        return sequence

    def _build_dejitter_seeds(self, paths):
        settings = self._dejitter_reference_settings()
        seeds = []
        for path in paths:
            key = path_key(path)
            raw = self.raw_metadata_cache.get(key) or self.photo_list_metadata_cache.get(key) or {}
            if self.current_path is not None and path_key(self.current_path) == key:
                raw = self.current_raw_metadata or raw
            seeds.append(RenderJobSeed(path, dict(settings), dict(raw), key in self.raw_metadata_cache))
        return seeds

    def _on_delete_dejitter_regions(self):
        rows = {self.dejitter_region_list.row(item) for item in self.dejitter_region_list.selectedItems()}
        if not rows:
            return
        self._dejitter_reference_regions = tuple(box for index, box in enumerate(self._dejitter_reference_regions)
                                                if index not in rows)
        if not self._dejitter_reference_regions:
            self._dejitter_reference_source = None
            self.dejitter_reference_check.setChecked(False)
        self._invalidate_reference_tracking('选区已删除，请重新分析。')
        self._update_dejitter_reference_clear_enabled()
        self._refresh_preview_label(preserve_view=True)
        self._on_output_settings_changed()

    def _on_dejitter_export_all(self):
        if self._sequence_worker is not None:
            return
        sequence = self._valid_sequence_for_export()
        if sequence is None:
            self._show_error('请先分析', '请先分析整组并检查成片。')
            return
        self.sequence_transport.stop(commit=False)
        self._sequence_upgrade_timer.stop()
        destination = QFileDialog.getExistingDirectory(self, '去抖动导出全部：选择保存目录')
        if not destination:
            return
        worker = EditorSequenceExportWorker(token=self._sequence_epoch, sequence=sequence,
                                            destination=destination,
                                            output_format=self.dejitter_output_format.currentData(), parent=self)
        self._sequence_worker = worker
        self._sequence_exporting = True
        worker.progress.connect(self._on_sequence_progress)
        worker.failed.connect(self._on_sequence_failed)
        worker.completed.connect(self._on_sequence_exported)
        worker.finished.connect(self._on_sequence_finished)
        worker.start()
        self._update_dejitter_controls()

    def _on_sequence_exported(self, token, folder):
        if self._accept_sequence_signal(token):
            self._sequence_message = f'整组导出完成：{folder}'
            self._set_status(self._sequence_message)
            self._update_dejitter_controls()

    def _show_dejitter_edit_preview(self, *, reset_view=False, preserve_view=False, **_kwargs):
        if not self._dejitter_tab_active() or self._sequence_result_mode():
            return False
        source = self.current_source_image
        quick = self._sequence_quick_frames.get(path_key(self.current_path)) if self.current_path and (source is None or self._sequence_fast_preview_active()) else None
        options = self._build_preview_overlay_options()
        options.show_crop_effect = False
        self.preview_label.apply_overlay_options(options)
        editable = self._reference_regions_editable()
        mode = self._current_edit_mode_id()
        self.preview_label.canvas.set_edit_mode(EDIT_MODE_REFERENCE_REGION
                                                if editable and mode == EDIT_MODE_REFERENCE_REGION else EDIT_MODE_NONE)
        tracked = self._tracking_result_for_current()
        labels = tuple(str(i + 1) for i, box in enumerate(tracked.boxes) if box is not None) if tracked and not editable else ()
        self.preview_label.canvas.set_reference_region_labels(labels)
        state = EditorPreviewOverlayState(reference_regions=self._visible_dejitter_reference_regions(),
                                          crop_effect_box=(0, 0, 1, 1))
        if source is not None or quick is not None:
            width, height = quick.source_size if quick else self._crop_display_source_size() or source.size
            state.focus_box = editor_core.resolve_focus_box_after_processing(
                self.current_raw_metadata, source_width=width, source_height=height, crop_box=None,
                outer_pad=(0, 0, 0, 0), apply_ratio_crop=False,
                camera_type=editor_core.resolve_focus_camera_type_from_metadata(self.current_raw_metadata))
            state.bird_box = self._bird_box_cache.get(self._source_signature(self.current_path)) if self.current_path else None
            self.preview_label.set_original_size(width, height)
        self.preview_label.set_cropped_size(None, None)
        self.preview_label.apply_overlay_state(state)
        self.preview_label.set_source_mode('去抖动原图')
        if source is not self._dejitter_edit_source:
            self._dejitter_edit_source = source
            self._dejitter_edit_pixmap = pil_to_qpixmap(source) if source is not None else None
        pixmap = QPixmap.fromImage(quick.source_image) if quick else self._dejitter_edit_pixmap
        self.preview_label.set_source_pixmap(pixmap,
                                             reset_view=reset_view, preserve_view=preserve_view,
                                             preserve_scale=preserve_view)
        return True
