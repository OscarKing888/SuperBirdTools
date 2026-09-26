from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt

from birdstamp.export_stage.sequence_preview import sequence_input_key, apply_sequence_plans
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from . import editor_core, editor_options
from .edit_modes import EDIT_MODE_NONE, EDIT_MODE_REFERENCE_REGION
from .editor_preview_canvas import EditorPreviewOverlayState
from .editor_sequence_preview_worker import EditorSequencePreviewWorker
from .editor_utils import path_key


class _BirdStampDejitterMixin:
    """去抖动页/共享画布协调；计算与作业结果由无窗口核心持有。"""

    def _init_dejitter_preview(self):
        self._sequence_worker = None
        self._sequence_epoch = 0
        self._sequence_shutdown = False
        self._sequence_preview = None
        self._sequence_frames = OrderedDict()
        self._sequence_frame_bytes = 0
        self._sequence_pending_path = None
        self._sequence_message = '框选参考区后，分析整组照片并生成成片。'
        self._ordinary_edit_mode = EDIT_MODE_NONE
        self._dejitter_edit_mode = EDIT_MODE_REFERENCE_REGION
        self._last_dejitter_tab = False

    def _build_dejitter_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        intro = QLabel('整组照片：确定参考 → 分析 → 在右侧检查成片')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        reference = QGroupBox('参考区对齐')
        form = QVBoxLayout(reference)
        form.setContentsMargins(10, 24, 10, 12)
        self.dejitter_reference_check = QCheckBox('启用参考区去抖动')
        self.dejitter_reference_check.setToolTip('框选后自动启用；参考区对齐优先于自动构图平滑。')
        form.addWidget(self.dejitter_reference_check)
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
        strength = QHBoxLayout()
        strength.addWidget(QLabel('补偿强度'))
        self.dejitter_reference_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.dejitter_reference_strength_slider.setRange(0, 100)
        self.dejitter_reference_strength_slider.setValue(editor_options.DEJITTER_REFERENCE_STRENGTH)
        self.dejitter_reference_value_label = QLabel('100%')
        strength.addWidget(self.dejitter_reference_strength_slider, 1)
        strength.addWidget(self.dejitter_reference_value_label)
        form.addLayout(strength)
        hint = QLabel('Shift 追加多个选区；四边与四角调节大小。其它照片的跟踪框只读。')
        hint.setWordWrap(True)
        form.addWidget(hint)
        layout.addWidget(reference)

        smooth = QGroupBox('自动构图平滑')
        smooth_layout = QVBoxLayout(smooth)
        smooth_layout.setContentsMargins(10, 24, 10, 12)
        smooth_hint = QLabel('未启用参考区对齐时生效；需在“导出设置”开启统一自动裁切尺寸。')
        smooth_hint.setWordWrap(True)
        smooth_layout.addWidget(smooth_hint)
        smooth_row = QHBoxLayout()
        smooth_row.addWidget(self.auto_crop_stabilization_slider, 1)
        smooth_row.addWidget(self.auto_crop_stabilization_value_label)
        smooth_layout.addLayout(smooth_row)
        layout.addWidget(smooth)
        self.dejitter_preprocess_btn = QPushButton('分析并预览成片')
        self.dejitter_preprocess_btn.clicked.connect(self._on_reference_preprocess_clicked)
        layout.addWidget(self.dejitter_preprocess_btn)
        self.dejitter_effective_status = QLabel()
        self.dejitter_effective_status.setWordWrap(True)
        layout.addWidget(self.dejitter_effective_status)
        self.dejitter_tracking_status = QLabel()
        self.dejitter_tracking_status.setWordWrap(True)
        layout.addWidget(self.dejitter_tracking_status)
        note = QLabel('成片与导出共用图像处理管线。参考线、焦点和鸟体框沿用右侧开关；辅助显示不写入导出照片。视频画布适配与编码不在此预览中。')
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
        row.addWidget(QLabel('去抖动'))
        self.dejitter_view_combo = QComboBox()
        self.dejitter_view_combo.addItem('编辑构图', 'edit')
        self.dejitter_view_combo.addItem('成片预览', 'result')
        self.dejitter_view_combo.currentIndexChanged.connect(self._on_dejitter_view_changed)
        row.addWidget(self.dejitter_view_combo)
        self.dejitter_preview_status = QLabel()
        self.dejitter_preview_status.setWordWrap(True)
        row.addWidget(self.dejitter_preview_status, 1)
        bar.setVisible(False)
        self.dejitter_view_bar = bar
        return bar

    def _dejitter_tab_active(self):
        tabs = getattr(self, 'export_tabs', None)
        return tabs is not None and tabs.currentWidget() is self.dejitter_page

    def _sequence_result_mode(self):
        combo = getattr(self, 'dejitter_view_combo', None)
        return self._dejitter_tab_active() and combo is not None and combo.currentData() == 'result'

    def _on_export_tab_changed(self, _index):
        if not hasattr(self, 'dejitter_view_bar'):
            return
        active = self._dejitter_tab_active()
        if active != self._last_dejitter_tab:
            if active:
                self._ordinary_edit_mode = self._current_edit_mode_id()
                self._set_edit_mode_button_checked(self._dejitter_edit_mode)
            else:
                self._dejitter_edit_mode = self._current_edit_mode_id()
                self._set_edit_mode_button_checked(self._ordinary_edit_mode)
        self._last_dejitter_tab = active
        self.dejitter_view_bar.setVisible(active)
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)

    def _on_dejitter_view_changed(self, _index):
        self._refresh_preview_label(reset_view=True)

    def _on_dejitter_draw(self):
        self.dejitter_view_combo.setCurrentIndex(0)
        if self._dejitter_reference_source and not self._reference_regions_editable():
            self._on_edit_reference_photo()
        else:
            self._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
            self._refresh_preview_label(preserve_view=True)

    def _invalidate_sequence_preview(self, *, shutdown=False):
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

    def _update_dejitter_controls(self):
        if not hasattr(self, 'dejitter_effective_status'):
            return
        crop_enabled = self._is_pipeline_stage_enabled('template_crop')
        no_crop = not crop_enabled or editor_core.is_ratio_no_crop(self._selected_ratio())
        reference = self.dejitter_reference_check.isChecked()
        smooth = self.uniform_auto_crop_check.isChecked() and not reference and not no_crop
        self.auto_crop_stabilization_slider.setEnabled(smooth)
        self.auto_crop_stabilization_value_label.setEnabled(smooth)
        self.dejitter_reference_strength_slider.setEnabled(reference and not no_crop)
        self.dejitter_edit_reference_btn.setEnabled(bool(self._dejitter_reference_source))
        if no_crop:
            effective = ('当前“不裁切”：去抖动未生效。请在导出设置中选择裁切比例。' if crop_enabled
                         else '模板裁切已关闭：去抖动未生效。请在导出设置中启用模板裁切。')
        elif reference:
            effective = ('参考区对齐：强度 0%，不补偿。' if self.dejitter_reference_strength_slider.value() == 0
                         else '参考区对齐作用于整组照片；失配区域及多区分歧不会强行补偿。')
        else:
            effective = '自动构图平滑生效。' if smooth and self.auto_crop_stabilization_slider.value() else '未启用去抖动；成片仍按当前裁切及叠加设置生成。'
        self.dejitter_effective_status.setText(effective)
        if not self._dejitter_tab_active():
            return
        worker = self._sequence_worker
        stopping = worker is not None and worker.isInterruptionRequested()
        self.dejitter_preprocess_btn.setText('正在停止…' if stopping else '取消分析' if worker else '分析并预览成片')
        self.dejitter_preprocess_btn.setEnabled(not stopping and not self._sequence_shutdown
                                               and (worker is not None or (self.current_path is not None and not no_crop)))
        self.dejitter_tracking_status.setText(self._sequence_message)
        if hasattr(self, 'dejitter_preview_status'):
            self.dejitter_preview_status.setText(self._sequence_message)

    def _on_dejitter_analyze(self):
        if self._sequence_shutdown:
            return
        if self._sequence_worker is not None:
            self._invalidate_sequence_preview()
            self._sequence_message = '已取消分析，可重新执行。'
            self._update_dejitter_controls()
            self._refresh_preview_label(preserve_view=True)
            return
        paths = self._list_photo_paths()
        if not paths or self.current_path is None:
            self._show_error('无法分析', '请先导入并选择照片。')
            return
        if self.dejitter_reference_check.isChecked() and not self._reference_tracking_input():
            self._show_error('缺少参考区', '请先框选一个或多个参考区。')
            return
        self._invalidate_sequence_preview()
        self._sequence_message = '正在准备整组分析…'
        seeds = self._build_video_export_job_seeds(paths, reuse_sequence_plans=False)
        self._launch_sequence_worker(seeds=seeds)
        self.dejitter_view_combo.setCurrentIndex(1)
        self._refresh_preview_label(preserve_view=True)

    def _launch_sequence_worker(self, *, seeds=()):
        worker = EditorSequencePreviewWorker(
            token=self._sequence_epoch, path=self.current_path, seeds=seeds,
            template_paths=self.template_paths, sequence=self._sequence_preview,
            bird_boxes=self._bird_box_cache, parent=self,
        )
        self._sequence_worker = worker
        worker.ready.connect(self._on_sequence_ready)
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
            self._sequence_message = message
            self._update_dejitter_controls()

    def _on_sequence_failed(self, token, message):
        if self._accept_sequence_signal(token):
            self._sequence_message = f'成片预览失败：{message}'
            self._sequence_pending_path = None
            self._update_dejitter_controls()

    def _on_sequence_ready(self, token, sequence, frame):
        if not self._accept_sequence_signal(token):
            return
        if not sequence.files_current():
            self._invalidate_sequence_preview()
            return
        self._sequence_preview = sequence
        # 元数据到达、用户切图可能改变顺序/设置；完整签名也要在接收时验证。
        seeds = self._build_video_export_job_seeds(self._list_photo_paths(), reuse_sequence_plans=False)
        if sequence_input_key(seeds, self.template_paths) != sequence.input_key:
            self._invalidate_sequence_preview()
            return
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
        self._sequence_message = f'整组 {len(sequence.jobs)} 张已分析；{failed} 张存在参考区失配。'
        self._reference_tracking_message = self._sequence_message
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)

    def _on_sequence_finished(self):
        worker = self.sender()
        if worker is None or worker is not self._sequence_worker:
            return
        self._sequence_worker = None
        worker.deleteLater()
        pending = self._sequence_pending_path
        self._sequence_pending_path = None
        self._update_dejitter_controls()
        if (not self._sequence_shutdown and pending is not None and self._sequence_preview is not None
                and self._sequence_result_mode() and self.current_path == pending
                and path_key(pending) not in self._sequence_frames):
            self._launch_sequence_worker()

    def _show_sequence_preview_result(self, *, reset_view=False, preserve_view=False, **_kwargs):
        if not self._sequence_result_mode():
            return False
        sequence = self._sequence_preview
        if sequence is not None and tuple(sequence.jobs) != tuple(path_key(path) for path in self._list_photo_paths()):
            self._invalidate_sequence_preview()
            sequence = None
        if sequence is not None and not sequence.files_current():
            self._invalidate_sequence_preview()
            sequence = None
        key = path_key(self.current_path) if self.current_path else ''
        frame = self._sequence_frames.get(key) if sequence else None
        options = self._build_preview_overlay_options()
        options.show_reference_regions = False
        options.show_crop_effect = False
        self.preview_label.apply_overlay_options(options)
        self.preview_label.canvas.set_edit_mode(EDIT_MODE_NONE)
        state = EditorPreviewOverlayState()
        if frame is not None:
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
            # 管线已经画过的焦点不再重复叠加；独立辅助开关仍沿用当前界面。
            focus_exported = job.settings.get('draw_focus') and job.settings.get('stage_focus_overlay_enabled', True)
            state = EditorPreviewOverlayState(focus_box=None if focus_exported else focus,
                                              bird_box=bird, crop_effect_box=(0, 0, 1, 1))
            self.preview_label.set_original_size(*frame.source_size)
            self.preview_label.set_cropped_size(*frame.output_size)
            pixmap = QPixmap.fromImage(frame.image)
        else:
            pixmap = None
            self.preview_label.set_cropped_size(None, None)
            if sequence is not None and key in sequence.jobs:
                if self._sequence_worker is None:
                    self._launch_sequence_worker()
                else:
                    self._sequence_pending_path = self.current_path
        self.preview_label.apply_overlay_state(state)
        self.preview_label.set_source_mode('去抖动成片' if frame else '成片待更新')
        self.preview_label.set_source_pixmap(pixmap, reset_view=reset_view, preserve_view=preserve_view,
                                             preserve_scale=preserve_view)
        return True

    def _valid_sequence_for_export(self):
        sequence = self._sequence_preview
        if sequence is None:
            return None
        seeds = self._build_video_export_job_seeds(self._list_photo_paths(), reuse_sequence_plans=False)
        if sequence_input_key(seeds, self.template_paths) != sequence.input_key:
            self._invalidate_sequence_preview()
            return None
        return sequence

    def _reuse_sequence_plans(self, jobs):
        sequence = self._valid_sequence_for_export()
        if sequence is not None:
            apply_sequence_plans(sequence, jobs)
