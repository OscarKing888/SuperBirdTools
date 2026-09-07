from __future__ import annotations

import importlib
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options
from app_common.qt_theme import browser_chrome_colors
from SuperViewer.superviewer import paths_settings, ui_theme
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    assert predicate(), "MainWindow background work did not settle"


def test_main_window_theme_preserves_real_preview_selection_and_unsaved_comment(tmp_path, monkeypatch):
    main = importlib.import_module("SuperViewer.main")
    previous = ui_theme.get_ui_theme_manager()
    if previous is not None:
        previous.close()
    original_palette = QPalette(_APP.palette())
    original_style = _APP.style().objectName()
    # Manual refresh exercises the palette fallback, independent of host theme.
    monkeypatch.setattr(_APP, "styleHints", lambda: SimpleNamespace())
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    (settings_dir / paths_settings.CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(settings_dir))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(settings_dir / "state"))
    monkeypatch.setattr(main, "_get_app_dir", lambda: str(settings_dir))
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(settings_dir))
    monkeypatch.setattr(superviewer_user_options, "_RUNTIME_OPTIONS", {
        **superviewer_user_options.get_runtime_user_options(),
        "thumbnail_loader_workers": 1, "metadata_loader_workers": 1,
        "persistent_thumb_workers": 1,
    })
    monkeypatch.setenv("LOCALAPPDATA", str(settings_dir / "cache"))
    library = tmp_path / "library"
    (library / ".superpicky").mkdir(parents=True)
    config = library / ".superpicky" / "tags.cfg"
    config.write_text("鸟类\n    白鹭\n", encoding="utf-8")
    monkeypatch.setattr(main, "SuperViewerTaggedFileListPanel", lambda: SuperViewerTaggedFileListPanel(tag_config_path=config))
    for name, color in (("first.png", (20, 60, 100)), ("selected.png", (40, 120, 180))):
        Image.new("RGB", (24, 18), color).save(library / name)
    selected = os.path.normpath(str(library / "selected.png"))
    manager = ui_theme.install_app_theme(_APP)
    window = main.MainWindow(initial_received_files=["skip-restore"])
    reads = {"preview": 0, "exif": 0, "info": 0}

    def record_read(name, callback):
        def wrapped(*args, **kwargs):
            reads[name] += 1
            return callback(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(window.preview_panel, "set_image", record_read("preview", window.preview_panel.set_image))
    monkeypatch.setattr(window.exif_info_panel, "_metadata_rows_loader", record_read("exif", window.exif_info_panel._metadata_rows_loader))
    monkeypatch.setattr(window.image_info_panel, "_metadata_provider", record_read("info", window.image_info_panel._metadata_provider))
    window.show()
    try:
        assert window._dir_browser.select_directory(str(library))
        panel = window._file_list
        thumbnails = panel._view_mode == panel._MODE_THUMB
        index_for_path = panel._thumb_index_for_path if thumbnails else panel._tree_index_for_path
        _wait_until(lambda: index_for_path(selected).isValid())
        view = panel._list_widget if thumbnails else panel._tree_widget
        index = index_for_path(selected)
        view.scrollTo(index)
        _APP.processEvents()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())
        _wait_until(lambda: window.preview_panel.current_path() == selected and window._file_list.get_selected_display_path() == selected)
        # Load the real EXIF tab once; later palette changes must not read it again.
        window.image_info_tabs.setCurrentWidget(window.exif_info_panel)
        _wait_until(lambda: reads["exif"] > 0 and window.exif_info_panel._loader is None)
        window.image_info_tabs.setCurrentWidget(window.image_info_panel)

        def selection_is_idle():
            panel = window._file_list
            workers = (
                panel._directory_scan_worker, panel._metadata_loader,
                panel._photo_tag_loader, window._focus_loader,
                window.preview_panel._full_preview_loader,
            )
            return (
                all(worker is None or not worker.isRunning() for worker in workers)
                and panel._meta_apply_index >= len(panel._meta_apply_items)
                and panel._pending_directory_listing_result is None
                and not window._dir_browser_sync_timer.isActive()
            )

        _wait_until(selection_is_idle)
        QTest.qWait(100)
        source_pixmap = window.preview_panel.source_pixmap_for_path(selected)
        assert source_pixmap is not None and (source_pixmap.width(), source_pixmap.height()) == (24, 18)
        cache_key = source_pixmap.cacheKey()
        selected_paths = window._file_list._active_view_selected_paths()
        directory_item = window._dir_browser._tree.currentItem()
        draft = "尚未保存的备注\n主题切换后保留"
        window.image_info_panel.comment_edit.setPlainText(draft)
        baseline_reads = dict(reads)
        assert baseline_reads["preview"] > 0 and baseline_reads["exif"] > 0
        for scheme in ("light", "dark"):
            manager.refresh(scheme)
            # FileListPanel coalesces QSS updates into an owned timer.
            # Wait for that work before checking the actual child palettes.
            _wait_until(lambda: not panel._theme_refresh_timer.isActive())
            colors = ui_theme.panel_theme_colors(scheme)
            assert manager.scheme == scheme
            assert reads == baseline_reads
            assert window.preview_panel.source_pixmap_for_path(selected).cacheKey() == cache_key
            assert window._file_list.get_selected_display_path() == selected
            assert window._file_list._active_view_selected_paths() == selected_paths
            assert window._dir_browser._tree.currentItem() is directory_item
            assert window.image_info_panel.comment_edit.toPlainText() == draft
            assert colors.section_title in window.image_info_panel._section_title_labels[0].styleSheet()
            assert colors.secondary_text in window.file_label.styleSheet()
            assert browser_chrome_colors(scheme).toolbar_bg in window._dir_browser._toolbar_widget.styleSheet()
            expected_palette = ui_theme.build_palette(scheme)
            for surface_name, surface in (
                ("thumbnail viewport", panel._list_widget.viewport()),
                ("tree viewport", panel._tree_widget.viewport()),
                ("filter edit", panel._filter_edit),
            ):
                for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Text):
                    actual = surface.palette().color(role)
                    expected = expected_palette.color(role)
                    assert actual == expected, (
                        f"{scheme} {surface_name} {type(surface).__name__} "
                        f"objectName={surface.objectName()!r} {role.name}: "
                        f"actual={actual.name()} expected={expected.name()} "
                        f"refresh_timer_active={panel._theme_refresh_timer.isActive()} "
                        f"styled_base={panel._styled_widget_palette.color(QPalette.ColorRole.Base).name()} "
                        f"panel_base={panel.palette().color(QPalette.ColorRole.Base).name()} "
                        f"app_base={_APP.palette().color(QPalette.ColorRole.Base).name()}"
                    )
            for label_name, label in (("size", panel._size_label), ("selection status", panel._selection_status_label)):
                actual = label.palette().color(QPalette.ColorRole.WindowText)
                expected = expected_palette.color(QPalette.ColorRole.Text)
                assert actual == expected, (
                    f"{scheme} {label_name} {type(label).__name__} "
                    f"objectName={label.objectName()!r} WindowText: "
                    f"actual={actual.name()} expected={expected.name()}"
                )
            screenshot_path = tmp_path / f"main-window-{scheme}.png"
            assert window.grab().save(str(screenshot_path))
            print(f"Theme screenshot: {screenshot_path}")
    finally:
        window.close()
        _wait_until(lambda: window._shutdown_finalized)
        callbacks = [manager._listener_callback(item) for item in manager._listeners]
        assert not any(getattr(callback, "__self__", None) in (window, window.image_info_tabs) for callback in callbacks)
        assert window.preview_panel._full_preview_loader is None
        assert window.exif_info_panel._loader is None
        assert not window._file_list._pending_loaders
        window.deleteLater()
        manager.close()
        _APP.processEvents()
        _APP.setStyle(original_style)
        _APP.setPalette(original_palette)
