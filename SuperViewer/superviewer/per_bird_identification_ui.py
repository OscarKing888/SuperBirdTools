# -*- coding: utf-8 -*-
"""逐只识别设置和已保存结果展示；鸟框只通过缓存数据触发绘制。"""
from __future__ import annotations

import os

from .bird_result_list import TraceBirdList
from .bird_identification import BirdIDOptions
from .per_bird_identification import PerBirdOptions, read_individuals, individual_species_metadata
from .rarity_badge import RarityBadge, ConservationBadge, MetadataBadge
from .qt_compat import QLabel, QVBoxLayout, QHBoxLayout, QWidget, pyqtSignal
try:
    from PyQt6.QtCore import QObject, Qt
    from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                                QFormLayout, QLineEdit, QSpinBox, QScrollArea, QTabWidget)
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, Qt
    from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                                QFormLayout, QLineEdit, QSpinBox, QScrollArea, QTabWidget)


class PerBirdSettingsDialog(QDialog):
    def __init__(self, parent, service, per_bird, params):
        super().__init__(parent)
        self.setWindowTitle("逐只识别")
        root = QVBoxLayout(self)
        tabs = QTabWidget(self)
        root.addWidget(tabs)
        page = QWidget(tabs)
        tabs.addTab(page, "识鸟与裁图")
        layout = QVBoxLayout(page)
        label = QLabel("逐只裁图后交给 SuperPicky 识鸟；可在「前置检测」覆盖清晰度参数。\n"
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
        layout.addStretch(1)
        from .bird_sharpness_params_form import AnalysisParamsForm
        detection = QWidget(tabs)
        detection_layout = QVBoxLayout(detection)
        self.override = QCheckBox("覆盖全局清晰度参数（仅本次逐只识别；本会话记住选择）", detection)
        self.override.setChecked(per_bird.analysis_overrides is not None)
        detection_layout.addWidget(self.override)
        note = QLabel("可独立选择 YOLOv8 xlarge 分割等模型，并调整检测置信度、输入及去重。\n"
                      "逐只模式不限制鸟数；最小裁图尺寸在「识鸟与裁图」中设置。", detection)
        note.setWordWrap(True)
        detection_layout.addWidget(note)
        scroll = QScrollArea(detection)
        scroll.setWidgetResizable(True)
        self.analysis_form = AnalysisParamsForm(scroll)
        self.analysis_form.set_params({**params, **(per_bird.analysis_overrides or {}), "max_birds": 0, "min_bird_side": 0})
        self.analysis_form.max_birds.setEnabled(False)
        self.analysis_form.min_bird_side.setEnabled(False)
        self.analysis_form.setEnabled(self.override.isChecked())
        self.override.toggled.connect(self.analysis_form.setEnabled)
        scroll.setWidget(self.analysis_form)
        detection_layout.addWidget(scroll)
        tabs.addTab(detection, "前置检测")
        self.resize(760, 700)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始逐只识别")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def accept(self):
        from .bird_sharpness_params_form import missing_models, download_models
        if self.override.isChecked():
            missing = missing_models(self.analysis_form.params())
            if missing and not download_models(self, missing):
                return
        super().accept()

    def options(self):
        return BirdIDOptions(self.url.text().strip(), self.threshold.value(), self.skip.isChecked())

    def per_bird_options(self):
        return PerBirdOptions(self.width.value(), self.height.value(), self.padding.value(),
                              self.analysis_form.params() if self.override.isChecked() else None)


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
            self.refresh_badges()
            return
        self.birds.set_rows([])  # 先清除旧照片的悬停状态。
        self.path, self._items = path, items
        rows, details = [], []
        if items:
            from bird_sharpness.trace import BIRD_COLORS, TraceBirdRow, hex_color
            for item in items:
                n = item["index"]
                x0, y0, x1, y1 = item["box_px"]
                name = item["cn_name"] or item["en_name"] or "未识别"
                score = item.get("confidence")
                status = {"candidate": " · 待确定", "failed": " · 失败", "skipped": " · 已过滤"}.get(item.get("status"), "")
                value = f"{name}\n" + (f"{score:.1f}%" if score is not None else "") + status
                detail = QWidget(self.birds)
                layout = QVBoxLayout(detail)
                layout.setContentsMargins(8, 3, 0, 6)
                layout.setSpacing(4)
                badges = QHBoxLayout()
                badges.setSpacing(4)
                species = individual_species_metadata(item)
                rarity, conservation = RarityBadge(detail), ConservationBadge(detail)
                rarity.set_score(species['gbif_rarity_100'])
                conservation.set_category(species['iucn_category'])
                for badge in (rarity, conservation):
                    if item.get('status') == 'candidate':
                        badge.setToolTip(badge.toolTip() + '\n待确定候选鸟种的信息')
                    badges.addWidget(badge)
                badges.addStretch(1)
                layout.addLayout(badges)
                description = f"检测 {item['detection_confidence']:.2f} · {x1-x0:g} × {y1-y0:g} px"
                if item.get("message"):
                    description += "\n" + item["message"]
                label = QLabel(description, detail)
                label.setTextFormat(getattr(Qt, "TextFormat", Qt).PlainText)
                label.setWordWrap(True)
                layout.addWidget(label)
                layout.addStretch(1)
                details.append(detail)
                rows.append(TraceBirdRow(f"鸟 #{n + 1}", value, hex_color(BIRD_COLORS[n % len(BIRD_COLORS)]),
                                         tuple(item["box"]), n))
        self.birds.set_rows(rows, details=details)
        self.empty.setVisible(not rows)

    def refresh_badges(self):
        for badge in self.birds.findChildren(MetadataBadge):
            badge.refresh_style()


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
