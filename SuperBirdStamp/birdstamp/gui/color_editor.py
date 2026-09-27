"""统一选色入口；命名调色板存入用户配置，所有颜色编辑器共享。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PIL import ImageColor
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QToolButton, QVBoxLayout, QWidget,
)

from birdstamp.config import get_config_path
from . import editor_options, editor_utils


def normalized_color(value, *, allow_none=False):
    text = str(value or '').strip()
    if allow_none and text.lower() in {'none', 'transparent'}:
        return 'none'
    try:
        rgb = ImageColor.getrgb(text)
        return '#%02X%02X%02X' % rgb[:3] + ('%02X' % rgb[3] if len(rgb) == 4 and rgb[3] != 255 else '')
    except (ValueError, TypeError):
        return None


def qt_color(value):
    return QColor(*ImageColor.getcolor(value if value != 'none' else '#FFFFFF', 'RGBA'))


def color_text(color):
    red, green, blue, alpha = color.getRgb()
    return '#%02X%02X%02X' % (red, green, blue) + ('%02X' % alpha if alpha != 255 else '')


class PaletteStore:
    """有限的命名色板；同目录原子替换，避免关闭时留下半份配置。"""

    def __init__(self, path: Path | None = None):
        self.path = path or get_config_path().parent / 'color_palettes.json'

    def read(self):
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            palettes = raw.get('palettes', {})
            if not isinstance(palettes, dict):
                return {}
            return {str(name)[:80]: [color for value in values[:16]
                                     if (color := normalized_color(value))]
                    for name, values in list(palettes.items())[:32] if isinstance(values, list)}
        except (OSError, ValueError, AttributeError):
            return {}

    def save(self, name, colors):
        palettes = self.read()
        name = str(name).strip()[:80] or '我的调色板'
        if name not in palettes and len(palettes) >= 32:
            raise ValueError('最多保存 32 个调色板。')
        palettes[name] = [color for value in colors[:16] if (color := normalized_color(value))]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent,
                                             suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'palettes': palettes}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def color_tool_button(kind, tooltip, parent=None):
    """用矢量笔画生成图标，不依赖平台字体中的 emoji 字形。"""
    button = QToolButton(parent)
    button.setAutoRaise(True)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setIconSize(QSize(20, 20))
    pixmap = QPixmap(40, 40)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(button.palette().windowText().color(), 1.5))
    if kind == 'picker':
        painter.drawLine(5, 15, 15, 5)
        painter.drawLine(3, 17, 7, 16)
        painter.drawLine(4, 13, 7, 16)
        painter.drawLine(12, 3, 17, 8)
    elif kind == 'save':
        painter.drawRoundedRect(3, 3, 14, 14, 1, 1)
        painter.drawRect(6, 3, 8, 5)
        painter.drawRect(6, 11, 8, 6)
    else:
        painter.drawEllipse(2, 2, 16, 16)
        for x, y, color in [(6, 6, '#E56A54'), (12, 6, '#EAC75C'), (6, 12, '#58B9A1')]:
            painter.setBrush(QColor(color))
            painter.drawEllipse(x-2, y-2, 4, 4)
    painter.end()
    button.setIcon(QIcon(pixmap))
    return button


class AdvancedColorDialog(QDialog):
    def __init__(self, value, parent=None, *, store=None, allow_alpha=False):
        super().__init__(parent)
        self.setWindowTitle('高级颜色编辑')
        self.store = store or PaletteStore()
        layout = QVBoxLayout(self)
        self.picker = QColorDialog(qt_color(value), self)
        options = QColorDialog.ColorDialogOption.DontUseNativeDialog | QColorDialog.ColorDialogOption.NoButtons
        if allow_alpha:
            options |= QColorDialog.ColorDialogOption.ShowAlphaChannel
        # Qt 6.6+ 隐藏自带文本吸管，所有入口统一使用下面的图标按钮。
        options |= getattr(QColorDialog.ColorDialogOption, 'NoEyeDropperButton', QColorDialog.ColorDialogOption(0))
        self.picker.setOptions(options)
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        layout.addWidget(self.picker)
        row = QHBoxLayout()
        row.addWidget(QLabel('调色板'))
        self.palette_names = QComboBox()
        self.palette_names.setEditable(True)
        self.palette_names.lineEdit().setMaxLength(80)
        self.palette_names.addItems(list(self.store.read()) or ['我的调色板'])
        self.palette_names.textActivated.connect(self.load_palette)
        row.addWidget(self.palette_names, 1)
        save = color_tool_button('save', '保存当前自定义颜色为命名调色板', self)
        save.clicked.connect(self.save_palette)
        row.addWidget(save)
        picker = color_tool_button('picker', '从屏幕吸取颜色（Esc 取消）', self)
        picker.clicked.connect(lambda: editor_utils.start_screen_color_picker(
            parent=self, on_picked=lambda color: self.picker.setCurrentColor(QColor(color))))
        row.addWidget(picker)
        layout.addLayout(row)
        hint = QLabel('将颜色添加到上方“自定义颜色”，输入调色板名称后点击保存图标。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_palette(self.palette_names.currentText())

    def load_palette(self, name):
        colors = self.store.read().get(name, [])
        for index in range(QColorDialog.customCount()):
            QColorDialog.setCustomColor(index, qt_color(colors[index] if index < len(colors) else '#FFFFFF'))

    def save_palette(self):
        try:
            self.store.save(self.palette_names.currentText(),
                            [color_text(QColorDialog.customColor(i)) for i in range(QColorDialog.customCount())])
            name = self.palette_names.currentText().strip() or '我的调色板'
            if self.palette_names.findText(name) < 0:
                self.palette_names.addItem(name)
            self.palette_names.setCurrentText(name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '调色板未保存', str(exc))


class ColorEditor(QWidget):
    colorChanged = pyqtSignal(str)

    def __init__(self, value='#FFFFFF', parent=None, *, allow_none=False, allow_alpha=False):
        super().__init__(parent)
        self.allow_none = allow_none
        self.allow_alpha = allow_alpha
        self._value = normalized_color(value, allow_none=allow_none) or '#FFFFFF'
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.combo = QComboBox()
        if allow_none:
            self.combo.addItem('无（透明）', 'none')
        for label, color in editor_options.COLOR_PRESETS:
            self.combo.addItem(label, color)
        self.combo.addItem('自定义', 'custom')
        self.combo.currentIndexChanged.connect(self._preset_changed)
        row.addWidget(self.combo)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText('#RRGGBB')
        self.edit.setMinimumWidth(75)
        self.edit.setMaximumWidth(125)
        self.edit.textChanged.connect(self._text_changed)
        self.edit.editingFinished.connect(lambda: self.set_value(self._value))
        row.addWidget(self.edit, 1)
        self.swatch = QToolButton()
        self.swatch.setFixedSize(30, 24)
        self.swatch.setAccessibleName('颜色预览，点击打开高级颜色编辑')
        self.swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.swatch.clicked.connect(self.open_dialog)
        row.addWidget(self.swatch)
        self.palette_button = color_tool_button('palette', '高级颜色编辑 / 保存调色板', self)
        self.palette_button.clicked.connect(self.open_dialog)
        row.addWidget(self.palette_button)
        self.picker_button = color_tool_button('picker', '从屏幕吸取颜色（Esc 取消）', self)
        self.picker_button.clicked.connect(self.pick_screen)
        row.addWidget(self.picker_button)
        self.set_value(self._value)

    def value(self):
        return self._value

    def set_value(self, value, *, emit=False):
        color = normalized_color(value, allow_none=self.allow_none)
        if color is None:
            return
        if not self.allow_alpha and len(color) == 9:
            color = color[:7]
        changed = color != self._value
        self._value = color
        blocked = [(widget, widget.blockSignals(True)) for widget in (self.combo, self.edit)]
        self.edit.setText(color)
        self._sync_preset()
        for widget, previous in blocked:
            widget.blockSignals(previous)
        self._refresh_swatch()
        if changed and emit:
            self.colorChanged.emit(color)

    def _sync_preset(self):
        index = next((i for i in range(self.combo.count())
                      if str(self.combo.itemData(i)).lower() == self._value.lower()), self.combo.findData('custom'))
        blocked = self.combo.blockSignals(True)
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(blocked)

    def _refresh_swatch(self):
        color = self._value
        if color == 'none':
            editor_utils.set_color_preview_swatch(self.swatch, color, allow_none=True)
        else:
            red, green, blue, alpha = qt_color(color).getRgb()
            self.swatch.setText("")
            self.swatch.setStyleSheet(f"background: rgba({red}, {green}, {blue}, {alpha}); "
                                     "border: 1px solid palette(mid); border-radius: 3px;")
        self.swatch.setToolTip(f'{color} · 点击打开高级颜色编辑')

    def _preset_changed(self):
        self.set_value(self.combo.currentData(), emit=True)

    def _text_changed(self, value):
        color = normalized_color(value, allow_none=self.allow_none)
        if color is None:
            return
        if not self.allow_alpha and len(color) == 9:
            color = color[:7]
        changed = color != self._value
        self._value = color
        self._sync_preset()
        self._refresh_swatch()
        # 输入中不重写文本或光标，允许逐字输入 #RGB / #RRGGBB / #RRGGBBAA。
        if changed:
            self.colorChanged.emit(color)

    def open_dialog(self):
        dialog = AdvancedColorDialog(self._value, self, allow_alpha=self.allow_alpha)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.set_value(color_text(dialog.picker.currentColor()), emit=True)
        finally:
            dialog.deleteLater()

    def pick_screen(self):
        editor_utils.start_screen_color_picker(parent=self, on_picked=lambda color: self.set_value(color, emit=True))
