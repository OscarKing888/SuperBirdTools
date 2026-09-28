"""A/B 激活侧选图、页签隔离、诊断叠加和解码线程归属。"""
from dataclasses import replace
import threading

import pytest

from PIL import Image
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from test_editor_dejitter import window, _APP
from test_reference_tracking import install_sequence, wait_until
from test_dejitter_tab import setup_tab, analyze
from birdstamp.gui.editor_utils import path_key


def finish(ab):
    wait_until(lambda: ab.worker is None and not ab.pending)


def test_photo_list_refreshes_only_clicked_active_view(window, monkeypatch):
    from test_sequence_transport import populate
    paths, target = install_sequence(window, monkeypatch)
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *args: None)
    populate(window, paths)
    window.show()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    assert ab.path == paths[0]
    assert ab.image is not None
    assert not hasattr(ab, 'pin') and not hasattr(ab.a_panel, 'photos') and not hasattr(ab.b_panel, 'photos')
    QTest.mouseClick(ab.a_panel.toolbar, Qt.MouseButton.LeftButton)
    assert ab.active_side == 'a'
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(paths[1]))
    finish(ab)
    assert ab.path == paths[1]
    assert window.current_path == paths[0]
    QTest.mouseClick(ab.b_panel.toolbar, Qt.MouseButton.LeftButton)
    assert ab.active_side == 'b'
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(paths[1]))
    wait_until(lambda: window._preview_decode_worker is None)
    assert window.current_path == paths[1]
    assert ab.path == paths[1]
    QTest.mouseClick(ab.a_panel.toolbar, Qt.MouseButton.LeftButton)
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(paths[0]))
    finish(ab)
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    window._set_dejitter_view('result')
    assert ab.enabled.isChecked() and ab.path == paths[0]
    assert ab.image is not None and ab.frame is None
    ab.enabled.setChecked(False)
    assert window.current_path == paths[1]
    finish(ab)
    wait_until(lambda: window._preview_decode_worker is None and window._sequence_worker is None)


def test_a_result_mode_independent_of_b_edit_mode_and_invalidates(window, monkeypatch):
    paths, target, _ = setup_tab(window, monkeypatch)
    analyze(window)
    window._set_dejitter_view('edit')
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    ab.mode.setCurrentIndex(1)
    finish(ab)
    assert ab.frame is not None
    assert not window._sequence_result_mode()
    assert len(ab.preview.canvas._reference_diagnostics) == 2
    assert all(row[2] for row in ab.preview.canvas._reference_diagnostics)
    assert ab.preview.canvas._focus_box is not None
    window._invalidate_sequence_preview()
    assert ab.path == paths[0]
    assert ab.frame is None and ab.image is None
    assert ab.enabled.isChecked()
    ab.mode.setCurrentIndex(0)
    finish(ab)
    assert ab.image is not None


def test_mode_buttons_show_source_and_aligned_pixels_on_ordinary_export_tab(window, monkeypatch):
    from test_sequence_transport import populate
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    window.export_tabs.setCurrentIndex(0)
    wait_until(lambda: window._preview_decode_worker is None)
    window.show()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    QTest.mouseClick(ab.b_mode.group.button(0), Qt.MouseButton.LeftButton)
    assert not window._edit_mode_buttons['crop_adjust'].isEnabled()
    wait_until(lambda: window.current_source_image is not None and window._preview_decode_worker is None)
    pixmap = window.preview_label.canvas._source_pixmap
    assert (pixmap.width(), pixmap.height()) == (200, 160)
    assert not window._sequence_result_mode() and ab.active_side == 'b'
    QTest.mouseClick(ab.b_mode.group.button(1), Qt.MouseButton.LeftButton)
    wait_until(lambda: window._sequence_worker is None)
    pixmap = window.preview_label.canvas._source_pixmap
    assert (pixmap.width(), pixmap.height()) == window._sequence_preview.output_size
    assert window.export_tabs.currentIndex() == 0
    assert ab.mode.currentIndex() == 0 and ab.path == paths[0]
    QTest.mouseClick(ab.mode.group.button(1), Qt.MouseButton.LeftButton)
    finish(ab)
    assert ab.active_side == 'a' and ab.frame is not None
    assert window._sequence_result_mode()
    ab.enabled.setChecked(False)
    assert window._edit_mode_buttons['crop_adjust'].isEnabled()
    wait_until(lambda: window._preview_decode_worker is None and window._sequence_worker is None)


def test_late_decode_is_closed_without_replacing_a(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    previous = ab.image
    stale = Image.new('RGB',(10,10),'red')
    ab._decoded(ab.token-1,str(paths[0]),stale,(10,10))
    assert ab.image is previous
    try:
        stale.getpixel((0,0))
        assert False, 'stale PIL frame was not released'
    except ValueError:
        pass


def test_a_keeps_owned_worker_until_finished_and_close_waits(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    ab.upgrade.stop()
    class SlowWorker:
        cancelled = False
        def requestInterruption(self): self.cancelled = True
    worker = SlowWorker()
    ab.worker = worker
    ab.select_a(paths[1])
    assert ab.worker is worker and worker.cancelled
    assert not ab.shutdown()
    assert ab.worker is worker
    ab.worker = None
    assert ab.shutdown()


def test_result_preview_has_yellow_and_red_regions_in_crop_coordinates(window, monkeypatch):
    paths, _, _ = setup_tab(window,monkeypatch)
    analyze(window)
    sequence = window._sequence_preview
    key = path_key(paths[0])
    original = sequence.tracking[key]
    sequence.tracking[key] = replace(original, boxes=(original.boxes[0],None),
                                     predicted_boxes=original.boxes, reasons=('', '遮挡'))
    window._refresh_preview_label()
    rows = window.preview_label.canvas._reference_diagnostics
    assert len(rows) == 2 and rows[0][2] and not rows[1][2]
    assert '2' in rows[1][1] and '预计位置' in rows[1][1]
    # 标记是画布叠加，不写入成片缓存。
    frame = window._sequence_frames[key]
    assert frame.image is not None


def test_active_a_playback_defers_full_decode_until_stop(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    analyze(window)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    monkeypatch.setattr(window, '_sequence_fast_preview_active', lambda: True)
    ab.activate('a')
    ab.select_a(paths[1])
    assert ab.image is window._sequence_quick_frames[path_key(paths[1])].source_image
    ab._start()
    assert ab.worker is None and ab.pending
    monkeypatch.setattr(window, '_sequence_fast_preview_active', lambda: False)
    finish(ab)
    assert not ab.pending


def test_failed_analysis_retains_per_region_diagnostics(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    from birdstamp.export_stage import sequence_preview
    def fail_crop(*args, **kwargs):
        raise ValueError('测试无共同画面')
    monkeypatch.setattr(sequence_preview, 'prepare_rigid_geometry', fail_crop)
    window.dejitter_preprocess_btn.click()
    wait_until(lambda: window._sequence_worker is None)
    assert window._sequence_preview is None
    assert '无共同画面' in window._sequence_message
    assert len(window._reference_tracking_results) == 2
    window._set_dejitter_view('edit')
    window.current_path = paths[1]
    window._refresh_preview_label()
    assert window.preview_label.canvas._reference_diagnostics


def test_active_b_list_selection_does_not_replace_a(window, monkeypatch):
    from test_sequence_transport import populate
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    ab.activate('b')
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(paths[1]))
    assert window.current_path == paths[1]
    assert window.photo_list.currentItem() is window._find_photo_item_by_path(paths[1])
    assert ab.path == paths[0]
    wait_until(lambda: window._sequence_worker is None)
    window._sequence_upgrade_timer.stop()


def test_close_event_waits_for_a_decoder(window, monkeypatch):
    ab = window.ab_preview
    class PendingWorker:
        interrupted = False
        def requestInterruption(self): self.interrupted = True
    worker = PendingWorker()
    ab.worker = worker
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert worker.interrupted and ab.worker is worker
    ab.worker = None


def test_a_rejects_file_changed_during_decode(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    previous = ab.image
    paths[0].write_bytes(b'changed')
    ab._decoded(ab.token, str(paths[0]), Image.new('RGB',(10,10),'red'), (10,10))
    assert ab.image is previous


@pytest.mark.parametrize('closing', [False, True])
def test_active_a_rapid_selection_and_close_reject_late_decoder(window, monkeypatch, closing):
    from birdstamp.gui import editor_ab_preview
    from birdstamp.gui.editor_preview_decode_worker import EditorPreviewDecodeWorker
    paths, target = install_sequence(window, monkeypatch)
    started, release = threading.Event(), threading.Event()
    delivered = []

    class DelayedWorker(EditorPreviewDecodeWorker):
        def run(self):
            with Image.open(self._path) as source:
                image = source.convert('RGB')
            delivered.append(image)
            if self._path == paths[0]:
                started.set()
                release.wait(6)
            self.decoded.emit(self._token, str(self._path), image, image.size)

    monkeypatch.setattr(editor_ab_preview, 'EditorPreviewDecodeWorker', DelayedWorker)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    try:
        wait_until(started.is_set)
        old = ab.worker
        ab.activate('a')
        assert ab.route_photo_selection(paths[1])
        assert ab.worker is old and old.isInterruptionRequested()
        assert ab.image is None
        if closing:
            event = QCloseEvent()
            window.closeEvent(event)
            assert not event.isAccepted() and ab.worker is old
    finally:
        release.set()
        finish(ab)
    with pytest.raises(ValueError):
        delivered[0].getpixel((0, 0))
    if closing:
        assert ab.image is None and not ab.pending
    else:
        assert ab.path == paths[1] and window.current_path == paths[0]
        assert ab.image.pixelColor(0, 0).getRgb()[:3] == target.getpixel((0, 0))


def test_canvas_renders_yellow_success_and_red_failure_and_clears_them():
    from PyQt6.QtGui import QPixmap, QColor
    from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState, EditorPreviewOverlayOptions
    canvas = EditorPreviewCanvas()
    pixmap = QPixmap(100,100)
    pixmap.fill(QColor('black'))
    canvas.set_source_pixmap(pixmap)
    canvas.apply_overlay_options(EditorPreviewOverlayOptions(show_reference_regions=True))
    canvas.apply_overlay_state(EditorPreviewOverlayState(reference_diagnostics=(
        ((.1,.1,.35,.35),'1',True), ((.55,.55,.85,.85),'2',False))))
    image = canvas.render_source_pixmap_with_overlays().toImage()
    colors = {image.pixelColor(x,y).name() for x in range(100) for y in range(100)}
    assert '#ffb703' in colors and '#ff5252' in colors
    canvas.set_source_pixmap(None)
    assert canvas._reference_diagnostics == ()
