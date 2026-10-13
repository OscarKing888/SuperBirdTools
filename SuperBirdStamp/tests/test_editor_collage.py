"""独立拼图状态、工作区与 Qt worker 真实 finished 交接。"""
from pathlib import Path
import time
import pytest

from PIL import Image
from PyQt6.QtCore import QPointF, QEvent, Qt
from PyQt6.QtTest import QTest
from birdstamp.overlays.assets import import_image
from birdstamp.overlays.model import new_item
from birdstamp.workspace import read_workspace_json, write_workspace_json
from test_overlay_editor import window, _APP, mouse


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    assert predicate()


def source(tmp_path, name='鸟.png', color='red'):
    path = tmp_path / name
    with Image.new('RGB', (160, 100), color) as image:
        image.save(path)
    return path


def ready(collage):
    wait_for(lambda: collage.worker is None and not collage.pending and collage.base is not None)


def test_tab_order_preview_export_overlay_and_workspace(window, tmp_path):
    collage = window.collage
    assert [window.export_tabs.tabText(i) for i in range(3)] == ['图片处理', '去抖动', '拼图']
    window.show()
    window.export_tabs.setCurrentWidget(collage.page)
    assert window.preview_pages.currentWidget() is collage.preview_host
    assert window.export_action_bar.actions.currentWidget() is collage.actions
    assert not window.export_action_bar.settings_section.isVisible()
    collage.add_paths([source(tmp_path)])
    ready(collage)
    assert collage.plan.size == (1080, 1440)
    assert collage.aspect_ratio.currentData() == '3:4'
    collage.preset.setCurrentIndex(collage.preset.findData('grid4'))
    ready(collage)
    collage.panel.add('image', asset=import_image(source(tmp_path, '蓝.png', 'blue')))
    assert collage.preview.canvas.edit_mode() == 'overlay'
    collage.panel.edit('name', '中文图像 overlay')
    collage.panel.edit('width', .3)
    collage.panel.undo()
    collage.panel.redo()
    _APP.processEvents()
    layer = collage.session.selected_layer()
    start = collage.session.to_widget(layer.center)
    previous_x = collage.panel.selected()['x']
    collage.session.press(mouse(QEvent.Type.MouseButtonPress, start))
    collage.session.move(mouse(QEvent.Type.MouseMove, start + QPointF(30, 10), modifiers=Qt.KeyboardModifier.AltModifier))
    collage.session.release(mouse(QEvent.Type.MouseButtonRelease, start + QPointF(30, 10)))
    assert collage.panel.selected()['x'] > previous_x
    collage.panel.undo()
    assert collage.panel.selected()['x'] == previous_x
    output = tmp_path / '拼图.png'
    collage.start_export(output)
    wait_for(lambda: collage.worker is None)
    assert output.exists()
    with Image.open(output) as image:
        assert image.size == (1080, 1440)
        assert image.getpixel((image.width // 2, image.height // 2))[2] == 255
    workspace = tmp_path / '测试.birdstamp-workspace.json'
    payload = window._collect_workspace_payload(workspace)
    write_workspace_json(workspace, payload)
    loaded = read_workspace_json(workspace)
    collage.clear()
    collage.restore(loaded['editor_state']['collage'], workspace)
    ready(collage)
    assert collage.paths == [tmp_path / '鸟.png']
    assert collage.options().preset == 'grid4'
    assert collage.panel.doc['overlays'][0]['name'] == '中文图像 overlay'
    assert collage.panel.doc['overlay_assets']
    window.export_tabs.setCurrentIndex(0)
    assert window.preview_pages.currentIndex() == 0
    window.export_tabs.setCurrentWidget(collage.page)
    assert collage.options().preset == 'grid4'
    collage.restore(None, workspace)
    wait_for(lambda: collage.worker is None and not collage.pending)
    assert not collage.paths and not collage.panel.doc['overlays']


def test_order_small_window_and_collapsing_preserve_state(window, tmp_path):
    collage = window.collage
    collage.add_paths([source(tmp_path, f'{i}.png') for i in range(4)])
    ready(collage)
    collage.files.item(1).setSelected(True)
    collage.files.item(2).setSelected(True)
    collage.move_selected(-1)
    assert [p.name for p in collage.paths] == ['1.png', '2.png', '0.png', '3.png']
    ready(collage)
    before = collage.state(tmp_path / 'workspace.json')
    window.resize(1120, 720)
    window.show()
    window.export_tabs.setCurrentWidget(collage.page)
    for _ in range(3):
        for section in (collage.layout_group, collage.sources_group):
            section.header_button.setFocus()
            QTest.keyClick(section.header_button, Qt.Key.Key_Space)
            QTest.keyClick(section.header_button, Qt.Key.Key_Space)
    _APP.processEvents()
    assert collage.state(tmp_path / 'workspace.json') == before
    assert collage.export_button.isVisible()
    assert window.rect().contains(collage.export_button.mapTo(window, collage.export_button.rect().bottomRight()))
    window.setStyleSheet(window.styleSheet() + '\nQWidget { font-size: 24px; }')
    _APP.processEvents()
    assert window.left_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    bar = window.left_scroll.horizontalScrollBar()
    bar.setValue(bar.maximum())
    _APP.processEvents()
    point = collage.background.mapTo(window.left_scroll.viewport(), collage.background.rect().topRight())
    assert point.x() < window.left_scroll.viewport().width()
    assert collage.state(tmp_path / 'workspace.json') == before
    window.export_tabs.setCurrentIndex(0)
    assert window.left_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_late_preview_and_shutdown_are_owned(window, tmp_path, monkeypatch):
    from threading import Event
    import birdstamp.gui.editor_collage as module
    real_render = module.render_collage
    started, release = Event(), Event()
    calls = []
    def delayed(paths, options, **kwargs):
        calls.append(options.width)
        if len(calls) == 1:
            started.set()
            release.wait(5)
            # 模拟解码已完成但逻辑结果迟到，真实线程仍受所有者管理。
            kwargs['cancel'] = lambda: False
        return real_render(paths, options, **kwargs)
    monkeypatch.setattr(module, 'render_collage', delayed)
    collage = window.collage
    collage.add_paths([source(tmp_path)])
    wait_for(started.is_set)
    first = collage.worker
    collage.canvas_width.setValue(400)
    assert collage.worker is first
    assert not collage.shutdown()
    assert collage.worker is first
    release.set()
    wait_for(lambda: collage.worker is None)
    assert collage.closed and collage.base is None and calls == [1080]
    assert collage.shutdown()


def test_latest_request_wins(window, tmp_path):
    collage = window.collage
    collage.add_paths([source(tmp_path)])
    collage.preset.setCurrentIndex(collage.preset.findData('grid9'))
    collage.canvas_width.setValue(100)
    ready(collage)
    assert collage.plan.size == (100, 133)
    assert collage.options().preset == 'grid9'


def test_stale_completed_pixels_are_discarded_before_next_worker(window, tmp_path, monkeypatch):
    from threading import Event
    import birdstamp.gui.editor_collage as module
    real_render = module.render_collage
    started, release = Event(), Event()
    calls = []
    def delayed(paths, options, **kwargs):
        calls.append(options.width)
        if len(calls) == 1:
            started.set()
            release.wait(5)
            kwargs['cancel'] = lambda: False
        return real_render(paths, options, **kwargs)
    monkeypatch.setattr(module, 'render_collage', delayed)
    collage = window.collage
    collage.add_paths([source(tmp_path)])
    wait_for(started.is_set)
    first = collage.worker
    collage.canvas_width.setValue(200)
    collage.canvas_width.setValue(300)
    assert collage.worker is first
    release.set()
    ready(collage)
    assert calls == [1080, 300]
    assert collage.plan.size == (300, 400)


def test_failure_disables_export_and_recovers(window, tmp_path):
    collage = window.collage
    path = source(tmp_path)
    collage.preset.setCurrentIndex(collage.preset.findData('grid4'))
    collage.add_paths([path] * 5)
    wait_for(lambda: collage.worker is None and not collage.pending)
    assert '最多容纳 4' in collage.status.text()
    assert not collage.export_button.isEnabled()
    collage.files.item(4).setSelected(True)
    collage.remove_selected()
    ready(collage)
    assert collage.export_button.isEnabled()


def test_ratio_resolution_and_custom_canvas_roundtrip(window, tmp_path):
    collage = window.collage
    collage.add_paths([source(tmp_path)] * 4)
    collage.preset.setCurrentIndex(collage.preset.findData('grid4'))
    collage.aspect_ratio.setCurrentIndex(collage.aspect_ratio.findData('16:9'))
    assert (collage.options().width, collage.options().height) == (1920, 1080)
    collage.resolution.setCurrentIndex(collage.resolution.findData(1440))
    assert (collage.options().width, collage.options().height) == (2560, 1440)
    collage.canvas_height.setValue(360)
    assert collage.canvas_width.value() == 640
    assert collage.resolution.currentData() is None
    collage.aspect_ratio.setCurrentIndex(collage.aspect_ratio.findData('custom'))
    collage.canvas_width.setValue(501)
    assert collage.canvas_height.value() == 360
    ready(collage)
    assert collage.plan.size == (501, 360)
    assert collage.fit.isEnabled()
    collage.panel.add('text')
    collage.panel.edit('text', '竖版测试')
    doc = collage.panel.doc.copy()
    saved = collage.state(tmp_path / 'workspace.json')
    collage.restore(None, tmp_path / 'workspace.json')
    collage.restore(saved, tmp_path / 'workspace.json')
    ready(collage)
    assert collage.plan.size == (501, 360)
    assert collage.aspect_ratio.currentData() == 'custom'
    assert collage.panel.doc == doc
    # 不足排版时关闭导出，恢复参数后仍保留选定输出大小与 Overlay。
    collage.margin.setValue(256)
    wait_for(lambda: collage.worker is None and not collage.pending)
    assert '空间不足' in collage.status.text() and not collage.export_button.isEnabled()
    collage.margin.setValue(12)
    ready(collage)
    assert collage.plan.size == (501, 360) and collage.panel.doc == doc


@pytest.mark.parametrize('preset,size', [('vertical', (124, 86)), ('horizontal', (184, 124)), ('grid4', (236, 236))])
def test_legacy_cell_size_workspace_migrates_in_worker(window, tmp_path, preset, size):
    collage = window.collage
    photo = source(tmp_path)
    item = new_item('text')
    item.update(text='旧拼图标注', x=.25, y=.75)
    state = dict(options=dict(preset=preset, cell_size=100, fit='cover'), paths=[str(photo)],
                 overlays={'overlays': [item]})
    collage.restore(state, tmp_path / 'workspace.json')
    ready(collage)
    assert collage.plan.size == size
    assert collage.options().cell_size is None
    assert collage.aspect_ratio.currentData() == 'custom'
    assert collage.panel.doc['overlays'][0]['text'] == '旧拼图标注'
    assert collage.panel.doc['overlays'][0]['x'] == .25
    restored = collage.state(tmp_path / 'workspace.json')
    assert (restored['options']['width'], restored['options']['height']) == size
    collage.restore(restored, tmp_path / 'workspace.json')
    ready(collage)
    assert collage.plan.size == size


def test_custom_grid_controls_export_and_workspace(window, tmp_path):
    collage = window.collage
    window.resize(1120, 720)
    window.show()
    window.export_tabs.setCurrentWidget(collage.page)
    assert collage.columns.isHidden() and collage.rows.isHidden()
    collage.preset.setCurrentIndex(collage.preset.findData('custom'))
    assert not collage.columns.isHidden() and not collage.rows.isHidden()
    collage.columns.setFocus()
    QTest.keyClick(collage.columns, Qt.Key.Key_Up)
    assert collage.columns.value() == 3
    collage.rows.setValue(2)
    collage.canvas_width.setValue(300)
    collage.fit.setCurrentIndex(collage.fit.findData('cover'))
    collage.add_paths([source(tmp_path)] * 4)
    ready(collage)
    assert collage.plan.size == (300, 400)
    assert '3 列 × 2 行' in collage.summary.text()
    assert collage.plan.boxes[3][0] == collage.plan.boxes[0][0]
    assert collage.plan.boxes[3][1] > collage.plan.boxes[0][3]
    collage.panel.add('image', asset=import_image(source(tmp_path, '蓝色标记.png', 'blue')))
    collage.panel.edit('width', .15)
    collage.panel.edit('x', .8)
    collage.panel.edit('y', .8)
    path = tmp_path / '自定义工作区.json'
    saved = collage.state(path)
    for _ in range(3):
        collage.layout_group.header_button.setFocus()
        QTest.keyClick(collage.layout_group.header_button, Qt.Key.Key_Space)
        QTest.keyClick(collage.layout_group.header_button, Qt.Key.Key_Space)
    assert collage.state(path) == saved
    collage.preset.setCurrentIndex(collage.preset.findData('grid4'))
    assert collage.columns.isHidden() and collage.rows.isHidden()
    collage.preset.setCurrentIndex(collage.preset.findData('custom'))
    assert (collage.columns.value(), collage.rows.value()) == (3, 2)
    collage.restore(None, path)
    collage.restore(saved, path)
    ready(collage)
    assert collage.state(path) == saved
    output = tmp_path / '横三竖二.png'
    collage.start_export(output)
    wait_for(lambda: collage.worker is None)
    with Image.open(output) as image:
        assert image.size == (300, 400)
        assert image.getpixel((240, 320)) == (0, 0, 255)
    _APP.processEvents()
    assert window.rect().contains(collage.export_button.mapTo(window, collage.export_button.rect().bottomRight()))


def test_custom_grid_capacity_failure_recovers(window, tmp_path):
    collage = window.collage
    collage.preset.setCurrentIndex(collage.preset.findData('custom'))
    collage.columns.setValue(9)
    collage.rows.setValue(8)
    collage.add_paths([source(tmp_path)] * 6)
    wait_for(lambda: collage.worker is None and not collage.pending)
    assert '最多 64 格' in collage.status.text() and not collage.export_button.isEnabled()
    collage.columns.setValue(2)
    collage.rows.setValue(2)
    wait_for(lambda: collage.worker is None and not collage.pending)
    assert '最多容纳 4' in collage.status.text() and not collage.export_button.isEnabled()
    collage.rows.setValue(3)
    ready(collage)
    assert len(collage.plan.boxes) == 6 and collage.export_button.isEnabled()
