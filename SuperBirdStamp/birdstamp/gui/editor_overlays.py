"""主编辑器叠加层实例的绑定；配置优先级由无窗口模型处理。"""
from copy import deepcopy
from pathlib import Path
from PyQt6.QtWidgets import QGridLayout,QPushButton,QFileDialog,QMessageBox,QLabel
from birdstamp.overlays.model import document, effective_payload
from .overlay_panel import OverlayPanel
from .overlay_edit import OverlaySession, EDIT_MODE_OVERLAY
from .editor_utils import path_key
from .overlay_editor_dialog import OverlayEditorDialog


class _BirdStampOverlaysMixin:
    def _build_overlay_panel(self,form):
        self.overlay_panel=OverlayPanel(self)
        self.overlay_panel.setEnabled(False)
        self.overlay_panel.changed.connect(self._overlay_document_changed)
        self.overlay_panel.activateRequested.connect(self._activate_overlay_edit)
        self.overlay_summary = QLabel('请选择照片')
        self.overlay_summary.setWordWrap(True)
        form.addRow(self.overlay_summary)
        self.overlay_editor_button = QPushButton('编辑叠加层…')
        self.overlay_editor_button.clicked.connect(self._activate_overlay_edit)
        form.addRow(self.overlay_editor_button)
        row=QGridLayout()
        for index,(label,slot) in enumerate([('恢复模板',self._restore_overlay_template),('应用到选中',lambda:self._apply_overlays(False)),
                           ('应用到全部',lambda:self._apply_overlays(True)),('另存模板',self._save_overlay_template)]):
            button=QPushButton(label); button.clicked.connect(slot); row.addWidget(button,index//2,index%2)
        self.overlay_dialog = OverlayEditorDialog(self, self.overlay_panel, row)
        self.overlay_panel.selectionChanged.connect(self._update_overlay_summary)

    def _update_overlay_summary(self, *_args):
        following = '跟随模板' if self.overlay_panel.following else '当前照片已自定义'
        count = len(self.overlay_panel.doc['overlays'])
        self.overlay_summary.setText(f'{following} · {count} 个图层' if self.current_path else '请选择照片')
        selected = self.overlay_panel.selected()
        name = selected.get('name', '') if selected else '未选择图层'
        if not self.overlay_panel.isEnabled():
            name = '正在加载叠加层…' if self.current_path else ''
            self.overlay_summary.setText(name or '请选择照片')
        filename = self.current_path.name if self.current_path else '请选择照片'
        self.overlay_dialog.context_label.setText(f'{filename}\n{name}')
        self.overlay_dialog.context_label.setToolTip(str(self.current_path or ''))
        self.overlay_editor_button.setEnabled(self.overlay_panel.isEnabled())

    def _overlay_session(self):
        canvas=self.preview_label.canvas
        session=getattr(canvas,'overlay_session',None)
        if session is None:
            session=OverlaySession(canvas,self.overlay_panel,committed=self._overlay_commit_preview)
            canvas.overlay_exit_requested.connect(self._exit_overlay_edit)
        return session

    def _sync_overlay_panel(self,settings=None):
        if not hasattr(self,'overlay_panel'): return
        self.overlay_panel.setEnabled(self.current_path is not None and self.current_source_image is not None)
        settings=settings or self._build_current_render_settings()
        payload=self._resolve_template_payload_for_render(settings)
        context='photo:'+path_key(self.current_path) if self.current_path else 'placeholder'
        self.overlay_panel.set_document(payload,context,following=settings.get('overlay_override') is None)
        self._update_overlay_summary()
        self._overlay_session()

    def _overlay_document_changed(self,doc):
        self._overlay_override=None if self.overlay_panel.following else deepcopy(doc)
        self._update_overlay_summary()
        self._on_output_settings_changed()
        self._overlay_commit_preview()

    def _overlay_commit_preview(self):
        self._preview_debounce_timer.stop()
        self.render_preview()

    def _reveal_overlay_panel(self):
        self._update_overlay_summary()
        self.overlay_dialog.reveal()

    def _activate_overlay_edit(self):
        if self.current_path is None or self.current_source_image is None:
            self._set_edit_mode_button_checked('none')
            self._set_status('请选择照片后编辑叠加层，或在模板管理中编辑默认图层。')
            return
        self._reveal_overlay_panel()
        if hasattr(self,'sequence_transport'): self.sequence_transport.stop(commit=False)
        if self._dejitter_tab_active(): self.export_tabs.setCurrentIndex(0)
        self._dejitter_view='edit'
        if hasattr(self,'ab_preview'): self.ab_preview.activate('b')
        self._set_edit_mode_button_checked(EDIT_MODE_OVERLAY)
        # 新增图层后立即可见，显式启用对应的输出类别。
        enabled=self._current_pipeline_stage_enabled_map()
        if not enabled.get('template_overlay', True):
            enabled['template_overlay']=True
            self._set_pipeline_stage_enabled_map(enabled,save=True,mark_dirty=True)
        item=self.overlay_panel.selected()
        if item:
            widget={'badge':self.draw_text_check,'text':self.draw_text_check,'image':self.draw_images_check,'background':self.draw_banner_check}[item['type']]
            widget.setChecked(True)
        self._overlay_session()
        self.render_preview()
        self.preview_label.canvas.setFocus()
        self._schedule_workspace_autosave()
        name=self.current_path.name if self.current_path else '预览'
        self._set_status(f'编辑叠加层 · B · {name} | 拖动移动，角手柄缩放，顶部手柄旋转')

    def _exit_overlay_edit(self):
        self._set_edit_mode_button_checked('none')
        self.render_preview()
        self._schedule_workspace_autosave()

    def _restore_overlay_template(self):
        self.overlay_panel.flush_text()
        settings=self._build_current_render_settings(); settings['overlay_override']=None
        self.overlay_panel.commit(document(self._resolve_template_payload_for_render(settings)),following=True)

    def _apply_overlays(self,all_photos):
        self.overlay_panel.flush_text()
        paths=self._list_photo_paths() if all_photos else self._selected_photo_paths()
        snapshot=deepcopy(self.overlay_panel.doc)
        for path in paths:
            settings=self._render_settings_for_path(path,prefer_current_ui=False)
            settings['overlay_override']=deepcopy(snapshot)
            self.photo_render_overrides[path_key(path)]=self._photo_override_settings_from_snapshot(settings)
            self._mark_photo_export_dirty(path)
        if self.current_path and any(path_key(p)==path_key(self.current_path) for p in paths):
            self._overlay_override=deepcopy(snapshot)
        self._schedule_workspace_autosave(); self.render_preview()
        self._set_status(f'已将叠加层应用到 {len(paths)} 张照片。')

    def _save_overlay_template(self):
        self.overlay_panel.flush_text()
        from .editor_template import save_template_payload
        path,_=QFileDialog.getSaveFileName(self,'另存为模板',str(self.template_dir/'自定义叠加层.json'),'模板 (*.json)')
        if not path: return
        target=Path(path).with_suffix('.json')
        payload=effective_payload(self.current_template_payload,self.overlay_panel.doc)
        payload['name']=target.stem
        try:
            save_template_payload(target,payload)
            if target.parent.resolve() == self.template_dir.resolve():
                self.template_paths[target.stem]=target
                if self.template_combo.findText(target.stem)<0:
                    blocked=self.template_combo.blockSignals(True)
                    self.template_combo.addItem(target.stem)
                    self.template_combo.blockSignals(blocked)
            self._set_status(f'已保存模板: {target.name}')
        except Exception as exc:
            QMessageBox.warning(self,'模板保存失败',str(exc))

    def _capture_overlay_scene(self,base,scene,crop,settings,raw_metadata,outer_pad):
        from birdstamp.export_stage import normalize_pipeline_stage_order
        order=normalize_pipeline_stage_order(settings.get('pipeline_stage_order'))
        post=None
        if order.index('focus_overlay')>order.index('template_overlay') and self._is_preview_stage_enabled(settings,'focus_overlay'):
            source=self.current_source_image
            post=lambda image:self._render_focus_box_for_image(image,raw_metadata=raw_metadata,source_image=source,
                settings=settings,crop_box=crop,outer_pad=outer_pad,apply_ratio_crop=False)
        self._overlay_session().capture(base,scene,crop,postprocess=post)
