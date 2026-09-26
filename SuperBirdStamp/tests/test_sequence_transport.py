"""原生 Tab、序列播放和快/清晰两阶段的真实 Qt 事件回归。"""
import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent, QImage
from PyQt6.QtWidgets import QLabel

from test_editor_dejitter import window, _APP
from test_dejitter_tab import setup_tab, analyze, sequence
from test_reference_tracking import wait_until
from birdstamp.gui.editor_photo_list import PhotoListItem, PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui.editor_sequence_preview_worker import EditorSequencePreviewWorker
from birdstamp.gui import editor_options


def populate(window, paths):
    window.photo_list.blockSignals(True)
    for path in paths:
        item = PhotoListItem([''] * 10)
        item.setData(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE, str(path))
        window.photo_list.addTopLevelItem(item)
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(paths[0]))
    window.photo_list.blockSignals(False)
    window.current_path = paths[0]


def key(window, kind, code, repeat=False):
    event = QKeyEvent(kind, code, Qt.KeyboardModifier.NoModifier, '', repeat)
    _APP.sendEvent(window.photo_list._tree_widget, event)


def test_native_tabs_and_filmstrip_real_selection(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    transport = window.sequence_transport
    assert window.dejitter_view_tabs.currentIndex() == 1
    assert not window.dejitter_view_bar.findChildren(QLabel)
    assert not transport.panel.isHidden()
    assert transport.strip.count() == 2
    assert '参考' in transport.strip.item(0).text()
    assert not transport.strip.item(1).icon().isNull()
    transport.strip.setCurrentRow(1)
    assert window.current_path == paths[1]
    assert window.photo_list.currentItem() is window._find_photo_item_by_path(paths[1])
    assert transport.position.text() == '2 / 2'
    assert window.preview_label.canvas._source_pixmap is not None
    assert path_key(paths[1]) not in window._sequence_frames  # 首帧立即来自小预览。
    wait_until(lambda: path_key(paths[1]) in window._sequence_frames and window._sequence_worker is None)
    transport.previous.click()
    assert window.current_path == paths[0]
    window.dejitter_view_tabs.setCurrentIndex(0)
    assert transport.panel.isHidden()
    wait_until(lambda: window._preview_decode_worker is None)


def test_held_keys_use_quick_frames_until_physical_release(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    transport = window.sequence_transport
    commits = []
    original = window._on_photo_selected
    monkeypatch.setattr(window, '_on_photo_selected', lambda *args: (commits.append(window.current_path), original(*args))[-1])
    monkeypatch.setattr(window, '_start_preview_decode_worker', lambda *args: pytest.fail('播放不应解码编辑原图'))
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *args: pytest.fail('播放不应启动识别'))
    # 初次物理键正常选择；第一次重复开启精确定时器；后续重复不驱动画面。
    key(window, QEvent.Type.KeyPress, Qt.Key.Key_Down)
    assert window.current_path == paths[1]
    key(window, QEvent.Type.KeyPress, Qt.Key.Key_Up, True)
    assert transport.active
    assert window.current_path == paths[0]
    before = len(commits)
    key(window, QEvent.Type.KeyRelease, Qt.Key.Key_Up, True)
    key(window, QEvent.Type.KeyPress, Qt.Key.Key_Up, True)
    assert transport.active and len(commits) == before
    assert not window._sequence_upgrade_timer.isActive()
    assert window._sequence_worker is None
    assert window.preview_label.canvas._source_pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888) == window._sequence_quick_frames[path_key(paths[0])].image
    key(window, QEvent.Type.KeyRelease, Qt.Key.Key_Up)
    assert not transport.active
    assert len(commits) == before + 1
    key(window, QEvent.Type.KeyRelease, Qt.Key.Key_Up)
    assert len(commits) == before + 1


def test_play_timer_loop_pause_and_tab_change(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    transport = window.sequence_transport
    transport.fps.setValue(20)
    assert transport.timer.interval() == 50
    monkeypatch.setattr(window, '_launch_sequence_worker', lambda **kw: pytest.fail('播放中不可请求清晰帧'))
    transport.play.click()
    assert transport.mode == 'play'
    wait_until(lambda: window.current_path == paths[1])
    wait_until(lambda: window.current_path == paths[0])
    assert transport.strip.currentRow() == 0
    transport.play.click()
    assert not transport.active
    assert not transport.timer.isActive()
    transport.play.click()
    window.dejitter_view_tabs.setCurrentIndex(0)
    assert not transport.active and transport.panel.isHidden()
    wait_until(lambda: window._preview_decode_worker is None)


def test_focus_loss_and_invalidation_stop_playback(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    transport = window.sequence_transport
    transport.play.click()
    _APP.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
    assert not transport.active
    transport.play.click()
    window._invalidate_sequence_preview()
    assert not transport.active
    assert not transport.paths and not window._sequence_quick_frames
    assert not transport.play.isEnabled()
    assert not window._sequence_upgrade_timer.isActive()


def test_worker_quick_frames_share_export_geometry_and_budget(sequence, monkeypatch):
    seeds, prepared = sequence
    monkeypatch.setattr(editor_options, 'DEJITTER_QUICK_MAX_EDGE', 128)
    worker = EditorSequencePreviewWorker(token=1, path=seeds[0].path, seeds=seeds)
    events, frames = [], {}
    worker.quick_ready.connect(lambda token, result, values: (events.append('quick'), frames.update(values)))
    worker.ready.connect(lambda *args: events.append('full'))
    worker.failed.connect(lambda *args: pytest.fail(str(args)))
    worker.run()
    assert events == ['quick', 'full']
    assert set(frames) == set(prepared.jobs)
    used = 0
    for key, frame in frames.items():
        width, height = prepared.source_sizes[key]
        assert frame.crop_plan[0] == tuple(value / (width if i % 2 == 0 else height)
                                          for i, value in enumerate(prepared.pixel_boxes[key]))
        assert frame.output_size == prepared.output_size
        assert max(frame.source_image.width(), frame.source_image.height()) <= 128
        used += frame.image.sizeInBytes() + frame.source_image.sizeInBytes()
    assert used <= editor_options.DEJITTER_QUICK_CACHE_BYTES


def test_quick_cache_budget_scales_with_sequence_size(sequence, monkeypatch):
    seeds, _ = sequence
    monkeypatch.setattr(editor_options, 'DEJITTER_QUICK_CACHE_BYTES', 8192)
    worker = EditorSequencePreviewWorker(token=1, path=seeds[0].path, seeds=seeds)
    sizes = []
    worker.quick_ready.connect(lambda token, result, frames: sizes.append(sum(
        frame.image.sizeInBytes() + frame.source_image.sizeInBytes() for frame in frames.values())))
    worker.run()
    assert sizes and sizes[0] <= 8192


def test_edit_hold_shows_source_tracking_then_commits_source_decode(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    window.dejitter_view_tabs.setCurrentIndex(0)
    wait_until(lambda: window._preview_decode_worker is None)
    key(window, QEvent.Type.KeyPress, Qt.Key.Key_Down, True)
    transport = window.sequence_transport
    assert transport.active and window.current_path == paths[1]
    frame = window._sequence_quick_frames[path_key(paths[1])]
    assert window.preview_label.canvas._source_pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888) == frame.source_image
    assert len(window.preview_label.canvas.reference_regions()) == 2
    key(window, QEvent.Type.KeyRelease, Qt.Key.Key_Down)
    wait_until(lambda: window.current_source_image is not None and window._preview_decode_worker is None)
    assert not transport.active
    assert window.current_photo_info.path == paths[1]


def test_reference_change_during_playback_preserves_per_photo_template_settings(window, monkeypatch):
    from copy import deepcopy
    from birdstamp.gui.editor import BirdStampEditorWindow
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    window.photo_render_overrides[path_key(paths[1])] = {'ratio': 1, 'crop_box': (.2, .2, .8, .8)}
    before = deepcopy(window.photo_render_overrides)
    window.sequence_transport.start('play', 1)
    window.sequence_transport._tick()
    assert window.current_path == paths[1]
    window.dejitter_reference_strength_slider.setValue(50)
    BirdStampEditorWindow._on_output_settings_changed(window)
    assert window.photo_render_overrides == before
    assert window.preview_label.canvas._source_pixmap is None
    window._preview_debounce_timer.stop()


def test_quick_to_full_upgrade_keeps_view_scale_and_position(window, monkeypatch):
    monkeypatch.setattr(editor_options, 'DEJITTER_QUICK_MAX_EDGE', 128)
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    canvas = window.preview_label.canvas
    canvas._zoom = 2.0
    before = canvas._display_rect()
    window.sequence_transport.next.click()
    quick = canvas._display_rect()
    assert quick.width() == pytest.approx(before.width(), rel=.01)
    assert quick.height() == pytest.approx(before.height(), rel=.01)
    wait_until(lambda: path_key(paths[1]) in window._sequence_frames and window._sequence_worker is None)
    full = canvas._display_rect()
    assert full.width() == pytest.approx(before.width(), rel=.01)
    assert full.height() == pytest.approx(before.height(), rel=.01)
    assert full.center().x() == pytest.approx(before.center().x(), abs=1)
    assert full.center().y() == pytest.approx(before.center().y(), abs=1)
