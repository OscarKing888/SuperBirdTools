"""整组导出后保存/切换工作区：真实输出、持久化和 Qt 线程生命周期。"""
import threading

import pytest

from test_editor_dejitter import window
from test_dejitter_tab import setup_tab, analyze, wait_until
from birdstamp.export_stage import sequence_export
from birdstamp.gui import editor, editor_dejitter, editor_workspace
from birdstamp.gui.editor_sequence_preview_worker import EditorSequenceExportWorker
from birdstamp.gui.editor_utils import path_key
from birdstamp.workspace import read_workspace_json, resolve_workspace_path, write_workspace_json


_SELECT_WORKSPACE_PHOTO = editor.BirdStampEditorWindow._schedule_workspace_photo_selection


def prepare(window, monkeypatch, tmp_path, *, enabled=True):
    paths, _, _ = setup_tab(window, monkeypatch)
    existing = set()
    for index, path in enumerate(paths, 1):
        window._append_photo_path_to_list(path, existing_keys=existing,
                                         default_settings=window._build_current_render_settings(),
                                         sequence_value=index)
    monkeypatch.setattr(window, '_list_photo_paths',
                        editor.BirdStampEditorWindow._list_photo_paths.__get__(window))
    monkeypatch.setattr(window, '_schedule_workspace_photo_selection',
                        _SELECT_WORKSPACE_PHOTO.__get__(window))
    monkeypatch.setattr(editor_dejitter.QFileDialog, 'getExistingDirectory', lambda *args: str(tmp_path))
    monkeypatch.setattr(window, '_show_error', lambda *args: pytest.fail(str(args)))
    window.dejitter_export_workspace_check.setChecked(enabled)
    analyze(window)
    return paths


def finish(window):
    wait_until(lambda: window._sequence_worker is None and not window._workspace_restore_in_progress())
    wait_until(lambda: window._preview_decode_worker is None)


def workspace_photos(path):
    payload = read_workspace_json(path)
    return payload, [resolve_workspace_path(entry['path'], workspace_path=path) for entry in payload['photos']]


@pytest.mark.parametrize('named,output_format', [(False, 'png'), (True, 'jpg')])
def test_export_saves_original_then_loads_only_exported_images(window, monkeypatch, tmp_path, named, output_format):
    paths = prepare(window, monkeypatch, tmp_path)
    original = tmp_path / '原工作区' / '编辑.birdstamp-workspace.json' if named else None
    window._workspace_path = original
    window.photo_render_overrides[path_key(paths[1])] = {'ratio': 1, 'crop_box': (.2, .2, .7, .7)}
    window._set_photo_crop_box_for_path(paths[1], (.2, .2, .7, .7))
    window.dejitter_output_format.setCurrentIndex(window.dejitter_output_format.findData(output_format))
    old_key = window._sequence_cache_key
    window.dejitter_export_btn.click()
    finish(window)

    target = window._workspace_path
    assert target and target.name == '去抖动成片.birdstamp-workspace.json'
    original = original or target.with_name('去抖动前工作区.birdstamp-workspace.json')
    saved_original, saved_paths = workspace_photos(original)
    assert saved_paths == paths
    assert saved_original['editor_state']['sequence_preview']['input_key'] == old_key
    assert saved_original['editor_state']['current_render_settings']['dejitter_reference_source'] == str(paths[0])
    assert saved_original['photos'][1]['render_settings']['crop_box'] == [.2, .2, .7, .7]
    saved_target, outputs = workspace_photos(target)
    assert [entry['sequence'] for entry in saved_target['photos']] == [1, 2]
    assert outputs == [target.parent / f'{index:04d}_{path.stem}.{output_format}'
                       for index, path in enumerate(paths, 1)]
    assert all(path.is_file() for path in outputs)
    assert window._list_photo_paths() == outputs
    wait_until(lambda: window.current_path == outputs[0] and window.current_source_image is not None)
    assert window.export_tabs.currentIndex() == 0
    assert window._sequence_preview is None
    assert window._dejitter_reference_source is None and not window._dejitter_reference_regions
    assert not window._dejitter_manual_matches
    assert not saved_target['report_databases']
    assert saved_target['editor_state']['sequence_preview']['input_key'] is None
    for path in outputs:
        settings = window._render_settings_for_path(path, prefer_current_ui=False)
        assert settings['ratio'] == 'no_crop'
        assert settings['crop_box'] is None
        assert settings['center_mode'] == 'image'
        assert settings['crop_padding_top'] == settings['crop_padding_left'] == 0
    # 自动保存也必须保存完整的新列表，原工作区仍然独立可恢复。
    window._autosave_workspace_now()
    assert workspace_photos(window._workspace_autosave_path())[1] == outputs
    assert workspace_photos(original)[1] == paths
    finish(window)


def test_unchecked_export_preserves_workspace_and_photo_list(window, monkeypatch, tmp_path):
    paths = prepare(window, monkeypatch, tmp_path, enabled=False)
    window.dejitter_export_btn.click()
    finish(window)
    assert window._workspace_path is None
    assert window._list_photo_paths() == paths
    assert len(list(tmp_path.glob('去抖动_*/*.png'))) == 2
    assert not list(tmp_path.glob('去抖动_*/*.birdstamp-workspace.json'))


@pytest.mark.parametrize('cancel', [False, True])
def test_switch_waits_for_real_finished_and_rejects_cancelled_result(window, monkeypatch, tmp_path, cancel):
    paths = prepare(window, monkeypatch, tmp_path)
    release = threading.Event()

    class DelayedWorker(EditorSequenceExportWorker):
        def run(self):
            super().run()
            release.wait(10)

    monkeypatch.setattr(editor_dejitter, 'EditorSequenceExportWorker', DelayedWorker)
    window.dejitter_export_btn.click()
    worker = window._sequence_worker
    try:
        wait_until(lambda: window._sequence_export_workspace_result is not None)
        assert window._sequence_worker is worker
        assert window._list_photo_paths() == paths and window._workspace_path is None
        assert not window.dejitter_export_workspace_check.isEnabled()
        assert not list(tmp_path.glob('去抖动_*/*.birdstamp-workspace.json'))
        if cancel:
            window.dejitter_preprocess_btn.click()
    finally:
        release.set()
        finish(window)
    assert window.dejitter_export_workspace_check.isEnabled()
    if cancel:
        assert window._list_photo_paths() == paths and window._workspace_path is None
        assert not list(tmp_path.glob('去抖动_*/*.birdstamp-workspace.json'))
    else:
        assert window._workspace_path.name == '去抖动成片.birdstamp-workspace.json'


@pytest.mark.parametrize('fail_write', [1, 2])
def test_workspace_write_failure_preserves_current_list_and_exported_images(window, monkeypatch, tmp_path, fail_write):
    paths = prepare(window, monkeypatch, tmp_path)
    original = tmp_path / '原工作区.birdstamp-workspace.json'
    window._workspace_path = original
    write_workspace_json(original, window._collect_workspace_payload(original))
    errors, writes = [], []

    def write(path, payload):
        writes.append(path)
        if len(writes) == fail_write:
            raise OSError('模拟磁盘写入失败')
        write_workspace_json(path, payload)

    monkeypatch.setattr(editor_workspace, 'write_workspace_json', write)
    monkeypatch.setattr(window, '_show_error', lambda *args: errors.append(args))
    window.dejitter_export_btn.click()
    finish(window)
    assert len(errors) == 1 and '未切换' in errors[0][0]
    assert str(original) in errors[0][1]
    assert window._list_photo_paths() == paths and window._workspace_path == original
    assert window._sequence_preview is not None
    assert len(list(tmp_path.glob('去抖动_*/*.png'))) == 2
    assert workspace_photos(original)[1] == paths
    assert not list(tmp_path.glob('去抖动_*/去抖动成片*.birdstamp-workspace.json'))


def test_workspace_preference_roundtrips_without_analysis(window):
    assert not window.dejitter_export_workspace_check.isChecked()
    window.dejitter_export_workspace_check.setChecked(True)
    state = window._collect_sequence_workspace_state()
    assert state['input_key'] is None
    window.dejitter_export_workspace_check.setChecked(False)
    window._restore_sequence_workspace_state(state)
    assert window.dejitter_export_workspace_check.isChecked()
    window._restore_sequence_workspace_state({})
    assert not window.dejitter_export_workspace_check.isChecked()


def test_failed_image_export_never_creates_or_switches_workspace(window, monkeypatch, tmp_path):
    paths = prepare(window, monkeypatch, tmp_path)

    def fail(*args, **kwargs):
        raise OSError('模拟图片写入失败')

    monkeypatch.setattr(sequence_export, 'save_export_image', fail)
    window.dejitter_export_btn.click()
    finish(window)
    assert window._workspace_path is None and window._list_photo_paths() == paths
    assert '失败' in window.dejitter_export_progress.format()
    assert not list(tmp_path.glob('去抖动_*'))
    assert window._sequence_export_workspace_result is None
