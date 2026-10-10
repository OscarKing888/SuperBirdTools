"""Draft settings, validation, persistence and rendered video options page."""
import os
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app_common import superviewer_user_options as options
from SuperViewer.superviewer import bird_archive_ui
from SuperViewer.superviewer import super_viewer_user_options_dialog as ui
from SuperViewer.superviewer.ui_theme import build_palette

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme,size", [("light", 10), ("dark", 16)])
def test_video_options_draft_validation_layout_and_roundtrip(tmp_path, monkeypatch, theme, size):
    monkeypatch.setattr(options, "get_user_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(options, "_get_app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(bird_archive_ui, "settings_path", lambda: tmp_path / "archive.json")
    old_palette, old_font = _APP.palette(), _APP.font()
    _APP.setPalette(build_palette(theme))
    font = _APP.font()
    if os.name == "nt":
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font_path)))
        if families:
            font.setFamily(families[0])
    font.setPointSize(size)
    _APP.setFont(font)
    dialog = ui.SuperViewerUserOptionsDialog(options=options.normalize_user_options({}))
    try:
        form = dialog._video_frame_form
        assert form.mode.currentData() == "source_subdir"
        assert form.suffix.text() == "_Frames"
        assert not form.directory.isEnabled()
        form.directory.setText(str(tmp_path / "固定根目录"))
        form.mode.setCurrentIndex(form.mode.findData("fixed"))
        assert form.directory.isEnabled() and form.browse.isEnabled()
        form.suffix.setFocus()
        form.suffix.selectAll()
        QTest.keyClicks(form.suffix, "_Custom")
        before = form.selected_options()
        group = dialog.option_groups["视频帧提取"]
        for _ in range(2):
            group.set_expanded(False)
            group.set_expanded(True)
            assert form.selected_options() == before
        for mode in ("ask", "source_subdir", "fixed"):
            form.mode.setCurrentIndex(form.mode.findData(mode))
            assert form.directory.text() == before["video_frame_output_directory"]
        form.suffix.setText("../bad")
        messages = []
        monkeypatch.setattr(ui.QMessageBox, "information", lambda *a: messages.append(a))
        dialog.accept()
        assert messages and dialog.result() == 0
        form.suffix.setText("_逐帧")
        form.directory.clear()
        assert form.validation_error()
        form.directory.setText(str(tmp_path / "固定根目录"))
        assert not form.validation_error()
        dialog.tabs.setCurrentIndex(2)
        dialog.resize(760, 520)
        dialog.show()
        _APP.processEvents()
        for widget in (form.mode, form.directory, form.browse, form.suffix, form.example):
            assert form.rect().contains(widget.geometry())
        assert dialog.buttons.geometry().bottom() < dialog.height()
        qa = os.environ.get("SUPERBIRD_FRAME_EXPORT_QA")
        if qa:
            Path(qa).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(qa) / f"options-{theme}.png"))
        draft = dialog.selected_options()
        dialog.reject()
        assert not (tmp_path / options.USER_OPTIONS_FILENAME).exists()
        options.save_user_options(draft)
        loaded = options.load_user_options()
        for key in options.VIDEO_FRAME_DEFAULT_OPTIONS:
            assert loaded[key] == draft[key]
    finally:
        dialog.close()
        dialog.deleteLater()
        _APP.processEvents()
        _APP.setPalette(old_palette)
        _APP.setFont(old_font)
