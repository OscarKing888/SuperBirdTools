from __future__ import annotations

import importlib
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


def test_main_menu_undo_redo_preserves_original_tags_and_survives_menu_rebuild(tmp_path, monkeypatch):
    main = importlib.import_module("SuperViewer.main")
    config = tmp_path / "tags.cfg"
    config.write_text("鸟类\n    白鹭\n", encoding="utf-8")
    monkeypatch.setattr(main, "SuperViewerTaggedFileListPanel", lambda: SuperViewerTaggedFileListPanel(tag_config_path=config))
    monkeypatch.setattr(main, "load_main_splitter_state_from_settings", lambda: None)
    monkeypatch.setattr(main, "save_main_splitter_state_to_settings", lambda _state: None)
    monkeypatch.setattr(main, "save_last_selected_directory_to_settings", lambda _path: None)
    photos = [tmp_path / "已有.png", tmp_path / "新增.png"]
    for path in photos:
        Image.new("RGB", (8, 6), (40, 120, 180)).save(path)
    metadata = PhotoMetaDataXMP()
    assert metadata.write_subjects(str(photos[0]), ["白鹭", "库外关键词"])
    window = main.MainWindow(initial_received_files=["skip-restore"])
    window.show()
    try:
        actions = window._tag_history_actions
        assert not actions.undo_action.isEnabled()
        window._file_list.set_photo_tag_for_paths([str(path) for path in photos], "白鹭", True)
        assert actions.undo_action.isEnabled()
        # Saving external-app settings rebuilds the menu using these same actions.
        window.menuBar().clear()
        window._init_menu_bar()
        edit = next(action.menu() for action in window.menuBar().actions() if action.text() == "编辑")
        assert edit.actions() == [actions.undo_action, actions.redo_action]
        actions.undo_action.trigger()
        assert metadata.read_subjects(str(photos[0]), strict=True) == ["白鹭", "库外关键词"]
        assert metadata.read_subjects(str(photos[1]), strict=True) == []
        assert actions.redo_action.isEnabled()
        actions.redo_action.trigger()
        assert metadata.read_subjects(str(photos[1]), strict=True) == ["白鹭"]
    finally:
        window.close()
        deadline = time.monotonic() + 5
        while not window._shutdown_finalized and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.005)
        assert window._shutdown_finalized
        window.deleteLater()
        _APP.processEvents()
