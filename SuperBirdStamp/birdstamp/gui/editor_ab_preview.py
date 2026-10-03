"""A/B 对照：照片列表刷新激活侧，独立视图模式与可选视野联动。"""
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QSplitter

from app_common.toggle_button import ToggleToolButton
from app_common.preview_toolbar import ViewportOverlayTools, iconize
from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, map_camera_focus_box
from app_common.preview_canvas import PreviewWithStatusBar
from birdstamp.decoders.preview_source import PREVIEW_SOURCE_MODE_KEY, PREVIEW_SOURCE_MESSAGE_KEY
from .editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState
from .editor_preview_decode_worker import EditorPreviewDecodeWorker
from .editor_sequence_preview_worker import EditorSequencePreviewWorker, pil_qimage
from .editor_tracking_overlay import tracking_overlays
from .preview_source_geometry import preview_to_camera_box, transform_source_overlays
from .edit_modes import EDIT_MODE_NONE, EDIT_MODE_REFERENCE_REGION
from .editor_utils import path_key
from .editor_preview_viewport import PreviewViewportPanel, align_viewport_rows
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from . import editor_core, editor_options
from birdstamp.image_dejitter.sequence_geometry import source_normalized_crop


class ABPreview(QObject):
    def __init__(self, editor, layout):
        super().__init__(editor)
        self.editor = editor
        self.path = None
        self.active_side = 'b'
        self.paths = ()
        self.worker = None
        self.bird_worker = None
        self._bird_attempt_signature = None
        self._image_is_quick = True
        self.token = 0
        self.request = None
        self.pending = False
        self.stopping = False
        self.frame = None
        self.image = None
        self.size = None
        self.camera_crop_box = None
        self.actual_source_mode = 'default'
        self.source_message = ''
        self.upgrade = QTimer(self)
        self.upgrade.setSingleShot(True)
        self.upgrade.setInterval(120)
        self.upgrade.timeout.connect(self._start)
        self.enabled = ToggleToolButton()
        self.enabled.setCheckable(True)
        self.enabled.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.enabled.setAccessibleName('A/B 对照')
        self.enabled.setChecked(editor_options.PREVIEW_AB_ENABLED)
        self.enabled.setToolTip('A/B 对照：开启或关闭左右对照预览。\n'
                                '点击任一视图激活，再从照片列表选图；另一侧保持当前照片。')
        self.linked = ToggleToolButton()
        self.linked.setText('同步缩放/移动')
        self.linked.setCheckable(True)
        self.linked.setToolTip('开启时锁定两侧当前的缩放比例和相对位置；之后同步缩放和平移变化。\n'
                               '启用联动会关闭自动焦点居中；重新开启焦点居中则退出联动。')
        iconize(self.enabled, "compare", "A/B 对照")
        iconize(self.linked, "link", "同步缩放/移动")
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.preview = PreviewWithStatusBar(canvas=EditorPreviewCanvas())
        self.preview.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.preview.canvas.reference_region_changed.connect(self._edit_reference_regions)
        self.preview.canvas.reference_match_edited.connect(self._edit_match)
        self.a_panel = PreviewViewportPanel('A', self.preview)
        self.mode, self.center = self.a_panel.mode, self.a_panel.center
        self.mode.setToolTip('A 独立选择原图或已分析的去抖动成片。')
        self.a_panel.source_mode_changed.connect(self._on_a_source_mode_changed)
        b_overlays = ViewportOverlayTools(focus=editor.show_focus_box_check, bird=editor.show_bird_box_check,
            grid=editor.preview_grid_combo, width=editor.preview_grid_line_width_combo,
            crop=editor.show_crop_effect_check, alpha=editor.crop_effect_alpha_slider, with_crop=True)
        self.a_panel.overlays.restore(b_overlays.state())
        self.a_panel.overlays.changed.connect(self._on_a_overlays_changed)
        self.b_panel = PreviewViewportPanel('B', editor.preview_label,
                                            center=editor.auto_focus_center_check, scale=editor.preview_scale_combo, overlays=b_overlays)
        self.b_mode = self.b_panel.mode
        self.b_mode.setToolTip('选择 B 的原图或已分析的去抖动成片。')
        self.b_mode.activated.connect(self._choose_b_mode)
        self.b_panel.source_mode_changed.connect(self._on_b_source_mode_changed)
        self.splitter.addWidget(self.a_panel)
        self.splitter.addWidget(self.b_panel)
        self.geometry_timer = QTimer(self)
        self.geometry_timer.setSingleShot(True)
        self.geometry_timer.timeout.connect(self._align_rows)
        for panel in (self.a_panel, self.b_panel):
            panel.metrics_changed.connect(self._schedule_alignment)
        self._align_rows()
        self.splitter.setSizes([500, 500])
        layout.addWidget(self.splitter, 1)
        self.a_panel.setVisible(self.enabled.isChecked())
        self.enabled.toggled.connect(self._toggle)
        self.mode.currentIndexChanged.connect(self._on_a_mode_changed)
        self.a_panel.activated.connect(lambda: self.activate('a'))
        self.b_panel.activated.connect(lambda: self.activate('b'))
        from .editor_ab_view_link import ABViewLink
        self.view_link = ABViewLink(self)
        self.linked.setChecked(editor_options.PREVIEW_AB_LINKED)
        self._update_active_panels()
        self._sync_controls()

    def _update_active_panels(self):
        compare_mode = self.enabled.isChecked()
        self.a_panel.set_active(self.active_side == 'a', compare_mode=compare_mode)
        self.b_panel.set_active(self.active_side == 'b', compare_mode=compare_mode)

    def _schedule_alignment(self):
        if not self.stopping and not self.geometry_timer.isActive():
            self.geometry_timer.start(0)

    def _align_rows(self):
        align_viewport_rows(self.a_panel, self.b_panel)

    def _sync_controls(self):
        editor = self.editor
        result = editor._sequence_result_mode()
        self.b_mode.setCurrentIndex(1 if result else 0)
        self.b_mode.setVisible(self.enabled.isChecked() or editor._dejitter_tab_active())
        self.linked.setVisible(self.enabled.isChecked())
        crop_tool = editor._edit_mode_buttons.get('crop_adjust')
        if crop_tool is not None:
            crop_tool.setEnabled(not self.enabled.isChecked() and not editor._dejitter_tab_active())
        # 每侧成片没有可裁切的外圈；禁用本侧遮罩但保留其设置。
        for panel, original in ((self.a_panel, self.mode.currentIndex() == 0), (self.b_panel, not result)):
            panel.overlays.crop.setEnabled(original)
            panel.overlays.alpha.setEnabled(original and panel.overlays.crop.isChecked())

    def _on_a_overlays_changed(self):
        self._sync_controls()
        self._display()
        self._schedule_bird_overlay()
        self.editor._schedule_workspace_autosave()

    def _choose_b_mode(self, index):
        self.activate('b')
        self.editor._set_dejitter_view('result' if index == 1 else 'edit')

    def _on_a_mode_changed(self, _index):
        self.editor.sequence_transport.stop(commit=False)
        self.sync(force=True)
        self.editor.sequence_transport.sync()

    def _on_a_source_mode_changed(self, _mode):
        if self.enabled.isChecked() and self.path is not None and self.mode.currentIndex() == 0:
            self.sync(force=True)

    def _on_b_source_mode_changed(self, _mode):
        editor = self.editor
        if editor.current_path is not None and not editor._sequence_result_mode():
            self.activate('b')
            item = editor._find_photo_item_by_path(editor.current_path)
            if item is not None:
                editor._on_photo_selected(item, None, target_view='b')

    def _toggle(self, enabled):
        self.a_panel.setVisible(enabled)
        self._update_active_panels()
        self._schedule_alignment()
        self._sync_controls()
        if enabled:
            self.splitter.setSizes([500, 500])
            self.sync(force=True)
            self.view_link.toggle(self.linked.isChecked())
        else:
            self._cancel()
            self.request = None
            self.activate('b')
        self.editor._restore_selected_preview_source()
        self.editor._refresh_preview_label(preserve_view=True)
        self.editor.sequence_transport.sync()

    def selected_path(self):
        return self.path if self.enabled.isChecked() and self.active_side == 'a' else self.editor.current_path

    def bind_transport(self, transport):
        self.a_panel.play.clicked.connect(lambda: self._play_side('a'))
        self.b_panel.play.clicked.connect(lambda: self._play_side('b'))
        transport.sync()

    def _play_side(self, side):
        self.activate(side)
        self.editor.sequence_transport.toggle()

    def activate(self, side, *, sync_selection=True):
        if self.stopping or (side == 'a' and not self.enabled.isChecked()) or side == self.active_side:
            return
        self.editor.sequence_transport.stop(commit=False)
        self.active_side = side
        self._update_active_panels()
        if sync_selection:
            item = self.editor._find_photo_item_by_path(self.selected_path()) if self.selected_path() else None
            if item is not None:
                previous = self.editor.photo_list.blockSignals(True)
                self.editor.photo_list.setCurrentItem(item)
                self.editor.photo_list.blockSignals(previous)
        self.editor.sequence_transport.sync()

    def select_a(self, path):
        self.path = Path(path)
        self.sync()
        self.editor.sequence_transport.sync()

    def route_photo_selection(self, path):
        if (self.enabled.isChecked() and self.active_side == 'a'
                and not self.editor._workspace_restore_in_progress() and not self.stopping):
            self.select_a(path)
            return True
        return False

    def compare_analysis_failure(self, failed_path):
        """左侧显示列表第一张，激活右侧并通过正常选图流程显示失败原图。"""
        if self.stopping or failed_path is None:
            return
        editor = self.editor
        paths = tuple(editor._list_photo_paths())
        failed = next((path for path in paths if path_key(path) == path_key(failed_path)), None)
        item = editor._find_photo_item_by_path(failed) if failed is not None else None
        if not paths or item is None:
            return
        self.activate('b', sync_selection=False)
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(0)
        self.mode.blockSignals(False)
        self.path = paths[0]
        editor.sequence_transport.stop(commit=False)
        editor.export_tabs.setCurrentWidget(editor.dejitter_page)
        if editor.photo_list.currentItem() is item:
            # 同一张失败也重走加载，避免保留文件损坏/变化前的旧像素。
            editor._on_photo_selected(item, None)
        else:
            editor.photo_list.setCurrentItem(item)
        if self.enabled.isChecked():
            self.sync(force=True)
        else:
            self.enabled.setChecked(True)
        editor._set_dejitter_view('edit')
        editor._sequence_upgrade_timer.stop()
        editor._sequence_pending_path = None

    def sync(self, *, force=False):
        if self.stopping:
            return
        self._sync_controls()
        self.b_panel.set_path(self.editor.current_path)
        if not self.enabled.isChecked():
            return
        editor = self.editor
        paths = tuple(editor._list_photo_paths())
        self.paths = paths
        if self.path not in paths:
            reference = getattr(editor, '_dejitter_reference_source', None)
            self.path = Path(reference) if reference and Path(reference) in paths else next(iter(paths), None)
        self.a_panel.set_path(self.path)
        self.b_panel.set_path(editor.current_path)
        self._sync_controls()
        sequence = editor._sequence_preview
        result = self.mode.currentIndex() == 1
        source_mode = self.a_panel.effective_source_mode() if not result else 'default'
        request = (self.path, result, id(sequence) if result else None,
                   image_file_signature(self.path) if self.path else None, source_mode)
        if not force and request == self.request:
            self._display()
            return
        self._cancel()
        self.request = request
        self.image = self.frame = self.size = None
        self._image_is_quick = True
        self.camera_crop_box = None
        self.actual_source_mode = 'default'
        self.source_message = ''
        self.preview.set_source_pixmap(None)
        self.preview.set_original_size(None, None)
        self.preview.set_cropped_size(None, None)
        self.preview.set_source_mode('A · 正在读取…' if self.path else 'A · 未选择')
        if self.path is None:
            return
        if not result and not editor._sequence_fast_preview_active():
            editor.sequence_transport.shared_scope.ensure(self.path, editor)
        key = path_key(self.path)
        frame = editor._sequence_quick_frames.get(key)
        if frame is not None:
            small = frame.image if result else frame.source_image
            self.image = (small.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                          if max(small.width(), small.height()) > 256 else small)
            self.size = frame.source_size
            self.frame = frame if result else None
        elif not result and hasattr(editor, 'sequence_transport'):
            source_entry = editor.sequence_transport.source_preview(self.path)
            if source_entry is not None:
                source_image, self.size = source_entry
                try:
                    self.image = pil_qimage(source_image)
                finally:
                    source_image.close()
        if result and (sequence is None or key not in sequence.jobs):
            self.preview.set_source_mode('成片待分析 · 可切回原图对照')
            return
        self._display()
        self.pending = True
        self.upgrade.start()

    def _cancel(self):
        self.preview.canvas.set_edit_mode(EDIT_MODE_NONE)
        self.token += 1
        self.pending = False
        self.upgrade.stop()
        self._bird_attempt_signature = None
        if self.bird_worker is not None:
            self.bird_worker.requestInterruption()
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
                                               max_long_edge=self.editor._preview_decode_max_long_edge(),
                                               source_mode=self.request[4],
                                               pool=self.editor._preview_action_pool, parent=self)
            worker.quick_decoded.connect(self._decoded)
            worker.decoded.connect(lambda token, path, image, size: self._decoded(token, path, image, size, full=True))
            worker.failed.connect(lambda token, _path, message: self._failed(token, message))
        self.worker = worker
        worker.finished.connect(self._finished)
        worker.start()

    def _accept(self, token):
        return (token == self.token and self.enabled.isChecked() and not self.stopping
                and not self.editor._sequence_fast_preview_active()
                and self.request is not None and self.path is not None
                and self.request[3] == image_file_signature(self.path))

    def _decoded(self, token, path, image, size, *, full=False):
        try:
            if not self._accept(token) or path_key(Path(path)) != path_key(self.path):
                return
            self._image_is_quick = not full
            self.camera_crop_box = image.info.get(RAW_FOCUS_CROP_KEY)
            self.actual_source_mode = image.info.get(PREVIEW_SOURCE_MODE_KEY, 'default')
            self.source_message = image.info.get(PREVIEW_SOURCE_MESSAGE_KEY, '')
            with image.convert('RGB') as rgb:
                self.image = pil_qimage(rgb)
            self.size = tuple(size)
            self.frame = None
            self._display()
            if full:
                self._schedule_bird_overlay()
        finally:
            image.close()

    def _schedule_bird_overlay(self):
        # A 只读本侧像素；转换与检测都在共享池执行，播放和缩略图阶段不提交。
        if (self.stopping or not self.enabled.isChecked() or self.path is None
                or self.image is None or self.frame is not None or self._image_is_quick
                or self.editor._sequence_fast_preview_active()
                or not self.a_panel.overlays.bird.isChecked() or self.bird_worker is not None):
            return
        signature = self.editor._source_signature(self.path)
        if signature in self.editor._bird_box_cache or signature == self._bird_attempt_signature:
            return
        self._bird_attempt_signature = signature
        from .bird_detect_worker import BirdDetectWorker
        worker = BirdDetectWorker(signature, self.image.copy(), parent=self,
            pool=self.editor._preview_action_pool, camera_crop=self.camera_crop_box)
        self.bird_worker = worker
        worker.result_ready.connect(self._bird_ready)
        worker.finished.connect(self._bird_finished)
        worker.start()

    def _bird_ready(self, signature, box):
        if self.stopping or self.sender() is not self.bird_worker:
            return
        self.editor._bird_box_cache[signature] = box
        if (self.path is not None and self.editor._source_signature(self.path) == signature
                and not self.editor._sequence_fast_preview_active()):
            self._display()

    def _bird_finished(self):
        worker = self.sender()
        if worker is self.bird_worker:
            self.bird_worker = None
            worker.deleteLater()
            self._schedule_bird_overlay()

    def _aligned(self, token, sequence, frame):
        if not self._accept(token) or sequence is not self.editor._sequence_preview:
            return
        if not sequence.files_current():
            self.editor._invalidate_sequence_preview()
            return
        self.camera_crop_box = None
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

    def _can_edit(self):
        return (self._accept(self.token) and self.image is not None and self.mode.currentIndex() == 0
                and self.editor._region_edit_enabled(self.path, original=True))

    def _edit_reference_regions(self, regions):
        if self._can_edit():
            source_regions = tuple(converted for box in regions
                                   if (converted := preview_to_camera_box(box, self.camera_crop_box)) is not None)
            self.editor._commit_source_reference_regions(self.path, source_regions)

    def _edit_match(self, index, box):
        if self._can_edit():
            converted = preview_to_camera_box(box, self.camera_crop_box)
            if box is None or converted is not None:
                self.editor._commit_manual_region_match(self.path, index, converted, original=True)

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
            camera_type=editor_core.resolve_focus_camera_type_from_metadata(metadata),
            camera_crop_box=self.camera_crop_box)
        bird = editor._bird_box_cache.get(editor._source_signature(self.path))
        bird = map_camera_focus_box(bird, self.camera_crop_box)
        state.bird_box = editor_core.transform_source_box_after_crop_padding(
            bird, crop_box=crop, source_width=width, source_height=height, pt=pad[0], pb=pad[1], pl=pad[2], pr=pad[3]) if crop else bird
        # 接力段照片按接力参考图的选区编号；参考图与接力参考图都可直接框选。
        regions = editor._definition_for_path(self.path)[1]
        prefix = '接力' if editor._relay_owner_for_path(self.path) is not None else ''
        tracking = editor._tracking_result_for_path(self.path)
        canvas = self.preview.canvas
        can_edit = self._can_edit()
        reference = editor._is_definition_photo(self.path)
        canvas.reference_region_creation_enabled = reference
        canvas.set_edit_mode(EDIT_MODE_REFERENCE_REGION if can_edit else EDIT_MODE_NONE)
        diagnostics = editor._tracking_diagnostics_for_path(self.path) if can_edit else tracking
        if can_edit:
            state.reference_regions = editor._editable_regions_for_path(self.path)
        if self.frame and self.frame.alignment and self.frame.alignment.rotated:
            from .editor_tracking_overlay import apply_frame_alignment
            focus = editor_core.resolve_focus_box_after_processing(
                metadata, source_width=width, source_height=height, crop_box=None,
                outer_pad=(0, 0, 0, 0), apply_ratio_crop=False,
                camera_type=editor_core.resolve_focus_camera_type_from_metadata(metadata))
            apply_frame_alignment(state, self.frame, focus, bird, regions, diagnostics, prefix=prefix)
        else:
            source_crop = source_normalized_crop(sequence.source_sizes[key], sequence.pixel_boxes[key]) if self.frame and sequence else None
            state.reference_diagnostics = tracking_overlays(regions, diagnostics, source_crop, prefix=prefix)
        if editor.dejitter_debug_check.isChecked():
            from .editor_tracking_overlay import subject_debug_points
            state.subject_points = subject_debug_points(diagnostics,
                source_normalized_crop(sequence.source_sizes[key],sequence.pixel_boxes[key])
                if self.frame and sequence and key in sequence.pixel_boxes else None)
        if not crop and reference:
            state.reference_regions = regions
            state.reference_diagnostics = ()
        options = editor._build_preview_overlay_options(self.a_panel.overlays)
        options.show_reference_regions = True
        options.show_crop_effect = False
        if self.frame and sequence and editor.dejitter_show_intersection_check.isChecked():
            from birdstamp.export_stage.sequence_intersection import normalized_intersection_box, normalized_union_box
            state.intersection_box = normalized_intersection_box(sequence)
            state.union_box = normalized_union_box(sequence)
        if not self.frame and sequence and key in sequence.pixel_boxes and not editor.dejitter_pad_to_union_check.isChecked():
            state.crop_effect_box = source_normalized_crop(sequence.source_sizes[key], sequence.pixel_boxes[key])
            state.alignment_crop_box = state.crop_effect_box
            options.show_crop_effect = self.a_panel.overlays.crop.isChecked()
        elif not self.frame and sequence and not editor.dejitter_pad_to_union_check.isChecked():
            from .editor_tracking_overlay import apply_alignment_crop
            if apply_alignment_crop(state, sequence, key):
                options.show_crop_effect = self.a_panel.overlays.crop.isChecked()
        if self.frame is None:
            transform_source_overlays(state, self.camera_crop_box)
        self.preview.apply_overlay_options(options)
        self.preview.apply_overlay_state(state)
        self.preview.set_original_size(width, height)
        self.preview.set_cropped_size(*(self.frame.output_size if self.frame else (None, None)))
        source_label = {'default': '原图', 'raw': '显示 RAW', 'denoised': '显示降噪'}.get(self.actual_source_mode, '原图')
        source_status = f'A · {self.source_message or source_label}'
        self.preview.set_source_mode('A · 去抖动成片' if self.frame else source_status)
        first = self.preview.canvas._source_pixmap is None
        self.preview.set_source_pixmap(QPixmap.fromImage(self.image), reset_view=first, preserve_view=not first,
                                       preserve_scale=not first)

    def shutdown(self):
        self.stopping = True
        self.geometry_timer.stop()
        self._cancel()
        return self.worker is None and self.bird_worker is None
