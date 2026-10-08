"""Viewer thumbnail identity rows; consume cached metadata, never read photo files."""
from __future__ import annotations

import html
import os
from dataclasses import dataclass

from app_common.bird_pinyin import stored_pinyin
from app_common.bird_rarity import rarity_metadata
from app_common.file_browser._browser_core import (
    _DisplayRole, _ToolTipRole, _UserRole, _MetaSpeciesCnRole,
    _AlignCenter, _ElideRight, _NoPen,
)
from app_common.file_browser._models import ThumbnailListModel, ThumbnailItemDelegate, _VideoInfoRole
from app_common.metadata_badges import metadata_badge_style
from app_common.shooting_location import shooting_location
from app_common.superviewer_user_options import get_runtime_user_options
from app_common.video import is_video

try:
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QFont
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QColor, QFont


_BirdDetailsRole = int(_UserRole) + 41


def _tooltip_with_lines(base, lines):
    # Browser path tooltips contain colored HTML; metadata must remain plain text.
    prefix, closing, tail = base.partition("</body>")
    if closing:
        tail = tail.removeprefix("</html>").strip()
        if tail:
            lines = tail.splitlines() + lines
    else:
        prefix = "<html><body>"
        lines = base.splitlines() + lines
    return prefix + "".join(f"<div>{html.escape(line)}</div>" for line in lines) + "</body></html>"


@dataclass(frozen=True)
class BirdThumbnailDetails:
    score: float | None = None
    category: str = ""
    pinyin: str = ""
    location: str = ""

    @classmethod
    def from_metadata(cls, metadata, name):
        return cls(*rarity_metadata(metadata), stored_pinyin(metadata, name), shooting_location(metadata))

    def badges(self, options):
        # Missing levels stay absent; valid zero is a common bird, not missing.
        return [metadata_badge_style(kind, value, options)
                for kind, value in (("rarity", self.score), ("iucn", self.category))
                if value is not None and value != ""]


class BirdThumbnailModel(ThumbnailListModel):
    def _build_entry(self, path, *, meta_cache, tooltip_fn, mismatch_fn):
        entry = super()._build_entry(path, meta_cache=meta_cache, tooltip_fn=tooltip_fn, mismatch_fn=mismatch_fn)
        metadata = meta_cache.get(os.path.normpath(path), {}) if isinstance(meta_cache, dict) else {}
        entry.bird_details = BirdThumbnailDetails.from_metadata(metadata, entry.species_cn)
        return entry

    def _set_meta_on_entry(self, entry, meta):
        roles = super()._set_meta_on_entry(entry, meta)
        details = BirdThumbnailDetails.from_metadata(meta or {}, entry.species_cn)
        if entry.bird_details != details:
            entry.bird_details = details
            roles.extend([_BirdDetailsRole, _ToolTipRole])
        if _MetaSpeciesCnRole in roles:
            roles.append(_ToolTipRole)
        return roles

    def data(self, index, role=_DisplayRole):
        if index.isValid() and 0 <= index.row() < len(self._entries):
            entry = self._entries[index.row()]
            details = entry.bird_details
            if role == _BirdDetailsRole:
                return details
            if role == _ToolTipRole:
                base = str(super().data(index, role) or entry.path)
                lines = []
                if entry.species_cn:
                    lines.append(f"鸟名：{entry.species_cn}")
                if details.pinyin:
                    lines.append(f"拼音：{details.pinyin}")
                options = get_runtime_user_options()
                if details.score is not None:
                    label = metadata_badge_style("rarity", details.score, options)[0]
                    lines.append(f"稀有度：{label} · GBIF {details.score:g}/100")
                if details.category:
                    label = metadata_badge_style("iucn", details.category, options)[0]
                    lines.append(f"IUCN 保护等级：{label}（{details.category}）")
                if details.location:
                    lines.append(f"拍摄地点：{details.location}")
                return _tooltip_with_lines(base, lines)
        return super().data(index, role)


class BirdThumbnailDelegate(ThumbnailItemDelegate):
    """Two identity rows above the shared status strip, including at the 128 tier."""

    identity_footer_height = 52
    show_species_overlay = False

    @staticmethod
    def badge_widths(desired, available):
        """Let a short label donate its unused width to the other badge."""
        if not desired:
            return []
        if len(desired) == 1:
            return [min(desired[0], available)]
        first = min(desired[0], max(available / 2, available - desired[1]))
        return [first, min(desired[1], available - first)]

    def paint_identity_footer(self, painter, rect, index, option):
        area = rect.adjusted(7, 3, -7, -3)
        name = str(index.data(_MetaSpeciesCnRole) or "")
        video = is_video(index.data(_UserRole))
        font = QFont(option.font)
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        title = name or ("视频" if video else "未填写鸟名")
        title_rect = QRectF(area.left(), area.top(), area.width(), 23)
        painter.setPen(QColor("#F3F4F6" if name else "#9CA3AF"))
        painter.drawText(title_rect, _AlignCenter,
                         painter.fontMetrics().elidedText(title, _ElideRight, int(area.width())))

        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        badges = (index.data(_BirdDetailsRole) or BirdThumbnailDetails()).badges(get_runtime_user_options())
        badge_row = QRectF(area.left(), area.top() + 25, area.width(), 19)
        if not badges:
            info = index.data(_VideoInfoRole) or {}
            if video:
                label = (f"{info['width']} × {info['height']}" if info.get("width") and info.get("height")
                         else "视频文件")
            else:
                label = "暂无等级信息"
            painter.setPen(QColor("#9CA3AF"))
            painter.drawText(badge_row, _AlignCenter, label)
            return

        gap = 5
        widths = self.badge_widths([metrics.horizontalAdvance(text) + 14 for text, *_ in badges],
                                  max(0, badge_row.width() - gap * (len(badges) - 1)))
        x = badge_row.center().x() - (sum(widths) + gap * (len(badges) - 1)) / 2
        for (text, background, foreground), width in zip(badges, widths):
            badge = QRectF(x, badge_row.top(), width, badge_row.height())
            painter.setPen(_NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(QColor(foreground))
            painter.drawText(badge, _AlignCenter, metrics.elidedText(text, _ElideRight, max(0, int(width) - 14)))
            x += width + gap
