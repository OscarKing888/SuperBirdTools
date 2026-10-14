"""Ordered, repeatable adjustment steps for any supported editing input."""
try:
    from PyQt6.QtCore import Qt, QSignalBlocker
    from PyQt6.QtWidgets import QComboBox,QPushButton,QHBoxLayout,QListWidget,QListWidgetItem,QWidget,QVBoxLayout,QFileDialog
except ImportError:
    from PyQt5.QtCore import Qt, QSignalBlocker
    from PyQt5.QtWidgets import QComboBox,QPushButton,QHBoxLayout,QListWidget,QListWidgetItem,QWidget,QVBoxLayout,QFileDialog

from app_common.photo_edit.catalog import ADJUSTMENT_TITLES,adjustment_defaults
from app_common.photo_edit.model import AdjustmentStep
from .image_info_tab_photo_edit import ImageInfoTabPanel_PhotoEdit
from .photo_edit_forms import AdjustmentForm

_CHECK = getattr(Qt,"CheckState",Qt)
_ROLE = getattr(Qt,"ItemDataRole",Qt)
_FLAG = getattr(Qt,"ItemFlag",Qt)


class ImageInfoTabPanel_ColorAdjust(ImageInfoTabPanel_PhotoEdit):
    tab_title = "调色"

    def create_editor(self,body):
        row = QHBoxLayout()
        self.kind = QComboBox(self)
        for kind,title in ADJUSTMENT_TITLES.items():
            self.kind.addItem(title,kind)
        add = QPushButton("添加",self)
        add.clicked.connect(self.add_step)
        row.addWidget(self.kind,1)
        row.addWidget(add)
        body.addLayout(row)
        self.steps = QListWidget(self)
        self.steps.setMinimumHeight(105)
        self.steps.setMaximumHeight(170)
        self.steps.setAccessibleName("调色步骤，按列表顺序执行")
        self.steps.currentRowChanged.connect(self._selected)
        self.steps.itemChanged.connect(self._enabled_changed)
        body.addWidget(self.steps)
        actions = QHBoxLayout()
        for label,callback in (("上移",lambda:self.move_step(-1)),("下移",lambda:self.move_step(1)),("删除",self.remove_step)):
            button = QPushButton(label,self)
            button.clicked.connect(callback)
            actions.addWidget(button)
        body.addLayout(actions)
        self.editor_host = QWidget(self)
        self.editor_layout = QVBoxLayout(self.editor_host)
        self.editor_layout.setContentsMargins(0,0,0,0)
        body.addWidget(self.editor_host)
        body.addStretch(1)
        self.form = None
        self._form_step_id = None

    def _step(self):
        session = self._session()
        row = self.steps.currentRow()
        if session and 0<=row<len(session.state.current.recipe.steps):
            return session.state.current.recipe.steps[row]

    def update_editor(self,recipe):
        item = self.steps.currentItem()
        selected = item.data(_ROLE.UserRole) if item else None
        row = self.steps.currentRow()
        with QSignalBlocker(self.steps):
            self.steps.clear()
            for index,step in enumerate(recipe.steps):
                item = QListWidgetItem(f"{index+1}. {ADJUSTMENT_TITLES[step.kind]}",self.steps)
                item.setData(_ROLE.UserRole,step.id)
                item.setFlags(item.flags()|_FLAG.ItemIsUserCheckable)
                item.setCheckState(_CHECK.Checked if step.enabled else _CHECK.Unchecked)
                if step.id==selected:
                    row = index
            self.steps.setCurrentRow(min(max(0,row),len(recipe.steps)-1))
        self._selected()

    def _selected(self,*_):
        step = self._step()
        if self._form_step_id!=(step.id if step else None):
            if self.form:
                self.editor_layout.removeWidget(self.form)
                self.form.deleteLater()
            self.form = AdjustmentForm(step.kind,self) if step else None
            self._form_step_id = step.id if step else None
            if self.form:
                self.editor_layout.addWidget(self.form)
                self.form.changed.connect(self._params_changed)
                self.form.invalid.connect(lambda error:self._error_changed(self.current_photo_path(),error))
                self.form.lut_requested.connect(self._import_lut)
        if self.form and step:
            self.form.set_values(step.params)

    def add_step(self):
        session = self._session()
        if not session:
            return
        recipe = session.state.current.recipe.validated()
        if len(recipe.steps)>=64:
            self._error_changed(self.current_photo_path(),"最多支持 64 个调色步骤。")
            return
        kind = self.kind.currentData()
        recipe.steps.append(AdjustmentStep(kind,adjustment_defaults(kind)))
        self.apply_recipe(recipe)
        self.steps.setCurrentRow(len(recipe.steps)-1)

    def _params_changed(self,value,commit):
        step,session = self._step(),self._session()
        if step and session and not self._refreshing:
            recipe = session.state.current.recipe.validated()
            recipe.steps[self.steps.currentRow()].params = value
            self.apply_recipe(recipe,commit)

    def _enabled_changed(self,item):
        session = self._session()
        if session and not self._refreshing:
            recipe = session.state.current.recipe.validated()
            for step in recipe.steps:
                if step.id==item.data(_ROLE.UserRole):
                    step.enabled = item.checkState()==_CHECK.Checked
            self.apply_recipe(recipe)

    def move_step(self,delta):
        session = self._session()
        row = self.steps.currentRow()
        if session and 0<=row+delta<len(session.state.current.recipe.steps) and row>=0:
            recipe = session.state.current.recipe.validated()
            recipe.steps[row],recipe.steps[row+delta] = recipe.steps[row+delta],recipe.steps[row]
            self.apply_recipe(recipe)

    def remove_step(self):
        session = self._session()
        row = self.steps.currentRow()
        if session and 0<=row<len(session.state.current.recipe.steps):
            recipe = session.state.current.recipe.validated()
            recipe.steps.pop(row)
            self.apply_recipe(recipe)

    def reset_editor(self):
        session = self._session()
        if session:
            recipe = session.state.current.recipe.validated()
            recipe.steps = []
            self.apply_recipe(recipe)

    def _import_lut(self):
        step = self._step()
        if not step:
            return
        path,_ = QFileDialog.getOpenFileName(self,"导入颜色查找表","","LUT (*.cube *.3dl *.look)")
        if not path:
            return
        step_id = step.id
        def prepare(snapshot,cancelled):
            from app_common.photo_edit.assets import import_lut
            from app_common.image_processing_types import check_cancelled
            check_cancelled(cancelled)
            return import_lut(snapshot.source,path)
        def apply(session,reference):
            recipe = session.state.current.recipe.validated()
            for item in recipe.steps:
                if item.id==step_id:
                    item.params.update(asset=reference,builtin="")
            session.edit(recipe)
        self.controller.run_operation("import-lut",prepare,apply=apply)
