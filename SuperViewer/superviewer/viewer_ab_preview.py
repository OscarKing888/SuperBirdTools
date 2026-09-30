# -*- coding: utf-8 -*-
"""SuperViewer A/B preview layout, selection ownership and linked viewports."""
from __future__ import annotations

import os
from pathlib import Path

from app_common.toggle_button import ToggleToolButton
from app_common.preview_canvas import configure_preview_scale_preset_combo, sync_preview_scale_preset_combo
from app_common.video import is_video
from app_common.image_formats import RAW_EXTENSIONS

from .qt_compat import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget, Qt,
    _Horizontal,
)

try:
    from PyQt6.QtCore import QEvent, QObject, pyqtSignal
except ImportError:
    from PyQt5.QtCore import QEvent, QObject, pyqtSignal

_POLICY = getattr(QSizePolicy, "Policy", QSizePolicy)
_EVENTS = getattr(QEvent, "Type", QEvent)
_ELIDE = getattr(Qt, "TextElideMode", Qt)


class ViewerViewportPanel(QWidget):
    activated = pyqtSignal()

    def __init__(self, name, preview, *, center=None, scale=None):
        super().__init__()
        self.name = name
        self.preview = preview
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.toolbar = QWidget(self)
        self.toolbar.setSizePolicy(_POLICY.Expanding, _POLICY.Fixed)
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.name_label = QLabel(name)
        row.addWidget(self.name_label)
        self.filename = QLabel("未选择")
        self.filename.setMinimumWidth(0)
        self.filename.setSizePolicy(_POLICY.Ignored, _POLICY.Preferred)
        row.addWidget(self.filename, 1)
        self.raw_toggle = ToggleToolButton(parent=self.toolbar)
        self.raw_toggle.setText("显示 RAW")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.setChecked(preview.show_raw())
        self.raw_toggle.setAccessibleName(f"{name} 显示 RAW")
        self.raw_toggle.setToolTip("开启：完整 RAW 解码；关闭：优先内嵌预览。仅影响本侧视口。")
        self.raw_toggle.toggled.connect(preview.set_show_raw)
        row.addWidget(self.raw_toggle)
        preview.source_changed.connect(self._update_available)
        self.center = center if center is not None else ToggleToolButton("自动焦点居中")
        if center is None:
            self.center.toggled.connect(preview.set_auto_focus_center)
        row.addWidget(self.center)
        self.fit = QPushButton("适应窗口")
        self.fit.clicked.connect(preview.canvas.fit_to_window)
        row.addWidget(self.fit)
        self.scale = scale if scale is not None else QComboBox(self)
        if scale is None:
            configure_preview_scale_preset_combo(self.scale, fixed_width=96)
            self.scale.activated.connect(self._zoom)
            preview.display_scale_percent_changed.connect(self._sync_scale)
            self._sync_scale(preview.current_display_scale_percent())
        row.addWidget(self.scale)
        layout.addWidget(self.toolbar)
        self.viewport_frame = QFrame(self)
        self.viewport_frame.setObjectName("ViewerABViewportFrame")
        frame_layout = QVBoxLayout(self.viewport_frame)
        frame_layout.setContentsMargins(2, 2, 2, 2)
        frame_layout.addWidget(preview)
        layout.addWidget(self.viewport_frame, 1)
        self._filename = "未选择"
        self.set_active(False, compare_mode=False)
        preview.display_scale_percent_changed.connect(self._update_available)
        preview.video_view.activated.connect(self.activated.emit)
        self._install_filters()
        self._update_available()

    def _install_filters(self):
        for widget in (self, *self.findChildren(QWidget)):
            widget.installEventFilter(self)

    def set_path(self, path):
        self._filename = Path(path).name if path else "未选择"
        self.filename.setToolTip(str(path) if path else "")
        self._elide_filename()
        self._update_available()

    def set_active(self, active, *, compare_mode):
        highlighted = bool(active and compare_mode)
        self.name_label.setVisible(compare_mode)
        self.name_label.setText(f"{self.name} · 当前" if highlighted else self.name)
        self.name_label.setStyleSheet("color: #2196f3; font-weight: 600;" if highlighted else "")
        color = "#2196f3" if highlighted else "transparent"
        self.viewport_frame.setStyleSheet(
            f"QFrame#ViewerABViewportFrame {{ border: 2px solid {color}; }}"
        )
        self.toolbar.setToolTip("当前视图响应文件列表选择" if highlighted else "点击激活此视图，再从列表选图")

    def _elide_filename(self):
        width = self.filename.contentsRect().width()
        self.filename.setText(self.filename.fontMetrics().elidedText(
            self._filename, _ELIDE.ElideMiddle, max(0, width)))

    def _zoom(self, index):
        value = self.scale.itemData(index)
        if value is not None:
            self.preview.set_display_scale_percent(value, preserve_view=True)
            self._sync_scale(self.preview.current_display_scale_percent())

    def _sync_scale(self, value):
        sync_preview_scale_preset_combo(self.scale, value)

    def _update_available(self, *_args):
        self.raw_toggle.setVisible(Path(self.preview.current_path() or "").suffix.lower() in RAW_EXTENSIONS)
        self.raw_toggle.setChecked(self.preview.show_raw())
        available = (self.preview.current_display_scale_percent() is not None
                     and not is_video(self.preview.current_path() or ""))
        self.fit.setEnabled(available)
        self.scale.setEnabled(available)
        self.center.setEnabled(available)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (_EVENTS.MouseButtonPress, _EVENTS.FocusIn):
            self.activated.emit()
        if watched is self.filename and kind in (_EVENTS.Resize, _EVENTS.FontChange):
            self._elide_filename()
        return super().eventFilter(watched, event)


class ViewerABViewLink(QObject):
    def __init__(self, ab):
        super().__init__(ab)
        self.ab = ab
        self.states = {}
        self.applying = False
        self.canvases = (ab.a_preview.canvas, ab.b_preview.canvas)
        for canvas in self.canvases:
            canvas.viewport_interacted.connect(lambda canvas=canvas: self.move_from(canvas))
            canvas.viewport_content_changed.connect(lambda canvas=canvas: self.apply_to(canvas))
        for center in (ab.a_panel.center, ab.b_panel.center):
            center.toggled.connect(self.focus_changed)
        ab.linked.toggled.connect(self.toggle)

    def enabled(self):
        return (self.ab.enabled.isChecked() and self.ab.linked.isChecked()
                and all(canvas.viewport_state() is not None for canvas in self.canvases))

    def toggle(self, checked):
        self.states.clear()
        if checked and self.ab.enabled.isChecked():
            self.ab.a_panel.center.setChecked(False)
            self.ab.b_panel.center.setChecked(False)
            self.states = {canvas: state for canvas in self.canvases
                           if (state := canvas.viewport_state()) is not None}

    def focus_changed(self, checked):
        if checked and self.ab.linked.isChecked():
            self.ab.linked.setChecked(False)

    def move_from(self, source):
        if not self.enabled() or self.applying:
            return
        previous = self.states.get(source)
        current = source.viewport_state()
        self.states[source] = current
        if previous is None or current is None or previous[0] <= 0:
            return
        zoom_ratio = current[0] / previous[0]
        center_delta = (current[1][0] - previous[1][0], current[1][1] - previous[1][1])
        self.applying = True
        try:
            for canvas in self.canvases:
                if canvas is source:
                    continue
                target = self.states.get(canvas)
                if target is None:
                    continue
                canvas.apply_viewport_state((target[0] * zoom_ratio,
                                             (target[1][0] + center_delta[0],
                                              target[1][1] + center_delta[1])))
                self.states[canvas] = canvas.viewport_state()
        finally:
            self.applying = False

    def apply_to(self, canvas):
        if not self.enabled() or self.applying:
            return
        state = self.states.get(canvas)
        if state is None:
            self.states[canvas] = canvas.viewport_state()
            return
        self.applying = True
        try:
            canvas.apply_viewport_state(state)
            self.states[canvas] = canvas.viewport_state() or state
        finally:
            self.applying = False


class ViewerABPreview(QObject):
    """Owns the extra viewport; the existing MainWindow preview remains B."""

    def __init__(self, owner, b_preview, a_preview, *, b_center, b_scale):
        super().__init__(owner)
        self.owner = owner
        self.a_preview = a_preview
        self.b_preview = b_preview
        self.active_side = "b"
        self.display_paths = {"a": "", "b": ""}
        self.video_info = {"a": None, "b": None}
        self.enabled = ToggleToolButton()
        self.enabled.setText("A/B 对照")
        self.enabled.setCheckable(True)
        self.enabled.setToolTip("左右对照；点击一侧后，文件列表选择只更新该侧。")
        self.linked = ToggleToolButton()
        self.linked.setText("同步缩放/移动")
        self.linked.setCheckable(True)
        self.linked.setToolTip("保留当前相对视野；之后同步缩放和平移变化。")
        self.linked.hide()
        self.splitter = QSplitter(_Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.a_panel = ViewerViewportPanel("A", a_preview)
        self.b_panel = ViewerViewportPanel("B", b_preview, center=b_center, scale=b_scale)
        self.splitter.addWidget(self.a_panel)
        self.splitter.addWidget(self.b_panel)
        self.a_panel.hide()
        self.a_panel.activated.connect(lambda: self.activate("a"))
        self.b_panel.activated.connect(lambda: self.activate("b"))
        self.view_link = ViewerABViewLink(self)
        self.enabled.toggled.connect(self._toggle)

    def active_panel(self):
        return self.a_preview if self.enabled.isChecked() and self.active_side == "a" else self.b_preview

    def active_viewport(self):
        return self.a_panel if self.enabled.isChecked() and self.active_side == "a" else self.b_panel

    def active_path(self):
        return self.active_panel().current_path() or ""

    def set_side_path(self, side, path, *, display_path=None):
        self.display_paths[side] = os.path.normpath(display_path or path) if path else ""
        (self.a_panel if side == "a" else self.b_panel).set_path(path)
        self.view_link.toggle(self.linked.isChecked())

    def activate(self, side):
        if not self.enabled.isChecked() or side == self.active_side:
            return
        self.owner._file_list.stop_key_navigation_playback(commit=False)
        self.active_side = side
        self._update_active()
        self.owner._on_ab_activated(side)

    def _update_active(self):
        compare = self.enabled.isChecked()
        self.a_panel.set_active(self.active_side == "a", compare_mode=compare)
        self.b_panel.set_active(self.active_side == "b", compare_mode=compare)

    def _toggle(self, enabled):
        self.active_side = "b"
        self.a_panel.setVisible(enabled)
        self.linked.setVisible(enabled)
        self._update_active()
        self.owner._on_ab_toggled(enabled)
        if enabled:
            self.splitter.setSizes([500, 500])
            self.view_link.toggle(self.linked.isChecked())
        else:
            self.view_link.states.clear()
