"""One maximum common rectangle for padded overlays, cache and native export."""
from dataclasses import replace
import json
from threading import Event

import numpy as np
import pytest
from PIL import Image
from PyQt6.QtGui import QColor, QPixmap

from test_dejitter_tab import sequence, setup_tab, analyze
from test_editor_dejitter import window, _APP
from test_reference_tracking import wait_until
from test_editor_ab_preview import finish
from test_rigid_alignment import rotated_seeds
from test_sequence_preview_cache import run_worker, fail_recompute
from birdstamp.export_stage.sequence_intersection import (
    compute_intersection_box, normalized_intersection_box, intersection_export_sequence,
    compute_union_box, normalized_union_box,
)
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview, render_sequence_preview_frame
from birdstamp.export_stage.sequence_export import export_aligned_sequence
from birdstamp.export_stage.video_export_cancelled_error import VideoExportCancelledError
from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState
from birdstamp.gui import editor_sequence_preview_worker as workers


@pytest.mark.parametrize('mode', ['translation', 'rigid'])
def test_padded_intersection_export_matches_unpadded_analysis(sequence,tmp_path,mode):
    seeds, _ = sequence
    seeds = [replace(s, settings={**s.settings, 'dejitter_alignment_mode':mode,
                                 'dejitter_pad_to_union':True}) for s in seeds]
    padded = prepare_sequence_preview(seeds,cancel_event=Event())
    expected = prepare_sequence_preview(
        [replace(s,settings={**s.settings,'dejitter_pad_to_union':False}) for s in seeds],cancel_event=Event())
    assert padded.intersection_box == (5,3,200,160)
    assert padded.union_box == (0,0,205,163)
    assert expected.union_box == (-5,-3,200,160)
    assert compute_union_box(intersection_export_sequence(padded)) == expected.union_box
    assert intersection_export_sequence(padded).union_box == expected.union_box
    assert normalized_intersection_box(padded) == (5/205,3/163,200/205,160/163)
    original_boxes = dict(padded.pixel_boxes)
    folder = export_aligned_sequence(padded,tmp_path,cancel_event=Event(),intersection_only=True)
    assert padded.output_size == (205,163) and padded.pixel_boxes == original_boxes
    for seed,path in zip(seeds,sorted(folder.glob('*.png'))):
        with Image.open(path) as actual,render_sequence_preview_frame(expected,seed.path).image as expected_image:
            assert actual.size == (195,157)
            np.testing.assert_array_equal(actual,expected_image)
    full = export_aligned_sequence(padded,tmp_path,cancel_event=Event())
    with Image.open(next(full.glob('*.png'))) as image:
        assert image.size == padded.output_size


def test_rotated_intersection_reuses_transform_and_single_native_render(rotated_seeds,tmp_path):
    seeds=[replace(s,settings={**s.settings,'dejitter_pad_to_union':True}) for s in rotated_seeds]
    padded=prepare_sequence_preview(seeds,cancel_event=Event())
    unpadded=prepare_sequence_preview(rotated_seeds,cancel_event=Event())
    cropped=intersection_export_sequence(padded)
    assert cropped.output_size == unpadded.output_size
    assert cropped.canvas_box == unpadded.canvas_box
    assert cropped.alignments is padded.alignments
    assert padded.union_box == (0,0,*padded.output_size)
    assert cropped.union_box == compute_union_box(cropped) == unpadded.union_box
    assert padded.output_size != cropped.output_size
    folder=export_aligned_sequence(padded,tmp_path,cancel_event=Event(),intersection_only=True)
    for seed,path in zip(seeds,sorted(folder.glob('*.png'))):
        with Image.open(path) as actual,render_sequence_preview_frame(unpadded,seed.path).image as expected:
            np.testing.assert_array_equal(actual,expected)


def test_no_common_rectangle_rejects_only_intersection_export_before_creating_files(sequence,tmp_path):
    _,original=sequence
    keys=list(original.jobs)
    jobs={key:replace(job,settings={**job.settings,'dejitter_pad_to_union':True})
          for key,job in original.jobs.items()}
    padded=replace(original,jobs=jobs,output_size=(410,160),
                   pixel_boxes={keys[0]:(0,0,410,160),keys[1]:(-210,0,200,160)},intersection_box=None)
    assert compute_intersection_box(padded) is None
    assert compute_union_box(padded) == (0,0,410,160)
    destination=tmp_path/'out'
    destination.mkdir()
    with pytest.raises(ValueError,match='没有共同有效区域'):
        export_aligned_sequence(padded,destination,cancel_event=Event(),intersection_only=True)
    assert not list(destination.iterdir())
    assert export_aligned_sequence(padded,destination,cancel_event=Event()).is_dir()
    with pytest.raises(VideoExportCancelledError):
        compute_intersection_box(padded,cancelled=lambda:True)


@pytest.mark.parametrize('version', [2,3,4])
def test_cache_roundtrip_and_old_cache_geometry_upgrade(rotated_seeds,tmp_path,monkeypatch,version):
    seeds=[replace(s,settings={**s.settings,'dejitter_pad_to_union':True}) for s in rotated_seeds]
    cache=SequencePreviewCache(tmp_path/'cache')
    first,quick,errors=run_worker(seeds,cache)
    assert first and not errors
    sequence=first[0][0]
    manifest=cache.root/sequence.input_key/'manifest.json'
    raw=json.loads(manifest.read_text(encoding='utf-8'))
    raw['version']=version
    if version==2:
        del raw['intersection_box']
    if version<4:
        del raw['union_box']
    manifest.write_text(json.dumps(raw),encoding='utf-8')
    monkeypatch.setattr(workers,'prepare_sequence_preview',fail_recompute)
    monkeypatch.setattr(workers,'render_sequence_preview_frame',fail_recompute)
    restored,_,errors=run_worker(seeds,cache,restore_only=True)
    assert restored and not errors
    assert restored[0][0].intersection_box == sequence.intersection_box
    assert restored[0][0].union_box == sequence.union_box
    assert restored[0][1].image == first[0][1].image
    raw['version']=3
    raw['intersection_box']=[-1,0,20,30]
    manifest.write_text(json.dumps(raw),encoding='utf-8')
    assert cache.load(seeds) is None
    raw['version']=4
    raw['intersection_box']=sequence.intersection_box
    raw['union_box']=[0,0,1,1]
    manifest.write_text(json.dumps(raw),encoding='utf-8')
    assert cache.load(seeds) is None


def test_options_refresh_ab_without_invalidating_analysis_and_persist_without_cache(window,monkeypatch):
    _,_,seeds=setup_tab(window,monkeypatch)
    for seed in seeds:
        seed.settings['dejitter_pad_to_union']=True
    analyze(window)
    sequence=window._sequence_preview
    epoch=window._sequence_epoch
    key=window._sequence_cache_key
    expected=normalized_intersection_box(sequence)
    assert window.preview_label.canvas._intersection_box == expected
    assert window.preview_label.canvas._union_box == normalized_union_box(sequence)
    assert window.dejitter_bounds_overview.union_box == sequence.union_box
    assert window.dejitter_bounds_overview.intersection_box == sequence.intersection_box
    assert '205 × 163' in window.dejitter_intersection_status.text()
    assert '195 × 157' in window.dejitter_intersection_status.text()
    ab=window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    ab.mode.setCurrentIndex(1)
    finish(ab)
    assert ab.preview.canvas._intersection_box == expected
    assert ab.preview.canvas._union_box == normalized_union_box(sequence)
    window.dejitter_show_intersection_check.setChecked(False)
    window.dejitter_export_intersection_check.setChecked(True)
    assert window.preview_label.canvas._intersection_box is None
    assert ab.preview.canvas._intersection_box is None
    assert window.preview_label.canvas._union_box is None
    assert ab.preview.canvas._union_box is None
    assert window._sequence_preview is sequence and window._sequence_epoch == epoch
    assert window._sequence_cache_key == key and window.dejitter_export_btn.isEnabled()
    state=window._collect_sequence_workspace_state()
    state['input_key']=None
    window.dejitter_show_intersection_check.setChecked(True)
    window.dejitter_export_intersection_check.setChecked(False)
    window._restore_sequence_workspace_state(state)
    assert not window.dejitter_show_intersection_check.isChecked()
    assert window.dejitter_export_intersection_check.isChecked()
    sequence.intersection_box=None
    window._update_dejitter_controls()
    assert not window.dejitter_export_btn.isEnabled()
    assert '没有共同有效区域' in window.dejitter_intersection_status.text()
    window.dejitter_export_intersection_check.setChecked(False)
    assert window.dejitter_export_btn.isEnabled()


def test_gui_export_option_reaches_worker_and_writes_cropped_output(window,monkeypatch,tmp_path):
    _,_,seeds=setup_tab(window,monkeypatch)
    for seed in seeds:
        seed.settings['dejitter_pad_to_union']=True
    analyze(window)
    destination=tmp_path/'export'
    destination.mkdir()
    from birdstamp.gui import editor_dejitter
    monkeypatch.setattr(editor_dejitter.QFileDialog,'getExistingDirectory',lambda *a:str(destination))
    window.dejitter_export_intersection_check.setChecked(True)
    window.dejitter_export_btn.click()
    assert window._sequence_worker.intersection_only
    wait_until(lambda:window._sequence_worker is None)
    outputs=list(destination.glob('*/*.png'))
    assert len(outputs)==2
    for path in outputs:
        with Image.open(path) as image:
            assert image.size==(195,157)
    assert window._sequence_preview.output_size==(205,163)


def test_intersection_overlay_is_independent_of_crop_shade_and_grid():
    canvas=EditorPreviewCanvas()
    pixmap=QPixmap(120,120)
    pixmap.fill(QColor('black'))
    canvas.set_source_pixmap(pixmap,log_performance=False)
    canvas.set_composition_grid_mode('thirds')
    canvas.apply_overlay_state(EditorPreviewOverlayState(crop_effect_box=(0,0,1,1),intersection_box=(.2,.2,.8,.8)))
    rendered=canvas.render_source_pixmap_with_overlays().toImage()
    assert any(rendered.pixelColor(24,y)!=QColor('black') for y in range(50,70))
    assert rendered.pixelColor(40,100)!=QColor('black')  # Grid is not cropped by the overlay.
    canvas.apply_overlay_state(EditorPreviewOverlayState(crop_effect_box=(0,0,1,1)))
    cleared=canvas.render_source_pixmap_with_overlays().toImage()
    assert cleared.pixelColor(24,60)==QColor('black')
    canvas.close()
