from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from time import monotonic

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY
from app_common.collapsible_section import CollapsibleSection

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QTabBar, QCheckBox, QComboBox, QFileDialog, QListWidget, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget
from PyQt6.QtCore import Qt, QTimer

from app_common.toggle_button import ToggleToolButton
from birdstamp.export_stage.sequence_preview import sequence_input_key
from birdstamp.export_stage.sequence_intersection import normalized_intersection_box, normalized_union_box
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY
from birdstamp.image_dejitter.relay_anchors import RELAY_ANCHORS_KEY
from . import editor_core, editor_options
from .edit_modes import EDIT_MODE_NONE, EDIT_MODE_REFERENCE_REGION
from .editor_preview_canvas import EditorPreviewOverlayState
from .editor_sequence_preview_worker import EditorSequencePreviewWorker, EditorSequenceExportWorker
from .sequence_preview_cache import SequencePreviewCache
from .editor_matching_controls import DejitterMatchingControls
from .editor_subject_controls import SubjectControls
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


_PRIMARY_BUTTON_STYLE = """
QPushButton { background: #2F80ED; color: white; font-weight: 600; border: none; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: #3D8BF2; }
QPushButton:pressed { background: #1F6FD8; }
QPushButton:disabled { background: rgba(47, 128, 237, 90); color: rgba(255, 255, 255, 150); }
"""

_EXPORT_NOTE = ('默认保留对齐后整组共同区域；交集太小时可开启补边，导出完整画面后再裁切。'
                '独立输出原图对齐结果，不叠加模板或文字；参考线、焦点和鸟体框仅用于预览。')
_REGION_EDIT_HINT = ('框内拖动；手柄缩放（Shift 保持比例，Alt 对称）。'
                     '参考图／接力参考图：空白处 Shift 追加，右键删除；其它原图：修正匹配位置，右键恢复自动匹配。')


def _make_primary_button(button):
    """每步唯一的主操作按钮：强调色，与次要按钮区分主次。"""
    button.setProperty('primary', True)
    button.setStyleSheet(_PRIMARY_BUTTON_STYLE)
    button.setMinimumHeight(32)


def _sequence_failure_photo_label(failure_path, paths):
    """按任务输入顺序标识失败图像；同名文件使用完整路径区分。"""
    if failure_path is None:
        return ''
    sources = tuple(paths)
    failed_key = path_key(Path(failure_path))
    for index, path in enumerate(sources, 1):
        if path_key(Path(path)) == failed_key:
            return f'第 {index}/{len(sources)} 张图像'
    return ''


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
        self._tracking_status_text = self._sequence_message

    def _collect_sequence_workspace_state(self):
        return dict(input_key=self._sequence_cache_key,
                    active=self._dejitter_tab_active(), view=self._dejitter_view,
                    show_intersection=self.dejitter_show_intersection_check.isChecked(),
                    export_intersection=self.dejitter_export_intersection_check.isChecked(),
                    auto_region_count=self.dejitter_auto_region_count.value(),
                    subject_debug=self.dejitter_debug_check.isChecked(),
                    open_export_workspace=self.dejitter_export_workspace_check.isChecked(),
                    hud_collapsed=self.dejitter_hud.is_collapsed(),
                    matching_expanded=self.dejitter_matching_section.is_expanded())

    def _restore_sequence_workspace_state(self, state):
        if self._sequence_shutdown:
            return
        options = state if isinstance(state, dict) else {}
        try:
            count = int(options.get('auto_region_count', editor_options.DEJITTER_AUTO_REGION_COUNT))
        except (TypeError, ValueError, OverflowError):
            count = editor_options.DEJITTER_AUTO_REGION_COUNT
        blocked = self.dejitter_auto_region_count.blockSignals(True)
        self.dejitter_auto_region_count.setValue(count)
        self.dejitter_auto_region_count.blockSignals(blocked)
        for checkbox, key, default in (
                (self.dejitter_debug_check, 'subject_debug', editor_options.DEJITTER_SUBJECT_DEBUG),
                (self.dejitter_show_intersection_check, 'show_intersection', editor_options.DEJITTER_SHOW_INTERSECTION),
                (self.dejitter_export_intersection_check, 'export_intersection', editor_options.DEJITTER_EXPORT_INTERSECTION),
                (self.dejitter_export_workspace_check, 'open_export_workspace', editor_options.DEJITTER_EXPORT_NEW_WORKSPACE)):
            blocked = checkbox.blockSignals(True)
            checkbox.setChecked(options.get(key, default) is True)
            checkbox.blockSignals(blocked)
        self.dejitter_hud.set_collapsed(options.get('hud_collapsed') is True)
        self.dejitter_matching_section.set_expanded(options.get('matching_expanded') is True)
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
        layout.setSpacing(10)
        # 状态、下一步提示、图例和范围示意统一放到预览区 HUD，参数面板只保留可操作项。
        from .dejitter_hud import DejitterHud
        preview = getattr(self, 'preview_label', None)
        self.dejitter_hud = DejitterHud(preview.canvas if preview is not None else None)
        self.dejitter_tracking_status = self.dejitter_hud.message
        self.dejitter_tracking_key = self.dejitter_hud.tracking_key
        self.dejitter_intersection_status = self.dejitter_hud.intersection_status
        self.dejitter_bounds_overview = self.dejitter_hud.bounds_overview
        self.dejitter_effective_status = QLabel(page)
        self.dejitter_effective_status.hide()
        self.dejitter_hud.toggle.clicked.connect(self._schedule_workspace_autosave)

        # 1. 方式：决定后续该框什么、是否需要目标鸟，必须放在最前。
        self.dejitter_method_group = CollapsibleSection('1. 方式')
        method = QVBoxLayout(self.dejitter_method_group.body)
        method.setContentsMargins(0, 0, 0, 0)
        self.dejitter_subject_controls = SubjectControls(editor_options.DEJITTER_SUBJECT_DEFAULTS)
        self.dejitter_subject_controls.changed.connect(self._on_dejitter_method_changed)
        method.addWidget(self.dejitter_subject_controls)
        layout.addWidget(self.dejitter_method_group)

        reference = CollapsibleSection('2. 选区')
        self.dejitter_selection_group = reference
        form = QVBoxLayout(reference.body)
        form.setContentsMargins(0, 0, 0, 0)
        self.dejitter_reference_check = QCheckBox('启用参考区去抖动', reference)
        self.dejitter_reference_check.setToolTip('框选后自动启用；独立处理原图，不读取模板裁切。')
        self.dejitter_reference_check.hide()
        self.dejitter_reference_status = QLabel('尚未选择参考区')
        self.dejitter_reference_status.setWordWrap(True)
        form.addWidget(self.dejitter_reference_status)
        buttons = QHBoxLayout()
        # “框选参考区”合并原“编辑参考图”与“框选 / 追加选区”：开启即进入参考区编辑。
        self.dejitter_edit_reference_btn = ToggleToolButton('框选参考区')
        self.dejitter_edit_reference_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.dejitter_edit_reference_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.dejitter_edit_reference_btn.setToolTip('开启后在参考图上框选、追加或调整选区；已有参考图时自动定位到参考图。')
        self.dejitter_edit_reference_btn.setAccessibleName('框选参考区')
        self.dejitter_edit_reference_btn.clicked.connect(self._on_edit_reference_toggled)
        self.dejitter_auto_regions_btn = QPushButton('一键推荐选区')
        self.dejitter_auto_regions_btn.clicked.connect(self._on_dejitter_auto_regions)
        self.dejitter_auto_region_count = QSpinBox()
        self.dejitter_auto_region_count.setRange(1, 36)
        self.dejitter_auto_region_count.setValue(editor_options.DEJITTER_AUTO_REGION_COUNT)
        self.dejitter_auto_region_count.setPrefix('目标 ')
        self.dejitter_auto_region_count.setSuffix(' 个')
        self.dejitter_auto_region_count.setKeyboardTracking(False)
        self.dejitter_auto_region_count.setAccessibleName('自动选区目标总数')
        self.dejitter_auto_region_count.setToolTip('一键推荐的目标总数（含已有选区）；默认 9 按 3×3 分格。\n只改变数量不会修改当前选区；点击按钮后补足，已达到目标时不新增。')
        buttons.addWidget(self.dejitter_edit_reference_btn, 1)
        buttons.addWidget(self.dejitter_auto_regions_btn, 1)
        buttons.addWidget(self.dejitter_auto_region_count)
        form.addLayout(buttons)
        from .region_recommendation_panel import RegionRecommendationPanel
        self.dejitter_recommendation = RegionRecommendationPanel(self)
        self._attach_recommendation_rows()
        self.dejitter_recommendation.status.message_changed.connect(lambda _text: self._sync_dejitter_hud())
        form.addWidget(self.dejitter_recommendation)
        self.dejitter_auto_region_count.valueChanged.connect(self._schedule_workspace_autosave)
        self.dejitter_region_list = QListWidget()
        self.dejitter_region_list.setMaximumHeight(110)
        self.dejitter_region_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.dejitter_region_list.setToolTip(_REGION_EDIT_HINT.replace('。', '。\n', 1))
        form.addWidget(self.dejitter_region_list)
        self.dejitter_delete_region_btn = QPushButton('删除选中')
        self.dejitter_delete_region_btn.clicked.connect(self._on_delete_dejitter_regions)
        self.dejitter_reference_clear_btn = QPushButton('清除全部')
        self.dejitter_reference_clear_btn.clicked.connect(self._on_dejitter_reference_clear)
        delete_row = QHBoxLayout()
        delete_row.addWidget(self.dejitter_delete_region_btn)
        delete_row.addWidget(self.dejitter_reference_clear_btn)
        form.addLayout(delete_row)
        relay_row = QHBoxLayout()
        self.dejitter_relay_add_btn = QPushButton('从当前照片接力追踪')
        self._dejitter_relay_tooltip = (
            '某张照片跟踪失败（原选区出画、被遮挡或场景已变）时使用：\n'
            '把当前照片设为接力参考图，在它上面框选新的稳定纹理；之后的照片改为匹配这组接力选区，\n'
            '并通过相邻且已对齐的衔接照片接回原参考图坐标，整组仍输出同一画幅。')
        self.dejitter_relay_add_btn.setToolTip(self._dejitter_relay_tooltip)
        self.dejitter_relay_add_btn.clicked.connect(self._on_dejitter_relay_add)
        self.dejitter_relay_remove_btn = QPushButton('移除接力')
        self.dejitter_relay_remove_btn.setToolTip('移除当前接力参考图；其后照片恢复匹配上一组选区。')
        self.dejitter_relay_remove_btn.clicked.connect(self._on_dejitter_relay_remove)
        relay_row.addWidget(self.dejitter_relay_add_btn, 1)
        relay_row.addWidget(self.dejitter_relay_remove_btn)
        form.addLayout(relay_row)
        self.dejitter_relay_status = QLabel()
        self.dejitter_relay_status.setWordWrap(True)
        self.dejitter_relay_status.hide()
        form.addWidget(self.dejitter_relay_status)
        self.dejitter_region_list.itemSelectionChanged.connect(self._update_dejitter_controls)
        layout.addWidget(reference)

        # 按操作顺序分组；不常调的匹配参数折叠，标题显示当前取值摘要。
        self.dejitter_analysis_group = CollapsibleSection('3. 分析')
        analysis = QVBoxLayout(self.dejitter_analysis_group.body)
        analysis.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.dejitter_analysis_group)
        self.dejitter_matching_section = CollapsibleSection('匹配参数', expanded=False)
        self.dejitter_matching_section.toggled.connect(self._schedule_workspace_autosave)
        matching = QWidget()
        matching_layout = QVBoxLayout(matching)
        matching_layout.setContentsMargins(0, 0, 0, 0)
        strength = QHBoxLayout()
        strength.addWidget(QLabel('补偿强度'))
        self.dejitter_reference_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.dejitter_reference_strength_slider.setRange(0, 100)
        self.dejitter_reference_strength_slider.setValue(editor_options.DEJITTER_REFERENCE_STRENGTH)
        self.dejitter_reference_value_label = QLabel('100%')
        strength.addWidget(self.dejitter_reference_strength_slider, 1)
        strength.addWidget(self.dejitter_reference_value_label)
        matching_layout.addLayout(strength)
        alignment_row = QHBoxLayout()
        self.dejitter_alignment_label = QLabel('对齐方式')
        alignment_row.addWidget(self.dejitter_alignment_label)
        self.dejitter_alignment_combo = QComboBox()
        self.dejitter_alignment_combo.addItem('平移＋旋转', 'rigid')
        self.dejitter_alignment_combo.addItem('仅平移', 'translation')
        self.dejitter_alignment_combo.setCurrentIndex(0 if editor_options.DEJITTER_ALIGNMENT_MODE == 'rigid' else 1)
        self.dejitter_alignment_combo.setToolTip('对齐到参考图的角度和位置；旋转需要重采样。角度证据不足时退回平移并标记，原图不变。')
        self.dejitter_alignment_combo.currentIndexChanged.connect(self._on_dejitter_matching_changed)
        alignment_row.addWidget(self.dejitter_alignment_combo,1)
        matching_layout.addLayout(alignment_row)
        self.dejitter_matching_controls = DejitterMatchingControls(editor_options.DEJITTER_MATCHING_DEFAULTS)
        self.dejitter_matching_controls.changed.connect(self._on_dejitter_matching_changed)
        matching_layout.addWidget(self.dejitter_matching_controls)
        self.dejitter_matching_section.set_content_widget(matching)
        analysis.addWidget(self.dejitter_matching_section)
        self.dejitter_pad_to_union_check = QCheckBox('补边保留完整画面（供二次裁切）')
        self.dejitter_pad_to_union_check.setChecked(editor_options.DEJITTER_PAD_TO_UNION)
        self.dejitter_pad_to_union_check.setToolTip('关闭：裁掉所有空白，取整组交集。开启：保留整组画面并集，统一画幅，缺失区域补黑。')
        self.dejitter_pad_to_union_check.toggled.connect(self._on_dejitter_options_changed)
        analysis.addWidget(self.dejitter_pad_to_union_check)
        self.dejitter_preprocess_btn = QPushButton('分析并预览成片')
        _make_primary_button(self.dejitter_preprocess_btn)
        self.dejitter_preprocess_btn.clicked.connect(self._on_reference_preprocess_clicked)
        analysis.addWidget(self.dejitter_preprocess_btn)
        self.dejitter_analysis_progress = QProgressBar()
        self.dejitter_analysis_progress.setAccessibleName('去抖动分析进度')
        self.dejitter_analysis_progress.hide()
        analysis.addWidget(self.dejitter_analysis_progress)
        self.dejitter_analysis_complete = _add_progress_completion_label(
            analysis, self.dejitter_analysis_progress, '去抖动分析完成')

        self.dejitter_export_group = QWidget(page)
        export = QVBoxLayout(self.dejitter_export_group)
        export.setContentsMargins(8, 8, 8, 8)
        self.dejitter_export_intersection_check = QCheckBox('仅导出共同无黑边范围（交集）')
        self.dejitter_export_intersection_check.setChecked(editor_options.DEJITTER_EXPORT_INTERSECTION)
        self.dejitter_export_intersection_check.setToolTip('导出范围与预览交集框一致；不改变当前补边预览，不需要重新匹配。')
        self.dejitter_export_intersection_check.toggled.connect(self._on_dejitter_intersection_options_changed)
        export.addWidget(self.dejitter_export_intersection_check)
        output = QHBoxLayout()
        self.dejitter_output_format = QComboBox()
        formats = [(suffix, label) for suffix, label in editor_options.OUTPUT_FORMAT_OPTIONS
                   if suffix in {'png', 'jpg', 'jpeg'}]
        for suffix, label in formats or [('png', 'PNG'), ('jpg', 'JPG')]:
            self.dejitter_output_format.addItem(label, 'jpg' if suffix == 'jpeg' else suffix)
        self.dejitter_export_btn = QPushButton('去抖动导出全部')
        _make_primary_button(self.dejitter_export_btn)
        self.dejitter_export_btn.clicked.connect(self._on_dejitter_export_all)
        output.addWidget(self.dejitter_output_format)
        output.addWidget(self.dejitter_export_btn, 1)
        export.addLayout(output)
        self.dejitter_export_workspace_check = QCheckBox('导出后保存当前工作区，新建工作区加载成片')
        self.dejitter_export_workspace_check.setChecked(editor_options.DEJITTER_EXPORT_NEW_WORKSPACE)
        self.dejitter_export_workspace_check.setToolTip(
            '导出成功后保存当前工作区，再建立成片工作区并加载本次导出的图片。\n'
            '未命名的原工作区和新成片工作区自动保存到本次导出目录；保存失败时保留当前列表。')
        self.dejitter_export_workspace_check.toggled.connect(self._schedule_workspace_autosave)
        export.addWidget(self.dejitter_export_workspace_check)
        self.dejitter_export_progress = QProgressBar()
        self.dejitter_export_progress.setAccessibleName('去抖动导出进度')
        self.dejitter_export_progress.hide()
        export.addWidget(self.dejitter_export_progress)
        self.dejitter_export_complete = _add_progress_completion_label(
            export, self.dejitter_export_progress, '去抖动导出完成')
        layout.addStretch(1)
        self.dejitter_reference_check.toggled.connect(self._on_dejitter_options_changed)
        self.dejitter_reference_strength_slider.valueChanged.connect(self._on_dejitter_options_changed)
        self.dejitter_reference_strength_slider.valueChanged.connect(self._update_matching_summary)
        self._update_matching_summary()
        return page

    def _attach_recommendation_rows(self):
        """把推荐面板的部位/目标鸟/模型控件放进“1. 方式”表单的对应位置。"""
        controls = self.dejitter_subject_controls
        panel = self.dejitter_recommendation
        # 顺序：识别方法 → (高级)稳定部位 → (基本)两段式 → 目标鸟 → 跟随窗口/主体稳定模式… → (高级)实验/模型。
        controls.insert_row(controls.follow, panel.part_label, panel.part)
        controls.insert_row(controls.follow_window, panel.target_label, panel.target_button)
        controls.insert_row(None, panel.experimental)
        controls.insert_row(None, panel.model_label, panel.model_row)

    def _dejitter_next_step(self):
        """返回 (步骤序号, 下一步提示)；只依据当前状态，不触发任何任务。"""
        regions = getattr(self, '_dejitter_reference_regions', ())
        sequence = self._sequence_preview
        controls = self.dejitter_subject_controls
        advanced = controls.method.currentData() == 'subject_local'
        if self._sequence_exporting:
            return 3, '正在导出；可点击“取消导出”停止。'
        if self._sequence_worker is not None and sequence is None:
            return 2, '正在分析整组照片；完成后在“成片预览”检查效果。'
        if sequence is not None and not sequence.partial:
            return 3, '下一步：在“成片预览”检查效果，然后点击“去抖动导出全部”。'
        if sequence is not None:
            return 2, '部分照片未匹配：切到失败照片拖动选区修正位置，再重新分析。'
        if self.current_path is None:
            return 1, '请先导入并选择照片。'
        if not regions:
            target_missing = (not advanced and controls.follow.isChecked()
                              and not self.dejitter_recommendation.metadata.get('target'))
            step = ('下一步：点击“框选参考区”，在目标鸟上框选有纹理的局部；或先选择目标鸟再“一键推荐选区”。'
                    if advanced else
                    '下一步：点击“框选参考区”，在参考图上框选静止背景（树木、建筑等）；或“一键推荐选区”。')
            if target_missing:
                step += '\n两段式：建议先“选择目标鸟…”（参考图只有一只鸟时可省略）。'
            return 1, step
        return 2, '下一步：点击“分析并预览成片”。'

    def _sync_dejitter_hud(self):
        hud = getattr(self, 'dejitter_hud', None)
        if hud is None or not hasattr(self, 'dejitter_recommendation'):
            return
        step, next_step = self._dejitter_next_step()
        hud.set_step(step)
        panel = self.dejitter_recommendation
        if panel.worker is not None and panel.status.text():
            # 推荐/识鸟/模型安装进行中，显示其实时进度。
            hud.set_message(panel.status.text())
        else:
            hud.set_message(self._tracking_status_text, panel.status.toolTip())
        controls = self.dejitter_subject_controls
        edit_hint = next_step
        if not getattr(self, '_dejitter_reference_regions', ()):
            edit_hint += '\n' + controls.hint.text()
        elif self._current_edit_mode_id() == EDIT_MODE_REFERENCE_REGION:
            edit_hint += '\n' + _REGION_EDIT_HINT
        result_hint = next_step
        if self.dejitter_effective_status.text():
            result_hint += '\n' + self.dejitter_effective_status.text()
        hud.set_hints(edit_hint, result_hint)
        hud.set_view(self._dejitter_view)

    def _update_matching_summary(self, *_args):
        section = getattr(self, 'dejitter_matching_section', None)
        if section is None:
            return
        parts = []
        if self.dejitter_subject_controls.method.currentData() != 'subject_local':
            parts.append(self.dejitter_alignment_combo.currentText())
            parts.append('自动' if self.dejitter_matching_controls.mode.currentData() == 'auto' else '自定义')
        parts.append(f'强度 {self.dejitter_reference_strength_slider.value()}%')
        section.header_button.setText('匹配参数 · ' + ' · '.join(parts))

    def _on_dejitter_debug_changed(self):
        self._refresh_preview_label(preserve_view=True)
        if hasattr(self, 'ab_preview'):
            self.ab_preview.sync()
        self._schedule_workspace_autosave()

    def _sync_dejitter_method_controls(self):
        advanced = self.dejitter_subject_controls.method.currentData() == 'subject_local'
        self.dejitter_alignment_label.setVisible(not advanced)
        self.dejitter_alignment_combo.setVisible(not advanced)
        self.dejitter_alignment_combo.setEnabled(not advanced)
        self.dejitter_matching_controls.setEnabled(not advanced)
        self.dejitter_matching_controls.setVisible(not advanced)
        self.dejitter_auto_region_count.setEnabled(not advanced)
        self.dejitter_auto_region_count.setVisible(not advanced)
        self.dejitter_recommendation.sync(advanced)
        self._update_matching_summary()

    def _on_dejitter_method_changed(self):
        self._sync_dejitter_method_controls()
        self._on_dejitter_matching_changed()

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
        # 纯视图开关放在预览区，不影响分析结果，也不占参数面板。
        self.dejitter_show_intersection_check = QCheckBox('显示交集／并集框')
        self.dejitter_show_intersection_check.setChecked(editor_options.DEJITTER_SHOW_INTERSECTION)
        self.dejitter_show_intersection_check.setToolTip('成片预览显示交集框（所有照片共同覆盖的最大无黑边矩形）和并集框（整组完整范围）。\n开启补边可在成片中完整查看两框；状态面板中的示意图始终显示两个范围。')
        self.dejitter_show_intersection_check.toggled.connect(self._on_dejitter_intersection_options_changed)
        row.addWidget(self.dejitter_show_intersection_check)
        self.dejitter_debug_check = QCheckBox('DEBUG：局部对应点')
        self.dejitter_debug_check.setChecked(editor_options.DEJITTER_SUBJECT_DEBUG)
        self.dejitter_debug_check.setToolTip('绿点：通过拟合；红点：拒绝。线段连接关键帧点与当前实测点；不表示世界静止，不进入导出。')
        self.dejitter_debug_check.toggled.connect(self._on_dejitter_debug_changed)
        row.addWidget(self.dejitter_debug_check)
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
        if hasattr(self, 'dejitter_hud'):
            self.dejitter_hud.setVisible(active)
        self._update_dejitter_controls()
        self._restore_selected_preview_source()
        self._refresh_preview_label(preserve_view=True)
        if hasattr(self, 'sequence_transport'):
            self.sequence_transport.sync()

    def _set_dejitter_view(self, view):
        if view == 'result' and self._current_edit_mode_id() == 'overlay':
            self._set_edit_mode_button_checked('none')
            self.preview_label.canvas.set_edit_mode('none')
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
        if hasattr(self, 'dejitter_hud'):
            self.dejitter_hud.set_view(view)
            self._sync_dejitter_hud()
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
        if self._sequence_result_mode() and not transport.active and path_key(path) in self._sequence_quick_frames:
            self._sequence_force_quick_key = path_key(path)
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
        if getattr(self, '_sequence_force_quick_key', None) == key:
            self._sequence_force_quick_key = None
            self._show_sequence_preview_result(preserve_view=True)
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

    def _sync_edit_reference_toggle(self):
        button = getattr(self, 'dejitter_edit_reference_btn', None)
        modes = getattr(self, '_edit_mode_buttons', None)
        if button is None or not modes:
            return
        blocked = button.blockSignals(True)
        button.setChecked(modes[EDIT_MODE_REFERENCE_REGION].isChecked())
        button.blockSignals(blocked)

    def _on_edit_reference_toggled(self, checked):
        if checked:
            # 无参考图时在当前照片开始框选；已有参考图时定位到参考图。
            self._on_dejitter_draw()
        else:
            self._set_edit_mode_button_checked(EDIT_MODE_NONE)
            self._on_edit_mode_changed()
        self._sync_edit_reference_toggle()

    def _on_dejitter_auto_regions(self):
        panel = self.dejitter_recommendation
        if panel.worker is not None:
            # 推荐/识鸟运行中，同一按钮即为取消入口。
            if panel.task_kind != 'install':
                panel.cancel()
                self._update_dejitter_controls()
            return
        panel.recommend()

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
        if hasattr(self, "dejitter_recommendation"):
            self.dejitter_recommendation.cancel()
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
        self.dejitter_recommendation.sync(self.dejitter_subject_controls.method.currentData() == 'subject_local')
        regions = getattr(self, '_dejitter_reference_regions', ())
        source = self._dejitter_reference_source
        relay = self._relay_anchor_for_path(self.current_path)
        owner = relay or self._relay_owner_for_path(self.current_path)
        status = f'{Path(source).name} · {len(regions)} 个选区' if source and regions else '尚未选择参考区'
        if relay is not None:
            status = f'接力参考图 {relay.path.name} · {len(relay.regions)} 个接力选区（原参考：{status}）'
        self.dejitter_reference_status.setText(status)
        self.dejitter_reference_strength_slider.setEnabled(bool(regions))
        relay_block = self._relay_add_block_reason(self.current_path)
        self.dejitter_relay_add_btn.setEnabled(not relay_block)
        self.dejitter_relay_add_btn.setToolTip(self._dejitter_relay_tooltip
                                               + (f'\n\n当前不可用：{relay_block}' if relay_block else ''))
        self.dejitter_relay_remove_btn.setEnabled(relay is not None and self._sequence_worker is None)
        relay_text = self._relay_status_text()
        self.dejitter_relay_status.setText(relay_text)
        self.dejitter_relay_status.setVisible(bool(relay_text))
        self._sync_dejitter_method_controls()
        self.dejitter_edit_reference_btn.setEnabled(
            bool(self._dejitter_reference_source) or self.current_path is not None)
        panel = self.dejitter_recommendation
        recommending = panel.worker is not None and panel.task_kind != 'install'
        cancelling = recommending and panel.worker.isInterruptionRequested()
        self.dejitter_auto_regions_btn.setText(
            '正在停止…' if cancelling else
            ('取消识别' if panel.task_kind == 'detect' else '取消推荐') if recommending else '一键推荐选区')
        self.dejitter_auto_regions_btn.setToolTip(
            '停止当前推荐/识别；已有选区保持不变。' if recommending else
            '识别目标鸟及部位，通过抽样预检后推荐局部；需显式开启实验功能。'
            if self.dejitter_subject_controls.method.currentData() == 'subject_local' else
            '保留已有人工选区，按纹理质量分格补足右侧目标数量；会抽样预检，建议选区仍可手动调整或删除。')
        self.dejitter_auto_regions_btn.setEnabled(
            not self._sequence_shutdown and self._sequence_worker is None
            and self.current_path is not None and not cancelling
            and (panel.worker is None or recommending))
        detail = ('保留对齐后全部图像范围，缺失区域补黑；可在导出后进行二次裁切。'
                  if self.dejitter_pad_to_union_check.isChecked() else
                  '取对齐后整组画面交集；编辑构图中可查看最终保留范围。')
        if self.dejitter_reference_strength_slider.value() == 0:
            detail = '强度 0%：不补偿位移。' + detail
        self.dejitter_effective_status.setText(detail if regions else '请先在参考图框选一个或多个区域。')
        if hasattr(self, 'dejitter_region_list'):
            # 列表编辑当前照片所属的定义：接力段显示接力选区，其余显示原参考选区。
            listed = owner.regions if owner is not None else regions
            prefix = '接力选区' if owner is not None else '选区'
            labels = [f'{prefix} {index + 1}  ·  {round((box[2]-box[0])*100)}% × {round((box[3]-box[1])*100)}%'
                      for index, box in enumerate(listed)]
            auto = {tuple(r) for r in self.dejitter_recommendation.metadata.get('auto_regions', [])}
            from birdstamp.image_dejitter.bird_parts.pose import PART_LABELS
            part = PART_LABELS.get(self.dejitter_recommendation.metadata.get('resolved_part'), '背景')
            labels = [label + (f' · 第 {owner.index + 1} 张' if owner is not None else
                               f' · {part}（自动）' if tuple(box) in auto else ' · 人工')
                      for label, box in zip(labels, listed)]
            if labels != [self.dejitter_region_list.item(i).text() for i in range(self.dejitter_region_list.count())]:
                self.dejitter_region_list.blockSignals(True)
                self.dejitter_region_list.clear()
                self.dejitter_region_list.addItems(labels)
                self.dejitter_region_list.blockSignals(False)
            self.dejitter_delete_region_btn.setEnabled(bool(self.dejitter_region_list.selectedItems()))
            self.dejitter_reference_clear_btn.setEnabled(bool(relay.regions if relay is not None else regions))
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
                                            else detail if export_blocked else _EXPORT_NOTE)
        current_tracking = self._tracking_result_for_current()
        diagnostic = current_tracking.observation.summary() if current_tracking and current_tracking.observation else ''
        self._tracking_status_text = self._sequence_message + ('\n' + diagnostic if diagnostic else '')
        self.dejitter_tracking_status.setText(self._tracking_status_text)
        self._sync_dejitter_hud()
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
        self._set_edit_mode_button_checked(EDIT_MODE_NONE)
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
        self.dejitter_hud.set_progress(busy=True)

    def _on_sequence_progress_counts(self, token, current, total, stage):
        if not self._accept_sequence_signal(token) or self._sequence_progress_kind is None:
            return
        bar = (self.dejitter_export_progress if self._sequence_progress_kind == 'export'
               else self.dejitter_analysis_progress)
        bar.setRange(0, max(0, total))
        bar.setValue(max(0, min(current, total)))
        bar.setFormat(f'{stage} %v/%m · %p%' if total > 0 else stage)
        bar.setToolTip(stage)
        if total > 0:
            self.dejitter_hud.set_progress(max(0, min(current, total)), total)
        else:
            self.dejitter_hud.set_progress(busy=True)
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
        self.dejitter_hud.set_progress()

    def _on_sequence_failed(self, token, message):
        if self._accept_sequence_signal(token):
            self._sequence_export_workspace_result = None
            worker = self._sequence_worker
            analysis_failed = (self._sequence_progress_kind == 'analysis'
                               and not getattr(worker, 'restore_only', False))
            sources = tuple(seed.path for seed in getattr(worker, 'seeds', ()) or ())
            if not sources:
                sources = tuple(self._list_photo_paths())
            photo_label = _sequence_failure_photo_label(getattr(worker, 'failure_path', None), sources)
            location = f'（{photo_label}）' if photo_label else ''
            progress_label = '导出失败' if self._sequence_exporting else '分析失败'
            if photo_label:
                progress_label += f' · {photo_label}'
            self._finish_sequence_progress(progress_label)
            self._sequence_message = f'去抖动任务失败{location}：{message}'
            sequence = self._sequence_preview
            if analysis_failed and sequence is not None and sequence.partial:
                completed, total = len(sequence.jobs), len(sequence.all_jobs)
                label = f'分析失败 · {photo_label} · 已生成前 {completed}/{total} 张成片预览' if photo_label else (
                    f'分析失败 · 已生成前 {completed}/{total} 张成片预览')
                self.dejitter_analysis_progress.setRange(0, total)
                self.dejitter_analysis_progress.setValue(completed)
                self.dejitter_analysis_progress.setFormat(label)
                self.dejitter_analysis_progress.setToolTip(label)
                self._sequence_message += f'\n已生成前 {completed}/{total} 张成片预览，切换“成片预览”查看。'
            self._sequence_pending_path = None
            failure_path = getattr(worker, 'failure_path', None)
            if (analysis_failed and failure_path is not None and '接力' not in message
                    and not self._relay_add_block_reason(Path(failure_path), ignore_worker=True)):
                self._sequence_message += ('\n若此后画面已变化（原选区出画、被遮挡），可在失败照片点击“从当前照片接力追踪”，'
                                           '框选新的稳定纹理后重新分析。')
            if analysis_failed:
                self.ab_preview.compare_analysis_failure(failure_path)
            self.dejitter_debug_check.setChecked(True)
            self._update_dejitter_controls()
            self._set_status(self._sequence_message)

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
            photo_label = _sequence_failure_photo_label(
                sequence.failure.source_path, (job.path for job in sequence.all_jobs.values()))
            location = f'（{photo_label}）' if photo_label else ''
            self._sequence_message = (f'已生成前 {len(sequence.jobs)}/{len(sequence.all_jobs)} 张成片预览；'
                                      f'{kind} {sequence.output_size[0]} × {sequence.output_size[1]}。\n'
                                      f'后续分析失败{location}：{sequence.failure}')
        if sequence.alignments and any(a.status in ('rigid','fallback') for a in sequence.alignments.values()):
            corrected = sum(a.status == 'rigid' for a in sequence.alignments.values())
            fallback = sum(a.status == 'fallback' for a in sequence.alignments.values())
            self._sequence_message += f'\n旋转估计成功 {corrected} 张，退回平移 {fallback} 张（未纠正旋转）。'
        relayed = [sequence.relay_segments[k][0] for k in sequence.jobs if k in sequence.relay_segments]
        if relayed:
            self._sequence_message += f'\n使用 {len(set(relayed))} 张接力参考图，{len(relayed)} 张照片匹配接力选区。'
        follow = [plan.status for plan in sequence.subject_plans.values() if plan.status.startswith('bird_follow')]
        if follow:
            trend_only = follow.count('bird_follow_trend')
            self._sequence_message += (f'\n两段式：背景逐帧去抖，画框跟随目标鸟平滑趋势；'
                                       f'{len(follow)-trend_only} 张有检测' +
                                       (f'，{trend_only} 张漏检按邻帧趋势' if trend_only else '') + '。')
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
        force_quick = getattr(self, '_sequence_force_quick_key', None) == key
        frame = (self._sequence_quick_frames.get(key) if fast or force_quick else
                 self._sequence_frames.get(key) or self._sequence_quick_frames.get(key)) if sequence else None
        if sequence is not None and key in sequence.jobs and (force_quick or key not in self._sequence_frames) and not fast:
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
            from .editor_tracking_overlay import tracking_overlays, apply_frame_alignment, relay_prefix
            if frame.alignment and frame.alignment.rotated:
                focus = editor_core.resolve_focus_box_after_processing(
                    job.raw_metadata, source_width=width, source_height=height, crop_box=None,
                    outer_pad=(0, 0, 0, 0), apply_ratio_crop=False,
                    camera_type=editor_core.resolve_focus_camera_type_from_metadata(job.raw_metadata))
                apply_frame_alignment(state, frame, focus,
                    self._bird_box_cache.get(self._source_signature(self.current_path)),
                    sequence.regions_for(key, self._dejitter_reference_regions), sequence.tracking.get(key),
                    prefix=relay_prefix(sequence, key))
            else:
                state.reference_diagnostics = tracking_overlays(
                    sequence.regions_for(key, self._dejitter_reference_regions), sequence.tracking.get(key),
                    source_normalized_crop(frame.source_size, sequence.pixel_boxes[key]),
                    prefix=relay_prefix(sequence, key))
            if self.dejitter_debug_check.isChecked():
                from .editor_tracking_overlay import subject_debug_points
                state.subject_points = subject_debug_points(sequence.tracking.get(key),
                    source_normalized_crop(frame.source_size,sequence.pixel_boxes[key]) if key in sequence.pixel_boxes else None)
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
        relays = self._relay_settings_value() if self._relay_enabled() else []
        if relays:
            settings[RELAY_ANCHORS_KEY] = relays
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
        owner = self._relay_owner_for_path(self.current_path)
        if owner is not None:
            self._commit_relay_regions(owner.path, tuple(box for index, box in enumerate(owner.regions)
                                                         if index not in rows))
            self._refresh_preview_label(preserve_view=True)
            return
        self._drop_manual_matches_for_source(self._dejitter_reference_source)
        self._dejitter_reference_regions = tuple(box for index, box in enumerate(self._dejitter_reference_regions)
                                                if index not in rows)
        if not self._dejitter_reference_regions:
            self._dejitter_reference_source = None
            self.dejitter_reference_check.setChecked(False)
            self._clear_relay_anchors()
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
        if self._current_edit_mode_id() == "overlay":
            return False
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
        camera_crop = (source.info.get(RAW_FOCUS_CROP_KEY)
                       if source is not None and quick is None and source_entry is None else None)
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
            regions = self._definition_for_path(self.current_path)[1]
            state.reference_diagnostics = tracking_overlays(
                regions, self._tracking_diagnostics_for_path(self.current_path) if can_edit else tracked,
                prefix='接力' if self._relay_owner_for_path(self.current_path) is not None else '')
        if self.dejitter_debug_check.isChecked():
            from .editor_tracking_overlay import subject_debug_points
            state.subject_points = subject_debug_points(tracked)
        options.show_reference_regions = bool(state.reference_regions or state.reference_diagnostics)
        self.preview_label.apply_overlay_options(options)
        if source is not None or quick is not None or source_entry is not None:
            width, height = (quick.source_size if quick else source_entry[1] if source_entry is not None
                             else self._crop_display_source_size() or source.size)
            state.focus_box = editor_core.resolve_focus_box_after_processing(
                raw_metadata, source_width=width, source_height=height, crop_box=None,
                outer_pad=(0, 0, 0, 0), apply_ratio_crop=False,
                camera_type=editor_core.resolve_focus_camera_type_from_metadata(raw_metadata),
                camera_crop_box=camera_crop)
            from app_common.raw_preview_geometry import map_camera_focus_box
            state.bird_box = map_camera_focus_box(
                self._bird_box_cache.get(self._source_signature(self.current_path)) if self.current_path else None,
                camera_crop)
            self.preview_label.set_original_size(width, height)
        self.preview_label.set_cropped_size(None, None)
        from .preview_source_geometry import transform_source_overlays
        transform_source_overlays(state, camera_crop)
        self.preview_label.apply_overlay_state(state)
        self.preview_label.set_source_mode(
            self._preview_source_label('去抖动原图')
            if source is not None and quick is None and source_entry is None else '去抖动原图 · 缓存预览')
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
