from __future__ import annotations

from pathlib import Path

import pytest

try:
    from PyQt6.QtCore import QEvent, QItemSelectionModel
except ImportError:
    from PyQt5.QtCore import QEvent, QItemSelectionModel

from SuperViewer.superviewer.qt_compat import QApplication
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
from SuperViewer.superviewer.ui_theme import build_palette, panel_theme_colors


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def panel_factory(qt_app, tmp_path: Path):
    original_palette = qt_app.palette()
    panels = []

    def create(scheme="dark", text="行为\n  飞行\n  捕食\n场景\n  湿地\n"):
        qt_app.setPalette(build_palette(scheme))
        cfg = tmp_path / f"tags-{len(panels)}.cfg"
        cfg.write_text(text, encoding="utf-8")
        panel = SuperViewerTaggedFileListPanel(tag_config_path=cfg)
        panels.append(panel)
        return panel

    yield create
    for panel in panels:
        panel.shutdown()
        panel.close()
        panel.deleteLater()
    qt_app.processEvents()
    qt_app.setPalette(original_palette)
    qt_app.processEvents()


def _assert_colors(panel, scheme):
    colors = panel_theme_colors(scheme)
    assert colors.secondary_text in panel._tag_filter_title_label.styleSheet()
    assert colors.label_text in panel._tag_filter_exact_match_checkbox.styleSheet()
    for button in panel._tag_filter_buttons.values():
        assert colors.chip_bg in button.styleSheet()
        assert colors.chip_text in button.styleSheet()
        assert "palette(highlighted-text)" in button.styleSheet()
    if panel._tag_filter_menu_button is not None:
        assert colors.chip_text in panel._tag_filter_menu_button.styleSheet()
    if panel._tag_filter_clear_button is not None:
        assert colors.secondary_text in panel._tag_filter_clear_button.styleSheet()
        assert colors.button_bg in panel._tag_filter_clear_button.styleSheet()
    if panel._tag_filter_empty_label is not None:
        assert colors.muted_text in panel._tag_filter_empty_label.styleSheet()


@pytest.mark.parametrize("scheme", ["dark", "light"])
@pytest.mark.parametrize("text", ["", "行为\n  飞行\n  捕食\n"])
def test_initial_and_rebuilt_filter_bar_use_current_palette(panel_factory, scheme, text):
    panel = panel_factory(scheme, text)
    _assert_colors(panel, scheme)
    # Rebuilding after a config change also styles newly created controls.
    panel._tag_config.path.write_text("场景\n  湿地\n  树林\n", encoding="utf-8")
    panel._load_tag_config_if_changed(force=True)
    assert set(panel._tag_filter_buttons) == {"湿地", "树林"}
    _assert_colors(panel, scheme)


def test_palette_changes_restyle_existing_controls_without_data_or_selection_work(
    qt_app, panel_factory, tmp_path: Path, monkeypatch,
):
    panel = panel_factory()
    panel._active_tag_filters = {"飞行", "湿地"}
    panel._tag_filter_partial_match = False
    panel._sync_tag_filter_widgets()
    path = str(tmp_path / "selected.jpg")
    panel._photo_tag_cache[path] = {"飞行"}
    panel._meta_cache[path] = {"photo_tags": ["飞行"], "comment": "草地"}
    panel._thumb_list_model.append_paths([path], meta_cache=panel._meta_cache, tooltip_fn=None, mismatch_fn=None)
    index = panel._thumb_list_model.index(0, 0)
    selection = panel._list_widget.selectionModel()
    flags = getattr(QItemSelectionModel, "SelectionFlag", QItemSelectionModel)
    selection.blockSignals(True)
    selection.setCurrentIndex(index, flags.ClearAndSelect)
    selection.blockSignals(False)
    qt_app.processEvents()

    controls = list(panel._tag_filter_buttons.values()) + [
        panel._tag_filter_menu_button, panel._tag_filter_clear_button,
        panel._tag_filter_exact_match_checkbox, panel._tag_filter_title_label,
    ]
    calls = []
    for method in (
        "_rebuild_tag_filter_bar", "_load_tag_config_if_changed", "_sync_tag_filter_widgets",
        "_refresh_filter_scope", "_apply_filter", "_refresh_metadata_state_for_paths",
        "_start_photo_tag_cache_loader_if_needed",
    ):
        monkeypatch.setattr(panel, method, lambda *args, name=method, **kwargs: calls.append(name))
    monkeypatch.setattr(panel._photo_tag_store, "load_tags_for_paths", lambda *args, **kwargs: calls.append("tag read"))
    monkeypatch.setattr(panel._photo_tag_store._metadata, "read_subjects", lambda *args, **kwargs: calls.append("XMP read"))

    for scheme in ("light", "dark", "light"):
        qt_app.setPalette(build_palette(scheme))
        qt_app.processEvents()
        _assert_colors(panel, scheme)
        assert controls == list(panel._tag_filter_buttons.values()) + [
            panel._tag_filter_menu_button, panel._tag_filter_clear_button,
            panel._tag_filter_exact_match_checkbox, panel._tag_filter_title_label,
        ]
        assert panel._active_tag_filters == {"飞行", "湿地"}
        assert panel._tag_filter_partial_match is False
        assert panel._tag_filter_buttons["飞行"].isChecked()
        assert panel._tag_filter_buttons["湿地"].isChecked()
        assert not panel._tag_filter_buttons["捕食"].isChecked()
        assert panel._tag_filter_exact_match_checkbox.isChecked()
        assert not panel._tag_filter_clear_button.isHidden()
        assert panel._tag_filter_menu_button.text() == "全部标签(2)"
        assert selection.currentIndex() == index
        assert selection.selectedIndexes() == [index]
        assert panel._photo_tag_cache[path] == {"飞行"}
        assert panel._meta_cache[path] == {"photo_tags": ["飞行"], "comment": "草地"}
        assert calls == []


def test_filter_palette_handler_ignores_reentrant_events(qt_app, panel_factory, monkeypatch):
    panel = panel_factory()
    qt_app.setPalette(build_palette("light"))
    qt_app.processEvents()
    button = panel._tag_filter_buttons["飞行"]
    button.setStyleSheet("")
    original_set_style = button.setStyleSheet
    reentries = []
    event_types = getattr(QEvent, "Type", QEvent)

    def set_style_with_palette_event(style):
        reentries.append(style)
        QApplication.sendEvent(panel, QEvent(event_types.PaletteChange))
        original_set_style(style)

    monkeypatch.setattr(button, "setStyleSheet", set_style_with_palette_event)
    QApplication.sendEvent(panel, QEvent(event_types.PaletteChange))
    assert len(reentries) == 1
    assert panel._tag_filter_theme_applying is False
    _assert_colors(panel, "light")
