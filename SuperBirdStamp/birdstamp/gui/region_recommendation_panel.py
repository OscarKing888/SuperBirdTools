"""选区推荐的后台任务与模型安装；完成前持有 QThread，结果按快照核验。"""
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QCheckBox,QPushButton,
                             QLabel,QFileDialog,QDialog,QDialogButtonBox)
from birdstamp.image_dejitter.region_recommendation import recommend_regions, normalize_recommendation
from birdstamp.image_dejitter.recognition import RECOMMENDATION_KEY
from birdstamp.image_dejitter.bird_parts.pose import PART_LABELS
from birdstamp.image_dejitter.bird_parts.model_store import install_model
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from .bird_model_status import BirdModelStatus


class RecommendationWorker(QThread):
    ready = pyqtSignal(object)
    message = pyqtSignal(str)
    preview = pyqtSignal(object)

    def __init__(self, task, parent):
        super().__init__(parent)
        self.task=task

    def run(self):
        try:
            result=self.task(self.isInterruptionRequested,self.message.emit,self.preview.emit)
            if not self.isInterruptionRequested():
                self.ready.emit(result)
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.message.emit(str(exc))


class RegionRecommendationPanel(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor=editor
        self.worker=None
        self.metadata={}
        self._snapshot=None
        self._installing=False
        self._shutdown=False
        self._draft_context=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        row=QHBoxLayout()
        self.part_label=QLabel('稳定部位');row.addWidget(self.part_label)
        self.part=QComboBox()
        for value,label in PART_LABELS.items():self.part.addItem(label,value)
        row.addWidget(self.part)
        self.target_button=QPushButton('选择目标鸟')
        self.target_button.clicked.connect(self.choose_target)
        row.addWidget(self.target_button);layout.addLayout(row)
        self.experimental=QCheckBox('使用实验性部位识别（需检查推荐位置）')
        self.experimental.setToolTip('真实样本的背身和遮挡仍可能误识别；未作为正式默认能力开放。')
        layout.addWidget(self.experimental)
        row=QHBoxLayout()
        self.download=QPushButton('下载部位模型（109 MiB）')
        self.offline=QPushButton('导入模型')
        self.cancel_button=QPushButton('取消')
        self.download.clicked.connect(lambda:self.install())
        self.offline.clicked.connect(self.import_model)
        self.cancel_button.clicked.connect(self.cancel)
        for button in (self.download,self.offline,self.cancel_button):row.addWidget(button)
        layout.addLayout(row)
        self.status=QLabel('一键推荐会抽样预检；已有人工选区会保留。');self.status.setWordWrap(True)
        layout.addWidget(self.status)
        from .region_candidate_preview import RegionCandidatePreview
        self.candidates=RegionCandidatePreview(self)
        self.candidates.adopt.connect(self.adopt_candidate)
        layout.addWidget(self.candidates)
        self.part.currentIndexChanged.connect(self.options_changed)
        self.experimental.toggled.connect(self.options_changed)
        self.model_status = BirdModelStatus(self)
        self.model_status.changed.connect(self._sync_model_buttons)
        self._sync_model_buttons()

    def options_changed(self):
        self.cancel()
        self.editor._schedule_workspace_autosave()

    def set_metadata(self, value):
        self.metadata=normalize_recommendation(value)
        for control in (self.part,self.experimental):control.blockSignals(True)
        self.part.setCurrentIndex(max(0,self.part.findData(self.metadata.get('part','auto'))))
        self.experimental.setChecked(self.metadata.get('experimental',False))
        for control in (self.part,self.experimental):control.blockSignals(False)

    def settings(self):
        value=dict(self.metadata,version=1,experimental=self.experimental.isChecked())
        value['part']=self.part.currentData()
        return normalize_recommendation(value)

    def edited_regions(self, regions):
        # 原样保留的自动框仍有来源；拖动/缩放后的框立即成为人工框。
        current={tuple(r) for r in regions}
        self.metadata['auto_regions']=[r for r in self.metadata.get('auto_regions',[]) if tuple(r) in current]
        if not current:
            self.metadata={}

    def context(self):
        e=self.editor
        paths=tuple(e._list_photo_paths())
        reference=Path(e._dejitter_reference_source or e.current_path) if e.current_path else None
        return (str(e.current_path),str(reference),image_file_signature(reference) if reference else None,
                tuple(image_file_signature(Path(p)) for p in paths),
                tuple(e._dejitter_reference_regions),e.dejitter_subject_controls.method.currentData(),
                e.dejitter_subject_controls.follow.isChecked(),
                repr(e.dejitter_matching_controls.settings()),self.part.currentData(),
                self.experimental.isChecked(),e.dejitter_auto_region_count.value(),repr(self.metadata.get('target')))

    def sync(self, advanced):
        for control in (self.part_label,self.part,self.experimental,self.download,self.offline):
            control.setVisible(advanced)
        self.target_button.setVisible(advanced or self._follow_mode())
        self.cancel_button.setEnabled(self.worker is not None)
        self._sync_model_buttons()
        if self._draft_context is not None and self._draft_context != self.context():
            self.candidates.clear();self._draft_context=None
        self.candidates.apply.setEnabled(self.worker is None and not self._shutdown and self._draft_context is not None)

    def _sync_model_buttons(self):
        status = self.model_status.status
        installing = self.worker is not None and self._installing
        labels = {'checking':'正在校验模型…', 'ready':'下载完成 ✅',
                  'invalid':'重新下载部位模型（109 MiB）', 'missing':'下载部位模型（109 MiB）'}
        self.download.setText('正在安装模型…' if installing else labels[status.state])
        self.download.setToolTip(status.message)
        self.download.setEnabled(not self._shutdown and self.worker is None
                                 and status.state in ('missing','invalid'))
        self.offline.setEnabled(not self._shutdown and self.worker is None and status.state != 'checking')

    def cancel(self):
        self._snapshot=None
        self.candidates.clear();self._draft_context=None
        if self.worker:self.worker.requestInterruption()

    def shutdown(self):
        self._shutdown=True;self.cancel()
        model_stopped = self.model_status.shutdown()
        return self.worker is None and model_stopped

    def start(self, task, *, installing=False):
        if self.worker or self._shutdown:return
        self._installing=installing
        self._snapshot=self.context()
        worker=RecommendationWorker(task,self)
        self.worker=worker
        worker.message.connect(self.on_message)
        worker.ready.connect(self.on_ready)
        worker.preview.connect(self.on_candidates)
        worker.finished.connect(self.on_finished)
        worker.finished.connect(worker.deleteLater)
        self.editor._update_dejitter_controls()
        worker.start()

    def on_message(self, text):
        if self.sender() is self.worker and not self._shutdown and not self.worker.isInterruptionRequested():
            self.status.setText(text)

    def on_finished(self):
        if self.sender() is self.worker:
            self.worker=None
            if self._installing:
                self.model_status.refresh(force=True)
        if not self._shutdown:self.editor._update_dejitter_controls()

    def install(self, source=None):
        if self.model_status.status.state == 'checking':return
        if source is None and self.model_status.status.state == 'ready':return
        self.start(lambda cancelled,progress,preview:install_model(source,cancelled=cancelled,
            progress=lambda n,t:progress(f'模型安装：{n*100/t:.0f}%')),installing=True)

    def import_model(self):
        path,_=QFileDialog.getOpenFileName(self,'导入官方 AK 鸟类 HRNet-W32 权重','','PyTorch (*.pth)')
        if path:self.install(path)

    def _follow_mode(self):
        controls=getattr(self.editor,'dejitter_subject_controls',None)
        return (controls is not None and controls.method.currentData() != 'subject_local'
                and controls.follow.isChecked())

    def choose_target(self):
        if self.worker:return
        self.metadata.pop('target',None)
        self.editor._invalidate_reference_tracking('目标鸟已变化，请重新推荐并分析。')
        if self._follow_mode():
            self.detect_targets()
        else:
            self.recommend()

    def detect_targets(self):
        """两段式跟随只需目标鸟，不生成选区；结果走同一选择对话框与快照核验。"""
        e=self.editor
        if self.worker or e._sequence_worker is not None or e._sequence_shutdown or e.current_path is None:return
        reference=Path(e._dejitter_reference_source or e.current_path)
        def task(cancelled,progress,preview):
            from birdstamp.decoders.image_decoder import decode_image
            from birdstamp.image_dejitter.bird_observation_cache import detect_cached
            from birdstamp.image_dejitter.region_recommendation import Recommendation
            progress('识别参考图中的鸟…')
            with decode_image(reference,decoder='auto') as image:
                birds=detect_cached(reference,image,cancelled=cancelled)
            return Recommendation('choose_target' if birds else 'no_target',
                                  '请选择要跟随的目标鸟。' if birds else '参考图中没有识别到鸟。',birds=birds)
        self.start(task)

    def recommend(self):
        e=self.editor
        if self.worker or e._sequence_worker is not None or e._sequence_shutdown:return
        if not e._reference_regions_editable():
            e._on_edit_reference_photo();self.status.setText('请在参考图加载后点击一键推荐。');return
        if e.current_path is None:return
        from birdstamp.image_dejitter.matching_options import MatchingOptions
        auto={tuple(r) for r in self.metadata.get('auto_regions',[])}
        manual=tuple(r for r in e._dejitter_reference_regions if tuple(r) not in auto)
        reference=Path(e._dejitter_reference_source or e.current_path)
        paths=tuple(e._list_photo_paths())
        kwargs=dict(method=e.dejitter_subject_controls.method.currentData(),existing=manual,
                    target=self.metadata.get('target'),part=self.part.currentData(),
                    target_count=e.dejitter_auto_region_count.value(),experimental=self.experimental.isChecked(),
                    options=MatchingOptions.from_settings(e.dejitter_matching_controls.settings()))
        self.candidates.clear();self._draft_context=None
        self.start(lambda cancelled,progress,preview:recommend_regions(reference,paths,cancelled=cancelled,
            progress=progress,candidate_callback=preview,**kwargs))

    def _show_candidates(self, result):
        self._draft_context=self.context() if result.candidates else None
        self.candidates.set_result(result,self.editor.current_source_image)
        self.candidates.apply.setEnabled(self.worker is None and self._draft_context is not None)

    def on_candidates(self, result):
        if (self.sender() is self.worker and not self._shutdown and not self.worker.isInterruptionRequested()
                and self._snapshot is not None and self._snapshot==self.context()):
            self._show_candidates(result)

    def adopt_candidate(self, candidate):
        e=self.editor
        if self.worker or self._draft_context is None or self._draft_context!=self.context() or self._shutdown:return
        result=self.candidates.result
        old_auto={tuple(r) for r in self.metadata.get('auto_regions',[])}
        if any(tuple(r) not in old_auto for r in e._dejitter_reference_regions):
            self.status.setText('已保留人工选区。请先处理人工区冲突，再采用候选，避免混合不同部位。');return
        metadata=dict(result.metadata,part=candidate.part,resolved_part=candidate.part,auto_regions=[])
        e._commit_source_reference_regions(e.current_path,candidate.regions)
        self.set_metadata(metadata)
        e._on_output_settings_changed();e._schedule_workspace_autosave()
        e._on_dejitter_draw()
        self.status.setText('已采用为人工待修正区；请在参考图调整，完整分析通过后才能导出。')
        e._sequence_message=self.status.text();e._update_dejitter_controls()
        e._refresh_preview_label(preserve_view=True)

    def on_ready(self, result):
        if self.sender() is not self.worker or self._shutdown or self.worker.isInterruptionRequested():return
        if self._installing:
            self.status.setText('部位模型已安装；照片在本机处理。');return
        if self._snapshot is None or self._snapshot != self.context() or self.editor._sequence_shutdown:
            self.status.setText('照片或设置已变化，已丢弃旧推荐。');return
        self.status.setText(result.message)
        if result.status == 'choose_target':
            self.show_targets(result.birds);return
        if result.status != 'ready':
            self._show_candidates(result)
            reasons=list(dict.fromkeys(
                f"{d.get('file','')} {PART_LABELS.get(d.get('part'),'')}：{d['reason']}"
                for d in result.diagnostics if d.get('reason') and not d.get('passed',False)))
            detail='\n'.join(reasons[:3])
            message=result.message + ('\n'+detail if detail else '')
            self.status.setText(message)
            self.status.setToolTip('\n'.join(reasons))
            self.editor._sequence_message=message
            self.editor._update_dejitter_controls()
            return
        e=self.editor
        old_auto={tuple(r) for r in self.metadata.get('auto_regions',[])}
        manual=tuple(r for r in e._dejitter_reference_regions if tuple(r) not in old_auto)
        e._commit_source_reference_regions(e.current_path,(*manual,*result.regions))
        self.metadata=result.metadata
        label=PART_LABELS.get(result.part,'背景')
        self.status.setText(f'推荐：{label}。{result.message}')
        e._on_output_settings_changed();e._schedule_workspace_autosave();e._update_dejitter_controls()
        e._sequence_message=self.status.text()
        self._show_candidates(result)
        e._update_dejitter_controls()
        e._refresh_preview_label(preserve_view=True)

    def show_targets(self, birds):
        from PIL.ImageQt import ImageQt
        dialog=QDialog(self);dialog.setWindowTitle('选择要稳定的目标鸟')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('点击目标鸟。确认后点击“分析并预览成片”。' if self._follow_mode()
                                else '点击目标鸟。确认后再次点击“一键推荐”。'))
        image=self.editor.current_source_image
        for index,bird in enumerate(birds):
            button=QPushButton(f'鸟 {index+1} · 置信度 {bird.confidence:.0%}')
            if image:
                with image.crop(tuple(round(v*s) for v,s in zip(bird.box,(*image.size,*image.size)))) as crop:
                    crop.thumbnail((140,100));button.setIcon(QIcon(QPixmap.fromImage(ImageQt(crop))))
                    from PyQt6.QtCore import QSize
                    button.setIconSize(QSize(140,100))
            def select(checked=False, box=bird.box):
                self.metadata['target']=box
                self.target_button.setText('更换目标鸟')
                self.editor._invalidate_reference_tracking('目标鸟已确认，请重新推荐并分析。')
                self.editor._on_output_settings_changed()
                self.editor._schedule_workspace_autosave();dialog.accept()
            button.clicked.connect(select);layout.addWidget(button)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec()
