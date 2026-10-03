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


@pytest.mark.parametrize('suffix, modes', [
    ('.ARW', ('raw', 'denoised', 'default')),
    ('.jpg', ('denoised', 'default')),
])
def test_viewport_source_button_cycles_supported_modes(window, tmp_path, suffix, modes):
    from app_common.preview_canvas import PreviewWithStatusBar
    from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas
    from birdstamp.gui.editor_preview_viewport import PreviewViewportPanel

    panel = PreviewViewportPanel('测试', PreviewWithStatusBar(canvas=EditorPreviewCanvas()))
    try:
        panel.set_path(tmp_path / f'照片{suffix}')
        emitted = []
        panel.source_mode_changed.connect(emitted.append)
        labels = {'default': '默认预览', 'raw': '显示 RAW', 'denoised': '显示降噪'}
        for mode in modes:
            panel.source_button.click()
            assert panel.source_mode() == mode
            assert panel.source_button.text() == labels[mode]
        assert emitted == list(modes)
        panel.mode.setCurrentIndex(1)
        assert not panel.source_button.isEnabled()
        panel.source_button.click()
        assert panel.source_mode() == modes[-1]
        panel.mode.setCurrentIndex(0)
        assert panel.source_button.isEnabled()
        panel.set_path(tmp_path / '视频.mp4')
        assert panel.source_button.isHidden()
    finally:
        panel.close()
        panel.deleteLater()


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


def test_raw_toggle_is_independent_per_viewport_and_keeps_export_source(window, monkeypatch, tmp_path):
    import io
    from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY
    from birdstamp.gui.editor_core import extract_focus_box_for_display
    from birdstamp.decoders import image_decoder
    from test_sequence_transport import populate

    raw = tmp_path / '鸟.arw'
    raw.write_bytes(b'RAW placeholder')
    jpeg = tmp_path / '普通.jpg'
    Image.new('RGB', (100, 80), 'green').save(jpeg)
    stream = io.BytesIO()
    Image.new('RGB', (1600, 800), 'blue').save(stream, format='JPEG')
    monkeypatch.setattr('app_common.thumb_stream.get_raw_preview_jpeg', lambda _path: stream.getvalue())
    def raw_pixels(*args, **kwargs):
        image = Image.new('RGB', (2400, 1200), 'red')
        image.info[RAW_FOCUS_CROP_KEY] = (.1, .1, .9, .9)
        return image
    monkeypatch.setattr(image_decoder, '_decode_raw', raw_pixels)
    metadata = {'Make': 'SONY', 'ExifImageWidth': 1600, 'ExifImageHeight': 800,
                'SubjectArea': [400, 200, 160, 80]}
    original_focus = extract_focus_box_for_display(metadata, 1600, 800)
    raw_focus = tuple(.1 + value * .8 for value in original_focus)
    monkeypatch.setattr(window, '_metadata_snapshot_for_selection', lambda _path: dict(metadata))
    window.raw_metadata_cache[path_key(raw)] = dict(metadata)
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *_args: None)
    populate(window, [raw, jpeg])
    window.show()
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    ab.select_a(raw)
    window._on_photo_selected(window._find_photo_item_by_path(raw), None, target_view='b')
    wait_until(lambda: window._preview_decode_worker is None and window.current_source_image is not None)
    finish(ab)
    assert ab.a_panel.show_raw.isVisible() and ab.b_panel.show_raw.isVisible()
    assert window.current_source_image.getpixel((0, 0))[2] > 200

    ab.b_panel.show_raw.click()
    wait_until(lambda: window._preview_decode_worker is None and window.current_source_image is not None)
    assert window.current_source_image.getpixel((0, 0)) == (255, 0, 0)
    assert not ab.a_panel.show_raw.isChecked()
    assert window.preview_label.canvas._focus_box == pytest.approx(raw_focus)
    assert ab.preview.canvas._focus_box == pytest.approx(original_focus)

    ab.a_panel.show_raw.click()
    finish(ab)
    assert ab.image.pixelColor(0, 0).red() == 255
    assert ab.preview.canvas._focus_box == pytest.approx(raw_focus)
    ab.select_a(jpeg)
    finish(ab)
    assert ab.a_panel.source_button.isVisible()
    assert ab.a_panel.source_button.text() == '默认预览'
    ab.select_a(raw)
    finish(ab)
    assert ab.a_panel.show_raw.isChecked() and ab.a_panel.show_raw.isVisible()
    ab.mode.setCurrentIndex(1)
    assert not ab.a_panel.source_button.isEnabled()
    ab.mode.setCurrentIndex(0)
    finish(ab)
    assert ab.a_panel.show_raw.isVisible()

    with image_decoder.decode_image(raw) as exported:
        assert exported.size == (1600, 800)
        assert exported.getpixel((0, 0))[2] > 200


def test_source_cycle_denoised_pixels_are_independent_and_keep_export_source(window, monkeypatch, tmp_path):
    from birdstamp.decoders import image_decoder
    from image_denoise.export import _prepare_sidecar
    from test_sequence_transport import populate

    source = tmp_path / '中文原图.jpg'
    Image.new('RGB', (100, 100), 'red').save(source)
    output = tmp_path / 'denoised' / '中文原图_denoised.jpg'
    output.parent.mkdir()
    Image.new('RGB', (100, 100), 'blue').save(output)
    _prepare_sidecar(source, output.with_suffix('.xmp'), 100, 100, camera_crop=(.1, .2, .9, .8))
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *_args: None)
    metadata = {'Make': 'SONY', 'ExifImageWidth': 100, 'ExifImageHeight': 100,
                'SubjectArea': [50, 50, 20, 20]}
    monkeypatch.setattr(window, '_metadata_snapshot_for_selection', lambda _path: dict(metadata))
    window.raw_metadata_cache[path_key(source)] = dict(metadata)
    bird = (.2, .3, .7, .8)
    window._bird_box_cache[window._source_signature(source)] = bird
    populate(window, [source])
    window._on_photo_selected(window._find_photo_item_by_path(source), None, target_view='b')
    wait_until(lambda: window._preview_decode_worker is None and window.current_source_image is not None)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    original_focus = ab.preview.canvas._focus_box

    ab.b_panel.source_button.click()
    wait_until(lambda: window._preview_decode_worker is None and window.current_source_image is not None)
    assert ab.b_panel.source_mode() == 'denoised'
    assert ab.b_panel.source_button.text() == '显示降噪'
    assert window.current_source_image.getpixel((0, 0))[2] > 240
    assert window.current_path == source
    assert ab.a_panel.source_mode() == 'default'
    assert ab.image.pixelColor(0, 0).red() > 240
    assert ab.preview.canvas._focus_box == pytest.approx(original_focus)
    assert ab.preview.canvas._bird_box == pytest.approx(bird)
    expected_focus = tuple((.1 + value * .8) if index % 2 == 0 else (.2 + value * .6)
                           for index, value in enumerate(original_focus))
    assert window.preview_label.canvas._focus_box == pytest.approx(expected_focus)

    ab.a_panel.source_button.click()
    finish(ab)
    assert ab.a_panel.source_mode() == 'denoised'
    assert ab.image.pixelColor(0, 0).blue() > 240
    assert ab.preview.canvas._focus_box == pytest.approx(expected_focus)
    assert ab.preview.canvas._bird_box == pytest.approx((.26, .38, .66, .68))
    assert '显示降噪' in ab.preview._status_label.text()
    ab.a_panel.source_button.click()
    finish(ab)
    assert ab.a_panel.source_mode() == 'default'
    assert ab.image.pixelColor(0, 0).red() > 240
    assert window.current_source_image.getpixel((0, 0))[2] > 240
    with image_decoder.decode_image(source) as exported:
        assert exported.getpixel((0, 0))[0] > 240


def test_a_missing_denoised_falls_back_without_changing_requested_mode(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *_args: None)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    ab.a_panel.set_source_mode('denoised')
    finish(ab)
    assert ab.path == paths[0]
    assert ab.image is not None
    assert ab.a_panel.source_mode() == 'denoised'
    assert ab.actual_source_mode == 'default'
    assert '未找到降噪成片' in ab.preview._status_label.text()


def test_a_playback_source_changes_defer_output_lookup_until_stop(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    monkeypatch.setattr(window, '_schedule_async_bird_detect', lambda *_args: None)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    from birdstamp.gui import editor_ab_preview
    real_worker = editor_ab_preview.EditorPreviewDecodeWorker
    modes = []
    def worker(*args, **kwargs):
        modes.append(kwargs.get('source_mode'))
        return real_worker(*args, **kwargs)
    monkeypatch.setattr(editor_ab_preview, 'EditorPreviewDecodeWorker', worker)
    monkeypatch.setattr(window, '_sequence_fast_preview_active', lambda: True)
    ab.a_panel.set_source_mode('denoised')
    ab.select_a(paths[1])
    ab._start()
    assert ab.worker is None and ab.pending
    assert modes == []
    monkeypatch.setattr(window, '_sequence_fast_preview_active', lambda: False)
    finish(ab)
    assert modes == ['denoised']


def test_a_source_change_invalidates_late_denoised_frame(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    ab.a_panel.set_source_mode('denoised')
    previous_token = ab.token
    ab.a_panel.set_source_mode('default')
    stale = Image.new('RGB', (20, 10), 'blue')
    ab._decoded(previous_token, str(paths[0]), stale, stale.size)
    assert ab.actual_source_mode == 'default'
    with pytest.raises(ValueError):
        stale.getpixel((0, 0))
    finish(ab)


def test_a_playback_rejects_late_full_frame_even_before_next_cached_selection(window, monkeypatch):
    paths, _ = install_sequence(window, monkeypatch)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    retained = ab.image
    monkeypatch.setattr(window, '_sequence_fast_preview_active', lambda: True)
    stale = Image.new('RGB', (20, 10), 'blue')
    ab._decoded(ab.token, str(paths[0]), stale, stale.size)
    assert ab.image is retained
    with pytest.raises(ValueError):
        stale.getpixel((0, 0))


def test_a_source_geometry_maps_reference_overlays_and_inverse_edits(window, monkeypatch):
    from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION
    from birdstamp.gui.preview_source_geometry import camera_to_preview_box

    paths, _, _ = setup_tab(window, monkeypatch)
    window._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    crop = (.1, .2, .9, .8)
    ab.camera_crop_box = crop
    original = window._dejitter_reference_regions
    ab._display()
    displayed = ab.preview.canvas.reference_regions()
    for source, shown in zip(original, displayed):
        assert shown == pytest.approx(camera_to_preview_box(source, crop))
    ab._edit_reference_regions(displayed)
    for source, saved in zip(original, window._dejitter_reference_regions):
        assert saved == pytest.approx(source)
    assert window._dejitter_reference_source == str(paths[0])

    ab.select_a(paths[1])
    finish(ab)
    ab.camera_crop_box = crop
    corrected = (.15, .25, .35, .45)
    ab._edit_match(0, camera_to_preview_box(corrected, crop))
    assert window._manual_boxes_for_path(paths[1])[0] == pytest.approx(corrected)
    # 传感器外圈不属于相机预览，不能把无纹理选区覆盖成有效手动匹配。
    ab._edit_match(0, (0, 0, .05, .1))
    assert window._manual_boxes_for_path(paths[1])[0] == pytest.approx(corrected)
    ab._edit_match(0, None)
    assert not any(window._manual_boxes_for_path(paths[1]))


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
        # 全列表预取可能已为新照片准备好快速帧；旧 worker 仍须保持所有权到退出。
        assert ab.path == paths[1]
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
        assert not ab.pending
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
