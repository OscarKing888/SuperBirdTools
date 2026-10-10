# -*- coding: utf-8 -*-
"""Two independent Viewer previews driven by the current file-list scope."""
from __future__ import annotations

import os
from pathlib import Path

from app_common.preview_canvas import configure_preview_scale_preset_combo, sync_preview_scale_preset_combo

from .ab_view_link import ABViewLink
from .preview_panel import PreviewPanel
from .qt_compat import (
    QComboBox, QEvent, QHBoxLayout, QLabel, QMenu, QSizePolicy, QSplitter,
    QToolButton, QVBoxLayout, QWidget, Qt, pyqtSignal,
)


def _key(path: str | None) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path))) if path else ""


class ABPreviewPanel(QWidget):
    """Own the two decoders; the list changes only the currently active side."""

    active_preview_changed = pyqtSignal(object)
    display_scale_percent_changed = pyqtSignal(object)
    full_preview_ready = pyqtSignal(str)
    comparison_toggled = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._enabled = False
        self._active_side = "B"
        self._paths: dict[str, str] = {"A": "", "B": ""}
        self._display_paths: list[str] = []
        self._current_list_path = ""
        self._shutdown_requested = False

        self.toggle_button = QToolButton(self)
        self.toggle_button.setText("A/B 对照")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolTip("左右比较两张图片；文件列表切换当前活动侧。")
        self.toggle_button.toggled.connect(self.set_enabled)
        self.link_button = QToolButton(self)
        self.link_button.setText("同步缩放/移动")
        self.link_button.setCheckable(True)
        self.link_button.setToolTip("保留两侧当前相对视野，之后同步缩放和平移变化。")
        self.link_button.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        orientation = getattr(getattr(Qt, "Orientation", Qt), "Horizontal")
        self.splitter = QSplitter(orientation, self)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        self._widgets: dict[str, QWidget] = {}
        self._headers: dict[str, QWidget] = {}
        self._filenames: dict[str, QLabel] = {}
        self._side_labels: dict[str, QLabel] = {}
        self._previews: dict[str, PreviewPanel] = {}
        self._scales: dict[str, QComboBox] = {}
        self._fits: dict[str, QToolButton] = {}
        self._toolbars: dict[str, QWidget] = {}
        self._canvas_sides: dict[object, str] = {}
        self._filename_sides: dict[object, str] = {}
        for side in ("A", "B"):
            panel = QWidget(self.splitter)
            panel.setObjectName(f"abSide{side}")
            column = QVBoxLayout(panel)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(3)
            header = QWidget(panel)
            row = QHBoxLayout(header)
            row.setContentsMargins(2, 2, 2, 2)
            side_label = QLabel(side, header)
            filename = QLabel("未选择", header)
            filename.setMinimumWidth(0)
            policy = getattr(QSizePolicy, "Policy", QSizePolicy)
            filename.setSizePolicy(policy.Ignored, policy.Preferred)
            filename.setTextFormat(getattr(getattr(Qt, "TextFormat", Qt), "PlainText"))
            self._filename_sides[filename] = side
            row.addWidget(side_label)
            row.addWidget(filename, 1)
            column.addWidget(header)
            preview = PreviewPanel(panel)
            toolbar = QWidget(panel)
            tools = QHBoxLayout(toolbar)
            tools.setContentsMargins(2, 0, 2, 0)
            fit = QToolButton(panel)
            fit.setText("适应窗口")
            fit.clicked.connect(preview.canvas.fit_to_window)
            scale = QComboBox(panel)
            configure_preview_scale_preset_combo(scale, fixed_width=96)
            scale.activated.connect(lambda index, s=side: self._zoom(s, index))
            tools.addWidget(fit)
            tools.addWidget(scale)
            grid = self._create_grid_button(preview, toolbar)
            tools.addWidget(grid)
            tools.addStretch(1)
            column.addWidget(toolbar)
            self._toolbars[side] = toolbar
            self._scales[side] = scale
            self._fits[side] = fit
            preview.display_scale_percent_changed.connect(
                lambda value, s=side: self._on_scale_changed(s, value))
            preview.full_preview_ready.connect(self.full_preview_ready.emit)
            column.addWidget(preview, 1)
            self.splitter.addWidget(panel)
            self._widgets[side] = panel
            self._headers[side] = header
            self._filenames[side] = filename
            self._side_labels[side] = side_label
            self._previews[side] = preview
            # Match main: focus/click anywhere inside a viewport (including
            # toolbar children and the filename) selects the list update target.
            for widget in (panel, *panel.findChildren(QWidget)):
                widget.installEventFilter(self)
                self._canvas_sides[widget] = side
        self.view_link = ABViewLink(self)
        self._update_layout()

    @staticmethod
    def _create_grid_button(preview: PreviewPanel, parent: QWidget) -> QToolButton:
        button = QToolButton(parent)
        button.setText("构图线")
        button.setToolTip("仅设置本侧预览及叠加导出的构图线")
        menu = QMenu(button)
        modes = (("none", "不显示"), ("thirds", "均分九宫格"),
                 ("golden_thirds", "黄金分割九宫格"), ("square", "方格网格"),
                 ("diag_square", "对角线 + 方格"), ("crosshair", "中心十字线"))
        mode_actions = []
        for mode, label in modes:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, mode=mode: preview.set_composition_grid_mode(mode))
            mode_actions.append((action, mode))
        widths = menu.addMenu("线宽")
        width_actions = []
        for width in (1, 2, 3, 4):
            action = widths.addAction(f"{width} px")
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, width=width: preview.set_composition_grid_line_width(width))
            width_actions.append((action, width))
        def sync():
            for action, mode in mode_actions:
                action.setChecked(mode == preview._composition_grid_mode)
            for action, width in width_actions:
                action.setChecked(width == preview._composition_grid_line_width)
        menu.aboutToShow.connect(sync)
        widths.aboutToShow.connect(sync)
        button.setMenu(menu)
        button.setPopupMode(getattr(getattr(QToolButton, "ToolButtonPopupMode", QToolButton), "InstantPopup"))
        return button

    @property
    def active_preview(self) -> PreviewPanel:
        return self._previews[self._active_side]

    def preview_for_side(self, side: str) -> PreviewPanel:
        return self._previews[side]

    def is_enabled(self) -> bool:
        return self._enabled

    def active_side(self) -> str:
        return self._active_side

    def path_for_side(self, side: str) -> str:
        return self._paths[side]

    def eventFilter(self, watched, event):  # type: ignore[override]
        events = getattr(QEvent, "Type", QEvent)
        if event.type() in (events.MouseButtonPress, events.FocusIn):
            side = self._canvas_sides.get(watched)
            if side and self._enabled:
                self.set_active_side(side)
        side = self._filename_sides.get(watched)
        if side and event.type() in (events.Resize, events.FontChange):
            self._update_filename(side)
        return super().eventFilter(watched, event)

    def set_active_side(self, side: str) -> None:
        if side not in self._previews or self._shutdown_requested or not self._enabled:
            return
        changed = side != self._active_side
        self._active_side = side
        self._update_active_style()
        if changed:
            self.active_preview_changed.emit(self.active_preview)
            self.display_scale_percent_changed.emit(self.active_preview.current_display_scale_percent())

    def set_enabled(self, enabled: bool) -> None:
        if self._shutdown_requested:
            return
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if self.toggle_button.isChecked() != enabled:
            self.toggle_button.setChecked(enabled)
        if enabled:
            if not self._paths["B"] and self._current_list_path:
                self._paths["B"] = self._current_list_path
            if not self._paths["A"]:
                self._paths["A"] = self._current_list_path or next(iter(self._display_paths), "")
            for side, preview in self._previews.items():
                if self._paths[side] and not preview.current_path():
                    preview.set_image(self._paths[side], load_full=True)
                self._update_filename(side)
        else:
            inactive = "A" if self._active_side == "B" else "B"
            self._previews[inactive].clear_image()
        self._update_layout()
        self.view_link.toggle(self.link_button.isChecked())
        self.comparison_toggled.emit(enabled)
        self.active_preview_changed.emit(self.active_preview)
        self.display_scale_percent_changed.emit(self.active_preview.current_display_scale_percent())

    def _update_layout(self) -> None:
        self.link_button.setVisible(self._enabled)
        for side in ("A", "B"):
            self._widgets[side].setVisible(self._enabled or side == self._active_side)
            self._headers[side].setVisible(self._enabled)
            self._toolbars[side].setVisible(self._enabled)
        if self._enabled:
            self.splitter.setSizes([500, 500])
        self._update_active_style()

    def _update_active_style(self) -> None:
        for side, label in self._side_labels.items():
            active = self._enabled and side == self._active_side
            label.setText(f"{side} · 当前" if active else side)
            label.setStyleSheet("color: #67a9d7; font-weight: 600;" if active else "")
            self._headers[side].setToolTip(
                "当前视图响应文件列表选择" if active else "点击激活此视图，再从列表选图")
            self._widgets[side].setStyleSheet(
                f"QWidget#abSide{side} {{ border: 1px solid #67a9d7; }}"
                if active else "")

    def set_display_paths(self, paths: list[str]) -> None:
        seen: set[str] = set()
        display: list[str] = []
        for path in paths:
            key = _key(path)
            if key and key not in seen:
                seen.add(key)
                display.append(os.path.normpath(path))
        if display == self._display_paths:
            return
        self._display_paths = display

    def _update_filename(self, side: str) -> None:
        path = self._paths[side]
        label = self._filenames[side]
        text = Path(path).name if path else "未选择"
        label.setToolTip(path)
        label.setText(label.fontMetrics().elidedText(
            text, getattr(getattr(Qt, "TextElideMode", Qt), "ElideMiddle"),
            max(0, label.contentsRect().width())))

    def set_side_path(self, side: str, path: str, *, load_full: bool = True) -> None:
        if self._shutdown_requested or side not in self._previews:
            return
        normalized = os.path.normpath(path) if path else ""
        self._paths[side] = normalized
        preview = self._previews[side]
        if normalized:
            preview.set_image(normalized, load_full=load_full)
        else:
            preview.clear_image()
        self._update_filename(side)

    def set_current_list_path(self, path: str, *, load_full: bool = True) -> None:
        self._current_list_path = os.path.normpath(path) if path else ""
        self.set_side_path(self._active_side, self._current_list_path, load_full=load_full)

    def set_quick_pixmap_for_list(self, path: str, pixmap, *, quick_size=None) -> None:
        self._current_list_path = os.path.normpath(path) if path else ""
        self._paths[self._active_side] = self._current_list_path
        self.active_preview.set_quick_pixmap(path, pixmap, quick_size=quick_size)
        self._update_filename(self._active_side)

    def source_pixmap_for_path(self, path: str):
        for preview in self._previews.values():
            pixmap = preview.source_pixmap_for_path(path)
            if pixmap is not None:
                return pixmap
        return None

    def set_composition_grid_mode(self, mode: str | None) -> None:
        for preview in self._previews.values():
            preview.set_composition_grid_mode(mode)

    def set_composition_grid_line_width(self, width: int | str | None) -> None:
        for preview in self._previews.values():
            preview.set_composition_grid_line_width(width)

    def set_keep_view_on_switch(self, enabled: bool) -> None:
        for preview in self._previews.values():
            preview.set_keep_view_on_switch(enabled)

    def _on_scale_changed(self, side: str, value: object) -> None:
        sync_preview_scale_preset_combo(self._scales[side], value)
        self._fits[side].setEnabled(value is not None)
        self._scales[side].setEnabled(value is not None)
        if side == self._active_side:
            self.display_scale_percent_changed.emit(value)

    def _zoom(self, side: str, index: int) -> None:
        self.set_active_side(side)
        value = self._scales[side].itemData(index)
        if value is not None:
            self._previews[side].set_display_scale_percent(value, preserve_view=True)

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        for preview in self._previews.values():
            preview.request_shutdown()

    def shutdown(self, *, wait_timeout_ms: int | None = None) -> bool:
        self.request_shutdown()
        results = [preview.shutdown(wait_timeout_ms=wait_timeout_ms)
                   for preview in self._previews.values()]
        return all(results)
