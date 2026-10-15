"""Shared editing-panel chrome; rendering and persistence belong to the controller."""
from __future__ import annotations
import json
from pathlib import Path

try:
    from PyQt6.QtCore import Qt, QSignalBlocker, pyqtSignal
    from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
        QScrollArea,QMenu,QFileDialog,QMessageBox,QApplication)
except ImportError:
    from PyQt5.QtCore import Qt, QSignalBlocker, pyqtSignal
    from PyQt5.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
        QScrollArea,QMenu,QFileDialog,QMessageBox,QApplication)

from app_common.photo_edit.model import Recipe
from app_common.photo_edit.qt_controls import HistogramView
from app_common.superviewer_user_options import get_runtime_user_options
from .image_info_tab_base import ImageInfoTabPanel
from .photo_edit_controller import source_key


class ImageInfoTabPanel_PhotoEdit(ImageInfoTabPanel):
    editing_started = pyqtSignal(str)

    def __init__(self,controller,parent=None):
        self.controller = controller
        self._refreshing = False
        self._histogram_request = 0
        self._target_name = ""
        self._operation_message = ""
        super().__init__(parent)
        controller.state_changed.connect(self._state_changed)
        controller.render_ready.connect(self._render_ready)
        controller.render_failed.connect(self._render_failed)
        controller.error_changed.connect(self._error_changed)
        controller.operation_finished.connect(self._operation_finished)
        self._update_state()

    def create_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8,8,8,8)
        self.target = QLabel("选择一张照片开始编辑",self)
        self.target.setWordWrap(True)
        layout.addWidget(self.target)
        self.input = QComboBox(self)
        self.input.setAccessibleName("编辑底图")
        self.input.setToolTip("原始文件与不同降噪版本分别保留编辑参数；此处选择实际处理的像素来源。")
        self.input.currentIndexChanged.connect(self._input_changed)
        layout.addWidget(self.input)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(getattr(Qt,"TextInteractionFlag",Qt).TextSelectableByMouse)
        layout.addWidget(self.status)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(getattr(scroll,"Shape",type(scroll)).NoFrame)
        self.content = QWidget(scroll)
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0,0,0,0)
        self.histogram = HistogramView(self.content)
        body.addWidget(self.histogram)
        self.preview_note = QLabel("编辑预览使用底图完整分辨率，可放大至 100% 检查细节。",self.content)
        self.preview_note.setWordWrap(True)
        body.addWidget(self.preview_note)
        self.create_editor(body)
        scroll.setWidget(self.content)
        layout.addWidget(scroll,1)
        actions = QHBoxLayout()
        self.undo_button = QPushButton("撤销",self)
        self.redo_button = QPushButton("重做",self)
        self.reset_button = QPushButton("重置",self)
        self.export_button = QPushButton("导出…",self)
        for button in (self.undo_button,self.redo_button,self.reset_button,self.export_button):
            actions.addWidget(button)
        self.undo_button.clicked.connect(self.controller.undo)
        self.redo_button.clicked.connect(self.controller.redo)
        self.reset_button.clicked.connect(self.reset_editor)
        self.export_button.clicked.connect(self._export)
        layout.addLayout(actions)
        self.more = QPushButton("参数与恢复",self)
        menu = QMenu(self.more)
        menu.addAction("复制完整配方",self._copy)
        menu.addAction("粘贴完整配方",self._paste)
        menu.addSeparator()
        menu.addAction("导入 Camera RAW 设置…",self._import_settings)
        menu.addAction("另存 Camera RAW 设置…",self._export_settings)
        menu.addSeparator()
        menu.addAction("重新载入 XMP…",self._reload)
        menu.addAction("重试保存",self.controller.retry_save)
        menu.addAction("清理编辑预览缓存",self.controller.clear_cache)
        self.more.setMenu(menu)
        layout.addWidget(self.more)

    def create_editor(self,body):
        raise NotImplementedError

    def set_target_name(self,value):
        self._target_name = value
        self._update_state()

    def refresh_ui(self):
        source = self.current_photo_path()
        if source != getattr(self,"_message_source",None):
            self._operation_message = ""
            self._message_source = source
        self.controller.select(self.current_photo_path())
        self._update_state()
        self._request_histogram()

    def _session(self):
        return self.controller.session(self.current_photo_path()) if self.current_photo_path() else None

    def _matches(self,source):
        return bool(source) and source_key(source)==source_key(self.current_photo_path())

    def _state_changed(self,source):
        if self._matches(source) or not source and not self.current_photo_path():
            self._update_state()
            self._request_histogram()

    def _update_state(self):
        session = self._session()
        self._refreshing = True
        try:
            filename = Path(self.current_photo_path()).name if self.current_photo_path() else "选择一张照片开始编辑"
            self.target.setText((self._target_name+" · " if self._target_name else "")+filename)
            self.target.setToolTip(self.current_photo_path())
            self.content.setEnabled(session is not None)
            self.input.setEnabled(session is not None)
            self.reset_button.setEnabled(session is not None)
            busy = bool(self.controller._operations.get(source_key(self.current_photo_path())))
            self.export_button.setEnabled(session is not None and not busy)
            self.more.setEnabled(session is not None or bool(self.current_photo_path()))
            self.undo_button.setEnabled(bool(session and session.history.can_undo))
            self.redo_button.setEnabled(bool(session and session.history.can_redo))
            if self.controller._file_busy:
                for widget in (self.content,self.input,self.reset_button,self.export_button,
                               self.more,self.undo_button,self.redo_button):
                    widget.setEnabled(False)
            with QSignalBlocker(self.input):
                self.input.clear()
                self.input.addItem("原始文件","original")
                if session:
                    for key,entry in session.state.inputs.items():
                        if entry.mode=="denoised":
                            self.input.addItem("降噪 · "+Path(entry.path).name,key)
                    self.input.addItem("查找最新降噪版本…","__find__")
                    self.input.addItem("选择已有降噪成片…","__browse__")
                    self.input.setCurrentIndex(self.input.findData(session.state.active))
            error = self.controller.error(self.current_photo_path()) if self.current_photo_path() else ""
            if session:
                saved = ("等待自动保存…" if session.dirty else
                         "参数已保存到 XMP" if session.state.revision else "尚无调整；修改后自动保存")
                if busy:
                    saved = "正在处理导出或参数操作…"
                self.status.setText(error or self._operation_message or saved)
                if self.controller._file_busy:
                    self.status.setText("文件操作进行中，完成后可继续编辑。")
                self.update_editor(session.state.current.recipe)
            else:
                self.status.setText(error or ("正在读取编辑参数…" if self.current_photo_path() else ""))
                self.histogram.set_histogram(None)
        finally:
            self._refreshing = False

    def _request_histogram(self):
        if self.isVisible() and self._session():
            self._histogram_request = self.controller.request_render(self.current_photo_path(),self)

    def _render_ready(self,source,consumer,request_id,result):
        if consumer is self and request_id==self._histogram_request and self._matches(source):
            self.histogram.set_histogram(result.histogram)

    def _render_failed(self,source,consumer,request_id,error):
        if consumer is self and request_id==self._histogram_request:
            self._error_changed(source,error)

    def _error_changed(self,source,error):
        if self._matches(source):
            self._operation_message = error
            self.status.setText(error)

    def _operation_finished(self,source,operation,value):
        if self._matches(source):
            if operation in {"denoised","paste","import-settings","import-lut"}:
                self.editing_started.emit(source)
            if operation=="export":
                self._operation_message = "已导出："+str(value)
            elif operation=="import-settings":
                ignored = value.get("ignored",[])
                self._operation_message = f"已导入 {value.get('applied',0)} 项设置"+(f"；未支持：{', '.join(ignored)}" if ignored else "")
            else:
                self._operation_message = "操作完成"
            self._update_state()

    def _input_changed(self,*_):
        if self._refreshing:
            return
        value = self.input.currentData()
        if value=="__find__":
            self.controller.choose_denoised()
        elif value=="__browse__":
            path,_ = QFileDialog.getOpenFileName(self,"选择降噪成片",str(Path(self.current_photo_path()).parent),
                                               "降噪成片 (*.tif *.tiff *.jpg *.jpeg)")
            if path:
                self.controller.choose_denoised(path)
        else:
            self.controller.select_input(value)
            self.editing_started.emit(self.current_photo_path())
        self._update_state()

    def apply_recipe(self,recipe,commit=True):
        if not self._refreshing and self._session():
            self._operation_message = ""
            self.controller.edit(recipe,commit=commit)
            self.editing_started.emit(self.current_photo_path())

    def _export(self):
        directory = None
        if get_runtime_user_options()["photo_edit_output_mode"]=="ask":
            directory = QFileDialog.getExistingDirectory(self,"选择编辑成片目录",str(Path(self.current_photo_path()).parent))
            if not directory:
                return
        if self.controller.export(directory=directory):
            self._operation_message = "正在生成完整分辨率成片…"
            self._update_state()

    def _copy(self):
        session = self._session()
        if session:
            QApplication.clipboard().setText(json.dumps(dict(source=session.state.source,
                recipe=session.state.current.recipe.to_dict()),ensure_ascii=False))

    def _paste(self):
        try:
            text = QApplication.clipboard().text()
            if len(text.encode("utf-8"))>2*1024*1024:
                raise ValueError("剪贴板配方过大")
            data = json.loads(text)
            recipe = Recipe.from_dict(data["recipe"])
            origin = data["source"]
            if not isinstance(origin,str):
                raise ValueError("剪贴板配方缺少资源来源")
            def prepare(snapshot,cancelled):
                from app_common.photo_edit.assets import copy_recipe_assets
                from app_common.image_processing_types import check_cancelled
                check_cancelled(cancelled)
                copy_recipe_assets(origin,snapshot.source,recipe)
                return recipe
            self.controller.run_operation("paste",prepare,apply=lambda session,value:session.edit(value))
        except (ValueError,TypeError,KeyError) as exc:
            self._error_changed(self.current_photo_path(),f"无法粘贴配方：{exc}")

    def _import_settings(self):
        path,_ = QFileDialog.getOpenFileName(self,"导入 Camera RAW 设置","","XMP 设置 (*.xmp)")
        if not path:
            return
        def load(snapshot,cancelled):
            from app_common.photo_edit.native import import_settings
            from app_common.photo_edit.__main__ import read_bounded
            return import_settings(snapshot.current.recipe.camera,read_bounded(path))
        def apply(session,result):
            recipe = session.state.current.recipe.validated()
            recipe.camera = result["cameraRaw"]
            session.edit(recipe)
        self.controller.run_operation("import-settings",load,apply=apply)

    def _export_settings(self):
        suggested = str(Path(self.current_photo_path()).with_name(Path(self.current_photo_path()).stem+"_settings.xmp"))
        path,_ = QFileDialog.getSaveFileName(self,"另存 Camera RAW 设置",suggested,"XMP 设置 (*.xmp)")
        if not path:
            return
        def save(snapshot,cancelled):
            from app_common.photo_edit.native import export_settings
            from app_common.image_processing_types import check_cancelled
            data = export_settings(snapshot.current.recipe.camera)
            check_cancelled(cancelled)
            with Path(path).open("xb") as stream:
                stream.write(data)
            return path
        self.controller.run_operation("export-settings",save)

    def _reload(self):
        session = self._session()
        if session and session.dirty:
            buttons = getattr(QMessageBox,"StandardButton",QMessageBox)
            answer = QMessageBox.question(self,"重新载入参数","丢弃当前未保存草稿并读取 XMP？可以先复制配方保留当前调整。",
                                          buttons.Yes|buttons.No,buttons.No)
            if answer!=buttons.Yes:
                return
        self._operation_message = ""
        self.controller.reload(self.current_photo_path())
        self._update_state()

    def showEvent(self,event):
        super().showEvent(event)
        if self.current_photo_path():
            self.refresh_ui()

    def hideEvent(self,event):
        self.controller.cancel_render(self)
        super().hideEvent(event)

    def request_shutdown(self):
        self.controller.request_shutdown()

    def shutdown(self,*,wait_timeout_ms=0):
        return self.controller.shutdown(wait_timeout_ms=wait_timeout_ms)
