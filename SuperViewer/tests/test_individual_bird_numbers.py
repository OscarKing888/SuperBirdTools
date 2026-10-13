"""逐鸟编号的真实绘制、缓存归属、RAW 映射及 Toggle 交互回归。"""
import json

import pytest
from PIL import Image
from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, map_camera_focus_box
from app_common.toggle_button import ToggleToolButton
from SuperViewer.superviewer.per_bird_identification import FIELD, INFO_FIELD
from SuperViewer.superviewer.per_bird_identification_ui import IndividualBirdsPanel
from SuperViewer.superviewer.preview_panel import PreviewPanel
from SuperViewer.tests.test_directory_selection_responsiveness import window

_APP = QApplication.instance() or QApplication([])


def _metadata(numbers=(10, 3)):
    return {FIELD: json.dumps([
        {'index': number - 1, 'cn_name': '白鹭', 'en_name': 'Egret', 'confidence': 95 - i,
         'detection_confidence': .9, 'status': 'confirmed',
         'box': [.15 + i * .4, .2, .4 + i * .4, .6], 'box_px': [15, 20, 40, 60]}
        for i, number in enumerate(numbers)]),
        INFO_FIELD: json.dumps({'schema': 1, 'coordinate_space': 'oriented_camera_normalized_xyxy'})}


def _pixmap():
    pix = QPixmap(400, 300)
    pix.fill(QColor('black'))
    return pix


class _LabelPainter(QPainter):
    def __init__(self, device):
        super().__init__(device)
        self.labels = []

    def drawText(self, rect, flags, text):
        self.labels.append((text, QRectF(rect)))
        super().drawText(rect, flags, text)


def _paint(panel, draw_rect=QRectF(0, 0, 400, 300)):
    rendered = _pixmap()
    painter = _LabelPainter(rendered)
    panel.canvas._paint_overlay_layers(painter, draw_rect, rendered.rect())
    painter.end()
    return rendered.toImage(), painter.labels


def test_labels_draw_original_numbers_at_fixed_screen_size_and_skip_export():
    panel = PreviewPanel()
    try:
        panel.set_quick_pixmap('鸟.jpg', _pixmap())
        panel.set_photo_metadata(_metadata())
        assert not _paint(panel)[1]
        before = panel.canvas.viewport_state()
        panel.set_show_individual_numbers(True)
        assert panel.canvas.viewport_state() == before
        image, labels = _paint(panel)
        assert [text for text, _ in labels] == ['#10', '#3']
        assert all(image.pixelColor(int(rect.left() + 3), int(rect.top() + 3)) != QColor('black')
                   for _, rect in labels)
        zoomed, enlarged = _paint(panel, QRectF(-60, -60, 800, 600))
        assert [rect.size() for _, rect in enlarged] == [rect.size() for _, rect in labels]
        assert enlarged[1][1].left() > labels[1][1].left()
        assert zoomed != image
        # 显示编号不替代构图网格；导出保留网格而不含预览编号。
        assert panel.canvas.render_source_pixmap_with_overlays().toImage() == _pixmap().toImage()
        panel.set_composition_grid_mode('thirds')
        numbered_export = panel.canvas.render_source_pixmap_with_overlays().toImage()
        panel.set_show_individual_numbers(False)
        assert panel.canvas.render_source_pixmap_with_overlays().toImage() == numbered_export
        assert numbered_export != _pixmap().toImage()
        assert not _paint(panel)[1]
    finally:
        panel.shutdown()
        panel.close()


def test_raw_mapping_playback_and_clear_use_only_cached_records(monkeypatch):
    from SuperViewer.superviewer import per_bird_identification
    panel = PreviewPanel()
    try:
        panel.set_quick_pixmap('鸟.ARW', _pixmap())
        panel.set_photo_metadata(_metadata((7,)))
        panel.set_show_individual_numbers(True)
        crop = (.1, .2, .9, .8)
        image = _pixmap().toImage()
        image.setText(RAW_FOCUS_CROP_KEY, json.dumps(crop))
        panel._on_full_preview_loaded(panel._preview_request_token, '鸟.ARW', image, 1)
        assert panel.canvas._individual_numbers[0][0] == 7
        assert panel.canvas._individual_numbers[0][1] == pytest.approx(
            map_camera_focus_box((.15, .2, .4, .6), crop))
        monkeypatch.setattr(per_bird_identification, 'read_individuals',
                            lambda *_: pytest.fail('长按期间不得解析逐鸟记录'))
        panel.set_navigation_playback_active(True)
        assert not panel.canvas._individual_numbers
        panel.set_quick_pixmap('快切.ARW', _pixmap())
        panel.set_photo_metadata(_metadata((12,)))
        panel.set_show_individual_numbers(False)
        panel.set_show_individual_numbers(True)
        assert not panel.canvas._individual_numbers
        monkeypatch.undo()
        panel.set_navigation_playback_active(False)
        assert panel.canvas._individual_numbers == ((12, (.15, .2, .4, .6)),)
        panel.set_quick_pixmap('无结果.jpg', _pixmap())
        assert not panel.canvas._individual_numbers
        panel.clear_image()
        assert not panel.canvas._individual_numbers
    finally:
        panel.shutdown()
        panel.close()


def test_ab_toggle_sort_late_metadata_and_directory_change(window, tmp_path, monkeypatch):
    paths = [str(tmp_path / name) for name in ('甲.jpg', '乙.jpg')]
    for path, numbers in zip(paths, ((10, 3), (8,))):
        Image.new('RGB', (400, 300), 'black').save(path)
        window._file_list._meta_cache[path] = _metadata(numbers)
    monkeypatch.setattr(window, '_update_preview_focus_box', lambda *args, **kwargs: None)
    window._on_file_selected_from_list(paths[0])
    birds = window.image_info_panel.individual_birds
    assert isinstance(birds.show_numbers, ToggleToolButton)
    assert not birds.show_numbers.isChecked()
    birds.show_numbers.click()
    assert [number for number, _ in window.preview_panel.canvas._individual_numbers] == [10, 3]
    birds.sort_order.setCurrentIndex(1)
    assert [row.bird + 1 for row in birds.birds.rows] == [10, 3]
    birds.sort_order.setCurrentIndex(0)
    assert [row.bird + 1 for row in birds.birds.rows] == [3, 10]
    assert [number for number, _ in window.preview_panel.canvas._individual_numbers] == [10, 3]
    window.ab_preview.enabled.setChecked(True)
    window.ab_preview.activate('a')
    window._on_file_selected_from_list(paths[1])
    assert [number for number, _ in window.preview_a.canvas._individual_numbers] == [8]
    assert [number for number, _ in window.preview_panel.canvas._individual_numbers] == [10, 3]
    window._file_list._on_metadata_batch_ready({paths[0]: _metadata((5,))})
    assert [number for number, _ in window.preview_panel.canvas._individual_numbers] == [5]
    assert [number for number, _ in window.preview_a.canvas._individual_numbers] == [8]
    # 清空活动侧（切目录）仍保留另一侧；折叠信息面板不关闭 Toggle。
    window.preview_a.clear_image()
    birds.set_metadata('', {})
    birds.hide()
    assert birds.show_numbers.isChecked()
    assert not window.preview_a.canvas._individual_numbers
    assert [number for number, _ in window.preview_panel.canvas._individual_numbers] == [5]
    state = window.preview_panel.canvas.viewport_state()
    with monkeypatch.context() as isolated:
        isolated.setattr(window._file_list, 'cached_preview_metadata_for_path',
                         lambda *_: pytest.fail('Toggle 不应重新读取元数据'))
        birds.show_numbers.click()
        assert not window.preview_panel.canvas._individual_numbers
        birds.show_numbers.click()
        assert [number for number, _ in window.preview_panel.canvas._individual_numbers] == [5]
        assert window.preview_panel.canvas.viewport_state() == state


@pytest.mark.parametrize('dark,points,width', [(False, 13, 300), (True, 20, 400)])
def test_toggle_layout_keyboard_and_state_retention(tmp_path, dark, points, width):
    panel = IndividualBirdsPanel()
    palette = QPalette(panel.palette())
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Button):
        palette.setColor(role, QColor('#252525' if dark else '#f3f3f3'))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(role, QColor('#eeeeee' if dark else '#202020'))
    try:
        panel.setPalette(palette)
        panel.setFont(QFont('Arial', points))
        panel.set_metadata('鸟.jpg', _metadata())
        panel.resize(width, 600)
        panel.show()
        _APP.processEvents()
        button = panel.show_numbers
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert button.isChecked()
        for _ in range(2):
            panel.hide()
            panel.show()
            panel.set_metadata('鸟.jpg', _metadata())
        _APP.processEvents()
        assert button.isChecked()
        assert button.width() >= button.sizeHint().width()
        assert panel.rect().contains(button.geometry())
        assert panel.sort_bar.geometry().bottom() < button.geometry().top()
        assert button.mapTo(panel, QPoint(button.width(), button.height())).x() <= width
        assert panel.grab().save(str(tmp_path / 'number-toggle.png'))
    finally:
        panel.close()
