"""Independent page, source coordinates, workspace and real Qt worker ownership."""
from copy import deepcopy
from threading import Event

from PIL import Image
import pytest
from PyQt6.QtCore import Qt, QPointF, QEvent
from PyQt6.QtTest import QTest

from test_overlay_editor import window, _APP, mouse
from test_editor_collage import wait_for
from birdstamp.gui.editor_utils import path_key
from birdstamp.seamless_split import XHS_FULLSCREEN_RATIO


@pytest.fixture(autouse=True)
def visual_environment(request):
    if 'test_rendered_geometry' not in request.node.name:
        yield
        return
    import os
    from pathlib import Path
    from PyQt6.QtGui import QColor, QPalette, QFont, QFontDatabase
    old_palette, old_font = QPalette(_APP.palette()), QFont(_APP.font())
    palette = QPalette(old_palette)
    dark = request.node.callspec.params['dark']
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Button, QPalette.ColorRole.Base):
        palette.setColor(role, QColor('#252525' if dark else '#f5f5f5'))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText, QPalette.ColorRole.Text):
        palette.setColor(role, QColor('#eeeeee' if dark else '#202020'))
    _APP.setPalette(palette)
    windows = os.environ.get('WINDIR')
    if windows:
        font_path = Path(windows) / 'Fonts' / 'msyh.ttc'
        if font_path.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                _APP.setFont(QFont(families[0]))
    yield
    _APP.setPalette(old_palette)
    _APP.setFont(old_font)


def source(tmp_path, name='风景.png', color='red'):
    path = tmp_path / name
    with Image.new('RGB', (600, 400), color) as image:
        image.save(path)
    return path


def ready(split):
    wait_for(lambda: split.worker is None and not split.pending)
    assert split._valid, split.status.text()


def test_page_workspace_ratios_and_navigation(window, tmp_path):
    split = window.seamless_split
    window.show()
    window.export_tabs.setCurrentWidget(split.page)
    assert window.preview_pages.currentWidget() is split.preview_host
    assert window.export_action_bar.actions.currentWidget() is split.actions
    assert not window.export_action_bar.settings_section.isVisible()
    split.set_source(source(tmp_path))
    ready(split)
    assert split.options().size == (3240, 1440)
    original_crop = split.crop()
    split.count.setValue(4)
    ready(split)
    assert split.crop() != original_crop
    split.single.setChecked(True)
    split.preview.canvas.setFocus()
    QTest.keyClick(split.preview.canvas, Qt.Key.Key_Right)
    assert split.page_index == 1
    saved = split.state(tmp_path / '工作区.json')
    split.restore(None, tmp_path / '工作区.json')
    assert split.path is None
    split.restore(saved, tmp_path / '工作区.json')
    ready(split)
    assert split.state(tmp_path / '工作区.json') == saved
    window.export_tabs.setCurrentIndex(0)
    window.export_tabs.setCurrentWidget(split.page)
    assert split.crop() == tuple(saved['crops'][0]['box'])
    assert split.page_index == 1


def test_defaults_ask_each_export_and_current_slice(window, tmp_path, monkeypatch):
    import birdstamp.gui.editor_seamless_split as module
    from test_seamless_split import stub_metadata
    stub_metadata(monkeypatch)
    split = window.seamless_split
    window.show()
    window.export_tabs.setCurrentWidget(split.page)
    assert split.output_form.mode.currentData() == 'ask'
    assert split.options().format == 'png'
    assert not split.quality.isEnabled()
    assert not split.export_current_button.isEnabled()
    split.set_source(source(tmp_path))
    split.ratio.setCurrentIndex(split.ratio.findData(XHS_FULLSCREEN_RATIO))
    split.width.setValue(128)
    ready(split)
    assert (split.options().width, split.options().height) == (128, 217)
    assert not split.export_current_button.isEnabled()
    split.single.setChecked(True)
    split.move_page(1)
    assert split.export_current_button.isEnabled()
    assert '2/3' in split.export_current_button.text()
    destinations = iter(['', str(tmp_path / '单张'), str(tmp_path / '整组')])
    asked = []
    def pick(*args):
        asked.append(args)
        return next(destinations)
    monkeypatch.setattr(module.QFileDialog, 'getExistingDirectory', pick)
    split.export_current_button.click()
    assert split.worker is None
    assert not (tmp_path / '单张').exists()
    split.export_current_button.setFocus()
    QTest.keyClick(split.export_current_button, Qt.Key.Key_Space)
    assert split.exporting()
    assert not split.export_current_button.isEnabled()
    assert not split.export_button.isEnabled()
    assert not split.pager.isEnabled()
    assert split.worker.page_index == 1
    wait_for(lambda: split.worker is None)
    assert [p.name for p in (tmp_path / '单张').rglob('*.png')] == ['风景_02.png']
    assert '已导出第 2 张' in split.status.text()
    assert [p.name for p in (tmp_path / '单张').rglob('*.xmp')] == ['风景_02.xmp']
    split.export_button.click()
    wait_for(lambda: split.worker is None)
    assert len(list((tmp_path / '整组').rglob('*.png'))) == 3
    one = next((tmp_path / '单张').rglob('*.png'))
    grouped = next((tmp_path / '整组').rglob('*_02.png'))
    assert one.read_bytes() == grouped.read_bytes()
    assert len(asked) == 3
    assert split.output_form.mode.currentData() == 'ask'


def test_restore_preserves_explicit_output_settings(window, tmp_path):
    split = window.seamless_split
    split.output_form.mode.setCurrentIndex(split.output_form.mode.findData('fixed'))
    split.output_form.directory.setText(str(tmp_path))
    split.output_form.format.setCurrentIndex(split.output_form.format.findData('jpeg'))
    saved = split.state(tmp_path / 'session.json')
    split.restore({}, tmp_path / 'session.json')
    assert split.output_form.mode.currentData() == 'ask'
    assert split.options().format == 'png'
    split.restore(saved, tmp_path / 'session.json')
    assert split.output_form.mode.currentData() == 'fixed'
    assert split.output_form.directory.text() == str(tmp_path)
    assert split.options().format == 'jpeg'
    assert split.quality.isEnabled()


def test_xhs_fullscreen_preset_dimensions_crop_and_restore(window, tmp_path):
    split = window.seamless_split
    window.show()
    window.export_tabs.setCurrentWidget(split.page)
    split.set_source(source(tmp_path))
    ready(split)
    before = split.crop()
    index = split.ratio.findData(XHS_FULLSCREEN_RATIO)
    assert index >= 0 and '小红书全屏（校准）' in split.ratio.itemText(index)
    split.ratio.setCurrentIndex(index - 1)
    split.ratio.setFocus()
    QTest.keyClick(split.ratio, Qt.Key.Key_Down)
    ready(split)
    assert split.ratio.currentData() == XHS_FULLSCREEN_RATIO
    assert split.ratio_hint.isVisible()
    assert (split.width.value(), split.height.value()) == (1080, 1826)
    assert split.options().size == (3240, 1826)
    left, top, right, bottom = split.crop()
    assert (left + right, top + bottom) == pytest.approx((before[0] + before[2], before[1] + before[3]))
    assert (right - left) * 600 / ((bottom - top) * 400) == pytest.approx(3240 / 1826)
    split.resolution.setCurrentIndex(split.resolution.findData(1440))
    ready(split)
    assert (split.width.value(), split.height.value()) == (1440, 2435)
    split.height.setValue(2000)
    ready(split)
    assert (split.width.value(), split.height.value()) == (1182, 2000)
    split.layout_group.header_button.click()
    split.layout_group.header_button.click()
    saved = split.state(tmp_path / '全屏.json')
    split.restore({}, tmp_path / '全屏.json')
    assert split.ratio.currentData() == '3:4'
    assert split.ratio_hint.isHidden()
    split.restore(saved, tmp_path / '全屏.json')
    ready(split)
    assert split.state(tmp_path / '全屏.json') == saved
    assert split.ratio_hint.isVisible()
    split.ratio.setCurrentIndex(split.ratio.findData('9:16'))
    ready(split)
    assert (split.width.value(), split.height.value()) == (1182, 2101)
    assert split.ratio_hint.isHidden()


def test_photo_list_routing_and_full_workspace_restore(window, tmp_path):
    split = window.seamless_split
    window.export_tabs.setCurrentWidget(split.page)
    photos = [source(tmp_path, 'LandscapeA.png'), source(tmp_path, 'LandscapeB.png', 'blue')]
    for photo in photos:
        window._append_photo_path_to_list(photo, existing_keys=set(), default_settings=window._build_current_render_settings())
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(photos[1]))
    ready(split)
    assert split.path == photos[1]
    split.count.setValue(4)
    ready(split)
    split.single.setChecked(True)
    split.move_page(2)
    workspace = tmp_path / '会话.birdstamp-workspace.json'
    payload = window._collect_workspace_payload(workspace)
    assert payload['editor_state']['seamless_split_active']
    window._restore_workspace_payload(payload, workspace, mark_as_current_workspace=False, autosave_after_restore=False)
    wait_for(lambda: window._workspace_restore_context is None)
    ready(split)
    assert split.active()
    assert split.path == photos[1]
    assert split.count.value() == 4
    assert split.single.isChecked() and split.page_index == 2


def test_source_change_invalidates_proxy(window, tmp_path):
    split = window.seamless_split
    photo = source(tmp_path)
    split.set_source(photo)
    ready(split)
    with Image.new('RGB', (800, 500), 'blue') as changed:
        changed.save(photo)
    split.set_source(photo)
    ready(split)
    assert split.source_size == (800, 500)
    assert split.source.getpixel((0, 0)) == (0, 0, 255)


def test_drag_undo_and_per_photo_crop(window, tmp_path):
    split = window.seamless_split
    window.show()
    window.export_tabs.setCurrentWidget(split.page)
    first, second = source(tmp_path), source(tmp_path, '第二张.png', 'blue')
    split.set_source(first)
    ready(split)
    canvas = split.preview.canvas
    before = split.crop()
    draw = canvas._display_rect()
    start = QPointF(draw.center())
    canvas.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, start))
    canvas.mouseMoveEvent(mouse(QEvent.Type.MouseMove, start + QPointF(0, 25)))
    canvas.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, start + QPointF(0, 25)))
    ready(split)
    moved = split.crop()
    assert moved != before
    assert len(split.undo_stacks[path_key(first)]) == 1
    split.undo_crop()
    ready(split)
    assert split.crop() == pytest.approx(before)
    split.undo_crop(True)
    ready(split)
    assert split.crop() == pytest.approx(moved)
    split.set_source(second)
    ready(split)
    assert split.crop() == pytest.approx(before)
    split.set_source(first)
    ready(split)
    assert split.crop() == pytest.approx(moved)


def test_invalid_options_contain_and_fold_state(window, tmp_path):
    split = window.seamless_split
    window.show()
    window.export_tabs.setCurrentWidget(split.page)
    split.set_source(source(tmp_path))
    ready(split)
    before = split.crop()
    split.fit.setCurrentIndex(1)
    ready(split)
    assert not split.preview.canvas.crop_edit_mode()
    assert not split.crop_tool.isEnabled()
    split.fit.setCurrentIndex(0)
    ready(split)
    assert split.crop() == pytest.approx(before)
    snapshot = deepcopy(split.state(tmp_path / '工作区.json'))
    for section in (split.layout_group, split.framing_group, split.output_group):
        for _ in range(2):
            section.header_button.click()
            section.header_button.click()
    assert split.state(tmp_path / '工作区.json') == snapshot
    split.ratio.setCurrentIndex(split.ratio.findData('custom'))
    split.width.setValue(8192)
    split.height.setValue(8192)
    assert not split.export_button.isEnabled()
    assert '1 亿像素' in split.status.text()
    split.width.setValue(128)
    split.height.setValue(128)
    ready(split)
    assert split.export_button.isEnabled()


def test_stale_worker_and_shutdown(window, tmp_path, monkeypatch):
    import birdstamp.gui.editor_seamless_split as module
    split = window.seamless_split
    entered, release = Event(), Event()
    real_load = module.load_preview
    calls = []
    def delayed(path):
        calls.append(path)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return real_load(path)
    monkeypatch.setattr(module, 'load_preview', delayed)
    split.set_source(source(tmp_path))
    wait_for(entered.is_set)
    first_worker = split.worker
    second = source(tmp_path, '新的.png', 'blue')
    split.set_source(second)
    assert split.worker is first_worker
    release.set()
    ready(split)
    assert split.path == second
    assert split.source.getpixel((0, 0)) == (0, 0, 255)
    split.count.setValue(4)
    wait_for(lambda: split.worker is not None)
    assert not split.shutdown()
    wait_for(lambda: split.worker is None)
    assert split.shutdown()
    assert split.source is None


def test_export_freezes_settings_and_workspace_cancels_old_result(window, tmp_path, monkeypatch):
    import birdstamp.gui.editor_seamless_split as module
    from test_seamless_split import stub_metadata
    stub_metadata(monkeypatch)
    split = window.seamless_split
    split.set_source(source(tmp_path, 'UpperCASE.png'))
    split.width.setValue(128)
    ready(split)
    entered, release = Event(), Event()
    real_export = module.export_split
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_export(*args, **kwargs)
    monkeypatch.setattr(module, 'export_split', delayed)
    split.start_export(tmp_path / '成片')
    wait_for(entered.is_set)
    assert not split.page.isEnabled()
    assert not split.preview.canvas.isEnabled()
    assert split.cancel_button.isEnabled()
    split.restore(None, tmp_path / '新工作区.json')
    old = split.worker
    assert old is not None
    release.set()
    wait_for(lambda: split.worker is None)
    assert split.path is None
    assert not list((tmp_path / '成片').glob('*'))
    split.set_source(source(tmp_path, 'NewPhoto.png'))
    ready(split)
    split.start_export(tmp_path / '成片')
    wait_for(lambda: split.worker is None)
    assert len(list((tmp_path / '成片').rglob('*.png'))) == 3
    assert len(list((tmp_path / '成片').rglob('*.xmp'))) == 3
    assert '已导出 3 张' in split.status.text()
    saved = split.state(tmp_path / '工作区.json')
    assert saved['crops'][0]['path']['absolute_path'].endswith('NewPhoto.png')


@pytest.mark.parametrize('dark,large', [(False, False), (True, True)])
@pytest.mark.parametrize('xhs', [False, True])
def test_rendered_geometry_small_window_font_theme(window, tmp_path, dark, large, xhs):
    import os
    from pathlib import Path
    from test_seamless_split import gradient
    split = window.seamless_split
    if large:
        window.setStyleSheet(window.styleSheet() + '\nQWidget { font-size: 24px; }')
    window.resize(1120 if large else 1400, 720 if large else 900)
    window.show()
    window.export_tabs.setCurrentWidget(split.page)
    photo = tmp_path / '山景.png'
    with gradient((1200, 800)) as image:
        image.save(photo)
    split.set_source(photo)
    if xhs:
        split.ratio.setCurrentIndex(split.ratio.findData(XHS_FULLSCREEN_RATIO))
    ready(split)
    window._photos_section.set_expanded(False)
    folder = os.environ.get('SPLIT_SCREENSHOT_DIR')
    prefix = 'xhs-' if xhs else ''
    if folder:
        directory = Path(folder)
        directory.mkdir(parents=True, exist_ok=True)
    if xhs:
        window.left_scroll.ensureWidgetVisible(split.ratio_hint)
        _APP.processEvents()
        if folder:
            window.grab().save(str(directory / ('xhs-settings-dark.png' if dark else 'xhs-settings-light.png')))
        from PyQt6.QtWidgets import QStyle, QStyleOptionComboBox
        option = QStyleOptionComboBox()
        split.ratio.initStyleOption(option)
        text_rect = split.ratio.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                                     QStyle.SubControl.SC_ComboBoxEditField, split.ratio)
        assert text_rect.width() >= split.ratio.fontMetrics().horizontalAdvance(split.ratio.currentText())
        assert split.ratio_hint.height() >= split.ratio_hint.heightForWidth(split.ratio_hint.width())
    window.left_scroll.verticalScrollBar().setValue(window.left_scroll.verticalScrollBar().maximum())
    _APP.processEvents()
    assert window.rect().contains(split.export_button.mapTo(window, split.export_button.rect().bottomRight()))
    assert window.rect().contains(split.export_current_button.mapTo(window, split.export_current_button.rect().bottomRight()))
    assert window.rect().contains(split.cancel_button.mapTo(window, split.cancel_button.rect().bottomRight()))
    for control in (split.whole, split.single, split.crop_tool, split.lines, split.zoom):
        assert split.tools.rect().contains(control.mapTo(split.tools, control.rect().bottomRight()))
    before = split.state(tmp_path / '保存.json')
    if folder:
        window.grab().save(str(directory / (prefix + ('split-dark-large.png' if dark else 'split-light.png'))))
    split.single.setChecked(True)
    _APP.processEvents()
    assert split.export_current_button.isEnabled()
    assert split.export_current_button.width() >= split.export_current_button.sizeHint().width()
    if folder:
        window.grab().save(str(directory / (prefix + ('split-single-dark.png' if dark else 'split-single-light.png'))))
    assert split.crop() == tuple(before['crops'][0]['box'])


def test_calibration_inputs_validation_retention_and_legacy_restore(window, tmp_path):
    split = window.seamless_split
    workspace = tmp_path / '校准.json'
    split.set_source(source(tmp_path))
    ready(split)
    split.ratio.setCurrentIndex(split.ratio.findData('3:5'))
    ready(split)
    assert split.ratio.currentText() == '3:5'
    assert split.options().size == (3240, 1800)
    legacy = split.state(workspace)
    del legacy['fullscreen_calibration']
    crop = split.crop()
    split.restore(legacy, workspace)
    ready(split)
    assert split.ratio.currentData() == '3:5'
    assert split.options().size == (3240, 1800)
    assert split.crop() == pytest.approx(crop)
    assert split.calibration_fields.isHidden()
    split.ratio.setCurrentIndex(split.ratio.findData(XHS_FULLSCREEN_RATIO))
    ready(split)
    split.single.setChecked(True)
    for invalid, message in [('', '正整数'), ('0', '正整数'), ('-5', '正整数'), ('1.5', '正整数'), ('1280', '大于宽度')]:
        split.display_height.setText(invalid)
        assert message in split.status.text()
        assert not split.export_button.isEnabled()
        assert not split.export_current_button.isEnabled()
        assert not split.pending
        snapshot = split.state(workspace)
        split.restore(snapshot, workspace)
        assert split.state(workspace) == snapshot
        assert not split.export_button.isEnabled()
    split.display_height.setText('99999')
    assert '8192' in split.status.text()
    assert '84375' in split.dimensions.text()
    invalid_size = split.state(workspace)
    assert invalid_size['options']['height'] == 84375
    split.restore(invalid_size, workspace)
    assert split.state(workspace) == invalid_size
    assert not split.export_button.isEnabled()
    split.display_height.setText('2200')
    ready(split)
    assert split.options().height == 1857
    snapshot = split.state(workspace)
    for _ in range(2):
        split.layout_group.header_button.click()
        split.layout_group.header_button.click()
    assert split.state(workspace) == snapshot
    split.ratio.setCurrentIndex(split.ratio.findData('3:4'))
    ready(split)
    assert split.display_height.text() == '2200'
    split.restore(split.state(workspace), workspace)
    ready(split)
    assert split.display_height.text() == '2200'
    split.ratio.setCurrentIndex(split.ratio.findData(XHS_FULLSCREEN_RATIO))
    ready(split)
    assert split.options().height == 1857
    split.restore({'ratio': XHS_FULLSCREEN_RATIO}, workspace)
    assert split.options().height == 1826
    split.restore({'ratio': XHS_FULLSCREEN_RATIO, 'fullscreen_calibration': {'height': 2200}}, workspace)
    assert split.options().height == 1857
    split.display_height.setText('')
    split.width.setValue(1440)
    snapshot = split.state(workspace)
    assert snapshot['options']['width'] == 1440
    split.restore(snapshot, workspace)
    assert split.state(workspace) == snapshot
    assert not split.export_button.isEnabled()
    split.restore({}, workspace)
    assert split.ratio.currentData() == '3:4'
    assert split.calibration() == dict(width=1280, height=2164)


def test_calibration_rounding_resolution_and_output_limits(window, tmp_path):
    split = window.seamless_split
    split.set_source(source(tmp_path))
    split.ratio.setCurrentIndex(split.ratio.findData(XHS_FULLSCREEN_RATIO))
    for width, height in [(1440, 2435), (2160, 3652), (1080, 1826)]:
        split.resolution.setCurrentIndex(split.resolution.findData(width))
        ready(split)
        assert (split.options().width, split.options().height) == (width, height)
    split.height.setValue(1825)
    ready(split)
    assert (split.options().width, split.options().height) == (1079, 1825)
    split.height.setValue(64)
    assert '64–8192' in split.status.text()
    assert not split.export_button.isEnabled()
    split.width.setValue(2160)
    ready(split)
    split.count.setValue(20)
    assert '1 亿像素' in split.status.text()
    assert not split.export_button.isEnabled()
    split.count.setValue(3)
    ready(split)
