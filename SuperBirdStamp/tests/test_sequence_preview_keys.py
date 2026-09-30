"""成片区真实焦点下复用照片列表的按键节拍，辅助输入与 A 对照保持独立。"""
from dataclasses import replace

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QImage
from PyQt6.QtTest import QTest

from test_editor_dejitter import window, _APP
from test_dejitter_tab import setup_tab, analyze
from test_sequence_transport import populate
from test_reference_tracking import wait_until
from birdstamp.gui.editor_utils import path_key


def prepare(window, monkeypatch):
    paths, _, seeds = setup_tab(window, monkeypatch)
    for index in range(2, 5):
        path = paths[0].with_name(f'连续帧{index}.png')
        path.write_bytes(paths[1].read_bytes())
        paths.append(path)
        seeds.append(replace(seeds[1], path=path))
    populate(window, paths)
    analyze(window)
    window.show()
    window.activateWindow()
    _APP.processEvents()
    return paths


def surface(window, name):
    if name == 'canvas':
        return window.preview_label.canvas
    if name == 'tabs':
        return window.dejitter_view_tabs
    return getattr(window.sequence_transport, name)


def send(widget, kind, code, repeat=False, modifiers=Qt.KeyboardModifier.NoModifier):
    _APP.sendEvent(widget, QKeyEvent(kind, code, modifiers, '', repeat))


@pytest.mark.parametrize('view', ['ordinary', 'source', 'result'])
@pytest.mark.parametrize('name', ['photo_list', 'strip'])
@pytest.mark.parametrize('different_crops', [False, True])
def test_left_boundary_then_right_resumes_selection(window, monkeypatch, view, name, different_crops):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    crops = [(0, .1, 1, .9), (.2, .2, .8, .8)]
    if different_crops:
        for path, ratio, box in zip(paths, (16 / 9, 1), crops):
            window.photo_render_overrides[path_key(path)] = {
                'ratio': ratio, 'center_mode': 'custom', 'crop_box': box,
            }
    if view == 'result':
        analyze(window)
    elif view == 'ordinary':
        window.export_tabs.setCurrentIndex(0)
    transport = window.sequence_transport
    transport._select(1)
    window.show()
    window.activateWindow()
    _APP.processEvents()
    target = window.photo_list._tree_widget if name == 'photo_list' else transport.strip
    target.setFocus()
    for code, row in [(Qt.Key.Key_Left, 0), (Qt.Key.Key_Left, 0),
                      (Qt.Key.Key_Right, 1), (Qt.Key.Key_Right, 1),
                      (Qt.Key.Key_Left, 0), (Qt.Key.Key_Right, 1)]:
        QTest.keyClick(target, code)
        _APP.processEvents()
        assert window.current_path == paths[row]
        assert transport.strip.currentRow() == row
        assert window.photo_list.currentItem() is window._find_photo_item_by_path(paths[row])
        assert _APP.focusWidget() is target
        if different_crops:
            assert window.photo_render_overrides[path_key(paths[row])]['crop_box'] == crops[row]
    wait_until(lambda: window._sequence_worker is None and window._preview_decode_worker is None)


@pytest.mark.parametrize('name', ['photo_list', 'strip'])
@pytest.mark.parametrize('direction', [-1, 1])
def test_ordinary_held_arrow_reverses_after_boundary(window, monkeypatch, name, direction):
    paths, _, _ = setup_tab(window, monkeypatch)
    extra = paths[0].with_name('third.png')
    extra.write_bytes(paths[1].read_bytes())
    paths.append(extra)
    populate(window, paths)
    window.export_tabs.setCurrentIndex(0)
    transport = window.sequence_transport
    transport._scan_source_list()
    wait_until(lambda: len(transport._source_ready) == len(paths))
    transport._select(2 if direction < 0 else 0)
    window.show()
    window.activateWindow()
    _APP.processEvents()
    target = window.photo_list._tree_widget if name == 'photo_list' else transport.strip
    target.setFocus()
    code = Qt.Key.Key_Left if direction < 0 else Qt.Key.Key_Right
    reverse = Qt.Key.Key_Right if direction < 0 else Qt.Key.Key_Left
    transport.start('source_play', direction)
    assert transport.timer.isActive()
    send(target, QEvent.Type.KeyPress, code)
    assert window.current_path == paths[1]
    assert not transport.timer.isActive()
    commits = []
    original = window._on_photo_selected
    monkeypatch.setattr(window, '_on_photo_selected', lambda *a: (commits.append(window.current_path), original(*a))[-1])
    send(target, QEvent.Type.KeyPress, code, True)
    transport.timer.stop()
    end = 0 if direction < 0 else 2
    assert window.current_path == paths[end]
    assert transport.mode == 'ordinary_keys'
    before = len(commits)
    transport._tick()
    send(target, QEvent.Type.KeyRelease, code, True)
    assert len(commits) == before and transport.active
    send(target, QEvent.Type.KeyRelease, code)
    assert len(commits) == before + 1 and not transport.active
    QTest.keyClick(target, reverse)
    assert window.current_path == paths[1]
    assert transport.strip.currentRow() == 1
    assert window.photo_list.currentItem() is window._find_photo_item_by_path(paths[1])
    wait_until(lambda: window._preview_decode_worker is None)


@pytest.mark.parametrize('name', ['photo_list', 'strip'])
def test_video_export_long_list_returns_to_first_and_can_advance(window, monkeypatch, name):
    paths, _, _ = setup_tab(window, monkeypatch)
    for index in range(2, 121):
        path = paths[0].with_name(f'frame-{index:03}.png')
        path.write_bytes(paths[0].read_bytes())
        paths.append(path)
    populate(window, paths)
    window.export_tabs.setCurrentIndex(0)
    window.export_stage_buttons['export_video'].setChecked(True)
    transport = window.sequence_transport
    transport._select(120)
    window.show()
    window.activateWindow()
    _APP.processEvents()
    target = window.photo_list._tree_widget if name == 'photo_list' else transport.strip
    target.setFocus()
    for row in range(119, -1, -1):
        QTest.keyClick(target, Qt.Key.Key_Left)
        assert window.current_path == paths[row]
    QTest.keyClick(target, Qt.Key.Key_Left)
    QTest.keyClick(target, Qt.Key.Key_Right)
    assert window.current_path == paths[1]
    assert transport.position.text() == '2 / 121'
    assert transport.strip.currentRow() == 1
    assert window.photo_list.currentItem() is window._find_photo_item_by_path(paths[1])
    wait_until(lambda: window._preview_decode_worker is None)


@pytest.mark.parametrize('name', ['canvas', 'panel', 'play', 'previous', 'next', 'strip', 'tabs', 'loop'])
@pytest.mark.parametrize('code,direction', [(Qt.Key.Key_Left, -1), (Qt.Key.Key_Up, -1),
                                           (Qt.Key.Key_Right, 1), (Qt.Key.Key_Down, 1)])
def test_result_focused_surfaces_switch_photo_without_switching_tab(window, monkeypatch, name, code, direction):
    paths = prepare(window, monkeypatch)
    transport = window.sequence_transport
    transport._select(2)
    target = surface(window, name)
    target.setFocus()
    assert _APP.focusWidget() is target
    QTest.keyClick(target, code)
    assert window.current_path == paths[2+direction]
    assert transport.strip.currentRow() == 2+direction
    assert window.photo_list.currentItem() is window._find_photo_item_by_path(paths[2+direction])
    assert window.dejitter_view_tabs.currentIndex() == 1
    assert _APP.focusWidget() is target
    transport.stop(commit=False)
    wait_until(lambda: window._sequence_worker is None)


@pytest.mark.parametrize('name', ['canvas', 'play', 'strip', 'tabs'])
@pytest.mark.parametrize('code', [Qt.Key.Key_Right, Qt.Key.Key_Down])
def test_held_preview_keys_use_quick_cache_and_commit_once_on_release(window, monkeypatch, name, code):
    paths = prepare(window, monkeypatch)
    transport = window.sequence_transport
    target = surface(window, name)
    target.setFocus()
    send(target, QEvent.Type.KeyPress, code)  # 初次正常选择。
    assert window.current_path == paths[1]
    commits = []
    original = window._on_photo_selected
    monkeypatch.setattr(window, '_on_photo_selected', lambda *a: (commits.append(window.current_path), original(*a))[-1])
    monkeypatch.setattr(window, '_start_preview_decode_worker', lambda *a: pytest.fail('长按不读取编辑原图'))
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *a: pytest.fail('长按不启动识别'))
    send(target, QEvent.Type.KeyPress, code, True)
    assert transport.mode == 'keys' and window.current_path == paths[2]
    transport.timer.stop()  # 手动节拍便于验证：OS 重复不能多走一张。
    count = len(commits)
    send(target, QEvent.Type.KeyRelease, code, True)
    send(target, QEvent.Type.KeyPress, code, True)
    assert window.current_path == paths[2] and len(commits) == count
    transport._tick()
    assert window.current_path == paths[3]
    assert not window._sequence_upgrade_timer.isActive() and window._sequence_worker is None
    shown = window.preview_label.canvas._source_pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
    assert shown == window._sequence_quick_frames[path_key(paths[3])].image
    count = len(commits)
    send(target, QEvent.Type.KeyRelease, code)
    assert not transport.active and len(commits) == count+1
    send(target, QEvent.Type.KeyRelease, code)
    assert len(commits) == count+1
    wait_until(lambda: path_key(paths[3]) in window._sequence_frames and window._sequence_worker is None)


def test_click_canvas_takes_focus_and_leaving_it_stops_keys(window, monkeypatch):
    prepare(window, monkeypatch)
    transport = window.sequence_transport
    transport.fps.setFocus()
    canvas = window.preview_label.canvas
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton)
    assert _APP.focusWidget() is canvas
    send(canvas, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    assert transport.active
    transport.fps.setFocus()
    assert not transport.active and not transport.timer.isActive()
    wait_until(lambda: window._sequence_worker is None)


def test_input_widgets_and_a_canvas_do_not_navigate_b(window, monkeypatch):
    paths = prepare(window, monkeypatch)
    transport = window.sequence_transport
    transport.fps.setFocus()
    before = transport.fps.value()
    QTest.keyClick(transport.fps, Qt.Key.Key_Up)
    assert transport.fps.value() == before+1 and window.current_path == paths[0]
    canvas = window.preview_label.canvas
    canvas.setFocus()
    QTest.keyClick(canvas, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
    assert window.current_path == paths[0]
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    wait_until(lambda: ab.worker is None and not ab.pending)
    # A 激活后快捷键导航 A，不能经窗口冒泡触发 B 切图。
    QTest.mouseClick(ab.a_panel.toolbar, Qt.MouseButton.LeftButton)
    QTest.keyClick(ab.preview.canvas, Qt.Key.Key_Down)
    assert ab.path == paths[1] and window.current_path == paths[0]
    wait_until(lambda: ab.worker is None and not ab.pending)
    window.export_tabs.setCurrentIndex(0)
    send(canvas, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    assert not transport.active
    wait_until(lambda: window._preview_decode_worker is None)


@pytest.mark.parametrize('fps', [5, 25])
@pytest.mark.parametrize('code,direction', [(Qt.Key.Key_Up, -1), (Qt.Key.Key_Down, 1)])
def test_focused_canvas_timer_cadence_and_end_boundary(window, monkeypatch, fps, code, direction):
    paths = prepare(window, monkeypatch)
    transport = window.sequence_transport
    transport.fps.setValue(fps)
    assert transport.timer.interval() == round(1000/fps)
    start = 0 if direction > 0 else 4
    transport._select(start)
    canvas = window.preview_label.canvas
    canvas.setFocus()
    send(canvas, QEvent.Type.KeyPress, code)
    send(canvas, QEvent.Type.KeyPress, code, True)
    assert window.current_path == paths[start+2*direction]
    # 不再发 OS 重复事件，应用定时器仍推进；即使开启循环，长按到边界也不能回绕。
    end = 4 if direction > 0 else 0
    wait_until(lambda: window.current_path == paths[end] and not transport.timer.isActive())
    assert transport.mode == 'keys'
    send(canvas, QEvent.Type.KeyRelease, code, True)
    assert transport.active
    send(canvas, QEvent.Type.KeyRelease, code)
    assert not transport.active and window.current_path == paths[end]
    wait_until(lambda: window._sequence_worker is None)
