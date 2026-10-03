"""RAW/降噪预览的参考区显示、编辑和手动修正始终保存相机原图坐标。"""
import pytest

from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, map_camera_focus_box
from birdstamp.gui.edit_modes import EDIT_MODE_REFERENCE_REGION
from birdstamp.gui.editor_utils import path_key
from test_editor_dejitter import window, _APP
from test_dejitter_tab import setup_tab, analyze
from test_reference_tracking import REGIONS, wait_until
from test_sequence_transport import populate


@pytest.mark.parametrize('surface', ['ordinary', 'dejitter', 'ab'])
def test_b_reference_regions_roundtrip_actual_pixels_without_persisting_camera_margins(window, monkeypatch, surface):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    if surface != 'dejitter':
        window.export_tabs.setCurrentIndex(0)
    if surface == 'ab':
        window.ab_preview.enabled.setChecked(True)
        wait_until(lambda: window.ab_preview.worker is None and not window.ab_preview.pending)
    wait_until(lambda: window._preview_decode_worker is None)
    crop = (.1, .2, .9, .8)
    window.current_source_image.info[RAW_FOCUS_CROP_KEY] = crop
    window._preview_outer_pad = (10, 30, 20, 40)
    box = (.2, .3, .7, .8)
    mapped = map_camera_focus_box(box, crop)
    if surface == 'ordinary':
        width, height = window.current_source_image.size
        mapped = ((mapped[0] * width + 20) / (width + 60),
                  (mapped[1] * height + 10) / (height + 40),
                  (mapped[2] * width + 20) / (width + 60),
                  (mapped[3] * height + 10) / (height + 40))
    assert window._reference_regions_source_to_preview((box,))[0] == pytest.approx(mapped)
    assert window._reference_region_preview_to_source(mapped) == pytest.approx(box)
    window._on_canvas_reference_region_changed((mapped,))
    assert window._dejitter_reference_regions[0] == pytest.approx(box)
    assert window._dejitter_reference_source == str(paths[0])
    # 裁去的相机外围没有源图纹理；不能成为持久化的参考区。
    current = window._reference_regions_source_to_preview((box,))[0]
    window._on_canvas_reference_region_changed((current, (0, 0, .01, .01)))
    assert len(window._dejitter_reference_regions) == 1
    assert window._dejitter_reference_regions[0] == pytest.approx(box)


def test_b_manual_region_edits_convert_once_and_margin_edit_preserves_previous_match(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    window.photo_list.setCurrentItem(window._find_photo_item_by_path(paths[1]))
    wait_until(lambda: window._preview_decode_worker is None and window.current_source_image is not None)
    window._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
    crop = (.1, .2, .9, .8)
    window.current_source_image.info[RAW_FOCUS_CROP_KEY] = crop
    camera_box = (.2, .25, .5, .65)
    displayed = map_camera_focus_box(camera_box, crop)
    window.preview_label.canvas.reference_match_edited.emit(0, displayed)
    assert window._manual_boxes_for_path(paths[1])[0] == pytest.approx(camera_box)
    window.preview_label.canvas.reference_match_edited.emit(0, (0, 0, .01, .01))
    assert window._manual_boxes_for_path(paths[1])[0] == pytest.approx(camera_box)
    # 已转为原图坐标的 A 提交不能再借用 B 的 RAW 几何转换一遍。
    window._commit_manual_region_match(paths[1], 0, camera_box, original=True)
    assert window._manual_boxes_for_path(paths[1])[0] == pytest.approx(camera_box)
    window.preview_label.canvas.reference_match_edited.emit(0, None)
    assert not any(window._manual_boxes_for_path(paths[1]))


def test_b_dejitter_source_overlays_follow_actual_pixels_and_quick_frame_resets_geometry(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    populate(window, paths)
    analyze(window)
    sequence = window._sequence_preview
    original_boxes = dict(sequence.pixel_boxes)
    window._set_dejitter_view('edit')
    wait_until(lambda: window._preview_decode_worker is None and window.current_source_image is not None)
    window.current_path = paths[1]
    tracked = sequence.tracking[path_key(paths[1])]
    monkeypatch.setattr(window, '_tracking_result_for_current', lambda: tracked)
    window._refresh_preview_label()
    canvas = window.preview_label.canvas
    baseline = tuple(canvas._reference_diagnostics)
    baseline_crop = canvas._crop_effect_box
    assert baseline
    crop = (.1, .2, .9, .8)
    window.current_source_image.info[RAW_FOCUS_CROP_KEY] = crop
    window._refresh_preview_label()
    assert len(canvas._reference_diagnostics) == len(baseline)
    for before, after in zip(baseline, canvas._reference_diagnostics):
        assert after[0] == pytest.approx(map_camera_focus_box(before[0], crop))
        assert after[1:] == before[1:]
    assert canvas._crop_effect_box == pytest.approx(map_camera_focus_box(baseline_crop, crop))
    assert sequence.pixel_boxes == original_boxes
    # 源缩略图没有 RAW 边距，即使仍保留旧的高清 PIL 源也不能继续套用其几何。
    monkeypatch.setattr(window, '_sequence_fast_preview_active', lambda: True)
    window._refresh_preview_label()
    assert canvas._reference_diagnostics == baseline
    assert canvas._crop_effect_box == baseline_crop


def test_region_texture_hint_samples_actual_pixel_region_after_source_switch(window, monkeypatch):
    setup_tab(window, monkeypatch)
    from birdstamp.image_dejitter import aperture
    definition = (*window._reference_tracking_input()[:3], 'subject_local', '')
    monkeypatch.setattr(window, '_reference_tracking_input', lambda: definition)
    captured = []
    monkeypatch.setattr(aperture, 'classify_regions', lambda image, regions: captured.append(regions) or ())
    monkeypatch.setattr(aperture, 'texture_summary', lambda textures: '测试')
    monkeypatch.setattr(aperture, 'aperture_problems', lambda textures: ())
    window._region_texture_hint()
    assert captured[-1] == REGIONS
    crop = (.1, .2, .9, .8)
    window.current_source_image.info[RAW_FOCUS_CROP_KEY] = crop
    window._region_texture_hint()
    for actual, original in zip(captured[-1], REGIONS):
        assert actual == pytest.approx(map_camera_focus_box(original, crop))
