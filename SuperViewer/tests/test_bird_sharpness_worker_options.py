"""并行照片设置的草稿、键盘操作、持久化与小窗口布局。"""
import os
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options as options
from SuperViewer.superviewer import bird_archive_ui
from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog
from SuperViewer.superviewer.ui_theme import build_palette

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme,font_size", [("light", 10), ("dark", 16)])
def test_parallel_options_draft_keyboard_collapse_and_save(tmp_path, monkeypatch, theme, font_size):
    monkeypatch.setattr(options, "get_user_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", options.normalize_user_options({}))
    monkeypatch.setattr(bird_archive_ui, "settings_path", lambda: tmp_path / "archive.json")
    monkeypatch.delenv("SuperViewer_ANALYSIS_WORKERS", raising=False)
    palette, font = _APP.palette(), _APP.font()
    _APP.setPalette(build_palette(theme))
    larger = _APP.font()
    larger.setPointSize(font_size)
    _APP.setFont(larger)
    dialog = SuperViewerUserOptionsDialog(options=options.normalize_user_options({}))
    try:
        spin = dialog._spin_bird_sharpness_workers
        assert spin.value() == 0 and spin.text().startswith("自动")
        dialog.tabs.setCurrentIndex(next(i for i in range(dialog.tabs.count())
                                         if dialog.tabs.tabText(i) == "鸟清晰度"))
        dialog.resize(640, 420)
        dialog.show()
        _APP.processEvents()
        spin.setFocus()
        spin.selectAll()
        QTest.keyClicks(spin, "12")
        QTest.keyClick(spin, Qt.Key.Key_Tab)
        assert spin.value() == 12
        for _ in range(3):
            group = dialog.option_groups["并行处理"]
            group.header_button.setFocus()
            QTest.keyClick(group.header_button, Qt.Key.Key_Space)
            assert not spin.isVisibleTo(dialog)
            QTest.keyClick(group.header_button, Qt.Key.Key_Space)
            assert spin.isVisibleTo(dialog) and spin.value() == 12
        _APP.processEvents()
        page = dialog.tabs.currentWidget()
        page.ensureWidgetVisible(spin, 0, 0)
        page.horizontalScrollBar().setValue(0)
        page.verticalScrollBar().setValue(0)
        assert page.viewport().rect().contains(spin.mapTo(page.viewport(), spin.rect().bottomRight()))
        assert spin.parentWidget().rect().contains(spin.geometry())
        assert spin.width() >= spin.minimumSizeHint().width()
        assert dialog.rect().contains(dialog.buttons.geometry())
        qa = os.environ.get("SUPERBIRD_WORKERS_QA")
        if qa:
            Path(qa).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(qa) / f"workers-{theme}.png"))
        draft = dialog.selected_options()
        assert draft[options.KEY_BIRD_SHARPNESS_WORKERS] == 12
        before_params = options.get_bird_sharpness_params()
        dialog.reject()
        assert not (tmp_path / options.USER_OPTIONS_FILENAME).exists()
        options.save_user_options(draft)
        saved = options.load_user_options()
        assert saved[options.KEY_BIRD_SHARPNESS_WORKERS] == 12
        assert options.get_bird_sharpness_params() == before_params
        reopened = SuperViewerUserOptionsDialog(options=saved)
        assert reopened._spin_bird_sharpness_workers.value() == 12
        reopened.close()
        reopened.deleteLater()
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()
        _APP.setPalette(palette)
        _APP.setFont(font)
