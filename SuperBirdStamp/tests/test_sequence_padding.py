"""补边画幅保持原像素、统一几何；默认仍取完整画面的共同交集。"""
from dataclasses import replace
import threading

import numpy as np
import pytest
from PIL import Image

from test_dejitter_tab import sequence, setup_tab, analyze
from test_editor_dejitter import window, _APP
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, render_sequence_preview_frame, common_alignment_crop
from birdstamp.export_stage.sequence_export import export_aligned_sequence
from birdstamp.gui.editor_sequence_preview_worker import EditorSequencePreviewWorker
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui import editor_core
from birdstamp.image_dejitter.sequence_geometry import aligned_crop_plan, source_normalized_crop, render_aligned_thumbnail
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult


def test_padding_preserves_every_source_pixel_and_preview_equals_export(sequence, tmp_path):
    seeds, original = sequence
    seeds = [replace(s, settings={**s.settings, 'dejitter_pad_to_union': True}) for s in seeds]
    padded = prepare_sequence_preview(seeds, cancel_event=threading.Event())
    assert original.output_size == (195, 157)
    assert padded.output_size == (205, 163)
    assert padded.input_key != original.input_key
    folder = export_aligned_sequence(padded, tmp_path, cancel_event=threading.Event())
    for seed, output in zip(seeds, sorted(folder.glob('*.png'))):
        box = padded.pixel_boxes[path_key(seed.path)]
        with Image.open(seed.path) as source, Image.open(output) as exported, render_sequence_preview_frame(padded, seed.path).image as preview:
            assert exported.size == (205, 163)
            np.testing.assert_array_equal(exported, preview)
            x, y = -box[0], -box[1]
            np.testing.assert_array_equal(np.asarray(exported)[y:y+source.height, x:x+source.width], source)
            mask = np.ones((163, 205), dtype=bool)
            mask[y:y+source.height, x:x+source.width] = False
            assert not np.asarray(exported)[mask].any()


def test_only_one_matching_region_is_enough_and_crop_is_not_region_intersection():
    regions = ((.05, .1, .15, .2), (.8, .7, .9, .8))
    results = {'a': RegionTrackingResult(regions),
               'b': RegionTrackingResult((None, (.85, .67, .95, .77)))}
    sizes = {'a': (200, 100), 'b': (200, 100)}
    boxes, size = common_alignment_crop(regions, results, sizes, (200, 100))
    assert size == (190, 97)
    assert boxes['a'] == (0, 3, 190, 100)
    boxes, size = common_alignment_crop(regions, results, sizes, (200, 100), pad_to_union=True)
    assert size == (210, 103)
    assert boxes['a'] == (-10, 0, 200, 103)


def test_padding_can_handle_no_common_area():
    regions = ((.1, .1, .2, .2),)
    tracked = {'a': RegionTrackingResult(regions), 'b': RegionTrackingResult(((1.2, .1, 1.3, .2),))}
    sizes = {'a': (100, 100), 'b': (100, 100)}
    with pytest.raises(ValueError, match='共同覆盖'):
        common_alignment_crop(regions, tracked, sizes, (100, 100))
    boxes, size = common_alignment_crop(regions, tracked, sizes, (100, 100), pad_to_union=True)
    assert size == (210, 100)
    assert boxes['a'] == (-110, 0, 100, 100)


def test_overlay_and_quick_preview_use_same_extended_source_geometry():
    box = (-20, -10, 220, 180)
    crop, (pt, pb, pl, pr) = aligned_crop_plan((200, 160), box)
    assert (pt, pb, pl, pr) == (10, 20, 20, 20)
    mapped = editor_core.transform_source_box_after_crop_padding(
        (.2, .25, .6, .75), crop_box=crop, source_width=200, source_height=160,
        pt=pt, pb=pb, pl=pl, pr=pr)
    assert mapped == pytest.approx((60/240, 50/190, 140/240, 130/190))
    assert source_normalized_crop((200, 160), box) == (-.1, -.0625, 1.1, 1.125)
    with Image.new('RGB', (200, 160), 'white') as source:
        with render_aligned_thumbnail(source, source.size, box, 240) as small, source.crop(box) as full:
            np.testing.assert_array_equal(small, full)
        with render_aligned_thumbnail(source, source.size, (-20000, -10000, 200, 160), 512) as small:
            assert max(small.size) == 512


def test_worker_quick_and_full_share_padding_geometry(sequence):
    seeds, _ = sequence
    seeds = [replace(s, settings={**s.settings, 'dejitter_pad_to_union': True}) for s in seeds]
    worker = EditorSequencePreviewWorker(token=1, path=seeds[0].path, seeds=seeds)
    quick, full, errors = [], [], []
    worker.quick_ready.connect(lambda token, seq, frames: quick.append((seq, frames)))
    worker.ready.connect(lambda token, seq, frame: full.append(frame))
    worker.failed.connect(lambda token, error: errors.append(error))
    worker.run()
    assert not errors
    first = quick[0][1][path_key(seeds[0].path)]
    assert first.crop_plan == full[0].crop_plan
    assert first.output_size == full[0].output_size == (205, 163)


def test_padding_settings_persist_and_invalidate_analysis(window, monkeypatch):
    _, _, seeds = setup_tab(window, monkeypatch)
    assert not window.dejitter_pad_to_union_check.isChecked()
    analyze(window)
    window.dejitter_view_tabs.setCurrentIndex(0)
    canvas = window.preview_label.canvas
    assert canvas._alignment_crop_box == (0, 3/160, 195/200, 1)
    window.dejitter_pad_to_union_check.setChecked(True)
    assert window._sequence_preview is None
    assert not window.dejitter_export_btn.isEnabled()
    snapshot = window._build_current_render_settings()
    assert snapshot['dejitter_pad_to_union'] is True
    assert 'dejitter_pad_to_union' not in window._photo_override_settings_from_snapshot(snapshot)
    window.dejitter_pad_to_union_check.setChecked(False)
    window._restore_dejitter_reference_from_settings(snapshot)
    assert window.dejitter_pad_to_union_check.isChecked()
    for seed in seeds:
        seed.settings['dejitter_pad_to_union'] = True
    analyze(window)
    assert window._sequence_preview.output_size == (205, 163)
    window.dejitter_view_tabs.setCurrentIndex(0)
    assert canvas._alignment_crop_box is None


def test_padded_ab_auxiliary_layers_match_main_result(window, monkeypatch):
    from test_editor_ab_preview import finish
    paths, _, seeds = setup_tab(window, monkeypatch)
    window.dejitter_pad_to_union_check.setChecked(True)
    for seed in seeds:
        seed.settings['dejitter_pad_to_union'] = True
    bird = (.2, .25, .6, .75)
    window._bird_box_cache[window._source_signature(paths[0])] = bird
    analyze(window)
    ab = window.ab_preview
    ab.enabled.setChecked(True)
    ab.mode.setCurrentIndex(1)
    finish(ab)
    main, compare = window.preview_label.canvas, ab.preview.canvas
    assert compare._focus_box is not None
    assert compare._focus_box == main._focus_box
    assert compare._bird_box == main._bird_box
    assert compare._reference_diagnostics == main._reference_diagnostics
    expected = ((.2*200+5)/205, .25*160/163, (.6*200+5)/205, .75*160/163)
    assert compare._bird_box == pytest.approx(expected)
    window.dejitter_pad_to_union_check.setChecked(False)
    assert ab.frame is None and ab.image is None
    assert ab.path == paths[0]


def test_edit_common_outline_respects_toggle_and_invalidates_on_source_change(window, monkeypatch):
    paths, _, _ = setup_tab(window, monkeypatch)
    analyze(window)
    window._set_dejitter_view('edit')
    assert window.preview_label.canvas._alignment_crop_box is not None
    window.show_crop_effect_check.setChecked(False)
    assert not window.preview_label.canvas._show_crop_effect
    window.show_crop_effect_check.setChecked(True)
    assert window.preview_label.canvas._show_crop_effect
    paths[1].with_suffix('.xmp').write_text('changed', encoding='utf-8')
    window._refresh_preview_label()
    assert window._sequence_preview is None
    assert window.preview_label.canvas._alignment_crop_box is None
