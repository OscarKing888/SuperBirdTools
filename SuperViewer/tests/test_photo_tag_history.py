from __future__ import annotations

import os
from pathlib import Path

import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import tagged_file_list as tagged_module
from SuperViewer.superviewer.photo_tags import PhotoTagSidecarStore
from SuperViewer.superviewer.qt_compat import QApplication


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def history_panel(qt_app, tmp_path: Path, monkeypatch):
    state_dir = tmp_path / ".superpicky"
    state_dir.mkdir()
    cfg = state_dir / "tags.cfg"
    cfg.write_text("行为\n  飞行\n  捕食\n", encoding="utf-8")
    panel = tagged_module.SuperViewerTaggedFileListPanel(tag_config_path=cfg)
    panel._set_tag_config_directory(tmp_path)
    panel._load_tag_config_if_changed(force=True)
    paths = []
    for name in ("甲.jpg", "乙.jpg"):
        photo = tmp_path / name
        photo.write_bytes(b"original photo")
        paths.append(os.path.normpath(str(photo)))
    panel._all_files = paths
    errors = []
    refreshed = []
    monkeypatch.setattr(tagged_module.QMessageBox, "warning", lambda parent, title, message: errors.append((title, message)))
    monkeypatch.setattr(panel, "_refresh_metadata_state_for_paths", lambda values: refreshed.append(list(values)))
    try:
        yield panel, paths, errors, refreshed
    finally:
        panel.shutdown()
        panel.close()
        qt_app.processEvents()


def test_store_state_roundtrip_preserves_unicode_unconfigured_subjects_and_other_xmp(tmp_path: Path):
    photo = tmp_path / "中文照片.jpg"
    photo.write_bytes(b"original")
    path = str(photo)
    meta = PhotoMetaDataXMP()
    assert meta.write(path, {"XMP-xmp:Rating": 4, "XMP-dc:Description": "清晨湿地"})
    assert meta.write_subjects(path, ["Lightroom", "行为;特殊", "飞行"])
    store = PhotoTagSidecarStore(meta)

    result = store.apply_tag_states({path: {"飞行": False, "捕食": True}}, allowed_tags=["飞行", "捕食"])
    assert result.failed_paths == {}
    assert result.inverse_states == {path: {"飞行": True, "捕食": False}}
    assert result.current_tags == {path: {"捕食"}}
    assert meta.read_subjects(path, strict=True) == ["Lightroom", "行为;特殊", "捕食"]
    assert meta.read(path)["XMP-xmp:Rating"] == "4"
    assert meta.read(path)["XMP-dc:Description"] == "清晨湿地"
    store.apply_tag_states(result.inverse_states, allowed_tags=["飞行", "捕食"])
    assert set(meta.read_subjects(path, strict=True)) == {"Lightroom", "行为;特殊", "飞行"}
    assert photo.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.superviewer.json"))


def test_strict_snapshot_error_does_not_write_or_invent_empty_state(tmp_path: Path, monkeypatch):
    photo = tmp_path / "damaged.jpg"
    photo.write_bytes(b"original")
    sidecar = photo.with_suffix(".xmp")
    damaged = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><damaged'
    sidecar.write_bytes(damaged)
    meta = PhotoMetaDataXMP()
    writes = []
    monkeypatch.setattr(meta, "write_subjects", lambda *args: writes.append(args))
    result = PhotoTagSidecarStore(meta).apply_tag_states({str(photo): {"飞行": True}}, allowed_tags=["飞行"])
    assert str(photo) in result.failed_paths
    assert result.current_tags == {} and result.inverse_states == {}
    assert writes == []
    assert sidecar.read_bytes() == damaged


def test_batch_add_undo_only_restores_paths_that_actually_changed(history_panel):
    panel, (first, second), errors, refreshed = history_panel
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(first, ["Lightroom", "飞行"])
    history_signals = []
    panel.command_history_changed.connect(lambda: history_signals.append(True))
    panel.set_photo_tag_for_paths([first, second], "飞行", True)
    assert panel.can_undo and not panel.can_redo
    assert panel._photo_tag_cache == {first: {"飞行"}, second: {"飞行"}}
    panel.undo()
    assert set(meta.read_subjects(first, strict=True)) == {"Lightroom", "飞行"}
    assert meta.read_subjects(second, strict=True) == []
    assert panel._photo_tag_cache == {first: {"飞行"}, second: set()}
    assert refreshed[-1] == [second]
    assert not panel.can_undo and panel.can_redo
    panel.redo()
    assert meta.read_subjects(second, strict=True) == ["飞行"]
    assert len(history_signals) == 3 and errors == []


def test_noop_preserves_redo_and_new_change_clears_it(history_panel):
    panel, paths, errors, _ = history_panel
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    panel.undo()
    assert panel.can_redo
    panel.set_photo_tag_for_paths(paths, "飞行", False)
    assert panel.can_redo and not panel.can_undo
    panel.set_photo_tag_for_paths(paths, "捕食", True)
    assert panel.can_undo and not panel.can_redo
    assert errors == []


def test_clear_undo_restores_each_photo_and_preserves_other_subjects(history_panel):
    panel, (first, second), errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(first, ["Lightroom", "飞行", "捕食"])
    assert meta.write_subjects(second, ["其它关键词", "捕食"])
    panel.clear_photo_tags_for_paths([first, second])
    assert meta.read_subjects(first) == ["Lightroom"]
    assert meta.read_subjects(second) == ["其它关键词"]
    panel.undo()
    assert set(meta.read_subjects(first)) == {"Lightroom", "飞行", "捕食"}
    assert set(meta.read_subjects(second)) == {"其它关键词", "捕食"}
    panel.redo()
    assert meta.read_subjects(first) == ["Lightroom"]
    assert meta.read_subjects(second) == ["其它关键词"]
    assert errors == []


def test_partial_initial_write_records_only_success_and_keeps_failed_cache(history_panel, monkeypatch):
    panel, (first, second), errors, refreshed = history_panel
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(second, ["捕食"])
    panel._photo_tag_cache[second] = {"捕食"}
    panel._meta_cache[second] = {"tags": ["捕食"]}
    generation = panel._photo_tag_generation(second)
    original_write = meta.write_subjects
    monkeypatch.setattr(meta, "write_subjects", lambda path, tags: False if path == second else original_write(path, tags))
    panel.set_photo_tag_for_paths([first, second], "飞行", True)
    assert panel.can_undo and not panel.can_redo
    assert panel._photo_tag_cache == {first: {"飞行"}, second: {"捕食"}}
    assert panel._photo_tag_generation(second) == generation
    assert refreshed[-1] == [first]
    assert len(errors) == 1 and second in errors[0][1]
    panel.undo()
    assert meta.read_subjects(first) == []
    assert meta.read_subjects(second) == ["捕食"]
    assert not panel.can_undo and panel.can_redo


@pytest.mark.parametrize("operation", ["undo", "redo"])
def test_partial_undo_redo_keeps_remaining_work_for_retry(history_panel, monkeypatch, operation):
    panel, (first, second), errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    panel.set_photo_tag_for_paths([first, second], "飞行", True)
    if operation == "redo":
        panel.undo()
    original_write = meta.write_subjects
    monkeypatch.setattr(meta, "write_subjects", lambda path, tags: False if path == second else original_write(path, tags))
    getattr(panel, operation)()
    expected_first = set() if operation == "undo" else {"飞行"}
    expected_second = {"飞行"} if operation == "undo" else set()
    assert panel._photo_tag_cache == {first: expected_first, second: expected_second}
    assert panel.can_undo and panel.can_redo
    assert len(errors) == 1
    monkeypatch.setattr(meta, "write_subjects", original_write)
    getattr(panel, operation)()
    assert panel._photo_tag_cache == {first: expected_first, second: expected_first}
    assert (not panel.can_undo) if operation == "undo" else (not panel.can_redo)
    opposite = panel.redo if operation == "undo" else panel.undo
    opposite()
    opposite()
    assert panel._photo_tag_cache == {first: expected_second, second: expected_second}


def test_all_failed_write_preserves_history_redo_and_reliable_cache(history_panel, monkeypatch):
    panel, paths, errors, _ = history_panel
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    panel.undo()
    before = {path: set(tags) for path, tags in panel._photo_tag_cache.items()}
    monkeypatch.setattr(panel._photo_tag_store._metadata, "write_subjects", lambda *args: False)
    panel.set_photo_tag_for_paths(paths, "捕食", True)
    assert panel._photo_tag_cache == before
    assert not panel.can_undo and panel.can_redo
    assert len(errors) == 1


def test_history_scope_and_vocabulary_changes_clear_but_groups_and_subfolders_do_not(history_panel, tmp_path: Path):
    panel, paths, _, _ = history_panel
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    child = tmp_path / "child"
    child.mkdir()
    assert not panel._set_tag_config_directory(child)
    assert panel.can_undo
    panel._tag_config.path.write_text("鸟类行为\n  飞行\n  捕食\n", encoding="utf-8")
    panel._load_tag_config_if_changed(force=True)
    assert panel.can_undo
    panel._tag_config.path.write_text("行为\n  捕食\n  飞行\n", encoding="utf-8")
    panel._load_tag_config_if_changed(force=True)
    assert panel.can_undo
    panel._tag_config.path.write_text("行为\n  飞行\n", encoding="utf-8")
    panel._load_tag_config_if_changed(force=True)
    assert not panel.can_undo and not panel.can_redo
    panel.set_photo_tag_for_paths(paths, "飞行", False)
    assert panel.can_undo
    other = tmp_path / "other"
    (other / ".superpicky").mkdir(parents=True)
    (other / ".superpicky" / "tags.cfg").write_text("飞行\n", encoding="utf-8")
    assert panel._set_tag_config_directory(other)
    assert not panel.can_undo and not panel.can_redo


def test_permission_and_shutdown_guards_do_not_consume_history(history_panel, monkeypatch):
    panel, paths, errors, _ = history_panel
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    monkeypatch.setattr(panel, "_sidecar_writes_allowed", lambda *args, **kwargs: False)
    panel.undo()
    panel.clear_photo_tags_for_paths(paths)
    assert panel.can_undo and not panel.can_redo
    assert all(tags == {"飞行"} for tags in panel._photo_tag_cache.values())
    monkeypatch.setattr(panel, "_sidecar_writes_allowed", lambda *args, **kwargs: True)
    panel.request_shutdown()
    panel.undo()
    panel._set_tag_for_paths(paths, "捕食", True)
    assert panel.can_undo and not panel.can_redo
    assert all(tags == {"飞行"} for tags in panel._photo_tag_cache.values())
    assert errors == []


def test_saved_edit_keeps_history_even_if_ui_refresh_fails(history_panel, monkeypatch):
    panel, paths, errors, _ = history_panel

    def fail_refresh(_paths):
        raise RuntimeError("test widget refresh failure")

    monkeypatch.setattr(panel, "_refresh_metadata_state_for_paths", fail_refresh)
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    assert panel.can_undo
    panel.undo()
    assert all(tags == set() for tags in panel._photo_tag_cache.values())
    assert errors == []


def test_saved_edit_keeps_inverse_when_metadata_cache_sync_fails(history_panel, monkeypatch):
    panel, paths, errors, _ = history_panel
    meta = panel._photo_tag_store._metadata

    def fail_sync(_paths):
        raise RuntimeError("test metadata cache failure")

    monkeypatch.setattr(panel, "_sync_photo_tags_to_meta_cache", fail_sync)
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    assert all(meta.read_subjects(path, strict=True) == ["飞行"] for path in paths)
    assert panel.can_undo
    panel.undo()
    assert all(meta.read_subjects(path, strict=True) == [] for path in paths)
    assert not panel.can_undo and panel.can_redo
    assert errors == []


def test_missing_photo_does_not_create_orphan_sidecar_and_undo_can_retry(history_panel):
    panel, (path, _), errors, _ = history_panel
    photo = Path(path)
    sidecar = photo.with_suffix(".xmp")
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(path, ["飞行"])
    panel.clear_photo_tags_for_paths([path])
    moved_photo = photo.with_name("moved.jpg")
    moved_sidecar = moved_photo.with_suffix(".xmp")
    photo.rename(moved_photo)
    sidecar.rename(moved_sidecar)
    panel.undo()
    assert not photo.exists() and not sidecar.exists()
    assert panel.can_undo and not panel.can_redo
    assert len(errors) == 1
    moved_photo.rename(photo)
    moved_sidecar.rename(sidecar)
    panel.undo()
    assert meta.read_subjects(path, strict=True) == ["飞行"]
    assert not panel.can_undo and panel.can_redo


def test_clear_tag_history_notifies_actions_and_discards_redo(history_panel):
    panel, paths, _, _ = history_panel
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    panel.undo()
    signals = []
    panel.command_history_changed.connect(lambda: signals.append(True))
    panel.clear_tag_history()
    assert not panel.can_undo and not panel.can_redo
    assert signals == [True]
