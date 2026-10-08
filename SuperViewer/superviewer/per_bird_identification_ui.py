# -*- coding: utf-8 -*-
"""逐只识别设置和已保存结果展示；鸟框只通过缓存数据触发绘制。"""
from __future__ import annotations

import os

from .bird_result_list import TraceBirdList
from .bird_identification import BirdIDOptions
from .per_bird_identification import PerBirdOptions, read_individuals
from .qt_compat import QLabel, QVBoxLayout, QWidget, pyqtSignal
try:
    from PyQt6.QtCore import QObject
    from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                                QFormLayout, QLineEdit, QSpinBox)
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject
    from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                                QFormLayout, QLineEdit, QSpinBox)


class PerBirdSettingsDialog(QDialog):
    def __init__(self, parent, service, per_bird, params):
        super().__init__(parent)
        self.setWindowTitle("逐只识别")
        layout = QVBoxLayout(self)
        label = QLabel("复用当前清晰度检测参数，逐只裁图后交给 SuperPicky 识鸟。\n"
                       "宽或高低于设置值的鸟框不送识别；尺寸按所选图像来源的原尺寸计算。\n"
                       "裁图关闭服务端二次 YOLO 和 GPS 过滤；鸟种及区域保存到独立 XMP 列表。", self)
        label.setWordWrap(True)
        layout.addWidget(label)
        from .bird_sharpness_params_form import params_summary
        summary = QLabel(params_summary({**params, "max_birds": 0, "min_bird_side": 0}) + "；逐只模式不限制鸟数。", self)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        form = QFormLayout()
        self.url = QLineEdit(service.url, self)
        self.width, self.height = QSpinBox(self), QSpinBox(self)
        for widget, value in ((self.width, per_bird.min_width), (self.height, per_bird.min_height)):
            widget.setRange(1, 16384)
            widget.setSuffix(" px")
            widget.setValue(value)
        self.padding = QDoubleSpinBox(self)
        self.padding.setRange(0, 50)
        self.padding.setSuffix(" %")
        self.padding.setValue(per_bird.padding_percent)
        self.padding.setToolTip("每边按鸟框宽/高外扩；保存和高亮仍使用未外扩的鸟框。")
        self.threshold = QDoubleSpinBox(self)
        self.threshold.setRange(0, 100)
        self.threshold.setSuffix(" %")
        self.threshold.setValue(service.threshold)
        self.skip = QCheckBox("跳过已有逐只识别记录的照片", self)
        self.skip.setChecked(service.skip_existing)
        for name, widget in (("本机服务地址", self.url), ("鸟框最小宽度", self.width),
                             ("鸟框最小高度", self.height), ("裁图每边外扩", self.padding),
                             ("鸟种确认阈值", self.threshold)):
            form.addRow(name, widget)
        form.addRow(self.skip)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始逐只识别")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self):
        return BirdIDOptions(self.url.text().strip(), self.threshold.value(), self.skip.isChecked())

    def per_bird_options(self):
        return PerBirdOptions(self.width.value(), self.height.value(), self.padding.value())


class IndividualBirdsPanel(QWidget):
    hovered = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ""
        self._items = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.empty = QLabel("暂无逐只识别结果（照片右键 → 逐只识别…）", self)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.birds = TraceBirdList(self)
        layout.addWidget(self.birds)
        layout.addStretch(1)
        self.birds.hovered.connect(lambda row: self.hovered.emit(self.path, row))

    def set_metadata(self, path, metadata):
        items = read_individuals(metadata)
        if self.path == path and items == self._items:
            return
        self.birds.set_rows([])  # 先清除旧照片的悬停状态。
        self.path, self._items = path, items
        rows = []
        if items:
            from bird_sharpness.trace import BIRD_COLORS, TraceBirdRow, hex_color
            for item in items:
                n = item["index"]
                x0, y0, x1, y1 = item["box_px"]
                name = item["cn_name"] or item["en_name"] or "未识别"
                score = item.get("confidence")
                status = {"candidate": " · 待确定", "failed": " · 失败", "skipped": " · 已过滤"}.get(item.get("status"), "")
                value = f"{name}" + (f" {score:.1f}%" if score is not None else "") + status
                value += f"\n检测置信度 {item['detection_confidence']:.2f}，框 {x1-x0:g} × {y1-y0:g} px"
                if item.get("message"):
                    value += "\n" + item["message"]
                rows.append(TraceBirdRow(f"鸟 #{n + 1}", value, hex_color(BIRD_COLORS[n % len(BIRD_COLORS)]),
                                         tuple(item["box"]), n))
        self.birds.set_rows(rows)
        self.empty.setVisible(not rows)


class IndividualBirdHover(QObject):
    """A/B 分别校验源图身份；切图、播放、隐藏列表时撤销，异步升级后重映射。"""
    def __init__(self, info, panels, parent=None):
        super().__init__(parent)
        self.panels = panels
        self.source, self.row = "", None
        info.individual_birds.hovered.connect(self.highlight)
        for panel in panels:
            panel.source_changed.connect(self.clear)
            panel.full_preview_ready.connect(self.refresh)

    def clear(self, *_):
        self.highlight("", None)

    def highlight(self, path, row):
        self.source = path if row else ""
        # 只使用视口已经解析的源图身份，不在鼠标事件里扫描路径或访问元数据。
        for panel in self.panels:
            if path and os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(panel.current_path() or "")):
                self.source = panel.source_identity_path() if row else ""
                break
        self.row = row
        self.refresh()

    def refresh(self, *_):
        key = lambda p: os.path.normcase(os.path.abspath(p)) if p else ""
        for panel in self.panels:
            valid = self.row is not None and key(panel.source_identity_path()) == key(self.source)
            valid = valid and not panel._navigation_playback_active
            panel.set_individual_bird_highlight(self.row.box if valid else None,
                                              self.row.color if valid else "#00c8ff")
