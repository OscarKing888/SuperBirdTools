# -*- coding: utf-8 -*-
"""缩略图角标布局/配色 Mockup 渲染脚本（离屏 Qt 绘制，不修改业务代码）。

- 「现状」直接调用 app_common.file_browser._models.ThumbnailItemDelegate.paint；
- 方案 A/B/C 为本脚本内的原型 delegate，仅用于视觉评审。

运行：.venv/bin/python docs/ui_mockups/thumbnail_badges/render_mockups.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if not (ROOT / "app_common" / "__init__.py").exists():
    # 工作树里 app_common 子模块未初始化时，回退到主检出目录。
    ROOT = Path("/Users/oscar/Pictures/SuperApps/SuperBirdTools")
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QModelIndex, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter,
    QPainterPath, QPalette, QPen, QPixmap, QStandardItem, QStandardItemModel,
)
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from app_common.file_browser import _models as M

SAMPLES = Path("/tmp/thumbmock")
OUT = HERE
THUMB = 256

# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------
SCENARIOS = [
    dict(cap="① 无标记", img="b", name="DSC05561.ARW"),
    dict(cap="② 仅4星·亮背景(截图复现)", img="bright", name="DSC05567.ARW", rating=4, sel=True),
    dict(cap="③ 5星+精选+精焦+鸟种", img="b", name="DSC05570.ARW", rating=5, pick=1,
         focus="精焦", box=(0.50, 0.28, 0.58, 0.40), species="黑脸琵鹭"),
    dict(cap="④ 排除+1星+失焦", img="b", name="DSC05571.ARW", rating=1, pick=-1,
         focus="失焦", box=(0.30, 0.45, 0.40, 0.58)),
    dict(cap="⑤ 3星+合焦+鸟种+红色标·暖色图", img="a", name="DSC08031.JPG", rating=3,
         focus="合焦", box=(0.52, 0.70, 0.58, 0.80), species="城市日落", color="Red"),
    dict(cap="⑥ 竖图+4星+精选+偏移+鸟种", img="p", name="DSC05580.ARW", rating=4, pick=1,
         focus="偏移", box=(0.40, 0.30, 0.60, 0.42), species="黑脸琵鹭"),
    dict(cap="⑦ 视频+2星+鸟种", img="b", name="C0012.MP4", rating=2, video=12.4,
         species="黑脸琵鹭"),
    dict(cap="⑧ 5星+精选+精焦·选中", img="b", name="DSC05575.ARW", rating=5, pick=1,
         focus="精焦", box=(0.50, 0.28, 0.58, 0.40), species="黑脸琵鹭", sel=True),
]
BURST = [
    dict(img="b", name="DSC05590.ARW", burst="1/4", group=(0, True, False), rating=3, focus="合焦",
         box=(0.50, 0.28, 0.58, 0.40)),
    dict(img="b", name="DSC05591.ARW", burst="2/4", group=(0, False, False), rating=5, pick=1,
         focus="精焦", box=(0.50, 0.28, 0.58, 0.40), species="黑脸琵鹭", sel=True),
    dict(img="b", name="DSC05592.ARW", burst="3/4", group=(0, False, False), rating=2, focus="偏移",
         box=(0.50, 0.28, 0.58, 0.40)),
    dict(img="b", name="DSC05593.ARW", burst="4/4", group=(0, False, True), pick=-1, focus="失焦",
         box=(0.50, 0.28, 0.58, 0.40)),
]

_PIX_CACHE: dict[tuple[str, int], QPixmap] = {}


def sample_pixmap(key: str, size: int) -> QPixmap:
    ck = (key, size)
    if ck not in _PIX_CACHE:
        pm = QPixmap(str(SAMPLES / f"{key}.jpg"))
        _PIX_CACHE[ck] = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
    return _PIX_CACHE[ck]


def make_item(sc: dict, size: int) -> QStandardItem:
    ext = ".mp4" if sc.get("video") else ".arw"
    it = QStandardItem(sc["name"])
    it.setData(f"/tmp/mock/{sc['name']}{'' if sc['name'].lower().endswith(ext) else ext}", M._UserRole)
    it.setData(sc.get("rating", 0), M._MetaRatingRole)
    it.setData(sc.get("pick", 0), M._MetaPickRole)
    it.setData(sc.get("focus", ""), M._MetaFocusRole)
    it.setData(sc.get("box"), M._MetaFocusBoxRole)
    it.setData(sc.get("species", ""), M._MetaSpeciesCnRole)
    it.setData(sc.get("burst", ""), M._MetaBurstTextRole)
    it.setData(sc.get("group"), M._MetaBurstGroupRole)
    it.setData(sc.get("color", ""), M._MetaColorRole)
    it.setData(sample_pixmap(sc["img"], size), M._ThumbPixmapRole)
    if sc.get("video"):
        it.setData({"duration": sc["video"]}, M._VideoInfoRole)
    return it


# ---------------------------------------------------------------------------
# 配色常量（方案用）
# ---------------------------------------------------------------------------
STAR_ON = QColor("#FFC53D")       # 亮金：深底/浅底均可读
STAR_OFF = QColor(255, 255, 255, 46)
PICK_GREEN = QColor("#22C55E")
REJECT_RED = QColor("#EF4444")
FOCUS_COLORS = {
    "精焦": QColor("#22C55E"),
    "合焦": QColor("#EAB308"),
    "偏移": QColor("#4C8DFF"),   # 原 #0051FF 在深底上过暗
    "失焦": QColor("#8A8A8A"),
}
LABEL_COLORS = {
    "Red": "#EF4444", "Orange": "#F97316", "Yellow": "#EAB308", "Green": "#22C55E",
    "Blue": "#3B82F6", "Purple": "#A855F7", "White": "#E5E5E5",
}
CARD_BG = QColor("#2D2D2D")
CARD_BORDER = QColor("#464646")
FOOTER_BG = QColor("#1F1F1F")
SPECIES_FG = QColor("#86EFAC")


def star_path(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    inner = r * 0.45
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else inner
        pt = QPointF(cx + rr * math.cos(ang), cy + rr * math.sin(ang))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


def draw_stars(p: QPainter, x: float, cy: float, r: float, rating: int, *, slots: int = 5,
               gap: float = 2.0, shadow: bool = False) -> float:
    """从 x 起绘制 slots 个星（前 rating 个点亮），返回总宽度。"""
    step = r * 2 + gap
    for i in range(slots):
        cx = x + r + i * step
        path = star_path(cx, cy, r)
        if shadow:
            p.setPen(QPen(QColor(0, 0, 0, 160), 1.6))
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(STAR_ON if i < rating else STAR_OFF)
        p.drawPath(path)
    return slots * step - gap


def draw_flag_chip(p: QPainter, rect: QRectF, pick: int) -> None:
    """精选=绿底白勾，排除=红底白叉（矢量绘制，替代跨平台不一致的 emoji）。"""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(PICK_GREEN if pick == 1 else REJECT_RED)
    p.drawEllipse(rect)
    pen = QPen(QColor("#FFFFFF"), max(1.4, rect.width() / 9.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    c = rect.center()
    s = rect.width() * 0.22
    if pick == 1:
        path = QPainterPath(QPointF(c.x() - s * 1.1, c.y()))
        path.lineTo(QPointF(c.x() - s * 0.25, c.y() + s * 0.9))
        path.lineTo(QPointF(c.x() + s * 1.2, c.y() - s * 0.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    else:
        p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
        p.drawLine(QPointF(c.x() - s, c.y() + s), QPointF(c.x() + s, c.y() - s))


def text_w(fm: QFontMetrics, t: str) -> int:
    return fm.horizontalAdvance(t)


def pill(p: QPainter, rect: QRectF, alpha: int = 185, border: bool = True) -> None:
    p.setBrush(QColor(0, 0, 0, alpha))
    p.setPen(QPen(QColor(255, 255, 255, 40), 1) if border else Qt.PenStyle.NoPen)
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)


def draw_focus_box(p: QPainter, draw_rect: QRect, box, color: QColor) -> None:
    norm = M._coerce_normalized_focus_box(box)
    if norm is None:
        return
    l, t, r, b = norm
    w, h = draw_rect.width(), draw_rect.height()
    base = max(1, min(w, h))
    min_side = max(9, min(28, int(round(base * 0.055))))
    x1, y1 = draw_rect.left() + l * w, draw_rect.top() + t * h
    x2, y2 = draw_rect.left() + r * w, draw_rect.top() + b * h
    if x2 - x1 < min_side:
        cx = (x1 + x2) / 2; x1, x2 = cx - min_side / 2, cx + min_side / 2
    if y2 - y1 < min_side:
        cy = (y1 + y2) / 2; y1, y2 = cy - min_side / 2, cy + min_side / 2
    rect = QRectF(x1, y1, x2 - x1, y2 - y1)
    lw = max(1.25, min(4.0, base / 150.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(0, 0, 0, 190), lw + 1.6))
    p.drawRect(rect)
    p.setPen(QPen(color, lw))
    p.drawRect(rect)


# ---------------------------------------------------------------------------
# 原型 delegate
# ---------------------------------------------------------------------------
class ProtoDelegate(M.ThumbnailItemDelegate):
    """方案 A/B/C 共用的数据读取与卡片/文件名绘制；mode 决定角标布局。"""

    def __init__(self, mode: str, footer_h: int = 0):
        super().__init__()
        self.mode = mode
        self.footer_h = footer_h

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: C901
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        name = str(index.data() or "")
        burst_text = str(index.data(M._MetaBurstTextRole) or "")
        rating = int(index.data(M._MetaRatingRole) or 0)
        pick = int(index.data(M._MetaPickRole) or 0)
        focus = str(index.data(M._MetaFocusRole) or "")
        box = index.data(M._MetaFocusBoxRole)
        species = str(index.data(M._MetaSpeciesCnRole) or "")
        label = str(index.data(M._MetaColorRole) or "")
        pixmap = index.data(M._ThumbPixmapRole)
        group = index.data(M._MetaBurstGroupRole)
        video = index.data(M._VideoInfoRole) if M.is_video(index.data(M._UserRole)) else None
        small = opt.rect.width() < 200
        p = painter
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if group:
            M._paint_burst_group_band(p, opt.rect, group)
        state_rect = opt.rect.adjusted(4, 4, -4, -4) if group else opt.rect
        if selected:
            p.fillRect(state_rect, opt.palette.highlight())

        cell = opt.rect.adjusted(6, 6, -6, -6)
        fm = p.fontMetrics()
        name_h = fm.lineSpacing() + 6
        fh = self.footer_h
        card = QRect(cell.left(), cell.top(), cell.width(), max(24, cell.height() - name_h - 6))
        thumb = QRect(card.left(), card.top(), card.width(), card.height() - fh)

        # 卡片：色标用 2px 描边表达
        border = QColor(LABEL_COLORS[label]) if label in LABEL_COLORS else CARD_BORDER
        p.setBrush(CARD_BG)
        p.setPen(QPen(border, 2 if label else 1))
        p.drawRoundedRect(QRectF(card).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        draw = QRect(thumb)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            sc = min(thumb.width() / pixmap.width(), thumb.height() / pixmap.height())
            dw, dh = int(pixmap.width() * sc), int(pixmap.height() * sc)
            draw = QRect(thumb.left() + (thumb.width() - dw) // 2, thumb.top() + (thumb.height() - dh) // 2, dw, dh)
            if fh:
                # 有信息条时图片贴卡片顶部圆角
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(card).adjusted(1, 1, -1, -1), 5, 5)
                p.save(); p.setClipPath(clip)
                p.drawPixmap(draw, pixmap)
                p.restore()
            else:
                p.drawPixmap(draw, pixmap)
        if pick == -1 and self.mode in ("B", "C"):
            p.fillRect(draw, QColor(0, 0, 0, 120))   # 排除：压暗

        focus_color = FOCUS_COLORS.get(focus, QColor("#00FF00"))
        draw_focus_box(p, draw, box, focus_color)

        f11 = QFont(opt.font); f11.setPixelSize(11)
        f12 = QFont(opt.font); f12.setPixelSize(12)
        getattr(self, f"_paint_{self.mode}")(
            p, opt, thumb, draw, card, rating, pick, focus, species, video, f11, f12, small)

        text_rect = QRect(cell.left(), card.bottom() + 4, cell.width(), name_h)
        p.setFont(opt.font)
        p.setPen(opt.palette.highlightedText().color() if selected else opt.palette.text().color())
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter,
                   fm.elidedText(M._format_burst_name(name, burst_text), Qt.TextElideMode.ElideRight, text_rect.width()))
        p.restore()

    # -- 公共小件 ----------------------------------------------------------
    def _video_pill(self, p, draw, video, f11, *, top_left=True, x_offset=0):
        p.setFont(f11)
        fm = p.fontMetrics()
        t = "▶ " + M.format_duration(video["duration"])
        w = text_w(fm, t) + 12
        r = QRectF(draw.left() + 4 + x_offset, draw.top() + 4 if top_left else draw.bottom() - 21, w, 17)
        pill(p, r)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, t)
        return w

    def _species_pill(self, p, draw, species, f12, y_bottom):
        if not species:
            return
        p.setFont(f12)
        fm = p.fontMetrics()
        t = fm.elidedText(species, Qt.TextElideMode.ElideRight, draw.width() - 20)
        w = text_w(fm, t) + 14
        r = QRectF(draw.center().x() - w / 2, y_bottom - fm.height() - 4, w, fm.height() + 4)
        pill(p, r, 165, border=False)
        p.setPen(SPECIES_FG)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, t)

    def _focus_tag(self, p, x, cy, focus, f11, with_text=True):
        if not focus:
            return 0
        c = FOCUS_COLORS.get(focus, QColor("#888"))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(c)
        p.drawEllipse(QPointF(x + 4, cy), 3.5, 3.5)
        if not with_text:
            return 9
        p.setFont(f11); p.setPen(c)
        fm = p.fontMetrics()
        p.drawText(QRectF(x + 11, cy - 8, 40, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, focus)
        return 11 + text_w(fm, focus)

    # -- 方案 A：利用上下留白（用户设想）----------------------------------------
    def _paint_A(self, p, opt, thumb, draw, card, rating, pick, focus, species, video, f11, f12, small):
        band_h = thumb.bottom() - draw.bottom()
        has_band = band_h >= 20
        row_cy = (draw.bottom() + thumb.bottom()) / 2 + 1 if has_band else draw.bottom() - 14
        area = thumb if has_band else draw
        x = area.left() + 8
        if not has_band and (pick or focus or rating):
            pill(p, QRectF(draw.left() + 4, row_cy - 10, draw.width() - 8, 20), 170)
        if pick:
            draw_flag_chip(p, QRectF(x, row_cy - 7, 14, 14), pick); x += 20
        if focus:
            x += self._focus_tag(p, x, row_cy, focus, f11) + 6
        if rating:
            draw_stars(p, area.right() - 8 - (5 * 11 + 8), row_cy, 5.5, rating)
        # 上留白：鸟种 / 视频
        top_has_band = draw.top() - thumb.top() >= 20
        top_cy = (thumb.top() + draw.top()) / 2 if top_has_band else draw.top() + 12
        if species:
            p.setFont(f12); fm = p.fontMetrics()
            t = fm.elidedText(species, Qt.TextElideMode.ElideRight, draw.width() - 20)
            w = text_w(fm, t) + 14
            r = QRectF(thumb.center().x() - w / 2, top_cy - 9, w, 18)
            if not top_has_band:
                pill(p, r, 165, border=False)
            p.setPen(SPECIES_FG); p.drawText(r, Qt.AlignmentFlag.AlignCenter, t)
        if video:
            p.setFont(f11); fm = p.fontMetrics()
            t = "▶ " + M.format_duration(video["duration"])
            r = QRectF(thumb.left() + 6, top_cy - 9, text_w(fm, t) + 12, 18)
            if not top_has_band:
                pill(p, r)
            p.setPen(QColor("#FFFFFF")); p.drawText(r, Qt.AlignmentFlag.AlignCenter, t)

    # -- 方案 B：覆盖层增强（位置不变，只改配色/图形）--------------------------
    def _paint_B(self, p, opt, thumb, draw, card, rating, pick, focus, species, video, f11, f12, small):
        x_off = 0
        if pick:
            draw_flag_chip(p, QRectF(draw.left() + 5, draw.top() + 5, 16, 16), pick)
            x_off = 22
        if video:
            self._video_pill(p, draw, video, f11, x_offset=x_off)
        if rating:
            r = 5.5
            w = rating * (2 * r + 2) - 2 + 12
            rect = QRectF(draw.right() - w - 4, draw.top() + 4, w, 18)
            pill(p, rect, 190)
            draw_stars(p, rect.left() + 6, rect.center().y(), r, rating, slots=rating)
        self._species_pill(p, draw, species, f12, draw.bottom() - 4)
        if focus:
            # 右下角小圆点提示对焦等级（框已按状态着色）
            c = FOCUS_COLORS.get(focus)
            p.setPen(QPen(QColor(0, 0, 0, 170), 2)); p.setBrush(c)
            p.drawEllipse(QPointF(draw.right() - 10, draw.bottom() - 10), 4.5, 4.5)

    # -- 方案 C：卡片底部固定信息条（推荐）------------------------------------
    def _paint_C(self, p, opt, thumb, draw, card, rating, pick, focus, species, video, f11, f12, small):
        fh = self.footer_h
        foot = QRectF(card.left() + 1, card.bottom() - fh + 1, card.width() - 2, fh - 1)
        path = QPainterPath()
        path.addRoundedRect(foot, 5, 5)
        path.addRect(QRectF(foot.left(), foot.top(), foot.width(), 6))
        path.setFillRule(Qt.FillRule.WindingFill)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(FOOTER_BG)
        p.drawPath(path)
        cy = foot.center().y()
        x = foot.left() + 6
        if pick:
            d = 13 if small else 14
            draw_flag_chip(p, QRectF(x, cy - d / 2, d, d), pick); x += d + 6
        if focus:
            x += self._focus_tag(p, x, cy, focus, f11, with_text=not small) + 6
        r = 4.5 if small else 5.5
        stars_w = 5 * (2 * r + (1.5 if small else 2)) - 2
        draw_stars(p, foot.right() - 6 - stars_w, cy, r, rating, gap=1.5 if small else 2)
        if video:
            self._video_pill(p, draw, video, f11)
        if not small:
            self._species_pill(p, draw, species, f12, draw.bottom() - 4)


# ---------------------------------------------------------------------------
# 合成画板
# ---------------------------------------------------------------------------
def make_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#2D7FD6"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#E6E6E6"))
    return pal


def render_cell(p: QPainter, delegate, sc: dict, rect: QRect, size: int, font: QFont, pal: QPalette):
    model = QStandardItemModel()
    model.appendRow(make_item(sc, size))
    idx = model.index(0, 0)
    opt = QStyleOptionViewItem()
    opt.rect = rect
    opt.font = font
    opt.palette = pal
    opt.state = QStyle.StateFlag.State_Enabled | (QStyle.StateFlag.State_Selected if sc.get("sel") else QStyle.StateFlag.State_None)
    opt.fontMetrics = QFontMetrics(font)
    p.save()
    p.setFont(font)
    delegate.paint(p, opt, idx)
    p.restore()


def render_sheet(title: str, subtitle: str, delegate, footer_h: int, out: Path, *, size: int = THUMB,
                 small_row: bool = False, extra_h: int | None = None) -> None:
    extra_h = footer_h if extra_h is None else extra_h
    font = QFont("PingFang SC"); font.setPixelSize(13)
    cw, ch = size + 32, size + 46 + extra_h
    gap, cap_h, pad = 18, 22, 24
    cols = 4
    rows_cells = math.ceil(len(SCENARIOS) / cols)
    sw, sh_ = 128 + 32, 128 + 46 + (18 if extra_h else 0)
    width = pad * 2 + cols * cw + (cols - 1) * gap
    height = 70 + rows_cells * (cap_h + ch + gap) + (cap_h + ch + gap) + pad
    if small_row:
        height += cap_h + sh_ + gap + 10
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#262626"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    pal = make_palette()
    tf = QFont("PingFang SC"); tf.setPixelSize(20); tf.setBold(True)
    p.setFont(tf); p.setPen(QColor("#FAFAFA"))
    p.drawText(pad, 34, title)
    sf = QFont("PingFang SC"); sf.setPixelSize(13)
    p.setFont(sf); p.setPen(QColor("#A1A1A1"))
    p.drawText(pad, 56, subtitle)
    capf = QFont("PingFang SC"); capf.setPixelSize(12)
    y = 70
    for i, sc in enumerate(SCENARIOS):
        c, r = i % cols, i // cols
        x = pad + c * (cw + gap)
        yy = y + r * (cap_h + ch + gap)
        p.setFont(capf); p.setPen(QColor("#8A8A8A"))
        p.drawText(x, yy + 14, sc["cap"])
        render_cell(p, delegate, sc, QRect(x, yy + cap_h, cw, ch), size, font, pal)
    yy = y + rows_cells * (cap_h + ch + gap)
    p.setFont(capf); p.setPen(QColor("#8A8A8A"))
    p.drawText(pad, yy + 14, "⑨ 连拍组 4 张（第 2 张选中，第 4 张排除）")
    for i, sc in enumerate(BURST):
        render_cell(p, delegate, sc, QRect(pad + i * cw, yy + cap_h, cw, ch), size, font, pal)
    if small_row:
        yy += cap_h + ch + gap + 10
        p.setFont(capf); p.setPen(QColor("#8A8A8A"))
        p.drawText(pad, yy + 14, "⑩ 128px 小尺寸自适应（隐藏鸟种与对焦文字，仅保留圆点）")
        if isinstance(delegate, ProtoDelegate):
            delegate.footer_h = 18
        for i, sc in enumerate([SCENARIOS[2], SCENARIOS[3], SCENARIOS[5], SCENARIOS[6], *BURST[:2]]):
            render_cell(p, delegate, dict(sc, sel=sc.get("sel", False)),
                        QRect(pad + i * (sw + 14), yy + cap_h, sw, sh_), 128, font, pal)
        if isinstance(delegate, ProtoDelegate):
            delegate.footer_h = footer_h
    p.end()
    img.save(str(out))
    print("saved", out)


def main() -> None:
    app = QApplication(sys.argv)
    if "--impl" in sys.argv:
        render_sheet("实现  ThumbnailItemDelegate（方案 C 落地后真实代码渲染）",
                     "app_common/file_browser/_models.py",
                     M.ThumbnailItemDelegate(), 0, OUT / "impl_C.png", small_row=True)
        return
    render_sheet("现状  ThumbnailItemDelegate（真实代码渲染）",
                 "（方案 C 落地前）星级=银色#c0c0c0 右上角；精选/排除=emoji 左上角；鸟种=#00ff00 底部居中；对焦框恒为绿色；对焦状态与色标未绘制",
                 M.ThumbnailItemDelegate(), 0, OUT / "0_current.png", small_row=False)
    render_sheet("方案 A  利用上下留白（文件名上方灰色区域）",
                 "横图：信息落在留白里很干净；竖图/1:1 没有留白 → 只能退回压图，位置随图片比例跳动",
                 ProtoDelegate("A"), 0, OUT / "A_letterbox.png")
    render_sheet("方案 B  覆盖层增强（位置不变，只改配色与图形）",
                 "亮金矢量星+深色描边胶囊；矢量勾/叉替代 emoji；对焦框按状态着色；排除压暗；视频移到左上避开鸟种",
                 ProtoDelegate("B"), 0, OUT / "B_overlay.png")
    render_sheet("方案 C  卡片底部固定信息条（推荐）",
                 "信息条从原缩略槽底部切出（单元格尺寸不变）：横图时正好占用文件名上方的灰色留白，竖图位置也一致；图上只留对焦框/鸟种/视频",
                 ProtoDelegate("C", 22), 22, OUT / "C_footer.png", small_row=True, extra_h=0)


if __name__ == "__main__":
    main()
