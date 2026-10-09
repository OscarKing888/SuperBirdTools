"""真实 Qt 结果列表、悬停绘制、A/B 和 RAW 升级的身份/几何验证。"""
import json
from types import SimpleNamespace

from PIL import Image
import pytest
from PyQt6.QtCore import QEvent, QRectF
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, map_camera_focus_box
from SuperViewer.superviewer.bird_identification import BirdIDOptions
from SuperViewer.superviewer.per_bird_identification import PerBirdOptions, FIELD, INFO_FIELD
from SuperViewer.superviewer.per_bird_identification_ui import (
    IndividualBirdsPanel, IndividualBirdHover, PerBirdSettingsDialog)
from SuperViewer.superviewer.preview_panel import PreviewPanel

_APP = QApplication.instance() or QApplication([])


def metadata():
    return {FIELD: json.dumps([{"index": 0, "cn_name": "白鹭", "en_name": "Egret", "confidence": 95,
             "detection_confidence": .8, "status": "confirmed", "box": [.2, .3, .7, .8],
             "box_px": [20, 30, 70, 80]}]),
            INFO_FIELD: json.dumps({"schema": 1, "coordinate_space": "oriented_camera_normalized_xyxy"})}


def test_settings_defaults_and_adjustments():
    dialog = PerBirdSettingsDialog(None, BirdIDOptions(), PerBirdOptions(), {"image_source": "jpeg"})
    try:
        assert dialog.width.value() == dialog.height.value() == 64
        dialog.width.setValue(128)
        dialog.height.setValue(96)
        dialog.padding.setValue(10)
        dialog.threshold.setValue(80)
        assert dialog.per_bird_options() == PerBirdOptions(128, 96, 10)
        assert dialog.options().threshold == 80
    finally:
        dialog.close()


def test_badges_columns_and_hover_share_row(monkeypatch):
    from SuperViewer.superviewer.rarity_badge import RarityBadge, ConservationBadge
    panel = IndividualBirdsPanel()
    saved = metadata()
    items = json.loads(saved[FIELD])
    items[0].update(gbif_rarity_100=0, iucn_category='EN')
    saved[FIELD] = json.dumps(items)
    events = []
    panel.hovered.connect(lambda path, row: events.append(row))
    try:
        panel.set_metadata('鸟.jpg', saved)
        panel.show()
        _APP.processEvents()
        rarity = panel.findChild(RarityBadge)
        conservation = panel.findChild(ConservationBadge)
        assert rarity.text() == '普通' and conservation.text() == 'EN · 濒危'
        assert panel.birds.rows[0].color == '#64748B'
        assert '#64748B' in panel.birds.cells[0][0].text()
        name = panel.birds.cells[0][2]
        detail = panel.birds.grid.itemAtPosition(0, 3).widget()
        assert '白鹭' in name.text() and '检测' not in name.text()
        assert detail.geometry().left() >= name.geometry().right()
        styles = rarity.styleSheet(), conservation.styleSheet()
        for widget in (rarity, conservation, detail):
            _APP.sendEvent(widget, QEvent(QEvent.Type.Enter))
            assert events[-1].bird == 0
            assert panel.birds.hovered_row == 0
            _APP.sendEvent(widget, QEvent(QEvent.Type.Leave))
            if widget is not detail:
                assert panel.birds.hovered_row == 0  # 进入同一信息列的留白仍保持悬停。
                _APP.sendEvent(detail, QEvent(QEvent.Type.Leave))
            assert events[-1] is None
        assert styles == (rarity.styleSheet(), conservation.styleSheet())
        _APP.sendEvent(rarity, QEvent(QEvent.Type.Enter))
        monkeypatch.setattr('SuperViewer.superviewer.rarity_badge.get_runtime_user_options',
                            lambda: {'rarity_badge_common_background': '#123456'})
        panel.set_metadata('鸟.jpg', saved)
        assert events[-1].color == '#123456'
        assert '#123456' in rarity.styleSheet() and '#123456' in panel.birds.cells[0][0].text()
        # 全图元数据不得为缺失的逐只数据提供徽章。
        missing = metadata()
        missing.update(gbif_rarity_100=90, iucn_category='CR')
        panel.set_metadata('另一鸟.jpg', missing)
        badges = panel.birds._detail_cells[0]
        assert badges.findChild(RarityBadge).text() == '未知'
        assert badges.findChild(ConservationBadge).text() == '未知'
        assert panel.birds.rows[0].color == '#6B7280'
    finally:
        panel.close()


def test_hover_ab_raw_upgrade_leave_switch_and_playback(tmp_path, monkeypatch):
    path, other = str(tmp_path / '鸟.ARW'), str(tmp_path / 'other.jpg')
    left, right = PreviewPanel(), PreviewPanel()
    birds = IndividualBirdsPanel()
    hover = IndividualBirdHover(SimpleNamespace(individual_birds=birds), (left, right))
    pix = QPixmap(100, 100)
    pix.fill(QColor('black'))
    try:
        left.set_quick_pixmap(path, pix)
        right.set_quick_pixmap(other, pix)
        birds.set_metadata(path, metadata())
        birds.show()
        _APP.processEvents()
        # 真正的 Enter / Leave 事件走共用 TraceBirdList.eventFilter。
        cell = birds.birds.cells[0][2]
        _APP.sendEvent(cell, QEvent(QEvent.Type.Enter))
        assert '白鹭' in cell.text() and '95.0%' in cell.text()
        assert left.canvas._individual_highlight[0] == (.2, .3, .7, .8)
        assert left.canvas._individual_highlight[1] == '#6B7280'
        assert right.canvas._individual_highlight is None
        crop = (.1, .2, .9, .8)
        image = pix.toImage()
        image.setText(RAW_FOCUS_CROP_KEY, json.dumps(crop))
        left._on_full_preview_loaded(left._preview_request_token, path, image, 1)
        assert left.canvas._individual_highlight[0] == pytest.approx(map_camera_focus_box((.2, .3, .7, .8), crop))
        _APP.sendEvent(cell, QEvent(QEvent.Type.Leave))
        assert left.canvas._individual_highlight is None
        _APP.sendEvent(cell, QEvent(QEvent.Type.Enter))
        left.set_quick_pixmap(other, pix)
        assert left.canvas._individual_highlight is None
        left.set_quick_pixmap(path, pix)
        left.set_navigation_playback_active(True)
        hover.highlight(path, birds.birds.rows[0])
        assert left.canvas._individual_highlight is None
        left.set_navigation_playback_active(False)
        hover.highlight(path, birds.birds.rows[0])
        birds.hide()
        assert left.canvas._individual_highlight is None
        # report.db 的临时 JPEG 显示路径仍映射到实际 RAW；悬停不探测磁盘。
        temporary = str(tmp_path / 'cached.jpg')
        left.set_quick_pixmap(temporary, pix)
        left.set_source_identity(path)
        right.set_quick_pixmap(path, pix)
        monkeypatch.setattr('os.path.isfile', lambda *_: pytest.fail('hover must not probe files'))
        hover.highlight(temporary, birds.birds.rows[0])
        assert left.canvas._individual_highlight is not None
        assert right.canvas._individual_highlight is not None
    finally:
        for widget in (birds, left, right): widget.close()


def test_highlight_draws_without_bird_toggle_and_is_excluded_from_export():
    panel = PreviewPanel()
    pix = QPixmap(100, 100)
    pix.fill(QColor('black'))
    try:
        panel.set_quick_pixmap('bird.jpg', pix)
        panel.set_individual_bird_highlight((.2, .2, .8, .8), '#00c8ff')
        rendered = pix.copy()
        painter = QPainter(rendered)
        panel.canvas._paint_overlay_layers(painter, QRectF(0, 0, 100, 100), rendered.rect())
        painter.end()
        assert rendered.toImage().pixelColor(50, 50).blue() > 0
        assert rendered.toImage().pixelColor(5, 5) == QColor('black')
        assert panel.canvas.render_source_pixmap_with_overlays().toImage().pixelColor(50, 50) == QColor('black')
        assert panel.canvas._individual_highlight is not None
        # 原有构图网格仍参与导出。
        panel.set_composition_grid_mode('thirds')
        grid = panel.canvas.render_source_pixmap_with_overlays().toImage()
        assert any(grid.pixelColor(x, 20) != QColor('black') for x in range(30, 36))
    finally:
        panel.close()


def test_info_panel_reloads_saved_list_without_resetting_drafts(tmp_path):
    from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
    path = str(tmp_path / '鸟.jpg')
    Image.new('RGB', (100, 100)).save(path)
    data = {}
    panel = ImageInfoTabPanel_ImageInfo(lambda: [], lambda _: set(), lambda *_: None,
        lambda *_: '', metadata_provider=lambda _: data)
    try:
        panel.on_photo_selected(path)
        panel.comment_edit.setPlainText('正在输入')
        data.update(metadata())
        panel.refresh_metadata_fields()
        assert len(panel.individual_birds.birds.rows) == 1
        assert panel.comment_edit.toPlainText() == '正在输入'
        panel.on_photo_selected('')
        assert panel.individual_birds.birds.rows == []
    finally:
        panel.close()


def test_detection_override_is_independent_and_can_return_to_global():
    from SuperViewer.superviewer.per_bird_identification import make_analyzer
    params = {'image_source': 'jpeg', 'detector': 'yolo11l-seg.pt', 'detect_conf_percent': 25}
    dialog = PerBirdSettingsDialog(None, BirdIDOptions(), PerBirdOptions(), params)
    try:
        assert not dialog.override.isChecked()
        dialog.override.setChecked(True)
        form = dialog.analysis_form
        form.detector.setCurrentIndex(form.detector.findData('yolov8x-seg.pt'))
        form.detect_long_edge.setValue(2048)
        form.detect_conf_percent.setValue(10)
        form.duplicate_mask_percent.setValue(90)
        form.flock_mode.setCurrentIndex(form.flock_mode.findData('off'))
        form.exclude_birds.setChecked(False)
        options = dialog.per_bird_options()
        analyzer = make_analyzer(params, options=options)
        assert analyzer.params.detector == 'yolov8x-seg.pt'
        assert analyzer.params.detect_conf_percent == 10 and analyzer.params.duplicate_mask_percent == 90
        assert analyzer.params.flock_mode == 'off' and not analyzer.params.exclude_birds
        assert analyzer.params.max_birds == analyzer.params.min_bird_side == 0
        assert params['detector'] == 'yolo11l-seg.pt' and params['detect_conf_percent'] == 25
        restored = PerBirdSettingsDialog(None, BirdIDOptions(), options, params)
        try:
            assert restored.override.isChecked() and restored.analysis_form.detect_conf_percent.value() == 10
        finally:
            restored.close()
        dialog.override.setChecked(False)
        assert dialog.per_bird_options().analysis_overrides is None
        assert make_analyzer(params, options=dialog.per_bird_options()).params.detect_conf_percent == 25
    finally:
        dialog.close()


@pytest.mark.parametrize('percent', [50, 200])
def test_hover_center_moves_only_matching_photo_and_preserves_scale(percent, tmp_path, monkeypatch):
    from SuperViewer.superviewer.viewer_ab_preview import ViewerViewportPanel
    path, other = str(tmp_path / '群鸟.jpg'), str(tmp_path / '另一张.jpg')
    left, right = PreviewPanel(), PreviewPanel()
    from SuperViewer.superviewer.video_preview import MediaPreviewPanel
    left.close()
    left = MediaPreviewPanel()
    toolbar = ViewerViewportPanel('A', left)
    birds = IndividualBirdsPanel()
    hover = IndividualBirdHover(SimpleNamespace(individual_birds=birds), (left, right))
    pix = QPixmap(1200, 800); pix.fill(QColor('black'))
    for panel, photo in ((left, path), (right, other)):
        panel.resize(600, 420); panel.show()
        panel.set_quick_pixmap(photo, pix)
        panel.set_display_scale_percent(percent)
    _APP.processEvents()
    try:
        birds.set_metadata(path, metadata()); birds.show(); _APP.processEvents()
        row = birds.birds.rows[0]
        before = left.canvas.viewport_state()
        hover.highlight(path, row)
        assert left.canvas.viewport_state() == before  # 默认关闭。
        toolbar.bird_hover_center.setChecked(True)
        # 只重用已缓存鸟框，不应加载图片或查询元数据。
        monkeypatch.setattr(left, 'set_image', lambda *a, **kw: pytest.fail('hover must not load'))
        before_right = right.canvas.viewport_state()
        scale, zoom = left.current_display_scale_percent(), left.canvas._zoom
        # 边缘鸟需要允许留白，不能用常规拖动边界把居中截断。
        from dataclasses import replace
        edge = replace(row, box=(.86, .02, .98, .12))
        hover.highlight(path, edge)
        assert left.canvas._view_center_ratio() == pytest.approx((.92, .07))
        assert left.current_display_scale_percent() == pytest.approx(scale)
        assert left.canvas._zoom == zoom
        assert right.canvas.viewport_state() == before_right
        # 焦点刷新不能抢走悬停定位，缩放仍不变。
        left.set_auto_focus_center(True)
        left.set_focus_box((.1, .6, .2, .7))
        assert left.canvas._view_center_ratio() == pytest.approx((.92, .07))
        assert left.current_display_scale_percent() == pytest.approx(scale)
        hover.clear()
        assert left.canvas._individual_highlight is None
        assert left.canvas._view_center_ratio() == pytest.approx((.92, .07))
        toolbar.bird_hover_center.setChecked(False)
        state = left.canvas.viewport_state()
        hover.highlight(path, row)
        assert left.canvas.viewport_state() == state
        left.set_navigation_playback_active(True)
        toolbar.bird_hover_center.setChecked(True)
        hover.refresh()
        assert left.canvas._individual_highlight is None
    finally:
        for widget in (birds, toolbar, right): widget.close()
        left.shutdown(); right.shutdown()


def test_hover_center_reapplies_raw_mapping_after_upgrade_without_zoom_change(tmp_path):
    path = str(tmp_path / '鸟.ARW')
    panel = PreviewPanel(); panel.resize(640, 480); panel.show(); _APP.processEvents()
    birds = IndividualBirdsPanel()
    hover = IndividualBirdHover(SimpleNamespace(individual_birds=birds), (panel,))
    try:
        pix = QPixmap(1200, 800); pix.fill(QColor('black'))
        panel.set_quick_pixmap(path, pix, quick_size=2048)
        panel.set_keep_view_on_switch(False)
        panel.set_display_scale_percent(200)
        panel.set_individual_bird_auto_center(True)
        birds.set_metadata(path, metadata())
        hover.highlight(path, birds.birds.rows[0])
        zoom = panel.canvas._zoom
        crop = (.1, .2, .9, .8)
        image = pix.toImage(); image.setText(RAW_FOCUS_CROP_KEY, json.dumps(crop))
        panel._on_full_preview_loaded(panel._preview_request_token, path, image, 1)
        mapped = map_camera_focus_box((.2, .3, .7, .8), crop)
        assert panel.canvas._view_center_ratio() == pytest.approx(((mapped[0]+mapped[2])/2, (mapped[1]+mapped[3])/2))
        assert panel.canvas._zoom == zoom
        panel.clear_image()
        assert panel.canvas._individual_highlight is None
    finally:
        panel.shutdown(); panel.close(); birds.close()
