# -*- coding: utf-8 -*-
"""配置 EXIF 标签优先显示顺序与禁止显示列表的对话框。"""

from __future__ import annotations

from .exif_helpers import (
    get_all_exif_tag_keys,
    load_tag_priority_from_settings,
    save_exif_tag_hidden_to_settings,
    save_tag_priority_to_settings,
)
from .paths_settings import _load_settings
from .qt_compat import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    _UserRole,
)


class ExifTagOrderDialog(QDialog):
    """配置 EXIF 标签优先显示顺序与禁止显示列表的对话框。"""

    def __init__(self, parent=None, use_chinese: bool = False):
        super().__init__(parent)
        self.setWindowTitle("EXIF 显示顺序")
        self.setMinimumSize(480, 420)
        self._all_tags = get_all_exif_tag_keys(use_chinese=use_chinese)
        self._priority_keys = []
        self._hidden_keys = []

        tabs = QTabWidget()
        order_w = QWidget()
        order_layout = QVBoxLayout(order_w)
        order_layout.addWidget(QLabel("以下标签将优先显示在 EXIF 列表顶部，按顺序排列："))
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        order_layout.addWidget(self.list_widget)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_up = QPushButton("上移")
        self.btn_up.clicked.connect(self._move_up)
        self.btn_down = QPushButton("下移")
        self.btn_down.clicked.connect(self._move_down)
        self.btn_remove = QPushButton("删除")
        self.btn_remove.clicked.connect(self._remove)
        self.btn_add = QPushButton("添加…")
        self.btn_add.clicked.connect(self._add_tag)
        btn_layout.addWidget(self.btn_up)
        btn_layout.addWidget(self.btn_down)
        btn_layout.addWidget(self.btn_remove)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addStretch()
        order_layout.addLayout(btn_layout)
        tabs.addTab(order_w, "显示顺序")

        hidden_w = QWidget()
        hidden_layout = QVBoxLayout(hidden_w)
        hidden_layout.addWidget(QLabel("以下标签将不在 EXIF 列表中显示（格式如 0th:279）："))
        self.hidden_list_widget = QListWidget()
        self.hidden_list_widget.setAlternatingRowColors(True)
        hidden_layout.addWidget(self.hidden_list_widget)
        hidden_btn = QHBoxLayout()
        hidden_btn.addStretch()
        self.btn_hidden_add = QPushButton("添加…")
        self.btn_hidden_add.clicked.connect(self._add_hidden_tag)
        self.btn_hidden_remove = QPushButton("删除")
        self.btn_hidden_remove.clicked.connect(self._remove_hidden)
        hidden_btn.addWidget(self.btn_hidden_add)
        hidden_btn.addWidget(self.btn_hidden_remove)
        hidden_btn.addStretch()
        hidden_layout.addLayout(hidden_btn)
        tabs.addTab(hidden_w, "禁止显示")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)
        self._load_from_settings()

    def _load_from_settings(self):
        self._priority_keys = load_tag_priority_from_settings()
        data = _load_settings()
        val = data.get("exif_tag_hidden", [])
        lst = val if isinstance(val, list) else []
        self._hidden_keys = [str(k).strip() for k in lst if isinstance(k, str) and k.strip()]
        self._refresh_list()
        self._refresh_hidden_list()

    def _refresh_list(self):
        key_to_text = {k: t for k, t in self._all_tags}
        self.list_widget.clear()
        for key in self._priority_keys:
            text = key_to_text.get(key, key)
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            self.list_widget.addItem(item)

    def _move_up(self):
        row = self.list_widget.currentRow()
        if row <= 0:
            return
        keys = self._priority_keys
        keys[row], keys[row - 1] = keys[row - 1], keys[row]
        self._priority_keys = keys
        self._refresh_list()
        self.list_widget.setCurrentRow(row - 1)

    def _move_down(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._priority_keys) - 1:
            return
        keys = self._priority_keys
        keys[row], keys[row + 1] = keys[row + 1], keys[row]
        self._priority_keys = keys
        self._refresh_list()
        self.list_widget.setCurrentRow(row + 1)

    def _remove(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self._priority_keys.pop(row)
        self._refresh_list()
        if self.list_widget.count():
            self.list_widget.setCurrentRow(min(row, self.list_widget.count() - 1))

    def _add_tag(self):
        d = QDialog(self)
        d.setWindowTitle("选择要优先显示的标签")
        d.setMinimumSize(400, 350)
        layout = QVBoxLayout(d)
        layout.addWidget(QLabel("搜索："))
        search = QLineEdit()
        search.setPlaceholderText("输入分组或标签名过滤…")
        layout.addWidget(search)
        all_list = QListWidget()
        existing = set(self._priority_keys)
        for key, text in self._all_tags:
            if key in existing:
                continue
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            all_list.addItem(item)
        layout.addWidget(all_list)

        def _filter_list(text):
            t = str(text or "").strip().lower()
            for i in range(all_list.count()):
                it = all_list.item(i)
                it.setHidden(bool(t) and t not in it.text().lower())

        search.textChanged.connect(_filter_list)
        chosen_key = [None]

        def on_accept():
            cur = all_list.currentItem()
            if cur is not None:
                chosen_key[0] = cur.data(_UserRole)
            d.accept()

        all_list.doubleClicked.connect(on_accept)
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bbox.accepted.connect(on_accept)
        bbox.rejected.connect(d.reject)
        layout.addWidget(bbox)
        if d.exec():
            key = chosen_key[0]
            if key and key not in self._priority_keys:
                self._priority_keys.append(key)
                self._refresh_list()

    def _refresh_hidden_list(self):
        self.hidden_list_widget.clear()
        key_to_text = {k: t for k, t in self._all_tags}
        for key in self._hidden_keys:
            text = key_to_text.get(key, key)
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            self.hidden_list_widget.addItem(item)

    def _add_hidden_tag(self):
        d = QDialog(self)
        d.setWindowTitle("选择要禁止显示的标签")
        d.setMinimumSize(400, 350)
        layout = QVBoxLayout(d)
        layout.addWidget(QLabel("搜索："))
        search = QLineEdit()
        search.setPlaceholderText("输入分组或标签名过滤…")
        layout.addWidget(search)
        all_list = QListWidget()
        existing = set(self._hidden_keys)
        for key, text in self._all_tags:
            if key in existing:
                continue
            item = QListWidgetItem(text)
            item.setData(_UserRole, key)
            all_list.addItem(item)
        layout.addWidget(all_list)

        def _filter_list(text):
            t = str(text or "").strip().lower()
            for i in range(all_list.count()):
                it = all_list.item(i)
                it.setHidden(bool(t) and t not in it.text().lower())

        search.textChanged.connect(_filter_list)
        chosen_key = [None]

        def on_accept():
            cur = all_list.currentItem()
            if cur is not None:
                chosen_key[0] = cur.data(_UserRole)
            d.accept()

        all_list.doubleClicked.connect(on_accept)
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            if hasattr(QDialogButtonBox.StandardButton, "Ok")
            else QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        bbox.accepted.connect(on_accept)
        bbox.rejected.connect(d.reject)
        layout.addWidget(bbox)
        if d.exec():
            key = chosen_key[0]
            if key and key not in self._hidden_keys:
                self._hidden_keys.append(key)
                self._refresh_hidden_list()

    def _remove_hidden(self):
        row = self.hidden_list_widget.currentRow()
        if row < 0:
            return
        self._hidden_keys.pop(row)
        self._refresh_hidden_list()
        if self.hidden_list_widget.count():
            self.hidden_list_widget.setCurrentRow(min(row, self.hidden_list_widget.count() - 1))

    def get_priority_keys(self):
        return list(self._priority_keys)

    def accept(self):
        save_tag_priority_to_settings(self._priority_keys)
        save_exif_tag_hidden_to_settings(self._hidden_keys)
        super().accept()
