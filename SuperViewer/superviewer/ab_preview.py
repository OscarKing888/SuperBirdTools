# -*- coding: utf-8 -*-
"""Two independent Viewer previews driven by the current file-list scope."""
from __future__ import annotations

import os
from pathlib import Path

from .preview_panel import PreviewPanel
from .qt_compat import (
    QComboBox, QEvent, QHBoxLayout, QSplitter, QToolButton, QVBoxLayout,
    QWidget, Qt, pyqtSignal,
)


def _key(path: str | None) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path))) if path else ""


class ABPreviewPanel(QWidget):
    """Own the two decoders; the list changes only the currently active side."""

    active_preview_changed = pyqtSignal(object)
    display_scale_percent_changed = pyqtSignal(object)
    full_preview_ready = pyqtSignal(str)

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        orientation = getattr(getattr(Qt, "Orientation", Qt), "Horizontal")
        self.splitter = QSplitter(orientation, self)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        self._widgets: dict[str, QWidget] = {}
        self._headers: dict[str, QWidget] = {}
        self._selectors: dict[str, QComboBox] = {}
        self._buttons: dict[str, QToolButton] = {}
        self._previews: dict[str, PreviewPanel] = {}
        self._canvas_sides: dict[object, str] = {}
        self._selector_sides: dict[object, str] = {}
        for side in ("A", "B"):
            panel = QWidget(self.splitter)
            panel.setObjectName(f"abSide{side}")
            column = QVBoxLayout(panel)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(3)
            header = QWidget(panel)
            row = QHBoxLayout(header)
            row.setContentsMargins(2, 2, 2, 2)
            button = QToolButton(header)
            button.setText(side)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, s=side: self.set_active_side(s))
            selector = QComboBox(header)
            selector.setMinimumWidth(100)
            selector.setToolTip(f"选择 {side} 侧图片")
            selector.installEventFilter(self)
            self._selector_sides[selector] = side
            selector.activated.connect(lambda _index, s=side: self._choose(s))
            row.addWidget(button)
            row.addWidget(selector, 1)
            column.addWidget(header)
            preview = PreviewPanel(panel)
            preview.canvas.installEventFilter(self)
            self._canvas_sides[preview.canvas] = side
            preview.display_scale_percent_changed.connect(
                lambda value, s=side: self._on_scale_changed(s, value))
            preview.full_preview_ready.connect(self.full_preview_ready.emit)
            column.addWidget(preview, 1)
            self.splitter.addWidget(panel)
            self._widgets[side] = panel
            self._headers[side] = header
            self._selectors[side] = selector
            self._buttons[side] = button
            self._previews[side] = preview
        self._update_layout()

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
        press = getattr(getattr(QEvent, "Type", QEvent), "MouseButtonPress")
        if event.type() == press:
            side = self._canvas_sides.get(watched) or self._selector_sides.get(watched)
            if side:
                self.set_active_side(side)
        return super().eventFilter(watched, event)

    def set_active_side(self, side: str) -> None:
        if side not in self._previews or self._shutdown_requested:
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
                self._paths["A"] = next(iter(self._display_paths), "")
            for side, preview in self._previews.items():
                # A normal single-view result has not passed A/B source-size
                # validation, even when it reports itself as fully loaded.
                preview.clear_image()
                preview.set_full_only_mode(True)
                if self._paths[side]:
                    preview.set_image(self._paths[side], load_full=True)
        else:
            for preview in self._previews.values():
                preview.set_full_only_mode(False)
            inactive = "A" if self._active_side == "B" else "B"
            self._previews[inactive].clear_image()
        self._update_layout()
        self._rebuild_selectors()
        self.active_preview_changed.emit(self.active_preview)
        self.display_scale_percent_changed.emit(self.active_preview.current_display_scale_percent())

    def _update_layout(self) -> None:
        for side in ("A", "B"):
            self._widgets[side].setVisible(self._enabled or side == self._active_side)
            self._headers[side].setVisible(self._enabled)
        if self._enabled:
            self.splitter.setSizes([500, 500])
        self._update_active_style()

    def _update_active_style(self) -> None:
        for side, button in self._buttons.items():
            button.setChecked(side == self._active_side)
            self._widgets[side].setStyleSheet(
                f"QWidget#abSide{side} {{ border: 1px solid #67a9d7; }}"
                if self._enabled and side == self._active_side else "")

    def set_display_paths(self, paths: list[str]) -> None:
        seen: set[str] = set()
        display: list[str] = []
        for path in paths:
            key = _key(path)
            if key and key not in seen:
                seen.add(key)
                display.append(os.path.normpath(path))
        self._display_paths = display
        self._rebuild_selectors()

    def _rebuild_selectors(self) -> None:
        available = {_key(path) for path in self._display_paths}
        for side, selector in self._selectors.items():
            selected = self._paths[side]
            selector.blockSignals(True)
            try:
                selector.clear()
                if selected and _key(selected) not in available:
                    selector.addItem(f"[筛选外] {Path(selected).name}", selected)
                    selector.setItemData(0, selected, getattr(getattr(Qt, "ItemDataRole", Qt), "ToolTipRole"))
                for path in self._display_paths:
                    selector.addItem(Path(path).name, path)
                    selector.setItemData(selector.count() - 1, path,
                                         getattr(getattr(Qt, "ItemDataRole", Qt), "ToolTipRole"))
                selector.setCurrentIndex(selector.findData(selected) if selected else -1)
                selector.setEnabled(selector.count() > 0)
            finally:
                selector.blockSignals(False)

    def _choose(self, side: str) -> None:
        self.set_active_side(side)
        path = self._selectors[side].currentData()
        if path:
            self.set_side_path(side, str(path))

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
        self._rebuild_selectors()

    def set_current_list_path(self, path: str, *, load_full: bool = True) -> None:
        self._current_list_path = os.path.normpath(path) if path else ""
        self.set_side_path(self._active_side, self._current_list_path, load_full=load_full)

    def set_quick_pixmap_for_list(self, path: str, pixmap, *, quick_size=None) -> None:
        self._current_list_path = os.path.normpath(path) if path else ""
        self._paths[self._active_side] = self._current_list_path
        self.active_preview.set_quick_pixmap(path, pixmap, quick_size=quick_size)
        self._rebuild_selectors()

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
        if side == self._active_side:
            self.display_scale_percent_changed.emit(value)

    def request_shutdown(self) -> None:
        self._shutdown_requested = True
        for preview in self._previews.values():
            preview.request_shutdown()

    def shutdown(self, *, wait_timeout_ms: int | None = None) -> bool:
        self.request_shutdown()
        results = [preview.shutdown(wait_timeout_ms=wait_timeout_ms)
                   for preview in self._previews.values()]
        return all(results)
