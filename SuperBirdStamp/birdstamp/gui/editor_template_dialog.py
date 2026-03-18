"""editor_template_dialog.py

Contains widget helpers and TemplateManagerDialog used by the main editor.

Extracted classes:
  _GradientBarWidget      – horizontal gradient preview bar
  _GradientEditorWidget   – Photoshop-style gradient stop editor
  _CropPaddingEditorWidget – four-direction crop padding + fill color editor
  TemplateManagerDialog   – full template management dialog
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QIntValidator, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QColorDialog,
)

from app_common.preview_canvas import (
    PREVIEW_COMPOSITION_GRID_LINE_WIDTHS,
    PREVIEW_COMPOSITION_GRID_MODES,
    PreviewWithStatusBar,
    configure_preview_scale_preset_combo,
    normalize_preview_composition_grid_line_width,
    normalize_preview_composition_grid_mode,
    sync_preview_scale_preset_combo,
)
from birdstamp.gui import editor_core, editor_options, editor_template, editor_utils, template_context as _template_context
from birdstamp.gui.editor_preview_canvas import (
    EditorPreviewCanvas,
    EditorPreviewOverlayOptions,
    EditorPreviewOverlayState,
)

# ---------------------------------------------------------------------------
# Local aliases (mirrors the pattern used in editor.py)
# ---------------------------------------------------------------------------
RATIO_OPTIONS = editor_options.RATIO_OPTIONS
RATIO_FREE = editor_options.RATIO_FREE
MAX_LONG_EDGE_OPTIONS = editor_options.MAX_LONG_EDGE_OPTIONS
COLOR_PRESETS = editor_options.COLOR_PRESETS
TAG_OPTIONS = editor_options.TAG_OPTIONS
SAMPLE_RAW_METADATA = editor_options.SAMPLE_RAW_METADATA
DEFAULT_FIELD_TAG = editor_options.DEFAULT_FIELD_TAG
STYLE_OPTIONS = editor_options.STYLE_OPTIONS

ALIGN_OPTIONS_HORIZONTAL = editor_utils.ALIGN_OPTIONS_HORIZONTAL
ALIGN_OPTIONS_VERTICAL = editor_utils.ALIGN_OPTIONS_VERTICAL
_get_fallback_context_vars = editor_utils.get_fallback_context_vars
_get_template_context_field_options = editor_utils.get_template_context_field_options
_DEFAULT_TEMPLATE_FONT_TYPE = editor_utils.DEFAULT_TEMPLATE_FONT_TYPE
_normalize_template_font_type = editor_utils.normalize_template_font_type
_template_font_choices = editor_utils.template_font_choices
_configure_form_layout = editor_utils.configure_form_layout
_configure_spinbox_minimum_width = editor_utils.configure_spinbox_minimum_width
_normalize_template_banner_color = editor_utils.normalize_template_banner_color
_build_color_preview_swatch = editor_utils.build_color_preview_swatch
_set_color_preview_swatch = editor_utils.set_color_preview_swatch
_safe_color = editor_utils.safe_color
_start_screen_color_picker = editor_utils.start_screen_color_picker
_build_placeholder_image = editor_utils.build_placeholder_image
_sanitize_template_name = editor_utils.sanitize_template_name
_DEFAULT_TEMPLATE_BANNER_COLOR = editor_utils.DEFAULT_TEMPLATE_BANNER_COLOR
_TEMPLATE_BANNER_COLOR_NONE = editor_utils.TEMPLATE_BANNER_COLOR_NONE
_TEMPLATE_BANNER_COLOR_CUSTOM = editor_utils.TEMPLATE_BANNER_COLOR_CUSTOM
_PREVIEW_GRID_MODE_ITEMS = editor_utils.PREVIEW_GRID_MODE_ITEMS
_PREVIEW_GRID_MODE_COMBO_WIDTH = editor_utils.PREVIEW_GRID_MODE_COMBO_WIDTH
_PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH = editor_utils.PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH
_PREVIEW_SCALE_COMBO_WIDTH = 96

_CENTER_MODE_BIRD = editor_core.CENTER_MODE_BIRD
_CENTER_MODE_FOCUS = editor_core.CENTER_MODE_FOCUS
_CENTER_MODE_IMAGE = editor_core.CENTER_MODE_IMAGE
_CENTER_MODE_CUSTOM = editor_core.CENTER_MODE_CUSTOM

_DEFAULT_CROP_PADDING_PX = editor_core.DEFAULT_CROP_PADDING_PX
_normalize_center_mode = editor_core.normalize_center_mode
_parse_bool_value = editor_core.parse_bool_value
_parse_ratio_value = editor_core.parse_ratio_value
_is_ratio_free = editor_core.is_ratio_free
_parse_padding_value = editor_core.parse_padding_value
_compute_crop_plan = editor_core.compute_crop_plan
_constrain_box_to_ratio = editor_core.constrain_box_to_ratio
_compute_crop_output_size = editor_core.compute_crop_output_size
_extract_focus_box_for_display = editor_core.extract_focus_box_for_display
_resolve_focus_camera_type_from_metadata = editor_core.resolve_focus_camera_type_from_metadata
_transform_source_box_after_crop_padding = editor_core.transform_source_box_after_crop_padding
_detect_primary_bird_box = editor_core.detect_primary_bird_box
_pad_image = editor_core.pad_image
_resize_fit = editor_core.resize_fit
_build_metadata_context = editor_utils.build_metadata_context
_default_placeholder_path = editor_utils._default_placeholder_path
_DEFAULT_CROP_EFFECT_ALPHA = editor_utils.DEFAULT_CROP_EFFECT_ALPHA

_BANNER_BACKGROUND_STYLE_SOLID = editor_template.BANNER_BACKGROUND_STYLE_SOLID
_BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM = editor_template.BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM
_normalize_banner_background_style = editor_template.normalize_banner_background_style
_BANNER_GRADIENT_HEIGHT_PCT_DEFAULT = editor_template.BANNER_GRADIENT_HEIGHT_PCT_DEFAULT
_BANNER_GRADIENT_HEIGHT_PCT_MIN = editor_template.BANNER_GRADIENT_HEIGHT_PCT_MIN
_BANNER_GRADIENT_HEIGHT_PCT_MAX = editor_template.BANNER_GRADIENT_HEIGHT_PCT_MAX
_BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT = editor_template.BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT
_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT = editor_template.BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT
_BANNER_GRADIENT_TOP_COLOR_DEFAULT = editor_template.BANNER_GRADIENT_TOP_COLOR_DEFAULT
_BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT = editor_template.BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT
_DEFAULT_TEMPLATE_CENTER_MODE = editor_template.DEFAULT_TEMPLATE_CENTER_MODE
_DEFAULT_TEMPLATE_MAX_LONG_EDGE = editor_template.DEFAULT_TEMPLATE_MAX_LONG_EDGE
_normalize_template_field = editor_template.normalize_template_field
_normalize_template_payload = editor_template.normalize_template_payload
_ensure_template_repository = editor_template.ensure_template_repository
_list_template_names = editor_template.list_template_names
_load_template_payload = editor_template.load_template_payload
_save_template_payload = editor_template.save_template_payload
_default_template_payload = editor_template.default_template_payload
render_template_overlay_in_crop_region = editor_template.render_template_overlay_in_crop_region


def _pil_to_qpixmap(image: Image.Image) -> QPixmap:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    q_image = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(q_image.copy())


class _LazyLoadComboBox(QComboBox):
    """在展开下拉前发出信号，用于首次懒加载数据。"""

    popupAboutToShow = pyqtSignal()

    def showPopup(self) -> None:  # type: ignore[override]
        self.popupAboutToShow.emit()
        super().showPopup()


# ---------------------------------------------------------------------------
# _GradientBarWidget
# ---------------------------------------------------------------------------

class _GradientBarWidget(QWidget):
    """Horizontal gradient preview bar (left = image-top stop, right = image-bottom stop)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMinimumWidth(120)
        self._top_color = QColor("#000000")
        self._top_alpha = 0
        self._bot_color = QColor("#000000")
        self._bot_alpha = int(round(62 * 2.55))

    def set_top(self, color: QColor, opacity_pct: float) -> None:
        self._top_color = QColor(color)
        self._top_alpha = int(round(max(0.0, min(100.0, opacity_pct)) * 2.55))
        self.update()

    def set_bottom(self, color: QColor, opacity_pct: float) -> None:
        self._bot_color = QColor(color)
        self._bot_alpha = int(round(max(0.0, min(100.0, opacity_pct)) * 2.55))
        self.update()

    def paintEvent(self, _event: Any) -> None:
        painter = QPainter(self)
        rect = self.rect()
        checker = 6
        light, dark = QColor(200, 200, 200), QColor(160, 160, 160)
        for row in range(0, rect.height(), checker):
            for col in range(0, rect.width(), checker):
                c = light if (row // checker + col // checker) % 2 == 0 else dark
                painter.fillRect(col, row, checker, checker, c)
        grad = QLinearGradient(0.0, 0.0, float(rect.width()), 0.0)
        c0 = QColor(self._top_color)
        c0.setAlpha(self._top_alpha)
        c1 = QColor(self._bot_color)
        c1.setAlpha(self._bot_alpha)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(1.0, c1)
        painter.fillRect(rect, grad)
        painter.setPen(QColor(120, 120, 120))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()


# ---------------------------------------------------------------------------
# _GradientEditorWidget
# ---------------------------------------------------------------------------

class _GradientEditorWidget(QWidget):
    """Photoshop-style gradient editor: preview bar + two color stops (top/bottom) + height."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._bar = _GradientBarWidget()
        layout.addWidget(self._bar)

        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        top_lbl = QLabel("顶端")
        top_row.addWidget(top_lbl)
        self._top_swatch = _build_color_preview_swatch()
        top_row.addWidget(self._top_swatch)
        top_pick = QPushButton("选色")
        top_pick.clicked.connect(self._pick_top_color)
        top_row.addWidget(top_pick)
        top_screen = QPushButton("吸管")
        top_screen.clicked.connect(self._pick_top_screen)
        top_row.addWidget(top_screen)
        top_row.addWidget(QLabel("不透明度"))
        self._top_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._top_opacity_slider.setRange(0, 100)
        self._top_opacity_slider.valueChanged.connect(self._on_top_slider)
        top_row.addWidget(self._top_opacity_slider, stretch=1)
        self._top_opacity_spin = QSpinBox()
        self._top_opacity_spin.setRange(0, 100)
        self._top_opacity_spin.setSuffix(" %")
        _configure_spinbox_minimum_width(self._top_opacity_spin, sample_text="100 %")
        self._top_opacity_spin.valueChanged.connect(self._on_top_spin)
        top_row.addWidget(self._top_opacity_spin)
        layout.addLayout(top_row)

        bot_row = QHBoxLayout()
        bot_row.setSpacing(4)
        bot_lbl = QLabel("底端")
        bot_row.addWidget(bot_lbl)
        self._bot_swatch = _build_color_preview_swatch()
        bot_row.addWidget(self._bot_swatch)
        bot_pick = QPushButton("选色")
        bot_pick.clicked.connect(self._pick_bot_color)
        bot_row.addWidget(bot_pick)
        bot_screen = QPushButton("吸管")
        bot_screen.clicked.connect(self._pick_bot_screen)
        bot_row.addWidget(bot_screen)
        bot_row.addWidget(QLabel("不透明度"))
        self._bot_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._bot_opacity_slider.setRange(0, 100)
        self._bot_opacity_slider.valueChanged.connect(self._on_bot_slider)
        bot_row.addWidget(self._bot_opacity_slider, stretch=1)
        self._bot_opacity_spin = QSpinBox()
        self._bot_opacity_spin.setRange(0, 100)
        self._bot_opacity_spin.setSuffix(" %")
        _configure_spinbox_minimum_width(self._bot_opacity_spin, sample_text="100 %")
        self._bot_opacity_spin.valueChanged.connect(self._on_bot_spin)
        bot_row.addWidget(self._bot_opacity_spin)
        layout.addLayout(bot_row)

        h_row = QHBoxLayout()
        h_row.setSpacing(4)
        h_row.addWidget(QLabel("渐变高度"))
        self._height_slider = QSlider(Qt.Orientation.Horizontal)
        self._height_slider.setRange(int(_BANNER_GRADIENT_HEIGHT_PCT_MIN), int(_BANNER_GRADIENT_HEIGHT_PCT_MAX))
        self._height_slider.valueChanged.connect(self._on_height_slider)
        h_row.addWidget(self._height_slider, stretch=1)
        self._height_spin = QSpinBox()
        self._height_spin.setRange(int(_BANNER_GRADIENT_HEIGHT_PCT_MIN), int(_BANNER_GRADIENT_HEIGHT_PCT_MAX))
        self._height_spin.setSuffix(" %")
        _configure_spinbox_minimum_width(self._height_spin, sample_text="100 %")
        self._height_spin.valueChanged.connect(self._on_height_spin)
        h_row.addWidget(self._height_spin)
        layout.addLayout(h_row)

        self._top_hex = _BANNER_GRADIENT_TOP_COLOR_DEFAULT
        self._bot_hex = _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT
        self._blocking = False
        self._load_defaults()

    def _load_defaults(self) -> None:
        self.set_values(
            top_color=_BANNER_GRADIENT_TOP_COLOR_DEFAULT,
            top_opacity_pct=_BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT,
            bot_color=_BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT,
            bot_opacity_pct=_BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT,
            height_pct=_BANNER_GRADIENT_HEIGHT_PCT_DEFAULT,
        )

    def set_values(
        self,
        *,
        top_color: str,
        top_opacity_pct: float,
        bot_color: str,
        bot_opacity_pct: float,
        height_pct: float,
    ) -> None:
        self._blocking = True
        try:
            self._top_hex = _safe_color(top_color, _BANNER_GRADIENT_TOP_COLOR_DEFAULT)
            self._bot_hex = _safe_color(bot_color, _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT)
            top_op = max(0, min(100, int(round(top_opacity_pct))))
            bot_op = max(0, min(100, int(round(bot_opacity_pct))))
            h_pct = max(
                int(_BANNER_GRADIENT_HEIGHT_PCT_MIN),
                min(int(_BANNER_GRADIENT_HEIGHT_PCT_MAX), int(round(height_pct))),
            )
            self._top_opacity_slider.setValue(top_op)
            self._top_opacity_spin.setValue(top_op)
            self._bot_opacity_slider.setValue(bot_op)
            self._bot_opacity_spin.setValue(bot_op)
            self._height_slider.setValue(h_pct)
            self._height_spin.setValue(h_pct)
            _set_color_preview_swatch(self._top_swatch, self._top_hex)
            _set_color_preview_swatch(self._bot_swatch, self._bot_hex)
            self._bar.set_top(QColor(self._top_hex), float(top_op))
            self._bar.set_bottom(QColor(self._bot_hex), float(bot_op))
        finally:
            self._blocking = False

    def get_values(self) -> dict[str, Any]:
        return {
            "banner_gradient_top_color": self._top_hex,
            "banner_gradient_top_opacity_pct": float(self._top_opacity_spin.value()),
            "banner_gradient_bottom_color": self._bot_hex,
            "banner_gradient_bottom_opacity_pct": float(self._bot_opacity_spin.value()),
            "banner_gradient_height_pct": float(self._height_spin.value()),
        }

    def _refresh_bar(self) -> None:
        self._bar.set_top(QColor(self._top_hex), float(self._top_opacity_spin.value()))
        self._bar.set_bottom(QColor(self._bot_hex), float(self._bot_opacity_spin.value()))

    def _on_top_slider(self, v: int) -> None:
        if self._blocking:
            return
        self._blocking = True
        try:
            self._top_opacity_spin.setValue(v)
        finally:
            self._blocking = False
        self._refresh_bar()
        self.changed.emit()

    def _on_top_spin(self, v: int) -> None:
        if self._blocking:
            return
        self._blocking = True
        try:
            self._top_opacity_slider.setValue(v)
        finally:
            self._blocking = False
        self._refresh_bar()
        self.changed.emit()

    def _on_bot_slider(self, v: int) -> None:
        if self._blocking:
            return
        self._blocking = True
        try:
            self._bot_opacity_spin.setValue(v)
        finally:
            self._blocking = False
        self._refresh_bar()
        self.changed.emit()

    def _on_bot_spin(self, v: int) -> None:
        if self._blocking:
            return
        self._blocking = True
        try:
            self._bot_opacity_slider.setValue(v)
        finally:
            self._blocking = False
        self._refresh_bar()
        self.changed.emit()

    def _on_height_slider(self, v: int) -> None:
        if self._blocking:
            return
        self._blocking = True
        try:
            self._height_spin.setValue(v)
        finally:
            self._blocking = False
        self.changed.emit()

    def _on_height_spin(self, v: int) -> None:
        if self._blocking:
            return
        self._blocking = True
        try:
            self._height_slider.setValue(v)
        finally:
            self._blocking = False
        self.changed.emit()

    def _pick_top_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._top_hex), self, "选择顶端颜色")
        if color.isValid():
            self._top_hex = color.name()
            _set_color_preview_swatch(self._top_swatch, self._top_hex)
            self._refresh_bar()
            self.changed.emit()

    def _pick_top_screen(self) -> None:
        def _apply(hex_: str) -> None:
            self._top_hex = _safe_color(hex_, _BANNER_GRADIENT_TOP_COLOR_DEFAULT)
            _set_color_preview_swatch(self._top_swatch, self._top_hex)
            self._refresh_bar()
            self.changed.emit()
        _start_screen_color_picker(parent=self, on_picked=_apply)

    def _pick_bot_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._bot_hex), self, "选择底端颜色")
        if color.isValid():
            self._bot_hex = color.name()
            _set_color_preview_swatch(self._bot_swatch, self._bot_hex)
            self._refresh_bar()
            self.changed.emit()

    def _pick_bot_screen(self) -> None:
        def _apply(hex_: str) -> None:
            self._bot_hex = _safe_color(hex_, _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT)
            _set_color_preview_swatch(self._bot_swatch, self._bot_hex)
            self._refresh_bar()
            self.changed.emit()
        _start_screen_color_picker(parent=self, on_picked=_apply)


# ---------------------------------------------------------------------------
# _CropPaddingEditorWidget
# ---------------------------------------------------------------------------

class _CropPaddingEditorWidget(QWidget):
    """可复用的裁切边界填充 + 外圈填充色编辑 Widget。"""

    changed = pyqtSignal()
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

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
        layout.addWidget(grid_widget)

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
        layout.addLayout(fill_row)

        self._blocking = False

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
            self.fill_combo.blockSignals(True)
            try:
                self._set_fill_value(fill)
            finally:
                self.fill_combo.blockSignals(False)
            self._refresh_fill_swatch()
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


# ---------------------------------------------------------------------------
# TemplateManagerDialog
# ---------------------------------------------------------------------------

class TemplateManagerDialog(QDialog):
    def __init__(
        self,
        template_dir: Path,
        placeholder: Image.Image | None = None,
        parent: QWidget | None = None,
    ) -> None:
        from app_common.stat import stat_span

        super().__init__(parent)
        self.setWindowTitle("模板管理")
        self.resize(1180, 780)
        self.setMinimumSize(640, 500)

        self.template_dir = template_dir
        with stat_span("tmpl_placeholder"):
            self.placeholder = placeholder.copy() if placeholder else _build_placeholder_image()
        self.preview_pixmap: QPixmap | None = None
        self.preview_overlay_state = EditorPreviewOverlayState()

        self.template_paths: dict[str, Path] = {}
        self.current_template_name: str | None = None
        self.current_payload: dict[str, Any] | None = None
        self._field_font_all_choices: list[tuple[str, str]] = []
        self._field_font_choices_loaded = False
        self._updating = False

        # 预览图源：优先使用 images/default.jpg 原图 + 真实 EXIF（与主界面一致）
        self._preview_source_path: Path = Path(".")
        self._preview_source_image: "Image.Image | None" = None
        self._preview_photo_info: _template_context.PhotoInfo | None = None
        self._preview_raw_metadata: dict[str, Any] = {}
        self._preview_metadata_context: dict[str, str] = {}
        self._preview_bird_box_cache: tuple[float, float, float, float] | None = None
        self._preview_bird_box_cache_ready: bool = False
        with stat_span("tmpl_load_preview_source"):
            self._load_preview_source()

        with stat_span("tmpl_setup_ui"):
            self._setup_ui()
        with stat_span("tmpl_reload_template_list"):
            self._reload_template_list(preferred=None)
        with stat_span("tmpl_refresh_preview_label"):
            self._refresh_preview_label()

    def _load_preview_source(self) -> None:
        """加载 images/default.jpg 原图及完整 EXIF 作为预览图源，与主界面保持一致。
        优先使用 ExifTool（extract_many）获取完整字段（含 LensModel 等），
        失败时降级为 Pillow EXIF。
        """
        from app_common.exif_io import extract_many, extract_pillow_metadata
        from birdstamp.decoders.image_decoder import decode_image as _decode_image

        src = _default_placeholder_path()
        if src.exists():
            try:
                self._preview_source_path = src
                self._preview_source_image = _decode_image(src, decoder="auto")
                resolved = src.resolve(strict=False)
                try:
                    raw_map = extract_many([resolved], mode="auto")
                    raw_meta = raw_map.get(resolved) or extract_pillow_metadata(src)
                except Exception:
                    raw_meta = extract_pillow_metadata(src)
                if not isinstance(raw_meta, dict):
                    raw_meta = {}
                self._preview_raw_metadata = raw_meta
                self._preview_photo_info = _template_context.ensure_editor_photo_info(src, raw_metadata=raw_meta)
                self._preview_metadata_context = _build_metadata_context(src, raw_meta)
                return
            except Exception:
                pass
        # 回退：使用 placeholder PIL 图 + 空 metadata
        self._preview_source_image = self.placeholder.copy()
        self._preview_photo_info = _template_context.ensure_editor_photo_info(self._preview_source_path, raw_metadata={})

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        from app_common.stat import stat_span

        root_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)

        with stat_span("tmpl_build_list_panel"):
            left_panel = self._build_template_list_panel()

        with stat_span("tmpl_build_editor_panel"):
            editor_panel = self._build_editor_panel()
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setWidget(editor_panel)
        editor_scroll.setMinimumWidth(0)

        with stat_span("tmpl_build_preview_panel"):
            preview_panel = self._build_preview_panel()

        splitter.addWidget(left_panel)
        splitter.addWidget(editor_scroll)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 6)
        splitter.setSizes([220, 300, 640])
        splitter.setChildrenCollapsible(False)

    def _build_template_list_panel(self) -> QWidget:
        """左侧面板：模板列表 + 新增/复制/删除按钮。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(QLabel("模板列表"))

        self.template_list = QListWidget()
        self.template_list.currentItemChanged.connect(self._on_template_selected)
        self.template_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.template_list.customContextMenuRequested.connect(self._on_template_list_context_menu)
        layout.addWidget(self.template_list, stretch=1)

        buttons = QHBoxLayout()
        btn_new = QPushButton("新增")
        btn_new.clicked.connect(self._create_template)
        buttons.addWidget(btn_new)

        btn_copy = QPushButton("复制")
        btn_copy.clicked.connect(self._copy_template)
        buttons.addWidget(btn_copy)

        btn_delete = QPushButton("删除")
        btn_delete.clicked.connect(self._delete_template)
        buttons.addWidget(btn_delete)

        layout.addLayout(buttons)
        return panel

    def _build_editor_panel(self) -> QWidget:
        """中间面板：当前模板各分组。"""
        from app_common.stat import stat_span

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        with stat_span("tmpl_build_header_group"):
            layout.addWidget(self._build_header_group())
        with stat_span("tmpl_build_fields_group"):
            layout.addWidget(self._build_fields_group(), stretch=1)
        with stat_span("tmpl_build_field_edit_group"):
            layout.addWidget(self._build_field_edit_group())
        return panel

    def _build_preview_panel(self) -> QWidget:
        """右侧面板：预览图。"""
        panel = QWidget()
        panel.setMinimumWidth(120)
        panel.setMaximumWidth(1024)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        preview_toolbar = QHBoxLayout()
        preview_toolbar.setContentsMargins(0, 0, 0, 0)
        preview_toolbar.setSpacing(8)

        self.show_crop_effect_check = QCheckBox("显示裁切效果")
        self.show_crop_effect_check.setChecked(True)
        self.show_crop_effect_check.toggled.connect(self._on_preview_overlay_toggled)
        preview_toolbar.addWidget(self.show_crop_effect_check)

        self.crop_edit_mode_check = QCheckBox("调整裁剪框")
        self.crop_edit_mode_check.setToolTip("在预览上拖动 9 宫格手柄调整裁剪范围；比例由「裁切比例」锁定（选「自由」时不锁定）")
        self.crop_edit_mode_check.toggled.connect(self._on_preview_overlay_toggled)
        preview_toolbar.addWidget(self.crop_edit_mode_check)

        self.crop_effect_alpha_label = QLabel("Alpha")
        preview_toolbar.addWidget(self.crop_effect_alpha_label)

        self.crop_effect_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.crop_effect_alpha_slider.setRange(0, 255)
        self.crop_effect_alpha_slider.setSingleStep(1)
        self.crop_effect_alpha_slider.setPageStep(16)
        self.crop_effect_alpha_slider.setValue(_DEFAULT_CROP_EFFECT_ALPHA)
        self.crop_effect_alpha_slider.setFixedWidth(120)
        self.crop_effect_alpha_slider.valueChanged.connect(self._on_preview_crop_effect_alpha_changed)
        preview_toolbar.addWidget(self.crop_effect_alpha_slider)

        self.crop_effect_alpha_value_label = QLabel(str(_DEFAULT_CROP_EFFECT_ALPHA))
        self.crop_effect_alpha_value_label.setMinimumWidth(28)
        preview_toolbar.addWidget(self.crop_effect_alpha_value_label)

        self.show_focus_box_check = QCheckBox("显示对焦点")
        self.show_focus_box_check.setChecked(True)
        self.show_focus_box_check.toggled.connect(self._on_preview_overlay_toggled)
        preview_toolbar.addWidget(self.show_focus_box_check)

        self.show_bird_box_check = QCheckBox("显示鸟体框")
        self.show_bird_box_check.setChecked(True)
        self.show_bird_box_check.toggled.connect(self._on_preview_overlay_toggled)
        preview_toolbar.addWidget(self.show_bird_box_check)

        self.preview_grid_combo = QComboBox()
        self.preview_grid_combo.setFixedWidth(_PREVIEW_GRID_MODE_COMBO_WIDTH)
        valid_grid_modes = set(PREVIEW_COMPOSITION_GRID_MODES)
        for mode, label in _PREVIEW_GRID_MODE_ITEMS:
            if mode in valid_grid_modes:
                self.preview_grid_combo.addItem(label, mode)
        current_grid_index = self.preview_grid_combo.findData("none")
        if current_grid_index < 0 and self.preview_grid_combo.count() > 0:
            current_grid_index = 0
        if current_grid_index >= 0:
            self.preview_grid_combo.setCurrentIndex(current_grid_index)
        self.preview_grid_combo.setToolTip("设置模板预览构图辅助线；显示范围会限制在当前裁切区域内。")
        self.preview_grid_combo.currentIndexChanged.connect(self._on_preview_grid_mode_changed)
        preview_toolbar.addWidget(self.preview_grid_combo)

        self.preview_grid_line_width_combo = QComboBox()
        self.preview_grid_line_width_combo.setFixedWidth(_PREVIEW_GRID_LINE_WIDTH_COMBO_WIDTH)
        for line_width in PREVIEW_COMPOSITION_GRID_LINE_WIDTHS:
            self.preview_grid_line_width_combo.addItem(f"{line_width} px", line_width)
        current_width_index = self.preview_grid_line_width_combo.findData(1)
        if current_width_index < 0 and self.preview_grid_line_width_combo.count() > 0:
            current_width_index = 0
        if current_width_index >= 0:
            self.preview_grid_line_width_combo.setCurrentIndex(current_width_index)
        self.preview_grid_line_width_combo.setToolTip("设置构图辅助线线宽。")
        self.preview_grid_line_width_combo.currentIndexChanged.connect(self._on_preview_grid_line_width_changed)
        preview_toolbar.addWidget(self.preview_grid_line_width_combo)

        self.preview_scale_combo = QComboBox()
        configure_preview_scale_preset_combo(
            self.preview_scale_combo,
            tooltip="设置预览缩放比例，表示当前显示像素相对原图像素的百分比。",
            fixed_width=_PREVIEW_SCALE_COMBO_WIDTH,
        )
        self.preview_scale_combo.activated.connect(self._on_preview_scale_preset_activated)
        preview_toolbar.addWidget(self.preview_scale_combo)

        preview_toolbar.addStretch(1)
        layout.addLayout(preview_toolbar)

        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = PreviewWithStatusBar(
            canvas=EditorPreviewCanvas(placeholder_text="暂无预览"),
        )
        canvas = self.preview_label.canvas
        if hasattr(canvas, "crop_box_changed"):
            canvas.crop_box_changed.connect(self._on_tmpl_canvas_crop_box_changed)
        if hasattr(self.preview_label, "display_scale_percent_changed"):
            self.preview_label.display_scale_percent_changed.connect(self._sync_preview_scale_combo)
        self._sync_preview_scale_combo(self.preview_label.current_display_scale_percent())
        preview_layout.addWidget(self.preview_label, stretch=1)
        layout.addWidget(preview_group, stretch=1)
        return panel

    def _build_header_group(self) -> QGroupBox:
        """当前模板 GroupBox：裁切参数 + Banner 颜色/样式/渐变。"""
        group = QGroupBox("当前模板")
        form = QFormLayout(group)
        _configure_form_layout(form)

        self.template_name_edit = QLineEdit()
        self.template_name_edit.setReadOnly(True)
        form.addRow("模板文件", self.template_name_edit)

        self.template_ratio_combo = QComboBox()
        for label, ratio in RATIO_OPTIONS:
            self.template_ratio_combo.addItem(label, ratio)
        if self.template_ratio_combo.count() == 0:
            self.template_ratio_combo.addItem("原比例", None)
        self.template_ratio_combo.currentIndexChanged.connect(self._on_template_ratio_changed)
        form.addRow("裁切比例", self.template_ratio_combo)

        self.template_center_mode_combo = QComboBox()
        self.template_center_mode_combo.addItem("鸟体", _CENTER_MODE_BIRD)
        self.template_center_mode_combo.addItem("焦点", _CENTER_MODE_FOCUS)
        self.template_center_mode_combo.addItem("图像中心", _CENTER_MODE_IMAGE)
        self.template_center_mode_combo.addItem("自定义", _CENTER_MODE_CUSTOM)
        self.template_center_mode_combo.currentIndexChanged.connect(self._on_tmpl_center_mode_changed)
        form.addRow("裁切中心", self.template_center_mode_combo)

        self.template_max_long_edge_combo = QComboBox()
        seen_edges: set[int] = set()
        for _val in MAX_LONG_EDGE_OPTIONS:
            try:
                edge = int(_val)
            except Exception:
                continue
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            self.template_max_long_edge_combo.addItem("不限制" if edge <= 0 else str(edge), edge)
        if self.template_max_long_edge_combo.count() == 0:
            self.template_max_long_edge_combo.addItem("不限制", 0)
        self.template_max_long_edge_combo.currentIndexChanged.connect(self._on_tmpl_max_long_edge_changed)
        form.addRow("最大长边", self.template_max_long_edge_combo)

        form.addRow("Banner颜色", self._build_banner_color_row())

        self.template_draw_banner_bg_check = QCheckBox("绘制 Banner 底")
        self.template_draw_banner_bg_check.setChecked(True)
        self.template_draw_banner_bg_check.toggled.connect(self._on_template_draw_banner_background_changed)
        form.addRow("Banner底", self.template_draw_banner_bg_check)

        self.banner_bg_style_combo = QComboBox()
        self.banner_bg_style_combo.addItem("实心", _BANNER_BACKGROUND_STYLE_SOLID)
        self.banner_bg_style_combo.addItem("底部渐变", _BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM)
        self.banner_bg_style_combo.currentIndexChanged.connect(self._on_banner_bg_style_changed)
        form.addRow("Banner样式", self.banner_bg_style_combo)

        self._gradient_editor = _GradientEditorWidget()
        self._gradient_editor.changed.connect(self._on_banner_gradient_widget_changed)
        self._gradient_editor_label = QLabel("渐变颜色")
        form.addRow(self._gradient_editor_label, self._gradient_editor)

        self._header_form = form
        self._update_banner_style_ui_visibility()
        return group

    def _build_banner_color_row(self) -> QWidget:
        """Banner 颜色行：预设下拉 + 文本输入 + 色块 + 调色板 + 吸管。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.template_banner_color_combo = QComboBox()
        self.template_banner_color_combo.addItem("无(透明)", _TEMPLATE_BANNER_COLOR_NONE)
        for label, value in COLOR_PRESETS:
            self.template_banner_color_combo.addItem(f"{label} {value}", value)
        self.template_banner_color_combo.addItem("自定义", _TEMPLATE_BANNER_COLOR_CUSTOM)
        self.template_banner_color_combo.currentIndexChanged.connect(
            self._on_template_banner_color_preset_changed
        )
        self.template_banner_color_combo.currentIndexChanged.connect(
            self._refresh_template_banner_color_swatch
        )
        layout.addWidget(self.template_banner_color_combo, stretch=1)

        self.template_banner_color_edit = QLineEdit(_DEFAULT_TEMPLATE_BANNER_COLOR)
        self.template_banner_color_edit.textChanged.connect(self._on_template_banner_color_text_changed)
        self.template_banner_color_edit.textChanged.connect(self._refresh_template_banner_color_swatch)
        layout.addWidget(self.template_banner_color_edit, stretch=1)

        self.template_banner_color_swatch = _build_color_preview_swatch()
        layout.addWidget(self.template_banner_color_swatch)
        self._refresh_template_banner_color_swatch()

        btn_palette = QPushButton("调色板")
        btn_palette.clicked.connect(self._pick_template_banner_color)
        layout.addWidget(btn_palette)

        btn_screen = QPushButton("吸管")
        btn_screen.clicked.connect(self._pick_template_banner_color_from_screen)
        layout.addWidget(btn_screen)

        self._banner_color_row_widget = row
        return row

    def _build_fields_group(self) -> QGroupBox:
        """文本项 GroupBox：列表 + 新增/删除按钮。"""
        group = QGroupBox("文本项")
        layout = QVBoxLayout(group)

        self.field_list = QListWidget()
        self.field_list.currentItemChanged.connect(self._on_field_selected)
        layout.addWidget(self.field_list)

        buttons = QHBoxLayout()
        btn_add = QPushButton("新增文本项")
        btn_add.clicked.connect(self._add_field)
        buttons.addWidget(btn_add)

        btn_remove = QPushButton("删除文本项")
        btn_remove.clicked.connect(self._remove_field)
        buttons.addWidget(btn_remove)

        layout.addLayout(buttons)
        return group

    def _build_field_edit_group(self) -> QGroupBox:
        """文本项编辑 GroupBox：所有文本项属性控件。"""
        group = QGroupBox("文本项编辑")
        form = QFormLayout(group)
        _configure_form_layout(form)

        self._field_fallback_combo = QComboBox()
        self._field_fallback_combo.setEditable(True)
        self._field_fallback_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._field_fallback_combo.addItem("（空）", ("", ""))
        for data_source, key, display_label in _get_template_context_field_options():
            source_name = _template_context.template_source_display_name(data_source)
            display_text = f"{source_name} — {key}  —  {display_label}"
            self._field_fallback_combo.addItem(display_text, (data_source, key))
        self._field_fallback_combo.setCurrentIndex(0)
        if self._field_fallback_combo.lineEdit():
            self._field_fallback_combo.lineEdit().setPlaceholderText("选数据源与字段，可输入自定义占位符")
        self.field_fallback_edit = self._field_fallback_combo.lineEdit()
        self._field_fallback_combo.currentIndexChanged.connect(self._on_fallback_var_selected)
        self.field_fallback_edit.textChanged.connect(self._apply_field_changes)
        form.addRow("数据源/字段", self._field_fallback_combo)

        self.field_align_h_combo = QComboBox()
        self.field_align_h_combo.addItems(list(ALIGN_OPTIONS_HORIZONTAL))
        self.field_align_h_combo.currentTextChanged.connect(self._apply_field_changes)
        form.addRow("水平对齐", self.field_align_h_combo)

        self.field_align_v_combo = QComboBox()
        self.field_align_v_combo.addItems(list(ALIGN_OPTIONS_VERTICAL))
        self.field_align_v_combo.currentTextChanged.connect(self._apply_field_changes)
        form.addRow("垂直对齐", self.field_align_v_combo)

        self.field_x_spin = QDoubleSpinBox()
        self.field_x_spin.setRange(-100.0, 100.0)
        self.field_x_spin.setDecimals(2)
        self.field_x_spin.setSingleStep(0.5)
        _configure_spinbox_minimum_width(self.field_x_spin, sample_text="-100.00", expanding=True)
        self.field_x_spin.valueChanged.connect(self._apply_field_changes)
        form.addRow("X偏移(%)", self.field_x_spin)

        self.field_y_spin = QDoubleSpinBox()
        self.field_y_spin.setRange(-100.0, 100.0)
        self.field_y_spin.setDecimals(2)
        self.field_y_spin.setSingleStep(0.5)
        _configure_spinbox_minimum_width(self.field_y_spin, sample_text="-100.00", expanding=True)
        self.field_y_spin.valueChanged.connect(self._apply_field_changes)
        form.addRow("Y偏移(%)", self.field_y_spin)

        form.addRow("文本颜色", self._build_field_color_row())

        self.field_font_filter_edit = QLineEdit()
        self.field_font_filter_edit.setPlaceholderText("过滤字体，如：微软雅黑 / PingFang / Arial")
        self.field_font_filter_edit.textChanged.connect(self._on_field_font_filter_changed)
        form.addRow("字体过滤", self.field_font_filter_edit)

        self.field_font_combo = _LazyLoadComboBox()
        self.field_font_combo.setMaxVisibleItems(24)
        self.field_font_combo.currentIndexChanged.connect(self._apply_field_changes)
        self.field_font_combo.popupAboutToShow.connect(self._ensure_field_font_choices_loaded)
        self.field_font_combo.setToolTip("首次展开时加载支持中文的字体列表")
        form.addRow("字体类型", self.field_font_combo)
        # 首次展开下拉时再加载字体列表，避免打开模板管理对话框时卡顿
        self._field_font_all_choices = []
        self._rebuild_field_font_combo(
            filter_text="",
            preferred_font_type=_DEFAULT_TEMPLATE_FONT_TYPE,
        )

        self.field_font_size_spin = QSpinBox()
        self.field_font_size_spin.setRange(8, 300)
        _configure_spinbox_minimum_width(self.field_font_size_spin, sample_text="300", expanding=True)
        self.field_font_size_spin.valueChanged.connect(self._apply_field_changes)
        form.addRow("字体大小", self.field_font_size_spin)

        self.field_style_combo = QComboBox()
        self.field_style_combo.addItems(list(STYLE_OPTIONS))
        self.field_style_combo.currentTextChanged.connect(self._apply_field_changes)
        form.addRow("字体样式", self.field_style_combo)

        return group

    def _build_field_color_row(self) -> QWidget:
        """文本颜色行：预设下拉 + 文本输入 + 色块 + 调色板 + 吸管。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.field_color_combo = QComboBox()
        for label, value in COLOR_PRESETS:
            self.field_color_combo.addItem(f"{label} {value}", value)
        self.field_color_combo.addItem("自定义", "custom")
        self.field_color_combo.currentIndexChanged.connect(self._on_color_preset_changed)
        self.field_color_combo.currentIndexChanged.connect(self._refresh_field_color_swatch)
        layout.addWidget(self.field_color_combo, stretch=1)

        self.field_color_edit = QLineEdit("#FFFFFF")
        self.field_color_edit.textChanged.connect(self._apply_field_changes)
        self.field_color_edit.textChanged.connect(self._refresh_field_color_swatch)
        layout.addWidget(self.field_color_edit, stretch=1)

        self.field_color_swatch = _build_color_preview_swatch()
        layout.addWidget(self.field_color_swatch)
        self._refresh_field_color_swatch()

        btn_palette = QPushButton("调色板")
        btn_palette.clicked.connect(self._pick_field_color)
        layout.addWidget(btn_palette)

        btn_screen = QPushButton("吸管")
        btn_screen.clicked.connect(self._pick_field_color_from_screen)
        layout.addWidget(btn_screen)

        return row

    def resizeEvent(self, event: Any) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._refresh_preview_label()

    # ------------------------------------------------------------------
    # Template list management
    # ------------------------------------------------------------------

    def _reload_template_list(self, preferred: str | None) -> None:
        _ensure_template_repository(self.template_dir)
        names = _list_template_names(self.template_dir)
        self.template_paths = {name: self.template_dir / f"{name}.json" for name in names}

        self.template_list.blockSignals(True)
        self.template_list.clear()
        for name in names:
            self.template_list.addItem(name)
        self.template_list.blockSignals(False)

        if not names:
            self.current_template_name = None
            self.current_payload = None
            self._populate_field_list([])
            self._refresh_preview()
            return

        target = preferred if preferred in self.template_paths else names[0]
        for idx in range(self.template_list.count()):
            item = self.template_list.item(idx)
            if item and item.text() == target:
                self.template_list.setCurrentRow(idx)
                break

    def _on_template_list_context_menu(self, pos: Any) -> None:
        item = self.template_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        selected = menu.exec(self.template_list.mapToGlobal(pos))
        if selected is rename_action:
            self._rename_template(item.text())
        elif selected is delete_action:
            self._delete_template(item.text())

    def _rename_template(self, source_name: str | None = None) -> None:
        origin_name = str(source_name or self.current_template_name or "").strip()
        if not origin_name:
            return
        source_path = self.template_paths.get(origin_name)
        if not source_path:
            return

        raw_name, ok = QInputDialog.getText(self, "重命名模板", "新模板名(仅文件名):", text=origin_name)
        if not ok:
            return
        target_name = _sanitize_template_name(raw_name)
        if not target_name:
            QMessageBox.warning(self, "模板管理", "模板名不能为空")
            return
        if target_name == origin_name:
            return

        target_path = self.template_dir / f"{target_name}.json"
        if target_path.exists():
            QMessageBox.warning(self, "模板管理", f"模板已存在: {target_path.name}")
            return

        try:
            payload = _load_template_payload(source_path)
            payload["name"] = target_name
            _save_template_payload(target_path, payload)
            source_path.unlink(missing_ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "重命名失败", str(exc))
            return

        self._reload_template_list(preferred=target_name)

    def _selected_template_name(self) -> str:
        item = self.template_list.currentItem()
        if item is not None:
            name = str(item.text() or "").strip()
            if name:
                return name
        return str(self.current_template_name or "").strip()

    def _on_template_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if not current:
            return
        name = current.text()
        path = self.template_paths.get(name)
        if not path:
            return
        try:
            payload = _load_template_payload(path)
        except Exception as exc:
            QMessageBox.critical(self, "模板错误", str(exc))
            return

        self.current_template_name = name
        self.current_payload = payload
        self.template_name_edit.setText(path.name)
        self._updating = True
        try:
            self._set_template_ratio_combo_value(payload.get("ratio"))
            self._set_tmpl_center_mode_value(payload.get("center_mode", _DEFAULT_TEMPLATE_CENTER_MODE))
            payload["auto_crop_by_bird"] = True  # 固定为根据鸟体计算，保留键以兼容旧模板
            self._set_tmpl_max_long_edge_value(
                int(payload.get("max_long_edge") or _DEFAULT_TEMPLATE_MAX_LONG_EDGE)
            )
            self._set_template_banner_color_value(payload.get("banner_color"))
            self._set_template_draw_banner_background_value(payload.get("draw_banner_background"))
            self._set_banner_bg_style_value(payload.get("banner_background_style"))
            self._gradient_editor.set_values(
                top_color=str(
                    payload.get("banner_gradient_top_color") or _BANNER_GRADIENT_TOP_COLOR_DEFAULT
                ),
                top_opacity_pct=float(
                    payload.get("banner_gradient_top_opacity_pct")
                    or _BANNER_GRADIENT_TOP_OPACITY_PCT_DEFAULT
                ),
                bot_color=str(
                    payload.get("banner_gradient_bottom_color") or _BANNER_GRADIENT_BOTTOM_COLOR_DEFAULT
                ),
                bot_opacity_pct=float(
                    payload.get("banner_gradient_bottom_opacity_pct")
                    or _BANNER_GRADIENT_BOTTOM_OPACITY_PCT_DEFAULT
                ),
                height_pct=float(
                    payload.get("banner_gradient_height_pct") or _BANNER_GRADIENT_HEIGHT_PCT_DEFAULT
                ),
            )
        finally:
            self._updating = False
        self._populate_field_list(payload.get("fields") or [])
        self._refresh_preview()

    # ------------------------------------------------------------------
    # Ratio / center mode / max edge
    # ------------------------------------------------------------------

    def _template_ratio_combo_index_for_value(self, ratio: Any) -> int:
        for idx in range(self.template_ratio_combo.count()):
            data = self.template_ratio_combo.itemData(idx)
            if data is None and ratio is None:
                return idx
            if data is RATIO_FREE or data == RATIO_FREE:
                if ratio is RATIO_FREE or ratio == RATIO_FREE:
                    return idx
                continue
            if data is None or ratio is None:
                continue
            if ratio is RATIO_FREE or ratio == RATIO_FREE:
                continue
            try:
                if abs(float(data) - float(ratio)) <= 0.0001:
                    return idx
            except Exception:
                continue
        return -1

    def _set_template_ratio_combo_value(self, ratio: Any) -> None:
        parsed = _parse_ratio_value(ratio)
        idx = self._template_ratio_combo_index_for_value(parsed)
        if idx < 0:
            return
        self.template_ratio_combo.setCurrentIndex(idx)

    def _on_template_ratio_changed(self, *_args: Any) -> None:
        if self._updating or not self.current_payload:
            return
        ratio = _parse_ratio_value(self.template_ratio_combo.currentData())
        self.current_payload["ratio"] = ratio
        if not _is_ratio_free(ratio):
            cb = self.current_payload.get("crop_box")
            img = self._preview_source_image or self.placeholder
            if cb is not None and isinstance(cb, (list, tuple)) and len(cb) == 4 and img is not None:
                try:
                    box = (float(cb[0]), float(cb[1]), float(cb[2]), float(cb[3]))
                    w, h = img.size
                    if w > 0 and h > 0:
                        new_box = _constrain_box_to_ratio(box, ratio, w, h)
                        self.current_payload["crop_box"] = [new_box[0], new_box[1], new_box[2], new_box[3]]
                except (TypeError, ValueError):
                    pass
        self._save_current_template()
        self._refresh_preview()

    def _set_tmpl_center_mode_value(self, value: Any) -> None:
        mode = _normalize_center_mode(value)
        for idx in range(self.template_center_mode_combo.count()):
            if self.template_center_mode_combo.itemData(idx) == mode:
                self.template_center_mode_combo.setCurrentIndex(idx)
                return

    def _on_tmpl_center_mode_changed(self, *_args: Any) -> None:
        if self._updating or not self.current_payload:
            return
        center_mode = str(self.template_center_mode_combo.currentData() or _DEFAULT_TEMPLATE_CENTER_MODE)
        self.current_payload["center_mode"] = center_mode
        self.current_payload["auto_crop_by_bird"] = True  # 固定为根据鸟体计算
        self._save_current_template()
        self._refresh_preview()

    def _set_tmpl_max_long_edge_value(self, value: int) -> None:
        edge = max(0, int(value))
        idx = self.template_max_long_edge_combo.findData(edge)
        if idx < 0:
            label = "不限制" if edge == 0 else str(edge)
            self.template_max_long_edge_combo.addItem(label, edge)
            idx = self.template_max_long_edge_combo.findData(edge)
        if idx >= 0:
            self.template_max_long_edge_combo.setCurrentIndex(idx)

    def _on_tmpl_max_long_edge_changed(self, *_args: Any) -> None:
        if self._updating or not self.current_payload:
            return
        try:
            edge = max(0, int(self.template_max_long_edge_combo.currentData() or 0))
        except Exception:
            edge = 0
        self.current_payload["max_long_edge"] = edge
        self._save_current_template()

    # ------------------------------------------------------------------
    # Font helpers
    # ------------------------------------------------------------------

    def _filtered_field_font_choices(self, filter_text: str) -> list[tuple[str, str]]:
        all_choices = self._field_font_all_choices or [("自动(系统默认)", _DEFAULT_TEMPLATE_FONT_TYPE)]
        query = str(filter_text or "").strip().lower()
        if not query:
            return list(all_choices)

        filtered: list[tuple[str, str]] = []
        for label, font_type in all_choices:
            if font_type == _DEFAULT_TEMPLATE_FONT_TYPE:
                filtered.append((label, font_type))
                continue
            haystack = f"{label} {font_type}".lower()
            if query in haystack:
                filtered.append((label, font_type))
        if not filtered:
            filtered.append(("自动(系统默认)", _DEFAULT_TEMPLATE_FONT_TYPE))
        return filtered

    def _field_font_combo_index_for_value(self, value: Any) -> int:
        target = _normalize_template_font_type(value)
        for idx in range(self.field_font_combo.count()):
            data = _normalize_template_font_type(self.field_font_combo.itemData(idx))
            if data == target:
                return idx
        return -1

    def _ensure_field_font_choices_loaded(self) -> None:
        """首次展开下拉时加载字体列表（仅显示支持中文的字体）。"""
        if self._field_font_choices_loaded and self._field_font_all_choices:
            return
        self.field_font_combo.setEnabled(False)
        try:
            self._field_font_all_choices = list(
                _template_font_choices(chinese_only=True, prefer_chinese_label=True)
            )
            self._field_font_choices_loaded = True
        finally:
            self.field_font_combo.setEnabled(True)
        preferred = _normalize_template_font_type(
            self.field_font_combo.currentData() if hasattr(self, "field_font_combo") else None
        )
        filter_text = (
            self.field_font_filter_edit.text()
            if hasattr(self, "field_font_filter_edit") else ""
        )
        self._rebuild_field_font_combo(
            filter_text=filter_text,
            preferred_font_type=preferred or _DEFAULT_TEMPLATE_FONT_TYPE,
        )

    def _rebuild_field_font_combo(self, *, filter_text: str, preferred_font_type: Any) -> None:
        choices = self._filtered_field_font_choices(filter_text)
        target = _normalize_template_font_type(preferred_font_type)
        self.field_font_combo.blockSignals(True)
        try:
            self.field_font_combo.clear()
            for label, font_type in choices:
                self.field_font_combo.addItem(label, font_type)

            idx = self._field_font_combo_index_for_value(target)
            if idx < 0 and target != _DEFAULT_TEMPLATE_FONT_TYPE:
                self.field_font_combo.addItem(f"当前字体: {target}", target)
                idx = self.field_font_combo.count() - 1
            if idx < 0:
                idx = 0 if self.field_font_combo.count() > 0 else -1
            if idx >= 0:
                self.field_font_combo.setCurrentIndex(idx)
        finally:
            self.field_font_combo.blockSignals(False)

    def _on_field_font_filter_changed(self, *_args: Any) -> None:
        if not self._field_font_choices_loaded:
            return
        preferred = _normalize_template_font_type(self.field_font_combo.currentData())
        self._rebuild_field_font_combo(
            filter_text=self.field_font_filter_edit.text(),
            preferred_font_type=preferred,
        )

    def _set_field_font_combo_value(self, value: Any) -> None:
        normalized = _normalize_template_font_type(value)
        filter_text = self.field_font_filter_edit.text() if hasattr(self, "field_font_filter_edit") else ""
        self._rebuild_field_font_combo(
            filter_text=filter_text,
            preferred_font_type=normalized,
        )

    # ------------------------------------------------------------------
    # Banner color
    # ------------------------------------------------------------------

    def _refresh_template_banner_color_swatch(self, *_args: Any) -> None:
        selected = str(self.template_banner_color_combo.currentData() or "").strip().lower()
        if selected == _TEMPLATE_BANNER_COLOR_NONE:
            value = _TEMPLATE_BANNER_COLOR_NONE
        elif selected and selected != _TEMPLATE_BANNER_COLOR_CUSTOM:
            value = selected
        else:
            typed = self.template_banner_color_edit.text().strip()
            value = typed if typed else _DEFAULT_TEMPLATE_BANNER_COLOR
        _set_color_preview_swatch(
            self.template_banner_color_swatch,
            value,
            fallback=_DEFAULT_TEMPLATE_BANNER_COLOR,
            allow_none=True,
        )

    def _template_banner_color_combo_index_for_value(self, value: str) -> int:
        target = str(value or "").strip().lower()
        for idx in range(self.template_banner_color_combo.count()):
            data = str(self.template_banner_color_combo.itemData(idx) or "").strip().lower()
            if data == target:
                return idx
        return -1

    def _set_template_banner_color_value(self, value: Any) -> None:
        normalized = _normalize_template_banner_color(value)
        custom_idx = self._template_banner_color_combo_index_for_value(_TEMPLATE_BANNER_COLOR_CUSTOM)
        if custom_idx < 0:
            custom_idx = max(0, self.template_banner_color_combo.count() - 1)

        if normalized == _TEMPLATE_BANNER_COLOR_NONE:
            idx = self._template_banner_color_combo_index_for_value(_TEMPLATE_BANNER_COLOR_NONE)
            if idx < 0:
                idx = custom_idx
            self.template_banner_color_combo.setCurrentIndex(idx)
            self.template_banner_color_edit.setText("")
            self._refresh_template_banner_color_swatch()
            return

        idx = self._template_banner_color_combo_index_for_value(normalized)
        if idx < 0:
            idx = custom_idx
        self.template_banner_color_combo.setCurrentIndex(idx)
        self.template_banner_color_edit.setText(normalized)
        self._refresh_template_banner_color_swatch()

    def _set_template_draw_banner_background_value(self, value: Any) -> None:
        self.template_draw_banner_bg_check.setChecked(_parse_bool_value(value, True))

    def _on_template_draw_banner_background_changed(self, *_args: Any) -> None:
        if self._updating or not self.current_payload:
            return
        self.current_payload["draw_banner_background"] = bool(
            self.template_draw_banner_bg_check.isChecked()
        )
        self._save_current_template()
        self._refresh_preview()

    def _update_banner_style_ui_visibility(self) -> None:
        is_gradient = (
            self.banner_bg_style_combo.currentData() == _BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM
        )
        lbl = self._header_form.labelForField(self._banner_color_row_widget)
        if lbl:
            lbl.setVisible(not is_gradient)
        self._banner_color_row_widget.setVisible(not is_gradient)
        self._gradient_editor_label.setVisible(is_gradient)
        self._gradient_editor.setVisible(is_gradient)

    def _on_banner_bg_style_changed(self, *_args: Any) -> None:
        self._update_banner_style_ui_visibility()
        if self._updating or not self.current_payload:
            return
        self.current_payload["banner_background_style"] = str(
            self.banner_bg_style_combo.currentData() or _BANNER_BACKGROUND_STYLE_SOLID
        )
        self._save_current_template()
        self._refresh_preview()

    def _on_banner_gradient_widget_changed(self) -> None:
        if self._updating or not self.current_payload:
            return
        self.current_payload.update(self._gradient_editor.get_values())
        self._save_current_template()
        self._refresh_preview()

    def _set_banner_bg_style_value(self, value: Any) -> None:
        style = _normalize_banner_background_style(value)
        for idx in range(self.banner_bg_style_combo.count()):
            if self.banner_bg_style_combo.itemData(idx) == style:
                self.banner_bg_style_combo.setCurrentIndex(idx)
                break
        self._update_banner_style_ui_visibility()

    def _apply_template_banner_color(self) -> None:
        if self._updating or not self.current_payload:
            return

        selected = str(self.template_banner_color_combo.currentData() or "").strip().lower()
        if selected == _TEMPLATE_BANNER_COLOR_NONE:
            banner_color = _TEMPLATE_BANNER_COLOR_NONE
        elif selected == _TEMPLATE_BANNER_COLOR_CUSTOM:
            typed = self.template_banner_color_edit.text().strip()
            banner_color = _normalize_template_banner_color(
                typed if typed else _DEFAULT_TEMPLATE_BANNER_COLOR
            )
            if banner_color == _TEMPLATE_BANNER_COLOR_NONE:
                banner_color = _normalize_template_banner_color(_DEFAULT_TEMPLATE_BANNER_COLOR)
        else:
            banner_color = _normalize_template_banner_color(selected)

        self.current_payload["banner_color"] = banner_color
        self._save_current_template()
        self._refresh_preview()
        self._refresh_template_banner_color_swatch()

    def _on_template_banner_color_preset_changed(self, *_args: Any) -> None:
        if self._updating or not self.current_payload:
            return

        selected = str(self.template_banner_color_combo.currentData() or "").strip().lower()
        self._updating = True
        try:
            if selected == _TEMPLATE_BANNER_COLOR_NONE:
                self.template_banner_color_edit.setText("")
            elif selected and selected != _TEMPLATE_BANNER_COLOR_CUSTOM:
                self.template_banner_color_edit.setText(selected)
        finally:
            self._updating = False
        self._apply_template_banner_color()

    def _on_template_banner_color_text_changed(self, *_args: Any) -> None:
        if self._updating or not self.current_payload:
            return

        selected = str(self.template_banner_color_combo.currentData() or "").strip().lower()
        text = self.template_banner_color_edit.text().strip()
        should_switch_to_custom = False
        if text:
            if selected == _TEMPLATE_BANNER_COLOR_NONE:
                should_switch_to_custom = True
            elif selected not in {_TEMPLATE_BANNER_COLOR_CUSTOM, ""} and text.lower() != selected:
                should_switch_to_custom = True
        if should_switch_to_custom:
            custom_idx = self._template_banner_color_combo_index_for_value(_TEMPLATE_BANNER_COLOR_CUSTOM)
            if custom_idx >= 0:
                self.template_banner_color_combo.blockSignals(True)
                try:
                    self.template_banner_color_combo.setCurrentIndex(custom_idx)
                finally:
                    self.template_banner_color_combo.blockSignals(False)
        self._apply_template_banner_color()

    def _pick_template_banner_color(self) -> None:
        initial_text = self.template_banner_color_edit.text().strip() or _DEFAULT_TEMPLATE_BANNER_COLOR
        initial = QColor(initial_text)
        chosen = QColorDialog.getColor(initial, self, "选择 Banner 颜色")
        if not chosen.isValid():
            return

        custom_idx = self._template_banner_color_combo_index_for_value(_TEMPLATE_BANNER_COLOR_CUSTOM)
        if custom_idx >= 0:
            self.template_banner_color_combo.setCurrentIndex(custom_idx)
        self.template_banner_color_edit.setText(chosen.name())

    def _pick_template_banner_color_from_screen(self) -> None:
        def _apply(color_hex: str) -> None:
            custom_idx = self._template_banner_color_combo_index_for_value(_TEMPLATE_BANNER_COLOR_CUSTOM)
            if custom_idx >= 0:
                self.template_banner_color_combo.setCurrentIndex(custom_idx)
            self.template_banner_color_edit.setText(
                _safe_color(color_hex, _DEFAULT_TEMPLATE_BANNER_COLOR)
            )

        _start_screen_color_picker(parent=self, on_picked=_apply)

    # ------------------------------------------------------------------
    # Field list / editor
    # ------------------------------------------------------------------

    def _fallback_combo_index_for_value(self, data_source: str, key: str) -> int:
        combo = self._field_fallback_combo
        target_source = str(data_source or "").strip().lower()
        target_key = str(key or "").strip()
        for idx in range(combo.count()):
            item_data = combo.itemData(idx)
            if not isinstance(item_data, (list, tuple)) or len(item_data) < 2:
                continue
            item_source = str(item_data[0] or "").strip().lower()
            item_key = str(item_data[1] or "").strip()
            if item_source == target_source and item_key == target_key:
                return idx
            if (
                item_source == _template_context.TEMPLATE_SOURCE_AUTO
                and item_key == target_key
                and target_source in {
                    _template_context.TEMPLATE_SOURCE_AUTO,
                    _template_context.TEMPLATE_SOURCE_EXIF,
                    _template_context.TEMPLATE_SOURCE_FROM_FILE,
                    _template_context.TEMPLATE_SOURCE_REPORT_DB,
                }
            ):
                return idx
        return -1

    def _field_source_display_text(self, field: dict[str, Any] | None, index: int = 0) -> str:
        normalized = _normalize_template_field(field or {}, index)
        text_source = normalized.get("text_source") or {}
        source_type = str(text_source.get("type") or "").strip()
        source_key = str(text_source.get("key") or "").strip()
        if not source_key:
            return "（空）"
        combo_idx = self._fallback_combo_index_for_value(source_type, source_key)
        if combo_idx >= 0:
            return str(self._field_fallback_combo.itemText(combo_idx) or "").strip()
        provider = _template_context.build_template_context_provider(
            source_type,
            source_key,
            display_label=str(normalized.get("name") or ""),
        )
        preview_photo = self._preview_photo_info or _template_context.ensure_photo_info(Path("."), raw_metadata={})
        return provider.get_display_caption(preview_photo)

    def _current_field_source_display_text(self) -> str:
        text = str(self._field_fallback_combo.currentText() or "").strip()
        return text or "（空）"

    def _fallback_combo_uses_selected_item(self) -> bool:
        idx = self._field_fallback_combo.currentIndex()
        if idx < 0:
            return False
        return self._current_field_source_display_text() == str(self._field_fallback_combo.itemText(idx) or "").strip()

    def _populate_field_list(self, fields: list[dict[str, Any]]) -> None:
        self.field_list.blockSignals(True)
        self.field_list.clear()
        for idx, field in enumerate(fields):
            display = self._field_source_display_text(field, idx)
            self.field_list.addItem(display)
        self.field_list.blockSignals(False)

        if self.field_list.count() > 0:
            self.field_list.setCurrentRow(0)
        else:
            self._apply_field_to_editor(None)

    def _selected_field_index(self) -> int:
        return self.field_list.currentRow()

    def _selected_field(self) -> dict[str, Any] | None:
        if not self.current_payload:
            return None
        fields = self.current_payload.get("fields") or []
        idx = self._selected_field_index()
        if idx < 0 or idx >= len(fields):
            return None
        field = fields[idx]
        if not isinstance(field, dict):
            return None
        return field

    def _on_field_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if not current:
            self._apply_field_to_editor(None)
            return
        self._apply_field_to_editor(self._selected_field())

    def _apply_field_to_editor(self, field: dict[str, Any] | None) -> None:
        self._updating = True
        try:
            if not field:
                self._set_fallback_combo_value("")
                self.field_align_h_combo.setCurrentText("left")
                self.field_align_v_combo.setCurrentText("top")
                self.field_x_spin.setValue(0.0)
                self.field_y_spin.setValue(0.0)
                self.field_color_edit.setText("#FFFFFF")
                self._set_field_font_combo_value(_DEFAULT_TEMPLATE_FONT_TYPE)
                self.field_font_size_spin.setValue(24)
                self.field_style_combo.setCurrentText(STYLE_OPTIONS[0])
                self.field_color_combo.setCurrentIndex(0)
                return

            normalized = _normalize_template_field(field, 0)
            text_source = normalized.get("text_source") or {}
            self._set_fallback_combo_value(
                str(text_source.get("key") or ""),
                data_source=str(text_source.get("type") or ""),
                source_key=str(text_source.get("key") or ""),
            )
            self.field_align_h_combo.setCurrentText(normalized["align_horizontal"])
            self.field_align_v_combo.setCurrentText(normalized["align_vertical"])
            self.field_x_spin.setValue(float(normalized["x_offset_pct"]))
            self.field_y_spin.setValue(float(normalized["y_offset_pct"]))
            self.field_color_edit.setText(normalized["color"])
            self._set_field_font_combo_value(normalized.get("font_type"))
            self.field_font_size_spin.setValue(int(normalized["font_size"]))
            self.field_style_combo.setCurrentText(normalized["style"])

            preset_index = self.field_color_combo.count() - 1
            for idx in range(self.field_color_combo.count() - 1):
                value = str(self.field_color_combo.itemData(idx) or "")
                if value.lower() == normalized["color"].lower():
                    preset_index = idx
                    break
            self.field_color_combo.setCurrentIndex(preset_index)
        finally:
            self._updating = False
            self._refresh_field_color_swatch()

    def _refresh_field_color_swatch(self, *_args: Any) -> None:
        _set_color_preview_swatch(
            self.field_color_swatch, self.field_color_edit.text().strip(), fallback="#FFFFFF"
        )

    def _on_color_preset_changed(self, *_args: Any) -> None:
        if self._updating:
            return
        value = str(self.field_color_combo.currentData() or "")
        if value and value != "custom":
            self.field_color_edit.setText(value)

    def _pick_field_color(self) -> None:
        initial = QColor(self.field_color_edit.text().strip() or "#ffffff")
        chosen = QColorDialog.getColor(initial, self, "选择文本颜色")
        if not chosen.isValid():
            return
        self.field_color_edit.setText(chosen.name())

    def _pick_field_color_from_screen(self) -> None:
        def _apply(color_hex: str) -> None:
            custom_idx = self.field_color_combo.findData("custom")
            if custom_idx >= 0:
                self.field_color_combo.setCurrentIndex(custom_idx)
            self.field_color_edit.setText(_safe_color(color_hex, "#FFFFFF"))

        _start_screen_color_picker(parent=self, on_picked=_apply)

    def _set_fallback_combo_value(
        self,
        fallback: str = "",
        *,
        data_source: str | None = None,
        source_key: str | None = None,
    ) -> None:
        old = self._updating
        self._updating = True
        try:
            combo = self._field_fallback_combo
            matched = -1
            ds = _template_context.normalize_template_source_type(data_source)
            key = (source_key or "").strip() or (fallback or "").strip()
            if ds and key:
                for i in range(combo.count()):
                    item_data = combo.itemData(i)
                    if isinstance(item_data, (list, tuple)) and len(item_data) >= 2:
                        item_source = str(item_data[0] or "").strip().lower()
                        item_key = str(item_data[1] or "").strip()
                        if item_source == ds and item_key == key:
                            matched = i
                            break
                        if (
                            item_source == _template_context.TEMPLATE_SOURCE_AUTO
                            and item_key == key
                            and ds in {
                                _template_context.TEMPLATE_SOURCE_AUTO,
                                _template_context.TEMPLATE_SOURCE_EXIF,
                                _template_context.TEMPLATE_SOURCE_FROM_FILE,
                                _template_context.TEMPLATE_SOURCE_REPORT_DB,
                            }
                        ):
                            matched = i
                            break
            elif not (fallback or "").strip():
                matched = 0
            if matched >= 0:
                combo.setCurrentIndex(matched)
                if combo.lineEdit():
                    combo.lineEdit().setText(str(combo.itemText(matched) or "").strip())
            if matched < 0:
                combo.setCurrentIndex(0)
                if combo.lineEdit():
                    combo.lineEdit().setText(key.strip())
        finally:
            self._updating = old

    def _on_fallback_var_selected(self, index: int) -> None:
        if self._updating:
            return
        data = self._field_fallback_combo.itemData(index)
        if data is not None and self.field_fallback_edit:
            old = self._updating
            self._updating = True
            try:
                self.field_fallback_edit.setText(
                    str(self._field_fallback_combo.itemText(index) or "").strip()
                )
            finally:
                self._updating = old
            self._apply_field_changes()

    def _apply_field_changes(self, *_args: Any) -> None:
        if self._updating:
            return
        field = self._selected_field()
        if not field or not self.current_payload:
            return

        item_data = self._field_fallback_combo.currentData()
        uses_selected_item = self._fallback_combo_uses_selected_item()
        if uses_selected_item and isinstance(item_data, (list, tuple)) and len(item_data) >= 2:
            source_type = _template_context.normalize_template_source_type(item_data[0])
            source_key = str(item_data[1] or "").strip()
        else:
            source_type = _template_context.TEMPLATE_SOURCE_AUTO
            source_key = str(self.field_fallback_edit.text() or "").strip()
        field["text_source"] = {
            "type": source_type,
            "key": source_key,
        }
        field["data_source"] = source_type
        field["report_field"] = source_key if source_type == _template_context.TEMPLATE_SOURCE_REPORT_DB else ""
        field["fallback"] = source_key if source_type == _template_context.TEMPLATE_SOURCE_FROM_FILE else ""
        if source_type == _template_context.TEMPLATE_SOURCE_EXIF:
            field["tag"] = source_key
        align_h = self.field_align_h_combo.currentText().strip().lower()
        align_v = self.field_align_v_combo.currentText().strip().lower()
        field["align_horizontal"] = align_h if align_h in ALIGN_OPTIONS_HORIZONTAL else "left"
        field["align_vertical"] = align_v if align_v in ALIGN_OPTIONS_VERTICAL else "top"
        field["x_offset_pct"] = round(self.field_x_spin.value(), 2)
        field["y_offset_pct"] = round(self.field_y_spin.value(), 2)
        field["color"] = _safe_color(self.field_color_edit.text(), "#FFFFFF")
        field["font_type"] = _normalize_template_font_type(self.field_font_combo.currentData())
        field["font_size"] = int(self.field_font_size_spin.value())
        style = self.field_style_combo.currentText().strip().lower()
        field["style"] = style if style in STYLE_OPTIONS else STYLE_OPTIONS[0]
        field["name"] = self._current_field_source_display_text()

        idx = self._selected_field_index()
        if idx >= 0:
            item = self.field_list.item(idx)
            if item:
                item.setText(self._field_source_display_text(field, idx))

        self._save_current_template()
        self._refresh_preview()

    def _add_field(self) -> None:
        if not self.current_payload:
            return
        fields = self.current_payload.setdefault("fields", [])
        if not isinstance(fields, list):
            fields = []
            self.current_payload["fields"] = fields

        default_field = _normalize_template_field({}, len(fields))
        fields.append(default_field)
        self._populate_field_list(fields)
        self.field_list.setCurrentRow(len(fields) - 1)
        self._save_current_template()
        self._refresh_preview()

    def _remove_field(self) -> None:
        if not self.current_payload:
            return
        fields = self.current_payload.get("fields") or []
        if not isinstance(fields, list) or not fields:
            return

        idx = self._selected_field_index()
        if idx < 0 or idx >= len(fields):
            return

        fields.pop(idx)
        if not fields:
            fields.append(_normalize_template_field({}, 0))
        self.current_payload["fields"] = fields

        self._populate_field_list(fields)
        self.field_list.setCurrentRow(max(0, idx - 1))
        self._save_current_template()
        self._refresh_preview()

    # ------------------------------------------------------------------
    # Template CRUD
    # ------------------------------------------------------------------

    def _create_template(self) -> None:
        name, ok = QInputDialog.getText(self, "新增模板", "模板名(仅文件名):")
        if not ok:
            return
        safe_name = _sanitize_template_name(name)
        if not safe_name:
            QMessageBox.warning(self, "模板管理", "模板名不能为空")
            return

        path = self.template_dir / f"{safe_name}.json"
        if path.exists():
            QMessageBox.warning(self, "模板管理", f"模板已存在: {path.name}")
            return

        payload = _default_template_payload(name=safe_name)
        _save_template_payload(path, payload)
        self._reload_template_list(preferred=safe_name)

    def _copy_template(self) -> None:
        if not self.current_template_name:
            return
        source_path = self.template_paths.get(self.current_template_name)
        if not source_path:
            return

        base_name = f"{self.current_template_name}_copy"
        candidate = base_name
        suffix = 1
        while (self.template_dir / f"{candidate}.json").exists():
            suffix += 1
            candidate = f"{base_name}_{suffix}"

        payload = _load_template_payload(source_path)
        payload["name"] = candidate
        _save_template_payload(self.template_dir / f"{candidate}.json", payload)
        self._reload_template_list(preferred=candidate)

    def _delete_template(self, source_name: str | None = None) -> None:
        target_name = str(source_name or self._selected_template_name()).strip()
        if not target_name:
            return
        if len(self.template_paths) <= 1:
            QMessageBox.warning(self, "模板管理", "至少保留一个模板")
            return

        path = self.template_paths.get(target_name)
        if not path:
            return

        confirm = QMessageBox.question(self, "删除模板", f"确定删除 {path.name} ?")
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return

        self._reload_template_list(preferred=None)

    def _save_current_template(self) -> None:
        if not self.current_template_name or not self.current_payload:
            return
        path = self.template_paths.get(self.current_template_name)
        if not path:
            return

        payload = _normalize_template_payload(
            self.current_payload, fallback_name=self.current_template_name
        )
        payload["name"] = self.current_template_name
        self.current_payload = payload
        _save_template_payload(path, payload)

    def _build_preview_overlay_options(self) -> EditorPreviewOverlayOptions:
        return EditorPreviewOverlayOptions(
            show_focus_box=bool(self.show_focus_box_check.isChecked()),
            show_bird_box=bool(self.show_bird_box_check.isChecked()),
            show_crop_effect=bool(self.show_crop_effect_check.isChecked()),
            crop_effect_alpha=int(self.crop_effect_alpha_slider.value()),
            composition_grid_mode=normalize_preview_composition_grid_mode(
                self.preview_grid_combo.currentData() if self.preview_grid_combo is not None else "none"
            ),
            composition_grid_line_width=normalize_preview_composition_grid_line_width(
                self.preview_grid_line_width_combo.currentData()
                if self.preview_grid_line_width_combo is not None
                else 1
            ),
        )

    def _apply_preview_overlay_options(self) -> None:
        self.preview_label.apply_overlay_options(self._build_preview_overlay_options())
        canvas = self.preview_label.canvas
        if hasattr(canvas, "set_crop_edit_mode"):
            canvas.set_crop_edit_mode(self.crop_edit_mode_check.isChecked())
        if hasattr(canvas, "set_crop_ratio_constraint"):
            r = _parse_ratio_value(self.template_ratio_combo.currentData())
            canvas.set_crop_ratio_constraint(
                r if (r is not None and not _is_ratio_free(r)) else None,
                _is_ratio_free(r),
            )

    def _on_tmpl_canvas_crop_box_changed(self, box: tuple[float, float, float, float]) -> None:
        if self.current_payload is not None:
            self.current_payload["crop_box"] = [box[0], box[1], box[2], box[3]]
            if self._preview_photo_info is not None:
                self._preview_photo_info = _template_context.ensure_editor_photo_info(
                    self._preview_photo_info,
                    crop_box=box,
                )
            self._refresh_preview()

    def _on_preview_overlay_toggled(self, _checked: bool) -> None:
        self._apply_preview_overlay_options()

    def _on_preview_grid_mode_changed(self, _index: int) -> None:
        self._apply_preview_overlay_options()

    def _on_preview_grid_line_width_changed(self, _index: int) -> None:
        self._apply_preview_overlay_options()

    def _on_preview_scale_preset_activated(self, index: int) -> None:
        percent = self.preview_scale_combo.itemData(index)
        try:
            parsed = float(percent)
        except Exception:
            return
        self.preview_label.set_display_scale_percent(parsed, preserve_view=True)
        self._sync_preview_scale_combo(self.preview_label.current_display_scale_percent())

    def _sync_preview_scale_combo(self, scale_percent: object) -> None:
        sync_preview_scale_preset_combo(self.preview_scale_combo, scale_percent)

    def _on_preview_crop_effect_alpha_changed(self, value: int) -> None:
        alpha = max(0, min(255, int(value)))
        self.crop_effect_alpha_value_label.setText(str(alpha))
        self._apply_preview_overlay_options()

    def _preview_source_bird_box(self) -> tuple[float, float, float, float] | None:
        if self._preview_bird_box_cache_ready:
            return self._preview_bird_box_cache
        self._preview_bird_box_cache_ready = True
        if self._preview_source_image is None:
            self._preview_bird_box_cache = None
            return None
        try:
            self._preview_bird_box_cache = _detect_primary_bird_box(self._preview_source_image)
        except Exception:
            self._preview_bird_box_cache = None
        return self._preview_bird_box_cache

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _refresh_preview(self) -> None:
        source = (self._preview_source_image or self.placeholder).copy()
        image = source
        crop_box: tuple[float, float, float, float] | None = None
        outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0)
        focus_camera_type = _resolve_focus_camera_type_from_metadata(self._preview_raw_metadata)

        if self.current_payload:
            ratio = _parse_ratio_value(self.current_payload.get("ratio"))
            center_mode = _normalize_center_mode(
                str(self.current_payload.get("center_mode") or _DEFAULT_TEMPLATE_CENTER_MODE)
            )
            fill_color = str(self.current_payload.get("crop_padding_fill") or "#FFFFFF")
            # 使用与主界面完全一致的裁切管线，含非对称内边距传入鸟体裁切算法
            inner_top = _parse_padding_value(self.current_payload.get("crop_padding_top"), 0)
            inner_bottom = _parse_padding_value(self.current_payload.get("crop_padding_bottom"), 0)
            inner_left = _parse_padding_value(self.current_payload.get("crop_padding_left"), 0)
            inner_right = _parse_padding_value(self.current_payload.get("crop_padding_right"), 0)

            crop_box_override = None
            cb_raw = self.current_payload.get("crop_box")
            if cb_raw is not None and isinstance(cb_raw, (list, tuple)) and len(cb_raw) == 4:
                try:
                    crop_box_override = (float(cb_raw[0]), float(cb_raw[1]), float(cb_raw[2]), float(cb_raw[3]))
                except (TypeError, ValueError):
                    pass
            if self._preview_photo_info is not None:
                self._preview_photo_info = _template_context.ensure_editor_photo_info(
                    self._preview_photo_info,
                    crop_box=crop_box_override,
                )
            crop_box, outer_pad = _compute_crop_plan(
                source,
                self._preview_raw_metadata,
                ratio=ratio,
                center_mode=center_mode,
                camera_type=focus_camera_type,
                inner_top=inner_top,
                inner_bottom=inner_bottom,
                inner_left=inner_left,
                inner_right=inner_right,
                crop_box_override=crop_box_override,
            )
            pad_top, pad_bottom, pad_left, pad_right = outer_pad
            if pad_top or pad_bottom or pad_left or pad_right:
                source = _pad_image(
                    source,
                    top=pad_top,
                    bottom=pad_bottom,
                    left=pad_left,
                    right=pad_right,
                    fill=fill_color,
                )

            # 与主编辑器预览一致：保留完整画面，仅在裁切区域渲染模板效果；
            # 裁切范围通过 EditorPreviewCanvas 的 crop-effect overlay 显示。
            image = render_template_overlay_in_crop_region(
                source,
                raw_metadata=self._preview_raw_metadata,
                metadata_context=self._preview_metadata_context,
                photo_info=self._preview_photo_info,
                template_payload=self.current_payload,
                crop_box=crop_box,
            )

        source_width, source_height = (self._preview_source_image or self.placeholder).size
        pad_top, pad_bottom, pad_left, pad_right = outer_pad
        # 模板管理器预览也必须沿用主编辑器同一套“元数据尺寸 + Orientation 映射”逻辑，
        # 否则后续 AI 改动很容易把对焦框重新改回错位状态。
        preview_focus_box = _transform_source_box_after_crop_padding(
            _extract_focus_box_for_display(
                self._preview_raw_metadata,
                source_width,
                source_height,
                camera_type=focus_camera_type,
            ),
            crop_box=None,
            source_width=source_width,
            source_height=source_height,
            pt=pad_top,
            pb=pad_bottom,
            pl=pad_left,
            pr=pad_right,
        )
        preview_bird_box = _transform_source_box_after_crop_padding(
            self._preview_source_bird_box(),
            crop_box=None,
            source_width=source_width,
            source_height=source_height,
            pt=pad_top,
            pb=pad_bottom,
            pl=pad_left,
            pr=pad_right,
        )
        self.preview_overlay_state = EditorPreviewOverlayState(
            focus_box=preview_focus_box,
            bird_box=preview_bird_box,
            crop_effect_box=crop_box,
        )
        self._preview_crop_size = _compute_crop_output_size(
            source_width,
            source_height,
            crop_box,
            outer_pad,
        )
        self.preview_pixmap = _pil_to_qpixmap(image)
        self._refresh_preview_label()

    def _refresh_preview_label(self) -> None:
        self._apply_preview_overlay_options()

        if self.preview_pixmap is None or self.preview_pixmap.isNull():
            self.preview_label.apply_overlay_state(EditorPreviewOverlayState())
            self.preview_label.set_original_size(None, None)
            self.preview_label.set_cropped_size(None, None)
            self.preview_label.set_source_mode("")
            self.preview_label.set_source_pixmap(None)
            return
        self.preview_label.apply_overlay_state(self.preview_overlay_state)
        if self._preview_source_image is not None:
            w, h = self._preview_source_image.size
            self.preview_label.set_original_size(w, h)
        else:
            self.preview_label.set_original_size(None, None)
        crop_size = getattr(self, "_preview_crop_size", None)
        if crop_size is not None:
            self.preview_label.set_cropped_size(crop_size[0], crop_size[1])
        else:
            self.preview_label.set_cropped_size(None, None)
        self.preview_label.set_source_mode("原图")
        self.preview_label.set_source_pixmap(self.preview_pixmap, preserve_view=True)
