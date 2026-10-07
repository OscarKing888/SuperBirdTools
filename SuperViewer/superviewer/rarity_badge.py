# -*- coding: utf-8 -*-
"""信息页稀有度徽章与用户选项编辑器。"""
from app_common.bird_rarity import RARITY_LEVELS, RARITY_DEFAULT_OPTIONS, normalize_rarity_options, rarity_level, rarity_score
from app_common.superviewer_user_options import get_runtime_user_options
from .qt_compat import QLabel, QWidget, QPushButton, QLineEdit, QGridLayout, QVBoxLayout, QSizePolicy
try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QColorDialog
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QColorDialog


def rarity_badge_style(score, options=None, *, level=None):
    """信息页和列表徽章共用名称与颜色。"""
    options = normalize_rarity_options(get_runtime_user_options() if options is None else options)
    key = f"rarity_badge_{level or rarity_level(score)}_"
    return tuple(options[key + field] for field in ("text", "background", "foreground"))


def rarity_badge_tooltip(score):
    score = rarity_score(score)
    return ("暂无 GBIF 稀有度数据" if score is None else
            f"GBIF 稀有度：{score:g}/100，越高越稀有。\n档位分界：8、25、50、75；与 IUCN 保护等级独立。")


class RarityBadge(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._score = None
        self.setTextFormat(getattr(Qt, "TextFormat", Qt).PlainText)
        self.setWordWrap(True)
        self.setMinimumWidth(60)
        self.setAlignment(getattr(Qt, "AlignmentFlag", Qt).AlignCenter)
        policy = getattr(QSizePolicy, "Policy", QSizePolicy)
        self.setSizePolicy(policy.Maximum, policy.Preferred)
        self.refresh_style()

    def set_score(self, score):
        self._score = rarity_score(score)
        self.setToolTip(rarity_badge_tooltip(self._score))
        self.refresh_style()

    def refresh_style(self, options=None, *, level=None):
        text, background, foreground = rarity_badge_style(self._score, options, level=level)
        self.setText(text)
        self.setStyleSheet(f"QLabel {{ background-color: {background}; "
                           f"color: {foreground}; border-radius: 6px; "
                           "padding: 3px 9px; font-size: 13px; font-weight: 600; }")


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


class RarityBadgesForm(QWidget):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        opts = normalize_rarity_options(options)
        layout = QVBoxLayout(self)
        note = QLabel("按 GBIF 稀有度分数显示徽章，分数越高越稀有。\n"
                      "每档名称、背景色和文字色均可修改，默认文字为白色。\n"
                      "IUCN 保护等级独立显示；缺失数据不会显示为普通或无危。", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(4, 70)
        for col, title in enumerate(("分数范围", "显示名称", "背景色", "文字色", "预览")):
            grid.addWidget(QLabel(title, self), 0, col)
        self.edits, self.colors, self.previews = {}, {}, {}
        for row, (level, interval, _text, _bg) in enumerate(RARITY_LEVELS, 1):
            prefix = f"rarity_badge_{level}_"
            grid.addWidget(QLabel(interval, self), row, 0)
            edit = QLineEdit(opts[prefix + "text"], self)
            edit.setMaxLength(32)
            edit.setMinimumWidth(70)
            self.edits[level] = edit
            grid.addWidget(edit, row, 1)
            for col, field in ((2, "background"), (3, "foreground")):
                button = _ColorButton(opts[prefix + field], self._refresh_previews, self)
                self.colors[(level, field)] = button
                grid.addWidget(button, row, col)
            preview = RarityBadge(self)
            preview.setMaximumWidth(110)
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

    def selected_options(self):
        return normalize_rarity_options({
            **{f"rarity_badge_{level}_text": edit.text() for level, edit in self.edits.items()},
            **{f"rarity_badge_{level}_{field}": button.color for (level, field), button in self.colors.items()},
        })

    def _refresh_previews(self, *_args):
        options = self.selected_options()
        for level, preview in self.previews.items():
            preview.refresh_style(options, level=level)

    def reset_defaults(self):
        for level, edit in self.edits.items():
            edit.setText(RARITY_DEFAULT_OPTIONS[f"rarity_badge_{level}_text"])
        for (level, field), button in self.colors.items():
            button.set_color(RARITY_DEFAULT_OPTIONS[f"rarity_badge_{level}_{field}"])
        self._refresh_previews()
