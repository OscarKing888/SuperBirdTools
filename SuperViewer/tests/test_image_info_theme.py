from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QPalette

from SuperViewer.superviewer.image_info_tab_base import ImageInfoTabPanel
from SuperViewer.superviewer.image_info_tab_exif import ImageInfoTabPanel_EXIF
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
from SuperViewer.superviewer.image_info_tab_tags import ImageInfoTabPanel_Tags
from SuperViewer.superviewer.image_info_tab_widget import ImageInfoTabWidget
from SuperViewer.superviewer.qt_compat import QApplication, QLabel
from SuperViewer.superviewer import ui_theme


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def app(monkeypatch):
    previous = ui_theme.get_ui_theme_manager()
    if previous is not None:
        previous.close()
    palette = QPalette(_APP.palette())
    style_name = _APP.style().objectName()
    monkeypatch.setattr(_APP, "styleHints", lambda: SimpleNamespace())
    yield _APP
    manager = ui_theme.get_ui_theme_manager()
    if manager is not None:
        manager.close()
    _APP.setStyle(style_name)
    _APP.setPalette(palette)
    _APP.processEvents()


def test_image_info_theme_restyles_existing_chips_without_data_or_widget_rebuild(
    app, tmp_path, monkeypatch
):
    calls = []
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    panel = ImageInfoTabPanel_ImageInfo(
        lambda: ["飞行"], lambda _path: {"飞行"},
        lambda *_args: calls.append("tag write"), lambda path, _name: path,
        metadata_provider=lambda _path: calls.append("metadata") or {},
        preview_pixmap_provider=lambda _path: calls.append("preview"),
        comment_save_callback=lambda *_args: calls.append("comment write") or True,
    )
    try:
        panel._current_photo_path = str(source)
        panel._current_tags = {"飞行"}
        panel._rebuild_tag_chips()
        panel._set_basic_info({"鸟名": "白鹭", "对焦": "精焦"})
        panel.comment_edit.setPlainText("未保存的备注\n第二行")
        panel.filename_edit.setText("未保存的文件名")
        chip = panel.tags_layout.itemAt(0).widget()
        chip_label = chip.findChild(QLabel, "svTagChipLabel")
        focus_style = panel.basic_rows["对焦"].styleSheet()
        before_widgets = [panel.tags_layout.itemAt(index).widget() for index in range(panel.tags_layout.count())]

        def unexpected_reload(*_args, **_kwargs):
            calls.append("reload")
            raise AssertionError("Theme change performed photo work")

        for name in ("refresh_ui", "_load_metadata", "_load_preview", "_load_basic_info", "_rebuild_tag_chips"):
            monkeypatch.setattr(panel, name, unexpected_reload)
        calls.clear()
        for scheme in ("light", "dark", "light"):
            colors = ui_theme.panel_theme_colors(scheme)
            panel.apply_theme(colors)
            chip_label.ensurePolished()
            assert colors.section_title in panel._section_title_labels[0].styleSheet()
            assert colors.separator in panel._separator_lines[0].styleSheet()
            assert colors.value_text in panel.basic_rows["鸟名"].styleSheet()
            assert colors.chip_bg in panel.tags_container.styleSheet()
            assert chip_label.palette().color(QPalette.ColorRole.WindowText).name() == colors.chip_text
            assert panel.basic_rows["对焦"].styleSheet() == focus_style
            assert panel.comment_edit.toPlainText() == "未保存的备注\n第二行"
            assert panel.filename_edit.text() == "未保存的文件名"
            assert [panel.tags_layout.itemAt(index).widget() for index in range(panel.tags_layout.count())] == before_widgets
            assert not panel.preview_label.isVisible()
            assert panel._preview_pixmap is None
        assert calls == []
    finally:
        panel.close()


def test_new_tag_chips_inherit_current_theme_without_another_theme_refresh(app):
    panel = ImageInfoTabPanel_ImageInfo(
        lambda: ["飞行"], lambda _path: set(), lambda *_args: None,
        lambda path, _name: path,
    )
    try:
        colors = ui_theme.panel_theme_colors("light")
        panel.apply_theme(colors)
        panel._current_tags = {"飞行"}
        panel._rebuild_tag_chips()
        label = panel.tags_container.findChild(QLabel, "svTagChipLabel")
        label.ensurePolished()
        assert label.palette().color(QPalette.ColorRole.WindowText).name() == colors.chip_text
    finally:
        panel.close()


def test_tags_and_exif_theme_changes_preserve_rows_filters_and_pending_requests(app, monkeypatch):
    calls = []
    tags = ImageInfoTabPanel_Tags(
        lambda: calls.append("tags read") or [], lambda _path: set(),
        lambda *_args: None, lambda *_args: None,
    )
    exif = ImageInfoTabPanel_EXIF(lambda *_args: calls.append("exif read") or [], lambda *_args: None)
    try:
        rows = [(None, None, "Group", "Name", "白鹭", None, None)]
        exif._last_rows = rows
        exif.exif_table.set_exif(rows)
        exif.exif_filter.setText("白鹭")
        exif._display_request_token = 17
        exif._pending_request = (18, "pending.jpg", False)
        tags.photo_label.setText("current.jpg")

        def unexpected_reload(*_args, **_kwargs):
            calls.append("reload")

        monkeypatch.setattr(tags, "refresh_ui", unexpected_reload)
        monkeypatch.setattr(exif, "refresh_ui", unexpected_reload)
        monkeypatch.setattr(exif, "_launch_request", unexpected_reload)
        monkeypatch.setattr(exif.exif_table, "set_exif", unexpected_reload)
        for scheme in ("light", "dark"):
            colors = ui_theme.panel_theme_colors(scheme)
            tags.apply_theme(colors)
            exif.apply_theme(colors)
            assert colors.secondary_text in tags.photo_label.styleSheet()
            assert colors.muted_text in tags.empty_label.styleSheet()
            assert colors.input_border in exif.exif_filter.styleSheet()
        assert calls == []
        assert tags.photo_label.text() == "current.jpg"
        assert exif.exif_filter.text() == "白鹭"
        assert exif.last_rows() == rows
        assert exif._display_request_token == 17
        assert exif._pending_request == (18, "pending.jpg", False)
        assert exif._loader is None
    finally:
        tags.close()
        exif.close()


class _ProbePanel(ImageInfoTabPanel):
    def __init__(self):
        self.refresh_count = 0
        self.theme_count = 0
        self.last_colors = None
        super().__init__()

    def create_ui(self):
        pass

    def refresh_ui(self):
        self.refresh_count += 1

    def apply_theme(self, colors=None):
        self.theme_count += 1
        self.last_colors = colors or ui_theme.current_panel_colors()


def test_theme_broadcast_keeps_inactive_tab_lazy_and_detaches_on_shutdown(app):
    manager = ui_theme.install_app_theme(app)
    tabs = ImageInfoTabWidget()
    first = _ProbePanel()
    second = _ProbePanel()
    try:
        tabs.add_info_panel(first)
        tabs.add_info_panel(second)
        tabs.on_photo_selected("pending.jpg")
        assert (first.refresh_count, second.refresh_count) == (1, 0)
        assert second in tabs._pending_panels
        for scheme in ("light", "dark"):
            manager.refresh(scheme)
            app.processEvents()
            assert first.last_colors == ui_theme.panel_theme_colors(scheme)
            assert second.last_colors == ui_theme.panel_theme_colors(scheme)
            assert (first.refresh_count, second.refresh_count) == (1, 0)
            assert second in tabs._pending_panels
        QApplication.sendEvent(first, QEvent(QEvent.Type.PaletteChange))
        assert (first.refresh_count, second.refresh_count) == (1, 0)
        tabs.setCurrentIndex(1)
        assert second.refresh_count == 1
        assert second not in tabs._pending_panels
        assert any(manager._listener_callback(item) == tabs.apply_theme for item in manager._listeners)
        tabs.request_shutdown()
        assert tabs._theme_manager is None
        assert not any(manager._listener_callback(item) == tabs.apply_theme for item in manager._listeners)
    finally:
        tabs.close()
