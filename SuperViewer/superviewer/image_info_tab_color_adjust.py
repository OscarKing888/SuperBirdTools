"""Flat adjustment groups backed by the existing ordered, repeatable recipe."""
from copy import deepcopy

try:
    from PyQt6.QtCore import QSignalBlocker
    from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton, QToolButton, QMenu, QHBoxLayout, QVBoxLayout, QWidget, QFileDialog
except ImportError:
    from PyQt5.QtCore import QSignalBlocker
    from PyQt5.QtWidgets import QCheckBox, QLabel, QPushButton, QToolButton, QMenu, QHBoxLayout, QVBoxLayout, QWidget, QFileDialog

from app_common.collapsible_section import CollapsibleSection
from app_common.photo_edit.catalog import ADJUSTMENT_TITLES, adjustment_defaults
from app_common.photo_edit.model import AdjustmentStep
from .image_info_tab_photo_edit import ImageInfoTabPanel_PhotoEdit
from .photo_edit_forms import AdjustmentForm


def adjustment_sections(steps):
    """Include unused types without changing the relative order of saved steps."""
    sections = [(step.kind, step) for step in steps]
    present = {step.kind for step in steps}
    ranks = {kind: index for index, kind in enumerate(ADJUSTMENT_TITLES)}
    for kind in ADJUSTMENT_TITLES:
        if kind not in present:
            index = next((i for i, (other, _) in enumerate(sections)
                          if ranks[other] > ranks[kind]), len(sections))
            sections.insert(index, (kind, None))
    return sections


class AdjustmentGroup(CollapsibleSection):
    """Visibility belongs to the section; enabled/parameters belong to the recipe."""

    def __init__(self, kind, panel):
        super().__init__(ADJUSTMENT_TITLES[kind], panel)
        self.kind = kind
        self.step_id = None
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.enabled = QCheckBox("启用", self.body)
        self.enabled.setAccessibleName("启用" + ADJUSTMENT_TITLES[kind])
        self.position = QLabel(self.body)
        self.actions = QToolButton(self.body)
        self.actions.setText("操作")
        self.actions.setAccessibleName(ADJUSTMENT_TITLES[kind] + "操作")
        self.actions.setPopupMode(getattr(QToolButton, "ToolButtonPopupMode", QToolButton).InstantPopup)
        menu = QMenu(self.actions)
        self.move_up = menu.addAction("上移", lambda: panel.move_step(self, -1))
        self.move_down = menu.addAction("下移", lambda: panel.move_step(self, 1))
        menu.addSeparator()
        self.duplicate = menu.addAction("复制此调整", lambda: panel.duplicate_step(self))
        self.remove = menu.addAction("移除此调整", lambda: panel.remove_step(self))
        self.actions.setMenu(menu)
        row.addWidget(self.enabled)
        row.addStretch(1)
        row.addWidget(self.position)
        row.addWidget(self.actions)
        body.addLayout(row)
        self.form = AdjustmentForm(kind, self.body)
        body.addWidget(self.form)
        self.enabled.toggled.connect(lambda value: panel.set_step_enabled(self, value))
        self.form.changed.connect(lambda value, commit: panel.set_step_params(self, value, commit))
        self.form.invalid.connect(lambda error: panel._error_changed(panel.current_photo_path(), error))
        self.form.lut_requested.connect(lambda: panel.import_lut(self))
        self.sync(None, None, 0)

    def sync(self, step, index, count):
        self.step_id = step.id if step else None
        enabled = bool(step and step.enabled)
        with QSignalBlocker(self.enabled):
            self.enabled.setChecked(enabled)
        self.form.setEnabled(enabled)
        values = step.params if step else adjustment_defaults(self.kind)
        # Save notifications and edits in other groups must not reset a focused field.
        if self.form.values != values:
            self.form.set_values(values)
        self.position.setText(f"第 {index + 1} 步" if index is not None else "")
        self.actions.setEnabled(step is not None)
        self.move_up.setEnabled(index is not None and index > 0)
        self.move_down.setEnabled(index is not None and index < count - 1)
        self.duplicate.setEnabled(step is not None and count < 64)
        self.enabled.setEnabled(step is not None or count < 64)
        suffix = "" if enabled else "（未启用）"
        self.setTitle(ADJUSTMENT_TITLES[self.kind] + suffix)


class ImageInfoTabPanel_ColorAdjust(ImageInfoTabPanel_PhotoEdit):
    tab_title = "调色"

    def create_editor(self, body):
        note = QLabel("已启用的调整按下方顺序处理；收起分组不关闭效果。", self)
        note.setWordWrap(True)
        body.addWidget(note)
        row = QHBoxLayout()
        self.expand_all = QPushButton("全部展开", self)
        self.collapse_all = QPushButton("全部收起", self)
        self.expand_all.clicked.connect(lambda: self.set_groups_expanded(True))
        self.collapse_all.clicked.connect(lambda: self.set_groups_expanded(False))
        row.addWidget(self.expand_all)
        row.addWidget(self.collapse_all)
        body.addLayout(row)
        host = QWidget(self)
        self.groups_layout = QVBoxLayout(host)
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.groups = [AdjustmentGroup(kind, self) for kind in ADJUSTMENT_TITLES]
        for group in self.groups:
            self.groups_layout.addWidget(group)
        body.addWidget(host)
        body.addStretch(1)

    def set_groups_expanded(self, expanded):
        for group in self.groups:
            group.set_expanded(expanded)

    def apply_theme(self, colors=None):
        super().apply_theme(colors)
        # Qt stylesheet widgets can retain their creation palette across an app
        # palette change. Propagate the host palette without recreating editors.
        for group in self.groups:
            for widget in (group, *group.findChildren(QWidget)):
                widget.setPalette(self.palette())

    def update_editor(self, recipe):
        remaining = list(self.groups)
        by_id = {group.step_id: group for group in remaining if group.step_id}
        indices = {step.id: i for i, step in enumerate(recipe.steps)}
        groups = []
        for kind, step in adjustment_sections(recipe.steps):
            group = by_id.get(step.id) if step else None
            if group is None or group.kind != kind:
                group = next((candidate for candidate in remaining
                              if candidate.kind == kind and candidate.step_id not in indices), None)
            if group is None:
                group = AdjustmentGroup(kind, self)
            else:
                remaining.remove(group)
            group.sync(step, indices.get(step.id) if step else None, len(recipe.steps))
            groups.append(group)
        for group in remaining:
            self.groups_layout.removeWidget(group)
            group.hide()
            group.deleteLater()
        for index, group in enumerate(groups):
            item = self.groups_layout.itemAt(index)
            if item is None or item.widget() is not group:
                self.groups_layout.insertWidget(index, group)
                group.show()
        self.groups = groups

    def _recipe_step(self, group):
        session = self._session()
        if session and not self._refreshing and group in self.groups:
            recipe = session.state.current.recipe.validated()
            index = next((i for i, step in enumerate(recipe.steps) if step.id == group.step_id), None)
            return recipe, index
        return None, None

    def set_step_enabled(self, group, enabled):
        recipe, index = self._recipe_step(group)
        if recipe is None:
            return
        if index is None:
            if not enabled or len(recipe.steps) >= 64:
                return
            # Empty groups occupy visible insertion slots, not recipe/history entries.
            index = sum(other.step_id is not None for other in self.groups[:self.groups.index(group)])
            recipe.steps.insert(index, AdjustmentStep(group.kind, adjustment_defaults(group.kind)))
        else:
            recipe.steps[index].enabled = enabled
        self.apply_recipe(recipe)

    def set_step_params(self, group, value, commit):
        recipe, index = self._recipe_step(group)
        if index is not None and recipe.steps[index].enabled:
            recipe.steps[index].params = value
            self.apply_recipe(recipe, commit)

    def move_step(self, group, delta):
        recipe, index = self._recipe_step(group)
        if index is not None and 0 <= index + delta < len(recipe.steps):
            recipe.steps[index], recipe.steps[index + delta] = recipe.steps[index + delta], recipe.steps[index]
            self.apply_recipe(recipe)

    def duplicate_step(self, group):
        recipe, index = self._recipe_step(group)
        if index is not None and len(recipe.steps) < 64:
            step = recipe.steps[index]
            recipe.steps.insert(index + 1, AdjustmentStep(step.kind, deepcopy(step.params), step.enabled))
            self.apply_recipe(recipe)

    def remove_step(self, group):
        recipe, index = self._recipe_step(group)
        if index is not None:
            recipe.steps.pop(index)
            self.apply_recipe(recipe)

    def reset_editor(self):
        session = self._session()
        if session:
            recipe = session.state.current.recipe.validated()
            recipe.steps = []
            self.apply_recipe(recipe)

    def import_lut(self, group):
        recipe, index = self._recipe_step(group)
        if index is None:
            return
        step_id = recipe.steps[index].id
        path, _ = QFileDialog.getOpenFileName(self, "导入颜色查找表", "", "LUT (*.cube *.3dl *.look)")
        if not path:
            return

        def prepare(snapshot, cancelled):
            from app_common.photo_edit.assets import import_lut
            from app_common.image_processing_types import check_cancelled
            check_cancelled(cancelled)
            return import_lut(snapshot.source, path)

        def apply(session, reference):
            recipe = session.state.current.recipe.validated()
            for item in recipe.steps:
                if item.id == step_id:
                    item.params.update(asset=reference, builtin="")
                    session.edit(recipe)
                    break

        self.controller.run_operation("import-lut", prepare, apply=apply)
