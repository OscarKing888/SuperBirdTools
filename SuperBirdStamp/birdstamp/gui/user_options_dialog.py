"""BirdStamp 用户选项：按命名组管理横竖屏安全区，确定后才写入。"""
from copy import deepcopy
import uuid

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from birdstamp.overlays.safe_area_options import current_options, default_options, save_options


class SafeAreaPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.margins = None
        self.setMinimumSize(250, 180)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            for index, orientation in enumerate(('portrait', 'landscape')):
                region = QRectF(index*self.width()/2, 0, self.width()/2, self.height())
                width, height = (70., 124.) if index == 0 else (144., 81.)
                frame = QRectF(region.center().x()-width/2, region.center().y()-height/2-8, width, height)
                painter.fillRect(frame, QColor('#5D646C'))
                if self.margins:
                    left, top, right, bottom = self.margins[orientation]
                    safe = QRectF(frame.left()+left*width, frame.top()+top*height,
                                  max(0., 1-left-right)*width, max(0., 1-top-bottom)*height)
                    painter.fillRect(safe, QColor('#D9EDE6'))
                    painter.setPen(QPen(QColor('#248E72'), 2, Qt.PenStyle.DashLine))
                    painter.drawRect(safe)
                painter.setPen(self.palette().text().color())
                painter.drawText(QRectF(region.left(), self.height()-24, region.width(), 22),
                                 Qt.AlignmentFlag.AlignCenter, '竖屏' if index == 0 else '横屏')
        finally:
            painter.end()


class UserOptionsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('用户选项')
        self.resize(760, 520)
        self.options = deepcopy(current_options())
        self._loading = False
        self.current_id = None
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        page = QWidget()
        tabs.addTab(page, '安全区')
        page_layout = QVBoxLayout(page)
        hint = QLabel('每组分别设置横屏、竖屏的遮挡边距，单位为画幅百分比。0% 表示该边不预留。\n'
                      '模板裁切中的安全框列表随组配置更新；布局避让保持原有字号和图像大小。')
        hint.setWordWrap(True)
        page_layout.addWidget(hint)
        body = QHBoxLayout()
        page_layout.addLayout(body)
        left = QVBoxLayout()
        body.addLayout(left, 1)
        self.groups = QListWidget()
        self.groups.setAccessibleName('安全区组')
        left.addWidget(self.groups)
        actions = QHBoxLayout()
        left.addLayout(actions)
        for label, method in (('新增', self.add_group), ('复制', self.copy_group), ('删除', self.remove_group)):
            button = QPushButton(label)
            button.clicked.connect(method)
            actions.addWidget(button)
        reset = QPushButton('恢复内置组')
        reset.setToolTip('恢复内置组名和边距，点击确定后保存；取消可放弃本次修改。')
        reset.clicked.connect(self.reset_groups)
        left.addWidget(reset)
        self.form_widget = QWidget()
        body.addWidget(self.form_widget, 2)
        right = QVBoxLayout(self.form_widget)
        form = QFormLayout()
        right.addLayout(form)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(80)
        self.name_edit.setAccessibleName('安全区组名')
        form.addRow('组名', self.name_edit)
        grid = QGridLayout()
        right.addLayout(grid)
        for column, name in enumerate(('左', '上', '右', '下'), start=1):
            grid.addWidget(QLabel(name), 0, column)
        self.margin_spins = {}
        for row, (orientation, label) in enumerate((('portrait', '竖屏'), ('landscape', '横屏')), start=1):
            grid.addWidget(QLabel(label), row, 0)
            spins = []
            for column, side in enumerate(('左', '上', '右', '下'), start=1):
                spin = QDoubleSpinBox()
                spin.setRange(0, 99.9)
                spin.setDecimals(1)
                spin.setSingleStep(.5)
                spin.setSuffix('%')
                spin.setAccessibleName(f'{label}{side}边距')
                spin.valueChanged.connect(self._edit)
                grid.addWidget(spin, row, column)
                spins.append(spin)
            self.margin_spins[orientation] = spins
        self.preview = SafeAreaPreview()
        right.addWidget(self.preview, 1)
        note = QLabel('左右之和、上下之和均须小于 100%。\n绿色为可用安全区，灰色为预留遮挡区。')
        note.setWordWrap(True)
        right.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确定')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.name_edit.textChanged.connect(self._edit)
        self.groups.currentRowChanged.connect(self._load_row)
        self._refresh_groups()

    def _refresh_groups(self, selected=None):
        self.groups.blockSignals(True)
        self.groups.clear()
        target = 0
        for key, name in self.options['labels'].items():
            if key == 'off':
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.groups.addItem(item)
            if key == selected:
                target = self.groups.count()-1
        self.groups.blockSignals(False)
        self.groups.setCurrentRow(target if self.groups.count() else -1)
        self._load_row(self.groups.currentRow())

    def _load_row(self, row):
        item = self.groups.item(row)
        self.current_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.form_widget.setEnabled(item is not None)
        self._loading = True
        try:
            self.name_edit.setText(self.options['labels'].get(self.current_id, ''))
            values = self.options['presets'].get(self.current_id)
            for orientation, spins in self.margin_spins.items():
                for spin, margin in zip(spins, values[orientation] if values else (0, 0, 0, 0)):
                    spin.setValue(margin*100)
            self.preview.margins = values
            self.preview.update()
        finally:
            self._loading = False

    def _edit(self, *_):
        if self._loading or self.current_id is None:
            return
        self.options['labels'][self.current_id] = self.name_edit.text()
        self.groups.currentItem().setText(self.name_edit.text().strip() or '（未命名）')
        values = {orientation: tuple(spin.value()/100 for spin in spins)
                  for orientation, spins in self.margin_spins.items()}
        self.options['presets'][self.current_id] = values
        self.preview.margins = values
        self.preview.update()

    def _append_group(self, name, margins):
        used = set(self.options['labels'].values())
        base, number = name, 2
        while name in used:
            name = f'{base} {number}'
            number += 1
        key = 'group-'+uuid.uuid4().hex
        self.options['labels'][key] = name
        self.options['presets'][key] = deepcopy(margins)
        self._refresh_groups(key)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def add_group(self):
        self._append_group('新安全区', dict(portrait=(0, 0, 0, 0), landscape=(0, 0, 0, 0)))

    def copy_group(self):
        if self.current_id:
            self._append_group(self.options['labels'][self.current_id]+' 副本',
                               self.options['presets'][self.current_id])

    def remove_group(self):
        if self.current_id:
            del self.options['labels'][self.current_id]
            del self.options['presets'][self.current_id]
            self._refresh_groups()

    def reset_groups(self):
        self.options = default_options()
        self._refresh_groups()

    def accept(self):
        try:
            save_options(self.options)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '保存失败', str(exc))
            return
        super().accept()
