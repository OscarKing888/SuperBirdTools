import importlib
import time

import pytest
from PIL import Image

from app_common import superviewer_user_options
from app_common.file_browser import _permissions
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
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS",
                        superviewer_user_options.normalize_user_options(None))
    for name, value in vars(_permissions).copy().items():
        if name.startswith("CURRENT_SUPERPICKY_"):
            monkeypatch.setattr(_permissions, name, value)
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


def test_ab_window_routes_list_to_active_side_and_keeps_info_on_list_selection(window, tmp_path):
    photos = [tmp_path / f"对照{i}.png" for i in range(3)]
    for photo in photos:
        Image.new("RGB", (40, 30)).save(photo)
    paths = [str(photo) for photo in photos]
    window.preview_compare.set_display_paths(paths)
    window._on_file_selected_from_list(paths[1])
    window.preview_compare.set_enabled(True)
    assert window.preview_compare.path_for_side("A") == paths[1]
    assert window.preview_compare.path_for_side("B") == paths[1]

    window.preview_compare.set_active_side("A")
    assert window.preview_panel is window.preview_compare.preview_for_side("A")
    window._on_file_selected_from_list(paths[2])
    assert window.preview_compare.path_for_side("A") == paths[2]
    assert window.preview_compare.path_for_side("B") == paths[1]
    assert window._current_exif_path == paths[2]

    window.preview_compare.set_side_path("B", paths[0])
    assert window._current_exif_path == paths[2]
    window._on_file_fast_preview_requested(paths[1])
    assert window.preview_compare.path_for_side("A") == paths[1]
    assert window.preview_compare.preview_for_side("A")._fast_preview_only
    window.preview_compare.set_enabled(False)
    assert window.preview_panel is window.preview_compare.preview_for_side("A")


def test_ab_retains_both_images_when_directory_auto_selects_first(window, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a = first / "甲.png"
    b = first / "乙.png"
    next_photo = second / "丙.png"
    for photo in (a, b, next_photo):
        Image.new("RGB", (30, 20)).save(photo)

    window._file_list.load_directory(str(first))
    deadline = time.monotonic() + 5
    while len(window._file_list._filtered_files) < 2 and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(.002)
    assert len(window._file_list._filtered_files) == 2
    window.preview_compare.set_enabled(True)
    window.preview_compare.set_side_path("A", str(a))
    window.preview_compare.set_side_path("B", str(b))
    window._on_ab_choice_changed(str(b))

    window._file_list.load_directory(str(second))
    deadline = time.monotonic() + 5
    while window._file_list._filtered_files != [str(next_photo)] and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(.002)
    assert window._file_list._filtered_files == [str(next_photo)]
    assert window._current_exif_path == str(b)
    assert window.preview_compare.path_for_side("A") == str(a)
    assert window.preview_compare.path_for_side("B") == str(b)
    assert window.preview_compare._filenames["A"].toolTip() == str(a)
    assert window.preview_compare._filenames["B"].toolTip() == str(b)
