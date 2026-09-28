from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from time import monotonic

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QTabBar, QCheckBox, QComboBox, QFileDialog, QListWidget, QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt, QTimer

from birdstamp.export_stage.sequence_preview import sequence_input_key
from birdstamp.export_stage.sequence_intersection import normalized_intersection_box, normalized_union_box
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY
from birdstamp.image_dejitter.auto_regions import suggest_reference_regions
from . import editor_core, editor_options
from .edit_modes import EDIT_MODE_NONE, EDIT_MODE_REFERENCE_REGION
from .editor_preview_canvas import EditorPreviewOverlayState
from .editor_sequence_preview_worker import EditorSequencePreviewWorker, EditorSequenceExportWorker
from .sequence_preview_cache import SequencePreviewCache
from .editor_matching_controls import DejitterMatchingControls
from .sequence_bounds_overview import SequenceBoundsOverview
from .color_key_rows import ColorKeyRows
from birdstamp.export_stage.render_job_seed import RenderJobSeed
from .editor_utils import pil_to_qpixmap
from .editor_utils import path_key
from birdstamp.image_dejitter.sequence_geometry import source_normalized_crop


def _add_progress_completion_label(layout, progress_bar, accessible_name):
    label = QLabel('完成✅')
    label.setAccessibleName(accessible_name)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet('color: #218838; font-weight: 600;')
    label.setMinimumHeight(progress_bar.sizeHint().height())
    label.hide()
    layout.addWidget(label)
    return label


class _BirdStampDejitterMixin:
    """去抖动页/共享画布协调；计算与作业结果由无窗口核心持有。"""

    def _init_dejitter_preview(self):
        self._sequence_worker = None
        self._sequence_exporting = False
        self._sequence_export_open_workspace = False
        self._sequence_export_workspace_result = None
        self._sequence_progress_kind = None
        self._sequence_epoch = 0
        self._sequence_shutdown = False
        self._sequence_preview = None
        self._sequence_cache_key = None
        self._sequence_restore_state = None
        self._sequence_restore_timer = QTimer(self)
        self._sequence_restore_timer.setSingleShot(True)
        self._sequence_restore_timer.timeout.connect(self._try_restore_sequence_cache)
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

    def _collect_sequence_workspace_state(self):
        return dict(input_key=self._sequence_cache_key,
                    active=self._dejitter_tab_active(), view=self._dejitter_view,
                    show_intersection=self.dejitter_show_intersection_check.isChecked(),
                    export_intersection=self.dejitter_export_intersection_check.isChecked(),
                    open_export_workspace=self.dejitter_export_workspace_check.isChecked())

    def _restore_sequence_workspace_state(self, state):
        if self._sequence_shutdown:
            return
        options = state if isinstance(state, dict) else {}
        for checkbox, key, default in (
                (self.dejitter_show_intersection_check, 'show_intersection', editor_options.DEJITTER_SHOW_INTERSECTION),
                (self.dejitter_export_intersection_check, 'export_intersection', editor_options.DEJITTER_EXPORT_INTERSECTION),
                (self.dejitter_export_workspace_check, 'open_export_workspace', editor_options.DEJITTER_EXPORT_NEW_WORKSPACE)):
            blocked = checkbox.blockSignals(True)
            checkbox.setChecked(options.get(key, default) is True)
            checkbox.blockSignals(blocked)
        if not isinstance(state, dict) or not state.get('input_key'):
            return
        self._sequence_restore_state = dict(state)
        # 异步读盘期间自动保存也保留引用，防止下次启动丢失已有分析。
        self._sequence_cache_key = state['input_key']
        self._sequence_restore_timer.start(0)

    def _try_restore_sequence_cache(self):
        state = self._sequence_restore_state
        if self._sequence_shutdown or not state or self._sequence_worker is not None:
            return
        self._sequence_restore_state = None
        paths = self._list_photo_paths()
        seeds = self._build_dejitter_seeds(paths)
        if not paths or not seeds or sequence_input_key(seeds) != state.get('input_key'):
            self._sequence_cache_key = None
            self._sequence_message = '照片或分析参数已变化，请重新分析。'
            self._update_dejitter_controls()
            return
        self._sequence_message = '正在加载已有成片缓存…'
        if state.get('active'):
            self.export_tabs.setCurrentWidget(self.dejitter_page)
        self._set_dejitter_view('result' if state.get('view') == 'result' else 'edit')
        selected = self.current_path if self.current_path in paths else paths[0]
        self._launch_sequence_worker(seeds=seeds, restore_only=True, path=selected)

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
        self.dejitter_auto_regions_btn = QPushButton('自动添加选区')
        self.dejitter_auto_regions_btn.setToolTip('在当前参考图中添加最多 4 个分散的纹理区域；已有选区保留，可继续手动调整。')
        self.dejitter_auto_regions_btn.clicked.connect(self._on_dejitter_auto_regions)
        buttons.addWidget(self.dejitter_edit_reference_btn)
        buttons.addWidget(self.dejitter_draw_btn)
        form.addLayout(buttons)
        form.addWidget(self.dejitter_auto_regions_btn)
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
        hint = QLabel('框内拖动；手柄缩放（Shift 保持比例，Alt 对称）。\n'
                      '参考图：空白处 Shift 追加，右键删除；其它原图：修正匹配位置，右键恢复自动匹配。')
        hint.setWordWrap(True)
        form.addWidget(hint)
        layout.addWidget(reference)

        alignment_row = QHBoxLayout()
        alignment_row.addWidget(QLabel('对齐方式'))
        self.dejitter_alignment_combo = QComboBox()
        self.dejitter_alignment_combo.addItem('平移＋旋转', 'rigid')
        self.dejitter_alignment_combo.addItem('仅平移', 'translation')
        self.dejitter_alignment_combo.setCurrentIndex(0 if editor_options.DEJITTER_ALIGNMENT_MODE == 'rigid' else 1)
        self.dejitter_alignment_combo.setToolTip('对齐到参考图的角度和位置；旋转需要重采样。角度证据不足时退回平移并标记，原图不变。')
        self.dejitter_alignment_combo.currentIndexChanged.connect(self._on_dejitter_matching_changed)
        alignment_row.addWidget(self.dejitter_alignment_combo,1)
        layout.addLayout(alignment_row)

        self.dejitter_matching_controls = DejitterMatchingControls(editor_options.DEJITTER_MATCHING_DEFAULTS)
        self.dejitter_matching_controls.changed.connect(self._on_dejitter_matching_changed)
        layout.addWidget(self.dejitter_matching_controls)

        self.dejitter_pad_to_union_check = QCheckBox('补边保留完整画面（供二次裁切）')
        self.dejitter_pad_to_union_check.setChecked(editor_options.DEJITTER_PAD_TO_UNION)
        self.dejitter_pad_to_union_check.setToolTip('关闭：裁掉所有空白，取整组交集。开启：保留整组画面并集，统一画幅，缺失区域补黑。')
        self.dejitter_pad_to_union_check.toggled.connect(self._on_dejitter_options_changed)
        layout.addWidget(self.dejitter_pad_to_union_check)
        self.dejitter_show_intersection_check = QCheckBox('成片预览显示交集／并集范围框')
        self.dejitter_show_intersection_check.setChecked(editor_options.DEJITTER_SHOW_INTERSECTION)
        self.dejitter_show_intersection_check.setToolTip('交集框：所有照片共同覆盖的最大无黑边矩形。并集框：整组完整范围。\n开启补边可在成片中完整查看两框；下方示意图始终显示两个范围。')
        self.dejitter_show_intersection_check.toggled.connect(self._on_dejitter_intersection_options_changed)
        layout.addWidget(self.dejitter_show_intersection_check)
        self.dejitter_preprocess_btn = QPushButton('分析并预览成片')
        self.dejitter_preprocess_btn.clicked.connect(self._on_reference_preprocess_clicked)
        layout.addWidget(self.dejitter_preprocess_btn)
        self.dejitter_analysis_progress = QProgressBar()
        self.dejitter_analysis_progress.setAccessibleName('去抖动分析进度')
        self.dejitter_analysis_progress.hide()
        layout.addWidget(self.dejitter_analysis_progress)
        self.dejitter_analysis_complete = _add_progress_completion_label(
            layout, self.dejitter_analysis_progress, '去抖动分析完成')
        self.dejitter_effective_status = QLabel()
        self.dejitter_effective_status.setWordWrap(True)
        layout.addWidget(self.dejitter_effective_status)
        self.dejitter_tracking_status = QLabel()
        self.dejitter_tracking_status.setWordWrap(True)
        layout.addWidget(self.dejitter_tracking_status)
        self.dejitter_tracking_key = ColorKeyRows((
            ('#FFB703', False, '跟踪成功'),
            ('#FF5252', True, '未匹配（预计位置）'),
        ))
        layout.addWidget(self.dejitter_tracking_key)
        self.dejitter_intersection_status = ColorKeyRows((
            ('#F5A623', False, '整组完整范围（并集）：待分析'),
            ('#45D6E8', False, '共同无黑边范围（交集）：待分析'),
        ))
        layout.addWidget(self.dejitter_intersection_status)
        self.dejitter_bounds_overview = SequenceBoundsOverview()
        layout.addWidget(self.dejitter_bounds_overview)
        self.dejitter_export_intersection_check = QCheckBox('仅导出共同无黑边范围（交集）')
        self.dejitter_export_intersection_check.setChecked(editor_options.DEJITTER_EXPORT_INTERSECTION)
        self.dejitter_export_intersection_check.setToolTip('导出范围与预览交集框一致；不改变当前补边预览，不需要重新匹配。')
        self.dejitter_export_intersection_check.toggled.connect(self._on_dejitter_intersection_options_changed)
        layout.addWidget(self.dejitter_export_intersection_check)
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
        self.dejitter_export_workspace_check = QCheckBox('导出后保存当前工作区，新建工作区加载成片')
        self.dejitter_export_workspace_check.setChecked(editor_options.DEJITTER_EXPORT_NEW_WORKSPACE)
        self.dejitter_export_workspace_check.setToolTip(
            '导出成功后保存当前工作区，再建立成片工作区并加载本次导出的图片。\n'
            '未命名的原工作区和新成片工作区自动保存到本次导出目录；保存失败时保留当前列表。')
        self.dejitter_export_workspace_check.toggled.connect(self._schedule_workspace_autosave)
        layout.addWidget(self.dejitter_export_workspace_check)
        self.dejitter_export_progress = QProgressBar()
        self.dejitter_export_progress.setAccessibleName('去抖动导出进度')
        self.dejitter_export_progress.hide()
        layout.addWidget(self.dejitter_export_progress)
        self.dejitter_export_complete = _add_progress_completion_label(
            layout, self.dejitter_export_progress, '去抖动导出完成')
        note = QLabel('默认保留对齐后整组共同区域；交集太小时可开启补边，导出完整画面后再裁切。独立输出原图对齐结果，不叠加模板或文字；参考线、焦点和鸟体框仅用于预览。')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self.dejitter_reference_check.toggled.connect(self._on_dejitter_options_changed)
        self.dejitter_reference_strength_slider.valueChanged.connect(self._on_dejitter_options_changed)
        return page

    def _on_dejitter_matching_changed(self):
        self._invalidate_reference_tracking('匹配参数已变化，请重新分析。')
        self._sequence_message = '匹配参数已变化，请重新分析。'
        self._update_dejitter_controls()
        self._on_output_settings_changed()
        self._schedule_workspace_autosave()

    def _on_dejitter_intersection_options_changed(self):
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)
        self._schedule_workspace_autosave()

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
        ab = getattr(self, 'ab_preview', None)
        return (self._dejitter_tab_active() or (ab is not None and ab.enabled.isChecked())) and self._dejitter_view == 'result'

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
            sequence = self._sequence_preview
            if (sequence is not None and sequence.partial
                    and (self.current_path is None or path_key(self.current_path) not in sequence.jobs)):
                self.sequence_transport._select(0)
        self.dejitter_view_tabs.blockSignals(True)
        self.dejitter_view_tabs.setCurrentIndex(1 if view == 'result' else 0)
        self.dejitter_view_tabs.blockSignals(False)
        self._restore_selected_preview_source()
        self._refresh_preview_label(reset_view=True)
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.sync()

    def _restore_selected_preview_source(self):
        if not self._sequence_result_mode() and self.current_source_image is None:
            ab = getattr(self, 'ab_preview', None)
            item = (self._find_photo_item_by_path(self.current_path)
                    if ab is not None and ab.enabled.isChecked() and self.current_path is not None
                    else self.photo_list.currentItem())
            if item is not None:
                if ab is not None and ab.enabled.isChecked() and ab.active_side == 'a':
                    self._on_photo_selected(item, None, target_view='b')
                else:
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
        if (tuple(sequence.all_jobs) != tuple(path_key(path) for path in self._list_photo_paths())
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

    def _on_dejitter_auto_regions(self):
        if self._sequence_shutdown or self._sequence_worker is not None:
            return
        self._set_dejitter_view('edit')
        if not self._reference_regions_editable():
            self._on_edit_reference_photo()
            self._set_status('请在参考图加载完成后再次点击“自动添加选区”。')
            return
        image = self.current_source_image
        if image is None or self.current_path is None:
            self._set_status('请先选择参考照片并等待原图预览加载完成。')
            return
        added = suggest_reference_regions(image, self._dejitter_reference_regions)
        if not added:
            self._set_status('当前参考图没有找到新的合适纹理区域；可手动框选。')
            return
        self._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
        self._commit_source_reference_regions(
            self.current_path, (*self._dejitter_reference_regions, *added))
        self._sequence_message = f'已自动添加 {len(added)} 个选区；请检查位置后重新分析。'
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)
        self._set_status(self._sequence_message)

    def _invalidate_sequence_preview(self, *, shutdown=False):
        self._sequence_export_open_workspace = False
        self._sequence_export_workspace_result = None
        if self._sequence_progress_kind is not None:
            self._finish_sequence_progress('已取消')
        elif hasattr(self, 'dejitter_analysis_progress'):
            self.dejitter_analysis_progress.hide()
            self.dejitter_export_progress.hide()
        if hasattr(self, 'dejitter_analysis_complete'):
            self.dejitter_analysis_complete.hide()
            self.dejitter_export_complete.hide()
        self._sequence_restore_timer.stop()
        self._sequence_restore_state = None
        if not shutdown:
            self._sequence_cache_key = None
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
        if hasattr(self, "ab_preview") and not shutdown:
            self.ab_preview.sync()

    def _update_dejitter_controls(self):
        if not hasattr(self, 'dejitter_effective_status'):
            return
        self.dejitter_export_workspace_check.setEnabled(not self._sequence_exporting and not self._sequence_shutdown)
        regions = getattr(self, '_dejitter_reference_regions', ())
        source = self._dejitter_reference_source
        self.dejitter_reference_status.setText(f'{Path(source).name} · {len(regions)} 个选区' if source and regions else '尚未选择参考区')
        self.dejitter_reference_strength_slider.setEnabled(bool(regions))
        self.dejitter_edit_reference_btn.setEnabled(bool(self._dejitter_reference_source))
        self.dejitter_auto_regions_btn.setEnabled(
            not self._sequence_shutdown and self._sequence_worker is None
            and self.current_path is not None)
        detail = ('保留对齐后全部图像范围，缺失区域补黑；可在导出后进行二次裁切。'
                  if self.dejitter_pad_to_union_check.isChecked() else
                  '取对齐后整组画面交集；编辑构图中可查看最终保留范围。')
        if self.dejitter_reference_strength_slider.value() == 0:
            detail = '强度 0%：不补偿位移。' + detail
        self.dejitter_effective_status.setText(detail if regions else '请先在参考图框选一个或多个区域。')
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
        sequence = self._sequence_preview
        partial = sequence is not None and sequence.partial
        box = sequence.intersection_box if sequence is not None else None
        union = sequence.union_box if sequence is not None else None
        missing_intersection = sequence is not None and box is None
        prefix = '当前成功前缀 · ' if partial else ''
        union_detail = (f'{union[2]-union[0]} × {union[3]-union[1]} 像素' if union else '待分析')
        if box is not None:
            detail = f'{box[2]-box[0]} × {box[3]-box[1]} 像素'
        else:
            detail = '没有共同有效区域，无法导出交集' if sequence else '待分析'
        self.dejitter_intersection_status.set_lines((
            f'{prefix}整组完整范围（并集）：{union_detail}',
            f'{prefix}共同无黑边范围（交集）：{detail}',
        ))
        self.dejitter_bounds_overview.set_bounds(union, box)
        export_blocked = missing_intersection and self.dejitter_export_intersection_check.isChecked()
        self.dejitter_export_intersection_check.setEnabled(not self._sequence_exporting)
        self.dejitter_export_btn.setEnabled(worker is None and sequence is not None and not partial
                                            and not export_blocked and not self._sequence_shutdown)
        self.dejitter_export_btn.setToolTip('当前仅保留失败前的成片预览，请完成整组分析后导出全部。' if partial
                                            else detail if export_blocked else '')
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

    def _launch_sequence_worker(self, *, seeds=(), restore_only=False, path=None):
        if self._sequence_preview is None:
            self._begin_sequence_progress('analysis')
        worker = EditorSequencePreviewWorker(
            token=self._sequence_epoch, path=path or self.current_path, seeds=seeds,
            restore_only=restore_only, cache=SequencePreviewCache(),
            template_paths=self.template_paths, sequence=self._sequence_preview,
            bird_boxes=self._bird_box_cache, parent=self,
        )
        self._sequence_worker = worker
        worker.ready.connect(self._on_sequence_ready)
        worker.quick_ready.connect(self._on_sequence_quick_ready)
        worker.diagnostics.connect(self._on_sequence_diagnostics)
        worker.failed.connect(self._on_sequence_failed)
        worker.progress.connect(self._on_sequence_progress)
        worker.progress_counts.connect(self._on_sequence_progress_counts)
        worker.finished.connect(self._on_sequence_finished)
        worker.start()
        self._update_dejitter_controls()

    def _accept_sequence_signal(self, token):
        worker = self.sender()
        return (worker is not None and worker is self._sequence_worker and token == self._sequence_epoch
                and not self._sequence_shutdown and not worker.isInterruptionRequested())

    def _on_sequence_diagnostics(self, token, payload):
        if not self._accept_sequence_signal(token):
            return
        from birdstamp.export_stage.sequence_preview import file_signatures
        key, tracking, signatures = payload
        if (key != sequence_input_key(self._build_dejitter_seeds(self._list_photo_paths()))
                or file_signatures(Path(path) for path, _ in signatures) != signatures):
            return
        # 即使共同裁切失败，也保留诊断，供原图和 A/B 对照定位问题。
        self._reference_tracking_results = tracking
        self._reference_tracking_manual_matches = dict(self._dejitter_manual_matches)
        self._reference_tracking_definition = self._reference_tracking_input()
        source = self._dejitter_reference_source
        self._reference_tracking_signature = image_file_signature(Path(source)) if source else None
        self._refresh_preview_label(preserve_view=True)

    def _on_sequence_progress(self, token, message):
        if self._accept_sequence_signal(token):
            if (self._sequence_preview is not None and not self._sequence_exporting
                    and self._sequence_progress_kind != 'analysis'):
                return  # 清晰帧升级不覆盖已经完成的整组分析摘要。
            self._sequence_message = message
            self._update_dejitter_controls()

    def _begin_sequence_progress(self, kind):
        self._sequence_progress_kind = kind
        bar = self.dejitter_export_progress if kind == 'export' else self.dejitter_analysis_progress
        completed = self.dejitter_export_complete if kind == 'export' else self.dejitter_analysis_complete
        completed.hide()
        bar.setRange(0, 0)
        bar.setValue(0)
        bar.setFormat('正在准备…')
        bar.setToolTip('正在准备…')
        bar.show()

    def _on_sequence_progress_counts(self, token, current, total, stage):
        if not self._accept_sequence_signal(token) or self._sequence_progress_kind is None:
            return
        bar = (self.dejitter_export_progress if self._sequence_progress_kind == 'export'
               else self.dejitter_analysis_progress)
        bar.setRange(0, max(0, total))
        bar.setValue(max(0, min(current, total)))
        bar.setFormat(f'{stage} %v/%m · %p%' if total > 0 else stage)
        bar.setToolTip(stage)
        if total <= 0:
            self._sequence_message = f'{stage}…'
            self._update_dejitter_controls()

    def _finish_sequence_progress(self, label, *, complete=False):
        kind = self._sequence_progress_kind
        if kind is None:
            return
        bar = self.dejitter_export_progress if kind == 'export' else self.dejitter_analysis_progress
        completed = self.dejitter_export_complete if kind == 'export' else self.dejitter_analysis_complete
        if complete:
            total = max(1, len(self._sequence_preview.jobs))
            bar.setRange(0, total)
            bar.setValue(total)
            bar.setFormat(f'{label} %v/%m · %p%')
            # 两个控件互斥显示，完成文字占用原进度条的位置。
            bar.hide()
            completed.setToolTip(bar.text())
            completed.show()
        else:
            completed.hide()
            bar.show()
            # 取消/失败要停止忙碌动画，不能显示一个永远在转或冒充完成的进度。
            if bar.maximum() == 0:
                bar.setRange(0, 1)
                bar.setValue(0)
            bar.setFormat(label)
        bar.setToolTip(label)
        self._sequence_progress_kind = None

    def _on_sequence_failed(self, token, message):
        if self._accept_sequence_signal(token):
            self._sequence_export_workspace_result = None
            worker = self._sequence_worker
            analysis_failed = (self._sequence_progress_kind == 'analysis'
                               and not getattr(worker, 'restore_only', False))
            self._finish_sequence_progress('导出失败' if self._sequence_exporting else '分析失败')
            self._sequence_message = f'去抖动任务失败：{message}'
            sequence = self._sequence_preview
            if analysis_failed and sequence is not None and sequence.partial:
                completed, total = len(sequence.jobs), len(sequence.all_jobs)
                label = f'分析失败 · 已生成前 {completed}/{total} 张成片预览'
                self.dejitter_analysis_progress.setRange(0, total)
                self.dejitter_analysis_progress.setValue(completed)
                self.dejitter_analysis_progress.setFormat(label)
                self.dejitter_analysis_progress.setToolTip(label)
                self._sequence_message += f'\n已生成前 {completed}/{total} 张成片预览，切换“成片预览”查看。'
            self._sequence_pending_path = None
            self._update_dejitter_controls()
            if analysis_failed:
                self.ab_preview.compare_analysis_failure(getattr(worker, 'failure_path', None))

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
        self._sequence_cache_key = None if sequence.partial else sequence.input_key
        if frame is not None and self._sequence_progress_kind == 'analysis' and not sequence.partial:
            self._finish_sequence_progress('分析完成', complete=True)
        self._schedule_workspace_autosave()
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
        self._reference_tracking_manual_matches = dict(self._dejitter_manual_matches)
        self._reference_tracking_definition = self._reference_tracking_input()
        source = self._dejitter_reference_source
        self._reference_tracking_signature = image_file_signature(Path(source)) if source else None
        failed = sum(r.matched_count < len(r.boxes) for r in sequence.tracking.values())
        padded = self.dejitter_pad_to_union_check.isChecked()
        kind = '补边画幅' if padded else '共同裁切'
        self._sequence_message = f'整组 {len(sequence.jobs)} 张已分析；{kind} {sequence.output_size[0]} × {sequence.output_size[1]}；{failed} 张存在部分选区失配。'
        if sequence.partial:
            self._sequence_message = (f'已生成前 {len(sequence.jobs)}/{len(sequence.all_jobs)} 张成片预览；'
                                      f'{kind} {sequence.output_size[0]} × {sequence.output_size[1]}。\n'
                                      f'后续分析失败：{sequence.failure}')
        if sequence.alignments:
            corrected = sum(a.status == 'rigid' for a in sequence.alignments.values())
            fallback = sum(a.status == 'fallback' for a in sequence.alignments.values())
            self._sequence_message += f'\n旋转估计成功 {corrected} 张，退回平移 {fallback} 张（未纠正旋转）。'
        self._reference_tracking_message = self._sequence_message
        self._set_status(self._sequence_message)
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)

    def _on_sequence_finished(self):
        worker = self.sender()
        if worker is None or worker is not self._sequence_worker:
            return
        self._finish_sequence_progress('已取消' if worker.isInterruptionRequested() else '任务已结束')
        workspace_result = (self._sequence_export_workspace_result
                            if worker.token == self._sequence_epoch and not worker.isInterruptionRequested()
                            and not self._sequence_shutdown else None)
        self._sequence_export_workspace_result = None
        self._sequence_export_open_workspace = False
        self._sequence_worker = None
        self._sequence_exporting = False
        worker.deleteLater()
        if workspace_result is not None:
            self._sequence_pending_path = None
            self._update_dejitter_controls()
            self._open_dejitter_export_workspace(*workspace_result)
            return
        if self._sequence_restore_state is not None:
            self._try_restore_sequence_cache()
            return
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
        options.show_reference_regions = True
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
            from .editor_tracking_overlay import tracking_overlays, apply_frame_alignment
            if frame.alignment and frame.alignment.rotated:
                focus = editor_core.resolve_focus_box_after_processing(
                    job.raw_metadata, source_width=width, source_height=height, crop_box=None,
                    outer_pad=(0, 0, 0, 0), apply_ratio_crop=False,
                    camera_type=editor_core.resolve_focus_camera_type_from_metadata(job.raw_metadata))
                apply_frame_alignment(state, frame, focus,
                    self._bird_box_cache.get(self._source_signature(self.current_path)),
                    self._dejitter_reference_regions, sequence.tracking.get(key))
            else:
                state.reference_diagnostics = tracking_overlays(
                    self._dejitter_reference_regions, sequence.tracking.get(key),
                    source_normalized_crop(frame.source_size, sequence.pixel_boxes[key]))
            self.preview_label.set_original_size(*frame.source_size)
            if self.dejitter_show_intersection_check.isChecked():
                state.intersection_box = normalized_intersection_box(sequence)
                state.union_box = normalized_union_box(sequence)
            self.preview_label.set_cropped_size(*frame.output_size)
            pixmap = QPixmap.fromImage(frame.image)
        else:
            pixmap = None
            self.preview_label.set_cropped_size(None, None)
            # 未分析或尚无结果时保持待更新状态；所有清晰请求由防抖定时器调度。
        self.preview_label.apply_overlay_state(state)
        missing = (f'该照片未生成成片 · 可预览前 {len(sequence.jobs)} 张'
                   if sequence is not None and sequence.partial and key not in sequence.jobs else '成片待更新')
        self.preview_label.set_source_mode('去抖动成片' if frame else missing)
        if frame is not None and self.preview_label.canvas._source_pixmap is None:
            reset_view, preserve_view = True, False
        self.preview_label.set_source_pixmap(pixmap, reset_view=reset_view, preserve_view=preserve_view,
                                             preserve_scale=preserve_view)
        return True

    def _valid_sequence_for_export(self):
        sequence = self._sequence_preview
        if sequence is None or sequence.partial:
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
            seeds.append(RenderJobSeed(path, {**settings, MANUAL_MATCHES_KEY: self._manual_record_for_path(path)},
                                       dict(raw), key in self.raw_metadata_cache))
        return seeds

    def _on_delete_dejitter_regions(self):
        rows = {self.dejitter_region_list.row(item) for item in self.dejitter_region_list.selectedItems()}
        if not rows:
            return
        self._dejitter_manual_matches.clear()
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
        if self._sequence_worker is not None or self._workspace_restore_in_progress():
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
                                            intersection_only=self.dejitter_export_intersection_check.isChecked(),
                                            output_format=self.dejitter_output_format.currentData(), parent=self)
        self._sequence_worker = worker
        self._sequence_exporting = True
        self._sequence_export_open_workspace = self.dejitter_export_workspace_check.isChecked()
        self._sequence_export_workspace_result = None
        self._begin_sequence_progress('export')
        worker.progress.connect(self._on_sequence_progress)
        worker.progress_counts.connect(self._on_sequence_progress_counts)
        worker.failed.connect(self._on_sequence_failed)
        worker.completed.connect(self._on_sequence_exported)
        worker.finished.connect(self._on_sequence_finished)
        worker.start()
        self._update_dejitter_controls()

    def _on_sequence_exported(self, token, folder):
        if self._accept_sequence_signal(token):
            if self._sequence_export_open_workspace:
                self._sequence_export_workspace_result = (Path(folder), self._sequence_worker.exported_paths)
            self._finish_sequence_progress('导出完成', complete=True)
            self._sequence_message = f'整组导出完成：{folder}'
            self._set_status(self._sequence_message)
            self._update_dejitter_controls()

    def _show_dejitter_edit_preview(self, *, reset_view=False, preserve_view=False, **_kwargs):
        ab = getattr(self, 'ab_preview', None)
        if not (self._dejitter_tab_active() or (ab is not None and ab.enabled.isChecked())) or self._sequence_result_mode():
            return False
        if self._sequence_preview is not None and (not self._sequence_fast_preview_active()
                or monotonic() - self._sequence_validated_at > 1):
            self._validate_sequence_preview()
        source = self.current_source_image
        quick = self._sequence_quick_frames.get(path_key(self.current_path)) if self.current_path and (source is None or self._sequence_fast_preview_active()) else None
        source_entry = (self.sequence_transport.source_preview(self.current_path)
                        if self.current_path and source is None and quick is None
                        and hasattr(self, 'sequence_transport') else None)
        raw_metadata = self.current_raw_metadata
        if self._sequence_preview is not None and self.current_path is not None:
            job = self._sequence_preview.jobs.get(path_key(self.current_path))
            if job is not None:
                # 分析已读取的焦点同样供原图快切使用，不在播放时重新读取 EXIF。
                raw_metadata = {**job.raw_metadata, **raw_metadata}
        options = self._build_preview_overlay_options()
        options.show_crop_effect = False
        self.preview_label.apply_overlay_options(options)
        editable = self._reference_regions_editable()
        canvas = self.preview_label.canvas
        canvas.set_reference_edit_source(path_key(self.current_path) if self.current_path else None)
        canvas.reference_region_creation_enabled = editable
        can_edit = self._region_edit_enabled(self.current_path) and (source is not None or quick is not None)
        self.preview_label.canvas.set_edit_mode(EDIT_MODE_REFERENCE_REGION
                                                if can_edit else EDIT_MODE_NONE)
        tracked = self._tracking_result_for_current()
        labels = () if can_edit else tuple(str(i + 1) for i, box in enumerate(tracked.boxes) if box is not None) if tracked and not editable else ()
        self.preview_label.canvas.set_reference_region_labels(labels)
        state = EditorPreviewOverlayState(reference_regions=(self._editable_regions_for_path(self.current_path)
                                                            if can_edit else self._visible_dejitter_reference_regions()),
                                          crop_effect_box=(0, 0, 1, 1))
        from .editor_tracking_overlay import tracking_overlays
        if self._sequence_preview is not None and self.current_path is not None:
            key = path_key(self.current_path)
            box = self._sequence_preview.pixel_boxes.get(key)
            size = self._sequence_preview.source_sizes.get(key)
            if box and size and not self.dejitter_pad_to_union_check.isChecked():
                state.crop_effect_box = source_normalized_crop(size, box)
                state.alignment_crop_box = state.crop_effect_box
                options.show_crop_effect = self.show_crop_effect_check.isChecked()
            elif size and not self.dejitter_pad_to_union_check.isChecked():
                from .editor_tracking_overlay import apply_alignment_crop
                if apply_alignment_crop(state, self._sequence_preview, key):
                    options.show_crop_effect = self.show_crop_effect_check.isChecked()
        if not editable:
            state.reference_diagnostics = tracking_overlays(
                self._dejitter_reference_regions, self._tracking_diagnostics_for_path(self.current_path) if can_edit else tracked)
        options.show_reference_regions = bool(state.reference_regions or state.reference_diagnostics)
        self.preview_label.apply_overlay_options(options)
        if source is not None or quick is not None or source_entry is not None:
            width, height = (quick.source_size if quick else source_entry[1] if source_entry is not None
                             else self._crop_display_source_size() or source.size)
            state.focus_box = editor_core.resolve_focus_box_after_processing(
                raw_metadata, source_width=width, source_height=height, crop_box=None,
                outer_pad=(0, 0, 0, 0), apply_ratio_crop=False,
                camera_type=editor_core.resolve_focus_camera_type_from_metadata(raw_metadata))
            state.bird_box = self._bird_box_cache.get(self._source_signature(self.current_path)) if self.current_path else None
            self.preview_label.set_original_size(width, height)
        self.preview_label.set_cropped_size(None, None)
        self.preview_label.apply_overlay_state(state)
        self.preview_label.set_source_mode('去抖动原图')
        if source is not self._dejitter_edit_source:
            self._dejitter_edit_source = source
            self._dejitter_edit_pixmap = pil_to_qpixmap(source) if source is not None else None
        if quick is not None:
            pixmap = QPixmap.fromImage(quick.source_image)
        elif source_entry is not None:
            source_image, _ = source_entry
            try:
                pixmap = pil_to_qpixmap(source_image)
            finally:
                source_image.close()
        else:
            pixmap = self._dejitter_edit_pixmap
        self.preview_label.set_source_pixmap(pixmap,
                                             reset_view=reset_view, preserve_view=preserve_view,
                                             preserve_scale=preserve_view)
        return True
