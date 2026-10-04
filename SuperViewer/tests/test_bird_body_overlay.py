# -*- coding: utf-8 -*-
"""蓝色鸟体框依附像素坐标，并参与共享画布的源尺寸叠加导出。"""
from app_common.preview_canvas import FocusCenteredPreviewCanvas
from SuperViewer.superviewer.bird_body_overlay import BirdBodyOverlayMixin
from SuperViewer.superviewer.qt_compat import QApplication, QColor, QPixmap
import json

import pytest

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, map_camera_focus_box
from SuperViewer.superviewer import preview_panel as preview_module
from SuperViewer.superviewer.preview_panel import PreviewPanel


_APP = QApplication.instance() or QApplication([])


class _Canvas(BirdBodyOverlayMixin, FocusCenteredPreviewCanvas):
    pass


def test_bird_body_overlay_renders_only_enabled_inside_box_and_clears_with_source():
    canvas = _Canvas()
    pixmap = QPixmap(100, 100)
    pixmap.fill(QColor("black"))
    canvas.set_source_pixmap(pixmap)
    canvas.set_bird_box((0.2, 0.2, 0.8, 0.8))
    disabled = canvas.render_source_pixmap_with_overlays().toImage()
    assert disabled.pixelColor(50, 50) == QColor("black")
    canvas.set_show_bird_box(True)
    enabled = canvas.render_source_pixmap_with_overlays().toImage()
    assert enabled.pixelColor(50, 50).blue() > 50
    assert enabled.pixelColor(5, 5) == QColor("black")
    canvas.set_source_pixmap(None)
    assert canvas._bird_box is None
    canvas.close()


def test_invalid_body_box_is_rejected():
    canvas = _Canvas()
    for box in ((0.5, 0.5, 0.1, 0.1), (float("nan"), 0.1, 0.8, 0.8), (1, 2)):
        canvas.set_bird_box(box)
        assert canvas._bird_box is None
    canvas.close()


def test_panel_bird_box_uses_actual_pixel_geometry_for_ab_and_full_upgrade(tmp_path):
    source = str(tmp_path / "bird.ARW")
    box = (0.2, 0.3, 0.7, 0.8)
    crop = (0.1, 0.2, 0.9, 0.8)
    quick = QPixmap(100, 100)
    quick.fill(QColor("black"))
    left, right = PreviewPanel(), PreviewPanel()
    try:
        for panel in (left, right):
            panel.set_quick_pixmap(source, quick)
            panel.set_bird_box(box)
            panel.set_show_bird_box(True)
        image = quick.toImage()
        image.setText(RAW_FOCUS_CROP_KEY, json.dumps(crop))
        right._on_full_preview_loaded(right._preview_request_token, source, image, 1.0)
        assert left.canvas._bird_box == box
        assert right.canvas._bird_box == pytest.approx(map_camera_focus_box(box, crop))
        # RAW 开关本身不是坐标来源，实际 full 像素携带的 crop 才是。
        assert right.show_raw() is False
        right.set_quick_pixmap(source, quick)
        right.set_bird_box(box)
        assert right.canvas._bird_box == box
    finally:
        left.close()
        right.close()


def test_raw_overlay_export_maps_default_pixels_and_restores_view_geometry(tmp_path, monkeypatch):
    panel = PreviewPanel()
    source = str(tmp_path / "bird.ARW")
    quick = QPixmap(100, 100)
    quick.fill(QColor("black"))
    box = (0.2, 0.3, 0.7, 0.8)
    crop = (0.1, 0.2, 0.9, 0.8)
    try:
        panel.set_quick_pixmap(source, quick)
        panel._raw_focus_crop_box = crop
        panel.set_bird_box(box)
        panel.set_show_bird_box(True)
        original = panel.canvas._bird_box
        # 默认嵌入 JPEG 无 full RAW crop；导出叠加须回到相机坐标。
        monkeypatch.setattr(preview_module, "_load_full_preview_qimage", lambda _path: quick.toImage())
        captured = []
        monkeypatch.setattr(panel.canvas, "render_source_pixmap_with_overlays",
                            lambda: captured.append(panel.canvas._bird_box) or quick)
        assert panel.render_source_pixmap_with_overlays() is quick
        assert captured == [box]
        assert panel.canvas._bird_box == original
    finally:
        panel.close()


def test_flock_draws_every_box_with_the_main_bird_emphasised():
    from SuperViewer.superviewer.bird_body_overlay import bird_overlay_boxes, map_bird_overlay

    canvas = _Canvas()
    pixmap = QPixmap(200, 100)
    pixmap.fill(QColor("black"))
    canvas.set_source_pixmap(pixmap)
    canvas.set_show_bird_box(True)
    main, other = (0.05, 0.1, 0.35, 0.9), (0.6, 0.3, 0.8, 0.7)
    canvas.set_bird_box((main, other, (0.5, 0.5, 0.1, 0.1)))  # the invalid one is dropped
    assert canvas._bird_box == (main, other)
    image = canvas.render_source_pixmap_with_overlays().toImage()
    inside_main, inside_other = image.pixelColor(40, 50), image.pixelColor(140, 50)
    assert inside_main.blue() > inside_other.blue() > 20  # both drawn, the main one stronger
    assert image.pixelColor(100, 50) == QColor("black")
    # one box behaves exactly as before
    canvas.set_bird_box((main,))
    assert canvas._bird_box == main
    assert bird_overlay_boxes(None) == () and bird_overlay_boxes(main) == (main,)
    crop = (0.0, 0.0, 0.5, 1.0)
    assert map_bird_overlay(main, crop, map_camera_focus_box) == map_camera_focus_box(main, crop)
    assert map_bird_overlay((main, other), crop, map_camera_focus_box) == (
        map_camera_focus_box(main, crop), map_camera_focus_box(other, crop))
    canvas.close()
