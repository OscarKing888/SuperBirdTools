# -*- coding: utf-8 -*-
"""信息页元数据徽章与共享用户选项编辑器；保留稀有度调用接口。"""
from app_common.bird_rarity import normalize_badge_options, rarity_score
from app_common.metadata_badges import BADGE_DEFINITIONS, metadata_badge_style
from app_common.superviewer_user_options import get_runtime_user_options
from .qt_compat import QLabel, QWidget, QPushButton, QLineEdit, QGridLayout, QHBoxLayout, QVBoxLayout, QSizePolicy
try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog, QInputDialog
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QColorDialog, QInputDialog


def rarity_badge_style(score, options=None, *, level=None):
    """信息页和列表徽章共用名称与颜色。"""
    return metadata_badge_style("rarity", score, get_runtime_user_options() if options is None else options, level=level)


def rarity_badge_tooltip(score):
    score = rarity_score(score)
    return ("暂无 GBIF 稀有度数据" if score is None else
            f"GBIF 稀有度：{score:g}/100，越高越稀有。\n档位分界：8、25、50、75；与 IUCN 保护等级独立。")


class MetadataBadge(QLabel):
    def __init__(self, parent=None, *, kind="rarity"):
        super().__init__(parent)
        self.kind = kind
        self._value = None
        self.setTextFormat(getattr(Qt, "TextFormat", Qt).PlainText)
        self.setWordWrap(False)
        self.setMinimumWidth(60)
        self.setAlignment(getattr(Qt, "AlignmentFlag", Qt).AlignCenter)
        policy = getattr(QSizePolicy, "Policy", QSizePolicy)
        self.setSizePolicy(policy.Maximum, policy.Preferred)
        self.refresh_style()

    def set_value(self, value):
        self._value = value
        self.setToolTip(rarity_badge_tooltip(value) if self.kind == "rarity" else
                       f"IUCN 保护等级：{value}" if value and value != "-" else "暂无 IUCN 保护等级数据")
        self.refresh_style()

    def refresh_style(self, options=None, *, level=None):
        text, background, foreground = metadata_badge_style(
            self.kind, self._value, get_runtime_user_options() if options is None else options, level=level)
        self.setText(text)
        self.setStyleSheet(f"QLabel {{ background-color: {background}; "
                           f"color: {foreground}; border-radius: 6px; "
                           "padding: 3px 9px; font-size: 13px; font-weight: 600; }")


class RarityBadge(MetadataBadge):
    def set_score(self, score):
        self.set_value(rarity_score(score))


class ConservationBadge(MetadataBadge):
    def __init__(self, parent=None):
        super().__init__(parent, kind="iucn")

    def set_category(self, category):
        self.set_value(str(category or "").split(" · ", 1)[0])


class _ColorButton(QPushButton):
    def __init__(self, color, changed, parent):
        super().__init__(parent)
        self._changed = changed
        self.set_color(color)
        self.clicked.connect(self._choose)

    def set_color(self, color):
        self.color = color
        self.setText(color)
        # 色块旁保留色值，用户可明确区分背景色和文字色。
        self.setStyleSheet(f"border: 1px solid #888888; border-left: 12px solid {color}; "
                           "border-radius: 3px; padding: 5px; background: palette(button);")

    def _choose(self):
        color = QColorDialog.getColor(QColor(self.color), self, "选择徽章颜色")
        if color.isValid():
            self.set_color(color.name().upper())
            self._changed()


class MetadataBadgesForm(QWidget):
    def __init__(self, options, parent=None, *, kind="rarity"):
        super().__init__(parent)
        self.definition = BADGE_DEFINITIONS[kind]
        opts = normalize_badge_options(options, self.definition.defaults)
        layout = QVBoxLayout(self)
        example = "传奇 → SSR" if kind == "rarity" else "EN · 濒危 → 重点保护"
        note = QLabel(("按 GBIF 稀有度分数显示徽章，分数越高越稀有。\n" if kind == "rarity" else
                       "IUCN 保护等级默认采用 Cornell / eBird 保护状态配色。\n") +
                      f"点击「编辑」修改显示文本（例如：{example}），或直接在输入框中修改。\n"
                      "修改后点击本窗口底部「确定」保存；「取消」放弃本次修改。\n"
                      "显示文本、背景色和文字色用于图片信息及 Overlay 自动映射。\n"
                      "缺失数据独立显示为未知，不视为普通、无危或未评估。", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(4, 70)
        for col, title in enumerate(("分数范围" if kind == "rarity" else "等级", "显示文本", "背景色", "文字色", "预览")):
            grid.addWidget(QLabel(title, self), 0, col)
        self.edits, self.colors, self.previews = {}, {}, {}
        self.edit_buttons = {}
        for row, (level, interval, *_rest) in enumerate(self.definition.levels, 1):
            prefix = f"{self.definition.prefix}_{level}_"
            grid.addWidget(QLabel(interval, self), row, 0)
            edit = QLineEdit(opts[prefix + "text"], self)
            edit.setMaxLength(32)
            edit.setMinimumWidth(70)
            edit.setAccessibleName(f"{interval} 显示文本")
            edit.setToolTip("可直接输入，最多 32 个字符；留空恢复该等级默认文本。")
            self.edits[level] = edit
            text_row = QHBoxLayout()
            text_row.addWidget(edit, 1)
            edit_button = QPushButton("编辑", self)
            edit_button.setAutoDefault(False)
            edit_button.setAccessibleName(f"编辑 {interval} 显示文本")
            edit_button.clicked.connect(lambda _checked=False, key=level: self._edit_text(key))
            self.edit_buttons[level] = edit_button
            text_row.addWidget(edit_button)
            grid.addLayout(text_row, row, 1)
            for col, field in ((2, "background"), (3, "foreground")):
                button = _ColorButton(opts[prefix + field], self._refresh_previews, self)
                self.colors[(level, field)] = button
                grid.addWidget(button, row, col)
            preview = MetadataBadge(self, kind=kind)
            self.previews[level] = preview
            grid.addWidget(preview, row, 4)
        for edit in self.edits.values():
            edit.textChanged.connect(self._refresh_previews)
        layout.addLayout(grid)
        reset = QPushButton("恢复默认徽章", self)
        reset.clicked.connect(self.reset_defaults)
        layout.addWidget(reset)
        layout.addStretch(1)
        self._refresh_previews()

    def _edit_text(self, level):
        """明确的文字编辑入口；只更新表单草稿，设置页确认后统一保存。"""
        interval = next(item[1] for item in self.definition.levels if item[0] == level)
        dialog = QInputDialog(self)
        dialog.setWindowTitle("编辑显示文本")
        dialog.setLabelText(f"{interval}\n显示文本（最多 32 个字符，留空恢复默认）：")
        dialog.setTextValue(self.edits[level].text())
        dialog.setOkButtonText("应用")
        dialog.setCancelButtonText("取消")
        editor = dialog.findChild(QLineEdit)
        editor.setMaxLength(32)
        editor.selectAll()
        try:
            accepted = getattr(QInputDialog, "DialogCode", QInputDialog).Accepted
            if dialog.exec() == accepted:
                text = dialog.textValue().strip()
                default = self.definition.defaults[f"{self.definition.prefix}_{level}_text"]
                self.edits[level].setText(text or default)
        finally:
            dialog.deleteLater()

    def selected_options(self):
        prefix = self.definition.prefix
        return normalize_badge_options({
            **{f"{prefix}_{level}_text": edit.text() for level, edit in self.edits.items()},
            **{f"{prefix}_{level}_{field}": button.color for (level, field), button in self.colors.items()},
        }, self.definition.defaults)

    def _refresh_previews(self, *_args):
        options = self.selected_options()
        for level, preview in self.previews.items():
            preview.refresh_style(options, level=level)

    def reset_defaults(self):
        for level, edit in self.edits.items():
            edit.setText(self.definition.defaults[f"{self.definition.prefix}_{level}_text"])
        for (level, field), button in self.colors.items():
            button.set_color(self.definition.defaults[f"{self.definition.prefix}_{level}_{field}"])
        self._refresh_previews()


class RarityBadgesForm(MetadataBadgesForm):
    pass


class ConservationBadgesForm(MetadataBadgesForm):
    def __init__(self, options, parent=None):
        super().__init__(options, parent, kind="iucn")
