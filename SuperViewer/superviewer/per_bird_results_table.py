# -*- coding: utf-8 -*-
"""逐只识别批量结果：沿用识鸟表格、照片分组与可见缩略图调度。"""
from pathlib import Path

from .bird_identification_table import BirdIDResultsModel, BirdIDResultsTable, Qt
from .per_bird_identification import individual_species_metadata


class PerBirdResultsModel(BirdIDResultsModel):
    HEADERS = ("照片预览", "状态", "操作", "鸟编号", "中文鸟名", "置信度", "英文鸟名", "拼音", "学名",
               "稀有度", "保护等级", "说明", "检测置信度", "鸟框位置（像素）", "分组")
    INITIAL_COLUMN = 4
    STATUS = {"confirmed": "已确认", "candidate": "待确定", "skipped": "已过滤", "failed": "失败"}
    PHOTO_STATUS = {"success": "已完成", "partial": "部分失败", "skipped": "跳过",
                    "failed": "失败", "cancelled": "取消"}

    @staticmethod
    def _entry_key(entry):
        attention = entry.needs_confirmation or entry.result.status in {"failed", "partial", "cancelled"}
        return not attention, entry.sequence

    def append_result(self, result):
        individuals = tuple(result.response.get("individuals", ()))
        # 鸟编号对应照片中的检测框；不能按置信度重排为候选鸟种。
        return self._append_entry(result, tuple(range(len(individuals))) or (0,), individuals=individuals)

    def can_adopt(self, row):
        return False

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry, number = self.rows[index.row()]
        result = entry.result
        item = entry.individuals[number] if entry.individuals else {}
        col = index.column()
        if col == 0 and role == Qt.ItemDataRole.DecorationRole and self.thumbnails is not None:
            return self.thumbnails.image(result.source)
        if role in (Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.AccessibleTextRole):
            if col in (0, self.GROUP_COLUMN):
                return f"{result.source}\n{result.message}"
            if col in (1, 11):
                return "\n".join(filter(None, (result.message, item.get("message"))))
            return self.data(index)
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if col == self.GROUP_COLUMN:
            return None
        if col == 0:
            return Path(result.source).name
        if col == 1:
            status = self.STATUS.get(item.get("status"), self.PHOTO_STATUS.get(result.status, result.status))
            if item and result.status in {"failed", "cancelled"}:
                return f"{status}（未保存）"
            return f"{status}（已有）" if item and result.status == "skipped" else status
        if col == 3:
            return f"#{item['index'] + 1}" if item else "—"
        if col in (5, 12):
            value = item.get("confidence" if col == 5 else "detection_confidence")
            return "—" if value is None else f"{float(value) * (100 if col == 12 else 1):.1f}%"
        if col == 11:
            return item.get("message") or (individual_species_metadata(item).get("description") if item else None) or result.message
        if col == 13:
            return ", ".join(str(value) for value in item.get("box_px", ())) or "—"
        species = individual_species_metadata(item) if item else {}
        if col == 9:
            value = species.get("gbif_rarity_100")
            return "—" if value is None else f"{value:g} / 100"
        key = {4: "cn_name", 6: "en_name", 7: "pinyin_name", 8: "scientific_name", 10: "iucn_category"}.get(col)
        return item.get(key) or species.get(key) or "—"


class PerBirdResultsTable(BirdIDResultsTable):
    def __init__(self, parent=None, *, thumbnails=None):
        super().__init__(parent, thumbnails=thumbnails, model_type=PerBirdResultsModel)
        self.setAccessibleName("逐只识别汇总表格")
        # 放大字体时汇总文字会换行，仍为表头、至少一只鸟和滚动条留空间。
        self.setMinimumHeight(180)
        self.setColumnHidden(self.results.ACTION_COLUMN, True)
        self.setColumnWidth(1, 150)
        self.setColumnWidth(3, 80)
