from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from app_common import superviewer_user_options
from app_common.exif_io.json_sidecar import json_sidecar_path_for
from app_common.exif_io.photo_meta import PhotoMetaDataJSON, PhotoMetaDataXMP
from app_common.file_browser import _permissions
from SuperViewer.superviewer import paths_settings, tagged_file_list as tagged_module
from SuperViewer.superviewer.photo_tags import PhotoTagSidecarStore
from SuperViewer.superviewer.qt_compat import QApplication


_APP = None


@pytest.fixture
def history_panel(tmp_path, monkeypatch):
    global _APP
    # Isolate persistence and permissions before creating any widgets.
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(tmp_path / "state"))
    monkeypatch.setattr(superviewer_user_options, "get_user_options_path", lambda: str(tmp_path / "options.json"))
    for key, value in vars(_permissions).copy().items():
        if key.startswith("CURRENT_SUPERPICKY_"):
            monkeypatch.setattr(_permissions, key, True if key.endswith("WRITABLE") else "")
    _APP = QApplication.instance() or QApplication([])
    state = tmp_path / ".superpicky"
    state.mkdir()
    cfg = state / "tags.cfg"
    cfg.write_text("行为\n  飞行\n  捕食\n", encoding="utf-8")
    paths = []
    for name in ("甲.png", "乙.png"):
        photo = tmp_path / name
        Image.new("RGB", (8, 6), "green").save(photo)
        paths.append(os.path.normpath(str(photo)))
    panel = tagged_module.SuperViewerTaggedFileListPanel(tag_config_path=cfg)
    panel._set_tag_config_directory(tmp_path)
    panel._all_files = paths
    errors, refreshed = [], []
    monkeypatch.setattr(tagged_module.QMessageBox, "warning", lambda parent, title, message: errors.append((title, message)))
    monkeypatch.setattr(panel, "_refresh_metadata_state_for_paths", lambda values: refreshed.append(list(values)))
    try:
        yield panel, paths, errors, refreshed
    finally:
        panel.request_shutdown()
        deadline = time.monotonic() + 3
        while not panel.shutdown(wait_timeout_ms=25) and time.monotonic() < deadline:
            _APP.processEvents()
        assert panel.shutdown(wait_timeout_ms=25)
        panel.close()
        _APP.processEvents()


@pytest.mark.parametrize("layout", ["sibling", "central", "configured"])
def test_store_roundtrip_preserves_other_subjects_and_json_fields(tmp_path, layout):
    if layout != "sibling":
        state = tmp_path / ".superpicky"
        state.mkdir()
        if layout == "configured":
            (state / "config.ini").write_text("[sidecar]\ndir=custom/meta\n", encoding="utf-8")
    photo = tmp_path / "中文照片.png"
    Image.new("RGB", (8, 6), "blue").save(photo)
    original = photo.read_bytes()
    path = str(photo)
    meta = PhotoMetaDataJSON(fallback=PhotoMetaDataXMP())
    assert meta.write(path, {"XMP-dc:Description": "清晨湿地", "XMP-xmp:Rating": 4})
    assert meta.write_subjects(path, ["Lightroom", "保留标签", "飞行"])
    store = PhotoTagSidecarStore(meta)
    result = store.apply_tag_states({path: {"飞行": False, "捕食": True}}, allowed_tags=["飞行", "捕食"])
    assert result.inverse_states == {path: {"飞行": True, "捕食": False}}
    assert result.failed_paths == {} and result.current_tags == {path: {"捕食"}}
    payload = json.loads(json_sidecar_path_for(photo).read_text(encoding="utf-8"))
    assert payload["metadata"]["XMP-dc:Subject"] == ["Lightroom", "保留标签", "捕食"]
    assert payload["metadata"]["XMP-dc:Description"] == "清晨湿地"
    assert payload["metadata"]["XMP-xmp:Rating"] == 4
    assert not store.apply_tag_states(result.inverse_states, allowed_tags=["飞行", "捕食"]).failed_paths
    assert set(meta.read_subjects(path, strict=True)) == {"Lightroom", "保留标签", "飞行"}
    assert photo.read_bytes() == original and not photo.with_suffix(".xmp").exists()
    if layout == "configured":
        assert json_sidecar_path_for(photo).parent == tmp_path / ".superpicky" / "custom" / "meta"


def test_xmp_fallback_snapshot_is_restored_into_json_without_rewriting_xmp(history_panel):
    panel, (path, _), errors, _ = history_panel
    xmp = PhotoMetaDataXMP()
    assert xmp.write_subjects(path, ["Lightroom", "飞行"])
    sidecar = Path(path).with_suffix(".xmp")
    before = sidecar.read_bytes()
    panel.clear_photo_tags_for_paths([path])
    assert panel._photo_tag_store._metadata.read_subjects(path, strict=True) == ["Lightroom"]
    panel.undo()
    assert set(panel._photo_tag_store._metadata.read_subjects(path, strict=True)) == {"Lightroom", "飞行"}
    assert json_sidecar_path_for(path).exists() and sidecar.read_bytes() == before
    assert errors == []


@pytest.mark.parametrize("kind", ["json", "xmp"])
def test_damaged_snapshot_is_not_overwritten_or_recorded(history_panel, kind):
    panel, (path, _), errors, _ = history_panel
    sidecar = json_sidecar_path_for(path) if kind == "json" else Path(path).with_suffix(".xmp")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    before = b'{"metadata":' if kind == "json" else b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><broken'
    sidecar.write_bytes(before)
    panel.set_photo_tag_for_paths([path], "飞行", True)
    assert sidecar.read_bytes() == before and not panel.can_undo
    assert path not in panel._photo_tag_cache and len(errors) == 1
    if kind == "xmp":
        assert not json_sidecar_path_for(path).exists()


@pytest.mark.parametrize("enabled", [True, False])
def test_mixed_initial_states_are_restored_per_file(history_panel, enabled):
    panel, (first, second), errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(first, ["Lightroom", "飞行"])
    panel.set_photo_tag_for_paths([first, second], "飞行", enabled)
    changed = second if enabled else first
    assert panel._photo_tag_generation(changed) > 0
    assert panel.can_undo
    panel.undo()
    assert set(meta.read_subjects(first, strict=True)) == {"Lightroom", "飞行"}
    assert meta.read_subjects(second, strict=True) == []
    panel.redo()
    assert all(("飞行" in meta.read_subjects(path, strict=True)) == enabled for path in (first, second))
    assert errors == []


def test_clear_undo_preserves_later_unrelated_subjects(history_panel):
    panel, (first, second), errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(first, ["Lightroom", "飞行"])
    assert meta.write_subjects(second, ["捕食"])
    panel.clear_photo_tags_for_paths([first, second])
    assert meta.write_subjects(first, ["Lightroom", "外部新标签"])
    panel.undo()
    assert set(meta.read_subjects(first, strict=True)) == {"Lightroom", "外部新标签", "飞行"}
    assert meta.read_subjects(second, strict=True) == ["捕食"]
    assert errors == []


def test_noop_and_total_failure_keep_redo(history_panel, monkeypatch):
    panel, (first, second), errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(first, ["飞行"])
    panel.set_photo_tag_for_paths([second], "飞行", True)
    panel.undo()
    panel.set_photo_tag_for_paths([first], "飞行", True)
    panel.set_photo_tag_for_paths([first], "未配置标签", True)
    assert panel.can_redo and not panel.can_undo
    monkeypatch.setattr(meta, "write_subjects", lambda *args: False)
    panel.set_photo_tag_for_paths([first], "捕食", True)
    assert panel.can_redo and not panel.can_undo and len(errors) == 1


def test_partial_new_write_only_records_successful_files(history_panel, monkeypatch):
    panel, paths, errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    original = meta.write_subjects
    monkeypatch.setattr(meta, "write_subjects", lambda path, tags: False if path == paths[1] else original(path, tags))
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    assert meta.read_subjects(paths[0], strict=True) == ["飞行"]
    assert meta.read_subjects(paths[1], strict=True) == []
    assert paths[1] not in panel._photo_tag_cache
    assert panel.can_undo and len(errors) == 1 and paths[1] in errors[0][1]
    panel.undo()
    assert not panel.can_undo and panel.can_redo
    assert meta.read_subjects(paths[0], strict=True) == []


@pytest.mark.parametrize("operation", ["undo", "redo"])
def test_partial_history_failure_retains_failed_subset_for_retry(history_panel, monkeypatch, operation):
    panel, paths, errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    if operation == "redo":
        panel.undo()
    original = meta.write_subjects
    monkeypatch.setattr(meta, "write_subjects", lambda path, tags: False if path == paths[1] else original(path, tags))
    getattr(panel, operation)()
    assert panel.can_undo and panel.can_redo and len(errors) == 1
    assert ("飞行" in meta.read_subjects(paths[0], strict=True)) == (operation == "redo")
    assert ("飞行" in meta.read_subjects(paths[1], strict=True)) == (operation == "undo")
    monkeypatch.setattr(meta, "write_subjects", original)
    getattr(panel, operation)()
    assert all(("飞行" in meta.read_subjects(path, strict=True)) == (operation == "redo") for path in paths)
    assert not getattr(panel, "can_" + operation)
    opposite = "redo" if operation == "undo" else "undo"
    getattr(panel, opposite)()
    getattr(panel, opposite)()
    assert all(("飞行" in meta.read_subjects(path, strict=True)) == (operation == "undo") for path in paths)


def test_missing_source_does_not_create_orphan_json_and_undo_can_retry(history_panel):
    panel, (path, _), errors, _ = history_panel
    meta = panel._photo_tag_store._metadata
    assert meta.write_subjects(path, ["飞行"])
    panel.clear_photo_tags_for_paths([path])
    photo = Path(path)
    sidecar = json_sidecar_path_for(path)
    moved_photo, moved_sidecar = photo.with_name("moved.png"), sidecar.with_name("moved.json")
    photo.rename(moved_photo)
    sidecar.rename(moved_sidecar)
    panel.undo()
    assert not sidecar.exists() and not photo.exists()
    assert panel.can_undo and not panel.can_redo and len(errors) == 1
    moved_photo.rename(photo)
    moved_sidecar.rename(sidecar)
    panel.undo()
    assert meta.read_subjects(path, strict=True) == ["飞行"]


@pytest.mark.parametrize("component", ["_sync_photo_tags_to_meta_cache", "_refresh_metadata_state_for_paths"])
def test_saved_inverse_survives_ui_refresh_failure(history_panel, monkeypatch, component):
    panel, paths, errors, _ = history_panel

    def fail(_paths):
        raise RuntimeError("test UI refresh failure")

    monkeypatch.setattr(panel, component, fail)
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    assert panel.can_undo
    panel.undo()
    assert not panel.can_undo and panel.can_redo
    assert all(panel._photo_tag_store._metadata.read_subjects(path, strict=True) == [] for path in paths)
    assert errors == []


def test_permission_gate_and_shutdown_leave_history_retriable(history_panel, monkeypatch):
    panel, paths, errors, _ = history_panel
    panel.set_photo_tag_for_paths(paths, "飞行", True)
    monkeypatch.setattr(_permissions, "CURRENT_SUPERPICKY_SIDECAR_WRITABLE", False)
    panel.undo()
    assert panel.can_undo and not panel.can_redo and len(errors) == 1
    assert all(panel._photo_tag_cache[path] == {"飞行"} for path in paths)
    monkeypatch.setattr(_permissions, "CURRENT_SUPERPICKY_SIDECAR_WRITABLE", True)
    panel.undo()
    assert panel.can_redo and not panel.can_undo
    panel.request_shutdown()
    panel.redo()
    panel.set_photo_tag_for_paths(paths, "捕食", True)
    assert panel.can_redo and not panel.can_undo
    assert all(panel._photo_tag_cache[path] == set() for path in paths)
