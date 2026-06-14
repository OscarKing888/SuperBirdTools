"""Crop padding editor widget: uniform + per-edge padding and outer fill color."""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QIntValidator
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from birdstamp.gui import editor_core, editor_options, editor_utils
from birdstamp.gui.editor_collapsible import refresh_layout_chain

COLOR_PRESETS = editor_options.COLOR_PRESETS
_DEFAULT_CROP_PADDING_PX = editor_core.DEFAULT_CROP_PADDING_PX
_build_color_preview_swatch = editor_utils.build_color_preview_swatch
_set_color_preview_swatch = editor_utils.set_color_preview_swatch
_safe_color = editor_utils.safe_color
_start_screen_color_picker = editor_utils.start_screen_color_picker


class CropPaddingEditorWidget(QWidget):
    """可复用的裁切边界填充 + 外圈填充色编辑 Widget。"""

    changed = pyqtSignal()
    detailsToggled = pyqtSignal(bool)
    _COMMON_PADDING_VALUES = [
        -1024,
        -768,
        -512,
        -384,
        -256,
        -192,
        -128,
        -96,
        -64,
        -48,
        -32,
        -24,
        -16,
        -12,
        -8,
        -4,
        0,
        4,
        8,
        12,
        16,
        24,
        32,
        48,
        64,
        96,
        128,
        192,
        256,
        384,
        512,
        768,
        1024,
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._blocking = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        uniform_row = QHBoxLayout()
        uniform_row.setSpacing(4)
        uniform_row.addWidget(QLabel("四边"))
        self.uniform_spin = QComboBox()
        self.uniform_spin.setEditable(True)
        self.uniform_spin.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.uniform_spin.addItems([str(value) for value in self._COMMON_PADDING_VALUES])
        self.uniform_spin.setCurrentText(str(_DEFAULT_CROP_PADDING_PX))
        uniform_line_edit = self.uniform_spin.lineEdit()
        if uniform_line_edit is not None:
            uniform_line_edit.setValidator(QIntValidator(-9999, 9999, self.uniform_spin))
            uniform_line_edit.setPlaceholderText("输入统一留边(px)")
        self.uniform_spin.currentTextChanged.connect(self._on_uniform_padding_changed)
        uniform_row.addWidget(self.uniform_spin, stretch=1)
        self._details_toggle = self._build_details_toggle()
        uniform_row.addWidget(self._details_toggle, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(uniform_row)

        self._details_widget = QWidget(self)
        details_layout = QVBoxLayout(self._details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(4)

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        def _make_combo_slider(label: str) -> tuple[QWidget, QComboBox, QSlider]:
            w = QWidget()
            w.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
            wl = QVBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(2)
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.setAccessibleName(label)
            combo.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
            combo.addItems([str(value) for value in self._COMMON_PADDING_VALUES])
            combo.setCurrentText(str(_DEFAULT_CROP_PADDING_PX))
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setValidator(QIntValidator(-9999, 9999, combo))
                line_edit.setPlaceholderText("输入像素值(px)")
            combo.setMinimumWidth(max(combo.minimumWidth(), combo.fontMetrics().horizontalAdvance("-9999") + 52))
            combo.currentTextChanged.connect(lambda _text, c=combo: self._on_padding_combo_changed(c))
            wl.addWidget(combo)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(-2048, 2048)
            slider.setSingleStep(1)
            slider.setPageStep(16)
            slider.setValue(max(slider.minimum(), min(slider.maximum(), _DEFAULT_CROP_PADDING_PX)))
            slider.setMinimumWidth(max(96, combo.minimumWidth()))
            slider.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
            wl.addWidget(slider)
            slider.valueChanged.connect(lambda v, c=combo: self._sync_combo(c, v))
            return w, combo, slider

        top_w, self.top_spin, self.top_slider = _make_combo_slider("上")
        left_w, self.left_spin, self.left_slider = _make_combo_slider("左")
        right_w, self.right_spin, self.right_slider = _make_combo_slider("右")
        bot_w, self.bottom_spin, self.bottom_slider = _make_combo_slider("下")

        grid.addWidget(top_w, 0, 1, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(left_w, 1, 0, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(right_w, 1, 2, Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(bot_w, 2, 1, Qt.AlignmentFlag.AlignCenter)
        details_layout.addWidget(grid_widget)

        fill_row = QHBoxLayout()
        fill_row.setSpacing(4)
        self.fill_combo = QComboBox()
        for lbl, val in COLOR_PRESETS:
            self.fill_combo.addItem(lbl, val)
        if self.fill_combo.count() == 0:
            self.fill_combo.addItem("白色", "#FFFFFF")
        idx_white = self.fill_combo.findData("#FFFFFF")
        if idx_white >= 0:
            self.fill_combo.setCurrentIndex(idx_white)
        self.fill_combo.currentIndexChanged.connect(self._on_fill_combo_changed)
        fill_row.addWidget(self.fill_combo, stretch=1)
        self.fill_swatch = _build_color_preview_swatch()
        fill_row.addWidget(self.fill_swatch)
        self._refresh_fill_swatch()
        pick_btn = QPushButton("调色板")
        pick_btn.clicked.connect(self._pick_fill_color)
        fill_row.addWidget(pick_btn)
        screen_btn = QPushButton("吸管")
        screen_btn.clicked.connect(self._pick_fill_screen)
        fill_row.addWidget(screen_btn)
        details_layout.addLayout(fill_row)

        layout.addWidget(self._details_widget)
        self._details_attached = True
        self._set_details_expanded(False)

    def _build_details_toggle(self) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("CropPaddingDetailsToggle")
        button.setCheckable(True)
        button.setChecked(False)
        button.setAutoRaise(True)
        button.setFixedSize(16, 16)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setArrowType(Qt.ArrowType.RightArrow)
        button.setToolTip("展开分边留边与填充色")
        button.toggled.connect(self._set_details_expanded)
        button.setStyleSheet(
            "QToolButton#CropPaddingDetailsToggle {"
            "border: none; padding: 0; margin: 0;"
            "}"
            "QToolButton#CropPaddingDetailsToggle:hover {"
            "background: palette(midlight); border-radius: 3px;"
            "}"
        )
        return button

    def _set_details_expanded(self, expanded: bool) -> None:
        state = bool(expanded)
        layout = self.layout()
        if layout is not None:
            if state:
                if not getattr(self, "_details_attached", False):
                    layout.addWidget(self._details_widget)
                    self._details_attached = True
                self._details_widget.show()
            else:
                if getattr(self, "_details_attached", False):
                    layout.removeWidget(self._details_widget)
                    self._details_attached = False
                self._details_widget.hide()
        else:
            self._details_widget.setVisible(state)
        self._details_toggle.blockSignals(True)
        self._details_toggle.setChecked(state)
        self._details_toggle.blockSignals(False)
        self._details_toggle.setArrowType(
            Qt.ArrowType.DownArrow if state else Qt.ArrowType.RightArrow
        )
        self._details_toggle.setToolTip(
            "收起分边留边与填充色" if state else "展开分边留边与填充色"
        )
        self.updateGeometry()
        self.detailsToggled.emit(state)
        refresh_layout_chain(self)

    def _collapsed_size_hint(self) -> QSize:
        uniform_hint = self.uniform_spin.sizeHint()
        layout = self.layout()
        extra_h = 0
        if layout is not None:
            margins = layout.contentsMargins()
            extra_h = margins.top() + margins.bottom()
        return QSize(
            max(self.uniform_spin.width(), uniform_hint.width() + self._details_toggle.width() + 8),
            uniform_hint.height() + extra_h,
        )

    def sizeHint(self) -> QSize:  # type: ignore[override]
        if not self._details_toggle.isChecked():
            return self._collapsed_size_hint()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        if not self._details_toggle.isChecked():
            return self._collapsed_size_hint()
        return super().minimumSizeHint()

    def details_expanded(self) -> bool:
        return self._details_toggle.isChecked()

    def set_details_expanded(self, expanded: bool) -> None:
        self._set_details_expanded(bool(expanded))

    @staticmethod
    def _format_padding_text(value: int) -> str:
        return str(int(value))

    @staticmethod
    def _parse_padding_text(value: str) -> int:
        text = str(value or "").strip().lower().replace("px", "").strip()
        if not text:
            return 0
        try:
            return int(text)
        except Exception:
            return 0

    def _set_combo_value(self, combo: QComboBox, value: int) -> None:
        text = self._format_padding_text(value)
        if combo.currentText() == text:
            return
        combo.blockSignals(True)
        try:
            combo.setCurrentText(text)
        finally:
            combo.blockSignals(False)

    def _sync_slider(self, slider: QSlider, value: int) -> None:
        clamped = max(slider.minimum(), min(slider.maximum(), value))
        if slider.value() == clamped:
            return
        slider.blockSignals(True)
        try:
            slider.setValue(clamped)
        finally:
            slider.blockSignals(False)

    def _sync_combo(self, combo: QComboBox, value: int) -> None:
        self._set_combo_value(combo, value)
        self._sync_uniform_from_edges()
        self._emit_changed()

    def _on_padding_combo_changed(self, combo: QComboBox) -> None:
        value = self._parse_padding_text(combo.currentText())
        self._set_combo_value(combo, value)
        slider_map = {
            self.top_spin: self.top_slider,
            self.bottom_spin: self.bottom_slider,
            self.left_spin: self.left_slider,
            self.right_spin: self.right_slider,
        }
        slider = slider_map.get(combo)
        if slider is not None:
            self._sync_slider(slider, value)
        self._sync_uniform_from_edges()
        self._emit_changed()

    def _edge_padding_values(self) -> tuple[int, int, int, int]:
        return (
            self._parse_padding_text(self.top_spin.currentText()),
            self._parse_padding_text(self.bottom_spin.currentText()),
            self._parse_padding_text(self.left_spin.currentText()),
            self._parse_padding_text(self.right_spin.currentText()),
        )

    def _sync_uniform_from_edges(self) -> None:
        values = self._edge_padding_values()
        text = self._format_padding_text(values[0]) if values.count(values[0]) == len(values) else ""
        self.uniform_spin.blockSignals(True)
        try:
            self.uniform_spin.setCurrentText(text)
        finally:
            self.uniform_spin.blockSignals(False)

    def _on_uniform_padding_changed(self, value_text: str) -> None:
        if self._blocking:
            return
        value = self._parse_padding_text(value_text)
        self._blocking = True
        try:
            self._set_combo_value(self.uniform_spin, value)
            for combo, slider in (
                (self.top_spin, self.top_slider),
                (self.bottom_spin, self.bottom_slider),
                (self.left_spin, self.left_slider),
                (self.right_spin, self.right_slider),
            ):
                self._set_combo_value(combo, value)
                self._sync_slider(slider, value)
        finally:
            self._blocking = False
        self._emit_changed()

    def _on_fill_combo_changed(self, *_: Any) -> None:
        self._refresh_fill_swatch()
        self._emit_changed()

    def _refresh_fill_swatch(self) -> None:
        _set_color_preview_swatch(
            self.fill_swatch,
            str(self.fill_combo.currentData() or "#FFFFFF"),
            fallback="#FFFFFF",
        )

    def _emit_changed(self, *_: Any) -> None:
        if not self._blocking:
            self.changed.emit()

    def _set_fill_value(self, color: str) -> None:
        normalized = _safe_color(color, "#FFFFFF")
        for idx in range(self.fill_combo.count()):
            if str(self.fill_combo.itemData(idx) or "").strip().lower() == normalized.lower():
                self.fill_combo.setCurrentIndex(idx)
                self._refresh_fill_swatch()
                return
        self.fill_combo.addItem(normalized.upper(), normalized)
        self.fill_combo.setCurrentIndex(self.fill_combo.count() - 1)
        self._refresh_fill_swatch()

    def _pick_fill_color(self) -> None:
        current = _safe_color(str(self.fill_combo.currentData() or "#FFFFFF"), "#FFFFFF")
        chosen = QColorDialog.getColor(QColor(current), self, "选择图像外圈填充色")
        if chosen.isValid():
            self._set_fill_value(chosen.name())

    def _pick_fill_screen(self) -> None:
        _start_screen_color_picker(parent=self, on_picked=lambda h: self._set_fill_value(h))

    def set_values(self, *, top: int, bottom: int, left: int, right: int, fill: str) -> None:
        """Set all values without emitting changed."""
        self._blocking = True
        try:
            for combo, slider, val in (
                (self.top_spin, self.top_slider, top),
                (self.bottom_spin, self.bottom_slider, bottom),
                (self.left_spin, self.left_slider, left),
                (self.right_spin, self.right_slider, right),
            ):
                combo.blockSignals(True)
                slider.blockSignals(True)
                try:
                    combo.setCurrentText(self._format_padding_text(val))
                    slider.setValue(max(slider.minimum(), min(slider.maximum(), val)))
                finally:
                    combo.blockSignals(False)
                    slider.blockSignals(False)
            self.uniform_spin.blockSignals(True)
            try:
                if top == bottom == left == right:
                    self.uniform_spin.setCurrentText(self._format_padding_text(top))
                else:
                    self.uniform_spin.setCurrentText("")
            finally:
                self.uniform_spin.blockSignals(False)
            self.fill_combo.blockSignals(True)
            try:
                self._set_fill_value(fill)
            finally:
                self.fill_combo.blockSignals(False)
            self._refresh_fill_swatch()
            if not (top == bottom == left == right):
                self.set_details_expanded(True)
        finally:
            self._blocking = False

    def get_values(self) -> dict[str, Any]:
        return {
            "crop_padding_top": self._parse_padding_text(self.top_spin.currentText()),
            "crop_padding_bottom": self._parse_padding_text(self.bottom_spin.currentText()),
            "crop_padding_left": self._parse_padding_text(self.left_spin.currentText()),
            "crop_padding_right": self._parse_padding_text(self.right_spin.currentText()),
            "crop_padding_fill": _safe_color(
                str(self.fill_combo.currentData() or "#FFFFFF"), "#FFFFFF"
            ),
        }


# Backward-compatible alias used by editor.py / editor_template_dialog.py
_CropPaddingEditorWidget = CropPaddingEditorWidget

__all__ = ["CropPaddingEditorWidget", "_CropPaddingEditorWidget"]
