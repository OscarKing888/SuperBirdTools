"""Viewer 文件列表的清晰度数值列，复用已载入的元数据与共享排序流程。"""
from app_common.bird_sharpness_fields import bird_sharpness_from_meta
from app_common.file_browser._browser_core import _TREE_COL_BIRD_SHARP
from app_common.file_browser._models import _file_sort_tiebreaker
from .rarity_file_table import RarityFileTableModel

try:
    from PyQt6.QtCore import Qt, QModelIndex
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import Qt, QModelIndex

_Role = getattr(Qt, "ItemDataRole", Qt)
_Horizontal = getattr(Qt, "Orientation", Qt).Horizontal
_MEAN = ("mean_score", "鸟清晰", 1, "平均清晰度（0–1000）：头部与身体分数各占 50%。")
_HEAD = ("head_score", "头部清晰度", 0, "头部清晰度（0–1000）：可靠定位头部的实测分数。")
_BODY = ("body_score", "身体清晰度", 0, "身体清晰度（0–1000）：排除头部全部采样窗口后的实测分数。")


def _score_text(value, decimals):
    return "—" if value is None else f"{value:.{decimals}f}"


def _score_sort_value(value):
    # 与现有数值列保持相同的缺值顺序；有效 0 分不是缺值。
    return (value is None, 0.0 if value is None else value)


class SharpnessFileTableModel(RarityFileTableModel):
    @property
    def head_score_column(self):
        # 仅追加逻辑列，避免移动共享相机、视频与稀有度列的索引。
        return super().columnCount()

    @property
    def body_score_column(self):
        return self.head_score_column + 1

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.body_score_column + 1

    def _score_spec(self, column):
        if column == _TREE_COL_BIRD_SHARP:
            return _MEAN
        if column == self.head_score_column:
            return _HEAD
        if column == self.body_score_column:
            return _BODY
        return None

    def headerData(self, section, orientation, role=int(_Role.DisplayRole)):
        spec = self._score_spec(section) if orientation == _Horizontal else None
        if spec:
            if role == _Role.DisplayRole:
                return spec[1]
            if role == _Role.ToolTipRole:
                return spec[3] + " 缺少有效测量时显示 —。"
        return super().headerData(section, orientation, role)

    def _display_value(self, entry, row, column):
        spec = self._score_spec(column)
        if spec:
            return _score_text(getattr(entry.bird_sharp, spec[0], None), spec[2])
        return super()._display_value(entry, row, column)

    def _sort_value(self, entry, column):
        spec = self._score_spec(column)
        if spec:
            return _score_sort_value(getattr(entry.bird_sharp, spec[0], None))
        return super()._sort_value(entry, column)

    def sort_key_for_path(self, path, meta, column):
        spec = self._score_spec(column)
        if spec:
            value = getattr(bird_sharpness_from_meta(meta), spec[0], None)
            return (_score_sort_value(value), *_file_sort_tiebreaker(path))
        return super().sort_key_for_path(path, meta, column)

    def data(self, index, role=int(_Role.DisplayRole)):
        if index.isValid() and 0 <= index.row() < len(self._entries):
            spec = self._score_spec(index.column())
            if spec:
                if role == _Role.ForegroundRole:
                    # 三项分数不同于原主判定，沿用普通文字色以免误用主判定颜色。
                    return None
                if role == _Role.ToolTipRole:
                    entry = self._entries[index.row()]
                    value = getattr(entry.bird_sharp, spec[0], None)
                    text = f"{entry.name}\n{spec[1]}：{_score_text(value, spec[2])}\n{spec[3]}"
                    if value is None:
                        text += "\n无有效分项；旧记录需重新检测后才能显示。"
                    return text
        return super().data(index, role)
