import importlib
import time

import pytest
from PIL import Image

from app_common import superviewer_user_options
from SuperViewer.superviewer import paths_settings, preview_panel
from SuperViewer.superviewer.qt_compat import QApplication, QImage
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])
main = importlib.import_module("SuperViewer.main")


@pytest.fixture
def window(tmp_path, monkeypatch):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / paths_settings.CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(settings / "state"))
    monkeypatch.setattr(main, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(settings))
    monkeypatch.setenv("LOCALAPPDATA", str(settings / "cache"))
    monkeypatch.setenv("APPDATA", str(settings / "appdata"))
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    monkeypatch.setattr(main, "SuperViewerTaggedFileListPanel", lambda: SuperViewerTaggedFileListPanel(tag_config_path=config))
    result = main.MainWindow(initial_received_files=["skip-restore"])
    try:
        yield result
    finally:
        result.close()
        deadline = time.monotonic() + 5
        while not result._shutdown_finalized and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.002)
        assert result._shutdown_finalized
        result.deleteLater()
        _APP.processEvents()


@pytest.mark.parametrize("hidden", [False, True])
def test_full_preview_signal_fills_info_preview_without_resetting_drafts(window, tmp_path, monkeypatch, hidden):
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (80, 60)).save(photo)
    path = str(photo)
    window._file_list._meta_cache[path] = {"comment": "原备注", "rating": 2}
    window.on_image_loaded(path)
    info = window.image_info_panel
    assert info._preview_pixmap is None
    info.comment_edit.insert("草稿")
    info.filename_edit.insert("新名字")
    info.comment_edit.setSelection(0, 2)
    before = (info.comment_edit.text(), info.comment_edit.selectedText(), info.filename_edit.text())
    if hidden:
        window.image_info_tabs.setCurrentWidget(window.tags_info_panel)
    metadata_reads = []
    original_provider = info._metadata_provider
    monkeypatch.setattr(info, "_metadata_provider", lambda path: metadata_reads.append(path) or original_provider(path))

    preview = window.preview_panel
    preview._current_path = path
    preview._preview_request_token += 1
    image = QImage(80, 60, preview_panel._qimage_rgb888_format())
    image.fill(80)
    preview._on_full_preview_loaded(preview._preview_request_token, path, image, 10.0)

    if hidden:
        assert info._preview_pixmap is None
        assert metadata_reads == []
        window.image_info_tabs.setCurrentWidget(info)
    assert metadata_reads == [path]
    assert info._preview_pixmap is not None
    assert (info._preview_pixmap.width(), info._preview_pixmap.height()) == (80, 60)
    assert (info.comment_edit.text(), info.comment_edit.selectedText(), info.filename_edit.text()) == before
    assert info.basic_rows["尺寸"].text() == "80 × 60"


@pytest.mark.parametrize("reason", ["stale", "playback", "closing"])
def test_full_preview_signal_does_not_refresh_stale_or_stopped_info(window, tmp_path, monkeypatch, reason):
    photo = tmp_path / "图片.png"
    Image.new("RGB", (8, 6)).save(photo)
    path = str(photo)
    window.on_image_loaded(path)
    calls = []
    monkeypatch.setattr(window.image_info_panel, "refresh_metadata_fields", lambda: calls.append(path))
    if reason == "stale":
        path += ".old"
    elif reason == "playback":
        window._file_list._selection_key_nav_hold_active = True
    else:
        window._shutdown_requested = True
    window.preview_panel.full_preview_ready.emit(path)
    assert calls == []
    window._shutdown_requested = False
