from __future__ import annotations

import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from birdstamp import config
from birdstamp.gui import editor
from birdstamp.gui.editor_photo_list import PHOTO_COL_TITLE
from birdstamp.gui.editor_utils import path_key


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for name in (
        "_start_bird_detector_preload", "_run_deferred_startup_tasks",
        "_schedule_workspace_photo_selection", "_on_photo_selected",
    ):
        monkeypatch.setattr(editor.BirdStampEditorWindow, name, lambda *args, **kwargs: None)
    instance = editor.BirdStampEditorWindow()
    try:
        yield instance
    finally:
        deadline = time.monotonic() + 5
        accepted = instance.close()
        while not accepted and time.monotonic() < deadline:
            _APP.processEvents()
            accepted = instance.close()
        assert accepted, "编辑器后台任务未及时结束"
        instance.deleteLater()
        _APP.processEvents()


def _wait_metadata_loaded(window, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if window._photo_list_metadata_loader is None and not window._photo_list_metadata_loading:
            return
        time.sleep(0.01)
    raise AssertionError("照片信息后台读取未在限定时间内完成")


def _add_photo(window, path):
    window._append_photo_path_to_list(
        path, existing_keys=set(), default_settings=window._build_current_render_settings(),
    )


def test_refresh_button_and_action_are_exposed(window):
    action = window.photo_list.refresh_metadata_action
    assert action.shortcut().toString() == "F5"
    assert action.shortcutContext() == Qt.ShortcutContext.WidgetShortcut
    assert action in window.photo_list._tree_widget.actions()
    buttons = [btn for btn in window.findChildren(QPushButton) if btn.text() == "刷新信息"]
    assert len(buttons) == 1
    assert buttons[0].toolTip() == action.toolTip()


def test_refresh_rereads_changed_xmp_without_touching_files(window, tmp_path):
    photo = tmp_path / "翠鸟.png"
    Image.new("RGB", (16, 12), "green").save(photo)
    original = photo.read_bytes()
    xmp = PhotoMetaDataXMP()
    assert xmp.write_title(str(photo), "普通翠鸟")
    _add_photo(window, photo)
    window._restart_photo_list_metadata_loader()
    _wait_metadata_loaded(window)
    item = window._find_photo_item_by_path(photo)
    assert item.text(PHOTO_COL_TITLE) == "普通翠鸟"

    # 外部工具（SuperViewer/SuperPicky）修改侧车后，列表需要手动刷新才能看到。
    assert xmp.write_title(str(photo), "斑鱼狗（雌）")
    sidecar_after_edit = xmp.sidecar_path_for(str(photo)).read_bytes()
    window.photo_list.refresh_metadata_action.trigger()
    assert path_key(photo) in window._photo_list_metadata_pending_keys or window._photo_list_metadata_loader
    _wait_metadata_loaded(window)

    assert item.text(PHOTO_COL_TITLE) == "斑鱼狗（雌）"
    assert path_key(photo) not in window._photo_list_metadata_pending_keys
    assert not window._photo_list_metadata_forced_refresh_keys
    assert path_key(photo) in window._photo_export_dirty_keys
    # 刷新只读：原图与侧车均不被改写。
    assert photo.read_bytes() == original
    assert xmp.sidecar_path_for(str(photo)).read_bytes() == sidecar_after_edit
    assert xmp.read(str(photo)).get("XMP-dc:Title") == "斑鱼狗（雌）"


def test_refresh_forces_current_photo_context_rebuild(window, tmp_path, monkeypatch):
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (16, 12), "white").save(photo)
    _add_photo(window, photo)
    metadata = {"SourceFile": str(photo), "XMP-dc:Title": "白鹭", "ImageWidth": 16, "ImageHeight": 12}
    window.current_path = photo
    window.current_raw_metadata = dict(metadata)
    applied = []
    monkeypatch.setattr(window, "_restart_photo_list_metadata_loader", lambda: None)
    monkeypatch.setattr(window, "render_preview", lambda *a, **k: applied.append("render"))
    monkeypatch.setattr(window, "_refresh_preview_label", lambda *a, **k: applied.append("label"))

    # 未刷新时，相同元数据不重建上下文。
    window._apply_photo_list_metadata_batch({str(photo): dict(metadata)})
    assert applied == []

    # 刷新后即使 EXIF/XMP 未变（例如仅 report.db 变化）也要重建当前预览。
    window._refresh_photo_list_metadata()
    assert path_key(photo) in window._photo_list_metadata_pending_keys
    window._apply_photo_list_metadata_batch({str(photo): dict(metadata)})
    assert applied == ["label"]
    assert not window._photo_list_metadata_forced_refresh_keys


def test_refresh_ignored_for_empty_list_and_during_restore(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "_restart_photo_list_metadata_loader", lambda: started.append(True))
    window._refresh_photo_list_metadata()
    assert started == []
    monkeypatch.setattr(window, "_workspace_restore_in_progress", lambda: True)
    window._refresh_photo_list_metadata()
    assert started == []
