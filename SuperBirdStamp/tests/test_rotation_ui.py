"""Rotation mode persistence, task epochs and shared A/B overlay geometry."""
from threading import Event
import numpy as np
from PIL import Image
from PyQt6.QtGui import QColor, QPixmap

from test_editor_dejitter import window, _APP
from test_reference_tracking import install_sequence, wait_until
from test_dejitter_tab import setup_tab, analyze
from test_rigid_alignment import rotated_seeds, REGIONS
from test_editor_ab_preview import finish
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState
from birdstamp.gui.editor_tracking_overlay import polygon_bounds


def test_new_default_old_workspace_and_mode_workspace_roundtrip(window,monkeypatch,tmp_path):
    assert window.dejitter_alignment_combo.currentData() == 'rigid'
    window._restore_dejitter_reference_from_settings({})
    assert window.dejitter_alignment_combo.currentData() == 'translation'
    window.dejitter_alignment_combo.setCurrentIndex(0)
    saved=window._clone_render_settings(window._build_current_render_settings())
    assert saved['dejitter_alignment_mode'] == 'rigid'
    assert 'dejitter_alignment_mode' not in window._photo_override_settings_from_snapshot(saved)
    paths,target=install_sequence(window,monkeypatch)
    target.close()
    existing=set()
    for i,path in enumerate(paths):
        window._append_photo_path_to_list(path,existing_keys=existing,default_settings=saved,sequence_value=i)
    monkeypatch.setattr(window,'_schedule_async_bird_detect',lambda *a,**k:None)
    from birdstamp.workspace import write_workspace_json,read_workspace_json
    path=tmp_path/'rotation.birdstamp-workspace.json'
    write_workspace_json(path,window._collect_workspace_payload(path))
    window.dejitter_alignment_combo.setCurrentIndex(1)
    window._restore_workspace_payload(read_workspace_json(path),path)
    wait_until(lambda:not window._workspace_restore_in_progress())
    assert window.dejitter_alignment_combo.currentData() == 'rigid'


def test_mode_switch_clears_cache_and_reports_translation_fallback(window,monkeypatch):
    paths,_,_=setup_tab(window,monkeypatch)
    analyze(window)
    assert '退回平移 1 张' in window._sequence_message
    assert '未纠正旋转' in window.sequence_transport.strip.item(1).text()
    epoch=window._sequence_epoch
    window.dejitter_alignment_combo.setCurrentIndex(1)
    assert window._sequence_epoch > epoch
    assert window._sequence_preview is None and window._sequence_cache_key is None
    assert not window._sequence_quick_frames and not window._reference_tracking_results


def test_mode_switch_cancels_worker_and_rejects_late_result(window,monkeypatch):
    from birdstamp.gui import editor_sequence_preview_worker as workers
    setup_tab(window,monkeypatch)
    entered,release=Event(),Event()
    original=workers.prepare_sequence_preview
    def delayed(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(workers,'prepare_sequence_preview',delayed)
    window.dejitter_preprocess_btn.click()
    worker=window._sequence_worker
    try:
        wait_until(entered.is_set)
        window.dejitter_alignment_combo.setCurrentIndex(1)
        assert window._sequence_worker is worker and worker.cancel_event.is_set()
        worker.diagnostics.emit(worker.token,{'input_key':'stale','tracking':{},'signatures':()})
        _APP.processEvents()
        assert not window._reference_tracking_results and window._sequence_preview is None
    finally:
        release.set()
        wait_until(lambda:window._sequence_worker is None)


def test_rotation_main_ab_focus_bird_reference_and_original_crop(window,monkeypatch,rotated_seeds):
    paths=[s.path for s in rotated_seeds]
    window.current_source_image.close()
    window.current_path=paths[0]
    window.current_source_image=Image.open(paths[0]).convert('RGB')
    window.current_source_full_size=window.current_source_image.size
    monkeypatch.setattr(window,'_list_photo_paths',lambda:paths)
    monkeypatch.setattr(window,'_schedule_async_bird_detect',lambda *a,**k:None)
    window._on_canvas_reference_region_changed(REGIONS)
    metadata={'Make':'SONY','Model':'ILCE-1','ImageWidth':1200,'ImageHeight':800,'SubjectArea':'600 400 120 80'}
    for seed in rotated_seeds:
        seed.raw_metadata.update(metadata)
    monkeypatch.setattr(window,'_build_dejitter_seeds',lambda p:rotated_seeds)
    window.export_tabs.setCurrentWidget(window.dejitter_page)
    analyze(window)
    window.current_path=paths[1]
    window.current_raw_metadata=metadata
    window.current_source_image.close()
    window.current_source_image=Image.open(paths[1]).convert('RGB')
    bird=(.4,.3,.6,.7)
    window._bird_box_cache[window._source_signature(paths[1])]=bird
    window._refresh_preview_label()
    key=path_key(paths[1])
    canvas=window.preview_label.canvas
    seq=window._sequence_preview
    assert len(canvas._focus_polygon) == len(canvas._bird_polygon) == 4
    expected=seq.alignments[key].output_polygon(bird,seq.source_sizes[key],seq.canvas_box)
    np.testing.assert_allclose(canvas._bird_polygon,expected)
    assert all(len(row[0]) == 4 and len(row[0][0]) == 2 for row in canvas._reference_diagnostics)
    window.sequence_transport.sync()
    assert '实测旋转' in window.dejitter_tracking_status.text()
    ab=window.ab_preview
    ab.enabled.setChecked(True)
    finish(ab)
    ab.activate('a', sync_selection=False)
    assert ab.route_photo_selection(paths[1])
    ab.mode.setCurrentIndex(1)
    finish(ab)
    assert ab.preview.canvas._focus_polygon == canvas._focus_polygon
    assert ab.preview.canvas._bird_polygon == canvas._bird_polygon
    assert ab.preview.canvas._reference_diagnostics == canvas._reference_diagnostics
    # Render/export the actual polygon painters, then return to original coordinates.
    assert canvas.render_source_pixmap_with_overlays() is not None
    window._set_dejitter_view('edit')
    window._refresh_preview_label()
    assert canvas._crop_polygon == seq.alignments[key].source_crop_polygon(seq.source_sizes[key],seq.canvas_box)
    assert not canvas._bird_polygon and not canvas._focus_polygon
    ab.mode.setCurrentIndex(0)
    finish(ab)
    assert ab.preview.canvas._crop_polygon == canvas._crop_polygon


def test_polygon_grid_is_clipped_and_exported(tmp_path):
    preview=EditorPreviewCanvas()
    pixmap=QPixmap(120,120)
    pixmap.fill(QColor('black'))
    preview.set_source_pixmap(pixmap,log_performance=False)
    diamond=((.5,.1),(.9,.5),(.5,.9),(.1,.5))
    preview.apply_overlay_state(EditorPreviewOverlayState(crop_effect_box=polygon_bounds(diamond),crop_polygon=diamond))
    preview.set_composition_grid_mode('thirds')
    image=preview.render_source_pixmap_with_overlays().toImage()
    assert image.pixelColor(44,20) == QColor('black')
    assert image.pixelColor(44,60) != QColor('black')
    assert preview.save_source_pixmap_with_overlays(str(tmp_path/'grid.png'),'PNG')
    preview.close()
