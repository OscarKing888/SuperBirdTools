"""真实图像缓存、跨窗口工作区恢复、失效与失败不破坏内存结果。"""
from dataclasses import replace
import json

import pytest

from birdstamp.gui import editor_sequence_preview_worker as workers, editor_options
from birdstamp.gui.editor_sequence_preview_worker import EditorSequencePreviewWorker
from birdstamp.gui.sequence_preview_cache import SequencePreviewCache
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui.editor import BirdStampEditorWindow
from test_dejitter_tab import sequence, setup_tab, analyze
from test_editor_dejitter import window, _APP
from test_reference_tracking import wait_until


def run_worker(seeds, cache, *, restore_only=False):
    worker = EditorSequencePreviewWorker(token=1, path=seeds[0].path, seeds=seeds,
                                         restore_only=restore_only, cache=cache)
    results, quick, errors = [], [], []
    worker.ready.connect(lambda _, result, frame: results.append((result, frame)))
    worker.quick_ready.connect(lambda _, result, frames: quick.append((result, frames)))
    worker.failed.connect(lambda _, message: errors.append(message))
    worker.run()
    return results, quick, errors


def fail_recompute(*args, **kwargs):
    raise AssertionError('缓存命中不应重新分析或解码成片')


def test_cache_reloads_geometry_tracking_quick_and_sharp_without_recompute(sequence, tmp_path, monkeypatch):
    seeds, expected = sequence
    root = tmp_path / 'cache'
    first, quick, errors = run_worker(seeds, SequencePreviewCache(root))
    assert not errors and first and quick
    monkeypatch.setattr(workers, 'prepare_sequence_preview', fail_recompute)
    monkeypatch.setattr(workers, 'render_sequence_preview_frame', fail_recompute)
    second, restored, errors = run_worker(seeds, SequencePreviewCache(root), restore_only=True)
    assert not errors and second and restored
    result, frame = second[0]
    assert result.pixel_boxes == expected.pixel_boxes
    assert result.tracking == first[0][0].tracking
    assert result.signatures == expected.signatures
    assert frame.image == first[0][1].image
    for key, original in quick[0][1].items():
        assert restored[0][1][key].image == original.image
        assert restored[0][1][key].source_image == original.source_image


@pytest.mark.parametrize('change', ['source', 'xmp', 'strength', 'regions', 'order', 'union', 'corrupt'])
def test_stale_or_corrupt_cache_is_not_restored(sequence, tmp_path, monkeypatch, change):
    seeds, result = sequence
    cache = SequencePreviewCache(tmp_path / 'cache')
    assert not run_worker(seeds, cache)[2]
    if change == 'source':
        seeds[1].path.write_bytes(seeds[1].path.read_bytes()+b'changed')
    elif change == 'xmp':
        seeds[1].path.with_suffix('.XMP').write_text('<xmp>中文</xmp>', encoding='utf-8')
    elif change == 'order':
        seeds = list(reversed(seeds))
    elif change == 'corrupt':
        (cache.root / result.input_key / 'quick-0.png').write_bytes(b'broken')
    else:
        key, value = {'strength': ('dejitter_reference_strength', 30),
                      'regions': ('dejitter_reference_regions', [(0,0,.5,.5)]),
                      'union': ('dejitter_pad_to_union', True)}[change]
        seeds = [replace(seed, settings={**seed.settings, key: value}) for seed in seeds]
    monkeypatch.setattr(workers, 'prepare_sequence_preview', fail_recompute)
    results, quick, errors = run_worker(seeds, cache, restore_only=True)
    assert not results and not quick and errors


def test_write_failure_does_not_discard_successful_analysis(sequence, tmp_path, monkeypatch):
    seeds, _ = sequence
    cache = SequencePreviewCache(tmp_path / 'cache')
    def disk_full(*args):
        raise OSError('disk full')
    monkeypatch.setattr(cache, '_save_image', disk_full)
    results, quick, errors = run_worker(seeds, cache)
    assert results and quick and not errors
    assert not list(cache.root.glob('*/manifest.json'))
    assert not list(cache.root.glob('.pending-*'))


def test_corrupt_cache_can_be_rebuilt_and_cancellation_never_publishes(sequence, tmp_path):
    seeds, result = sequence
    cache = SequencePreviewCache(tmp_path / 'cache')
    _, quick, errors = run_worker(seeds, cache)
    assert not errors
    manifest = cache.root / result.input_key / 'manifest.json'
    manifest.write_text('{broken', encoding='utf-8')
    assert cache.load(seeds) is None
    cache.save(quick[0][0], quick[0][1])
    assert cache.load(seeds) is not None
    other = SequencePreviewCache(tmp_path / 'cancelled')
    other.save(quick[0][0], quick[0][1], cancelled=lambda: True)
    assert not list(other.root.glob('*/manifest.json'))


def test_workspace_restores_into_new_window_and_close_keeps_cache_reference(window, monkeypatch, tmp_path):
    paths, _, seeds = setup_tab(window, monkeypatch)
    existing = set()
    for index, path in enumerate(paths):
        window._append_photo_path_to_list(path, existing_keys=existing,
            default_settings=window._build_current_render_settings(), sequence_value=index)
    analyze(window)
    workspace_path = tmp_path / 'test.birdstamp-workspace.json'
    payload = window._collect_workspace_payload(workspace_path)
    input_key = window._sequence_preview.input_key
    assert payload['editor_state']['sequence_preview']['input_key'] == input_key
    monkeypatch.setattr(BirdStampEditorWindow, "_schedule_async_bird_detect", lambda *a, **k: None)
    fresh = BirdStampEditorWindow()
    monkeypatch.setattr(workers, 'prepare_sequence_preview', fail_recompute)
    monkeypatch.setattr(workers, 'render_sequence_preview_frame', fail_recompute)
    try:
        fresh._restore_workspace_payload(payload, workspace_path)
        wait_until(lambda: not fresh._workspace_restore_in_progress() and fresh._sequence_preview is not None
                   and fresh._sequence_worker is None)
        assert fresh._sequence_preview.pixel_boxes == window._sequence_preview.pixel_boxes
        assert fresh._sequence_result_mode()
        assert len(fresh._sequence_quick_frames) == len(paths)
        fresh._invalidate_sequence_preview(shutdown=True)
        saved = fresh._collect_workspace_payload(workspace_path)
        assert saved['editor_state']['sequence_preview']['input_key'] == input_key
    finally:
        fresh.close()
        wait_until(lambda: fresh._sequence_worker is None)
        fresh.deleteLater()
        _APP.processEvents()


def test_disk_budget_evicts_old_results_but_keeps_active_quick_frames(sequence, tmp_path, monkeypatch):
    seeds, _ = sequence
    cache = SequencePreviewCache(tmp_path / 'cache')
    _, quick, errors = run_worker(seeds, cache)
    assert not errors
    old_sequence, frames = quick[0]
    current = replace(old_sequence, input_key='a'*64)
    monkeypatch.setattr(editor_options, 'DEJITTER_DISK_CACHE_BYTES', 1)
    cache.save(current, frames)
    assert not (cache.root / old_sequence.input_key).exists()
    assert (cache.root / current.input_key / 'manifest.json').is_file()
    assert (cache.root / current.input_key / 'quick-0.png').is_file()
    assert not list(cache.root.glob('*/sharp-*.png'))
