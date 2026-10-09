"""BirdStamp 用户选项：按命名组管理横竖屏安全区，确定后才写入。"""
from copy import deepcopy
from pathlib import Path
import uuid

from app_common.collapsible_section import CollapsibleSection

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QDoubleSpinBox, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSizePolicy, QStyle, QVBoxLayout, QWidget,
)

from app_common.settings_dialog import SettingsDialog

from birdstamp.overlays.safe_area_options import current_options, default_options, save_options
from .safe_area_reference import ReferenceImageLoader


class SafeAreaPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.margins = None
        self.images = {}
        self.show_reference = True
        self.setMinimumWidth(280)
        self.setFixedHeight(172)

    def set_reference_image(self, orientation, image):
        self.images[orientation] = image
        self.setFixedHeight(360 if any(not image.isNull() for image in self.images.values()) else 172)
        self.update()

    def frame_rect(self, index):
        orientation = ('portrait', 'landscape')[index]
        region = QRectF(index*self.width()/2, 0, self.width()/2, self.height())
        image = self.images.get(orientation, QImage())
        if not image.isNull():
            width, height = image.width(), image.height()
            scale = min((region.width()-24)/width, (region.height()-40)/height)
        else:
            width, height = (72., 128.) if index == 0 else (144., 81.)
            scale = min(1., (region.width()-24)/width)
        width, height = width*scale, height*scale
        return QRectF(region.center().x()-width/2, 30+(region.height()-40-height)/2, width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        try:
            for index, orientation in enumerate(('portrait', 'landscape')):
                region = QRectF(index*self.width()/2, 0, self.width()/2, self.height())
                frame = self.frame_rect(index)
                image = self.images.get(orientation, QImage())
                has_image = self.show_reference and not image.isNull()
                painter.fillRect(frame, QColor('#5D646C'))
                if has_image:
                    painter.drawImage(frame, image)
                if self.margins:
                    left, top, right, bottom = self.margins[orientation]
                    safe = QRectF(frame.left()+left*frame.width(), frame.top()+top*frame.height(),
                                  max(0., 1-left-right)*frame.width(), max(0., 1-top-bottom)*frame.height())
                    painter.save()
                    painter.setClipRect(frame)
                    if has_image:
                        mask = QPainterPath()
                        mask.setFillRule(Qt.FillRule.OddEvenFill)
                        mask.addRect(frame)
                        mask.addRect(safe)
                        painter.fillPath(mask, QColor(0, 0, 0, 125))
                    else:
                        painter.fillRect(safe, QColor('#D9EDE6'))
                    painter.setPen(QPen(QColor('#38CBA3'), 2, Qt.PenStyle.DashLine))
                    painter.drawRect(safe)
                    painter.restore()
                painter.setPen(self.palette().text().color())
                painter.drawText(QRectF(region.left(), 2, region.width(), 22),
                                 Qt.AlignmentFlag.AlignCenter, '竖屏' if index == 0 else '横屏')
        finally:
            painter.end()


class UserOptionsDialog(SettingsDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.options = deepcopy(current_options())
        self.reference_loader = ReferenceImageLoader(self)
        self.reference_loader.ready.connect(self._reference_ready)
        self.destroyed.connect(self.reference_loader.close)
        self._loading = False
        self.current_id = None
        self.safe_area_page = self._build_safe_area_page()
        self.safe_area_scroll = self.add_page(
            self.safe_area_page, '安全区',
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.name_edit.textChanged.connect(self._edit)
        self.groups.currentRowChanged.connect(self._load_row)
        self._refresh_groups()

    def _build_safe_area_page(self):
        """安全区控件集中在独立页面，后续选项通过 add_page 追加。"""
        page = QWidget(self.tabs)
        page_layout = QVBoxLayout(page)
        title = QLabel('安全区设置')
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        page_layout.addWidget(title)
        hint = QLabel('选择或新增一组安全区，分别设置横屏、竖屏的遮挡边距。')
        hint.setWordWrap(True)
        page_layout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(16)
        page_layout.addLayout(body)
        group_box = CollapsibleSection('安全区组', page)
        group_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        left = QVBoxLayout(group_box.body)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        self.groups = QListWidget()
        self.groups.setAccessibleName('安全区组')
        self.groups.setMinimumWidth(150)
        self.groups.setMaximumWidth(220)
        self.groups.setFixedHeight(220)
        left.addWidget(self.groups)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        left.addLayout(actions)
        for label, method in (('新增', self.add_group), ('复制', self.copy_group), ('删除', self.remove_group)):
            button = QPushButton(label)
            button.clicked.connect(method)
            actions.addWidget(button)
        reset = QPushButton('恢复内置组')
        reset.setToolTip('恢复内置组名和边距，点击确定后保存；取消可放弃本次修改。')
        reset.clicked.connect(self.reset_groups)
        left.addWidget(reset)
        body.addWidget(group_box, 0, Qt.AlignmentFlag.AlignTop)

        self.form_widget = QWidget()
        body.addWidget(self.form_widget, 1, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout(self.form_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        right.addLayout(form)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(80)
        self.name_edit.setAccessibleName('安全区组名')
        form.addRow('组名', self.name_edit)

        margins_box = CollapsibleSection('遮挡边距（画幅百分比）')
        margins_layout = QVBoxLayout(margins_box.body)
        margins_layout.setContentsMargins(0, 0, 0, 0)
        margins_layout.setSpacing(10)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        margins_layout.addLayout(grid)
        for column, name in enumerate(('左', '上', '右', '下'), start=1):
            grid.addWidget(QLabel(name), 0, column, Qt.AlignmentFlag.AlignHCenter)
            grid.setColumnStretch(column, 1)
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
                spin.setMinimumWidth(spin.sizeHint().width())
                spin.setAccessibleName(f'{label}{side}边距')
                spin.valueChanged.connect(self._edit)
                grid.addWidget(spin, row, column)
                spins.append(spin)
            self.margin_spins[orientation] = spins
        note = QLabel('0% 表示不预留；左右之和、上下之和均须小于 100%。')
        note.setWordWrap(True)
        margins_layout.addWidget(note)
        right.addWidget(margins_box)

        preview_box = CollapsibleSection('安全区预览')
        preview_layout = QVBoxLayout(preview_box.body)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(4)
        self.preview = SafeAreaPreview()
        preview_layout.addWidget(self.preview)
        self.reference_path_edits = {}
        self.reference_status = {}
        for orientation, title in (('portrait', '竖屏'), ('landscape', '横屏')):
            row = QHBoxLayout()
            row.addWidget(QLabel(title))
            edit = QLineEdit()
            edit.setReadOnly(True)
            edit.setPlaceholderText(f'{title}参考图（可选）')
            edit.setAccessibleName(f'{title}参考图路径')
            self.reference_path_edits[orientation] = edit
            row.addWidget(edit, 1)
            choose = QPushButton('选图…')
            choose.setAccessibleName(f'选择{title}参考图')
            choose.clicked.connect(lambda checked=False, o=orientation: self._choose_reference(o))
            row.addWidget(choose)
            clear = QPushButton('清除')
            clear.setAccessibleName(f'清除{title}参考图')
            clear.clicked.connect(lambda checked=False, o=orientation: self._set_reference(o, ''))
            row.addWidget(clear)
            preview_layout.addLayout(row)
            status = QLabel()
            status.setWordWrap(True)
            status.setVisible(False)
            self.reference_status[orientation] = status
            preview_layout.addWidget(status)
        self.reference_visible_check = QCheckBox('显示参考图')
        self.reference_visible_check.setChecked(True)
        self.reference_visible_check.toggled.connect(self._toggle_reference)
        preview_layout.addWidget(self.reference_visible_check)
        legend = QLabel('虚线内为安全区；参考图完整显示，框外变暗。确定后记住各组参考图路径。')
        legend.setWordWrap(True)
        legend.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(legend)
        right.addWidget(preview_box)
        return page

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
            self._refresh_references()
        finally:
            self._loading = False

    def _refresh_references(self, *, force=None):
        paths = self.options.get('reference_images', {}).get(self.current_id, {})
        for orientation, edit in self.reference_path_edits.items():
            path = paths.get(orientation, '')
            edit.setText(path)
            edit.setToolTip(path)
        self.reference_loader.set_paths(paths, force=force)

    def _choose_reference(self, orientation):
        from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
        previous = self.options.get('reference_images', {}).get(self.current_id, {}).get(orientation, '')
        extensions = ' '.join('*'+ext for ext in sorted(SUPPORTED_IMAGE_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(self, '选择安全区参考图', previous,
                                             f'图像 ({extensions});;所有文件 (*)')
        if path:
            self._set_reference(orientation, str(Path(path).expanduser().resolve()))

    def _set_reference(self, orientation, path):
        if self.current_id is None:
            return
        paths = self.options.setdefault('reference_images', {}).setdefault(self.current_id, {})
        if path:
            paths[orientation] = path
        else:
            paths.pop(orientation, None)
        self._refresh_references(force=orientation)

    def _reference_ready(self, orientation, path, image, message):
        self.preview.set_reference_image(orientation, image)
        self.reference_status[orientation].setText(message)
        self.reference_status[orientation].setVisible(bool(message))

    def _toggle_reference(self, checked):
        self.preview.show_reference = checked
        self.preview.update()

    def done(self, result):
        self.reference_loader.close()
        super().done(result)

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
            references = deepcopy(self.options.get('reference_images', {}).get(self.current_id, {}))
            self._append_group(self.options['labels'][self.current_id]+' 副本',
                               self.options['presets'][self.current_id])
            if references:
                self.options.setdefault('reference_images', {})[self.current_id] = references
                self._refresh_references()

    def remove_group(self):
        if self.current_id:
            del self.options['labels'][self.current_id]
            del self.options['presets'][self.current_id]
            self.options.get('reference_images', {}).pop(self.current_id, None)
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
