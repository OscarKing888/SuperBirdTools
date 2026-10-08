"""Cached thumbnail identity, shared badge colors, and preview-preserving layout."""
from pathlib import Path

import pytest
from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from app_common.bird_rarity import RARITY_FIELD, IUCN_FIELD, RARITY_SOURCE_FIELD
from app_common.file_browser._browser_core import _MetaSpeciesCnRole
from app_common.file_browser._models import ThumbnailItemDelegate
from app_common.file_browser._panel import FileListPanel
from SuperViewer.superviewer import thumbnail_metadata as module
from SuperViewer.superviewer.thumbnail_metadata import BirdThumbnailModel, BirdThumbnailDelegate, _BirdDetailsRole
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel

_APP = QApplication.instance() or QApplication([])


def _model(meta, path="bird.jpg"):
    model = BirdThumbnailModel()
    model.append_paths([path], meta_cache={path: meta}, tooltip_fn=None, mismatch_fn=None)
    return model


def test_cached_details_refresh_and_stale_species_markers():
    meta = {"title": "白鹭", "pinyin_name": "bái lù", "pinyin_name_source": "白鹭",
            RARITY_FIELD: 0, IUCN_FIELD: "LC", RARITY_SOURCE_FIELD: "白鹭", "shooting_location": "杭州西溪"}
    model = _model(meta)
    index = model.index(0, 0)
    assert index.data(_BirdDetailsRole).score == 0
    tooltip = index.data(Qt.ItemDataRole.ToolTipRole)
    assert all(text in tooltip for text in ("白鹭", "bái lù", "杭州西溪", "GBIF 0/100", "LC"))
    model.set_meta_for_paths([("bird.jpg", dict(meta, title="黑脸琵鹭"))])
    assert index.data(_MetaSpeciesCnRole) == "黑脸琵鹭"
    assert index.data(_BirdDetailsRole).badges({}) == []
    assert "bái lù" not in index.data(Qt.ItemDataRole.ToolTipRole)
    model.set_meta_for_path("bird.jpg", {})
    assert index.data(_BirdDetailsRole).location == ""


def test_viewer_layout_is_opt_in():
    assert FileListPanel.thumbnail_delegate_class is ThumbnailItemDelegate
    assert SuperViewerTaggedFileListPanel.thumbnail_delegate_class is BirdThumbnailDelegate
    assert SuperViewerTaggedFileListPanel.thumbnail_model_class is BirdThumbnailModel
    for size in (128, 256, 512, 1024, 2048):
        basic = ThumbnailItemDelegate.grid_size(size)
        viewer = BirdThumbnailDelegate.grid_size(size)
        assert basic.width() == viewer.width() == size + 32
        assert viewer.height() - basic.height() == BirdThumbnailDelegate.identity_footer_height


def _render(size, meta, *, delegate_class=BirdThumbnailDelegate, portrait=False, selected=False, path="bird.jpg", light=False):
    # Offscreen Windows has no system font collection unless a font is registered.
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.exists() and "Microsoft YaHei" not in QFontDatabase.families():
        QFontDatabase.addApplicationFont(str(font_path))
    model = _model(meta, path)
    pixmap = QPixmap(120, 200) if portrait else QPixmap(300, 200)
    pixmap.fill(QColor("#808080"))
    model.set_pixmap_for_path(path, pixmap, size)
    delegate = delegate_class()
    image = QImage(delegate.grid_size(size), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#F3F4F6" if light else "#303030"))
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, image.width(), image.height())
    opt.font = QFont("Microsoft YaHei", 9)
    opt.palette.setColor(QPalette.ColorRole.Text, QColor("#18181B" if light else "#F3F4F6"))
    opt.state = QStyle.StateFlag.State_Enabled
    if selected:
        opt.state |= QStyle.StateFlag.State_Selected
    painter = QPainter(image)
    painter.setFont(opt.font)
    try:
        delegate.paint(painter, opt, model.index(0, 0))
    finally:
        painter.end()
    return image


def _bounds(image, color):
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y).name().upper() == color.upper()]
    assert points, color
    return min(x for x, y in points), min(y for x, y in points), max(x for x, y in points), max(y for x, y in points)


@pytest.mark.parametrize("size,portrait,selected,light", [(128, False, False, False), (128, True, True, False),
        (256, False, True, False), (256, True, False, False), (128, False, False, True), (256, True, True, True)])
def test_identity_rows_keep_image_size_and_render_configured_badges(monkeypatch, size, portrait, selected, light):
    options = {"rarity_badge_common_background": "#1234AB", "iucn_badge_lc_background": "#BC3412"}
    monkeypatch.setattr(module, "get_runtime_user_options", lambda: options)
    meta = {"title": "白鹭", RARITY_FIELD: 0, IUCN_FIELD: "LC", "rating": 5, "pick": 1}
    image = _render(size, meta, portrait=portrait, selected=selected, light=light)
    old = _render(size, {**meta, "title": ""}, portrait=portrait, delegate_class=ThumbnailItemDelegate)
    assert _bounds(image, "#808080") == _bounds(old, "#808080")
    rarity, conservation = _bounds(image, "#1234AB"), _bounds(image, "#BC3412")
    assert rarity[2] < conservation[0]
    assert rarity[1] == conservation[1]
    assert rarity[1] > _bounds(image, "#808080")[3]
    # The title has visible light text between the photo and badge row.
    assert any(image.pixelColor(x, y).name() == "#f3f4f6"
               for y in range(_bounds(image, "#808080")[3] + 1, rarity[1])
               for x in range(15, image.width() - 15))
    assert _bounds(image, "#ffc53d")[1] > rarity[3]


def test_long_badges_and_full_tooltip_follow_config_without_metadata_reload(monkeypatch):
    options = {"rarity_badge_legendary_text": "极其罕见的鸟类" * 3, "iucn_badge_cr_pew_text": "极危且可能野外灭绝" * 3}
    monkeypatch.setattr(module, "get_runtime_user_options", lambda: options)
    model = _model({"title": "很长的鸟名" * 5, RARITY_FIELD: 80, IUCN_FIELD: "CR(PEW)"})
    assert options["rarity_badge_legendary_text"] in model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    widths = BirdThumbnailDelegate.badge_widths([300, 300], 129)
    assert sum(widths) <= 129
    assert min(widths) >= 60
    options["rarity_badge_legendary_text"] = "特别稀有"
    assert "特别稀有" in model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)


def test_paint_does_not_resolve_tooltip_or_read_metadata():
    model = _model({"title": "白鹭", RARITY_FIELD: 0})
    model._tooltip_fn = lambda path: pytest.fail("painting must not resolve file tooltips")
    image = QImage(BirdThumbnailDelegate.grid_size(128), QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, image.width(), image.height())
    try:
        BirdThumbnailDelegate().paint(painter, option, model.index(0, 0))
    finally:
        painter.end()


def test_tooltip_keeps_path_markup_and_escapes_metadata():
    model = _model({"title": "白鹭<幼鸟>", "shooting_location": "西溪 & 湿地"})
    model._tooltip_fn = lambda _: "<html><body><span style='color:red'>路径</span></body></html>\n连拍: (1/2)"
    text = model.index(0, 0).data(Qt.ItemDataRole.ToolTipRole)
    assert text.count("</body>") == 1
    assert "<div>鸟名：白鹭&lt;幼鸟&gt;</div>" in text
    assert "西溪 &amp; 湿地" in text
    assert "<div>连拍: (1/2)</div>" in text
    assert "style='color:red'" in text
