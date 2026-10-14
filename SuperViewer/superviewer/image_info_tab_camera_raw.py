"""Camera RAW: seven groups, with pixel work delegated to the shared controller."""
try:
    from PyQt6.QtCore import QSignalBlocker
    from PyQt6.QtWidgets import QCheckBox
except ImportError:
    from PyQt5.QtCore import QSignalBlocker
    from PyQt5.QtWidgets import QCheckBox

from app_common.photo_edit.catalog import camera_defaults
from .image_info_tab_photo_edit import ImageInfoTabPanel_PhotoEdit
from .photo_edit_forms import CameraRawForm


class ImageInfoTabPanel_CameraRaw(ImageInfoTabPanel_PhotoEdit):
    tab_title = "Camera RAW"

    def create_editor(self,body):
        self.enabled = QCheckBox("启用 Camera RAW 调整",self)
        body.addWidget(self.enabled)
        self.form = CameraRawForm(self)
        body.addWidget(self.form)
        self.enabled.toggled.connect(self._enabled_changed)
        self.form.changed.connect(self._camera_changed)
        self.form.invalid.connect(lambda error:self._error_changed(self.current_photo_path(),error))

    def update_editor(self,recipe):
        with QSignalBlocker(self.enabled):
            self.enabled.setChecked(recipe.camera_enabled)
        self.form.setEnabled(recipe.camera_enabled)
        self.form.set_values(recipe.camera)

    def _enabled_changed(self,value):
        session = self._session()
        if session and not self._refreshing:
            recipe = session.state.current.recipe.validated()
            recipe.camera_enabled = value
            self.apply_recipe(recipe)

    def _camera_changed(self,value,commit):
        session = self._session()
        if session:
            recipe = session.state.current.recipe.validated()
            recipe.camera = value
            self.apply_recipe(recipe,commit)

    def reset_editor(self):
        session = self._session()
        if session:
            recipe = session.state.current.recipe.validated()
            recipe.camera = camera_defaults()
            recipe.camera_enabled = True
            self.apply_recipe(recipe)
