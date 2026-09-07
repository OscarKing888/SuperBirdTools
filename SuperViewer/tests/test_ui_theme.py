from __future__ import annotations

import gc
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel

from SuperViewer.superviewer import ui_theme


_APP = QApplication.instance() or QApplication([])


class _StyleHints(QObject):
    colorSchemeChanged = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.value = 0

    def colorScheme(self):
        return self.value


@pytest.fixture
def app(monkeypatch):
    previous = ui_theme.get_ui_theme_manager()
    if previous is not None:
        previous.close()
    palette = QPalette(_APP.palette())
    style_name = _APP.style().objectName()
    # Exercise the palette fallback unless a test supplies modern style hints.
    monkeypatch.setattr(_APP, "styleHints", lambda: SimpleNamespace())
    yield _APP
    manager = ui_theme.get_ui_theme_manager()
    if manager is not None:
        manager.close()
    _APP.setStyle(style_name)
    _APP.setPalette(palette)
    _APP.processEvents()


def test_palette_contains_readable_active_and_disabled_colors() -> None:
    for scheme in ("dark", "light"):
        palette = ui_theme.build_palette(scheme)
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        assert abs(window.lightness() - text.lightness()) > 100
        assert disabled != text
    assert ui_theme.panel_theme_colors("dark").preview_bg != ui_theme.panel_theme_colors("light").preview_bg


def test_install_reuses_manager_and_palette_updates_do_not_reload_photo(app, monkeypatch) -> None:
    manager = ui_theme.install_app_theme(app)
    assert ui_theme.install_app_theme(app) is manager
    assert ui_theme.install_ui_theme(app) is manager
    label = QLabel()
    pixmap = QPixmap(32, 24)
    pixmap.fill(QColor("#39aaff"))
    label.setPixmap(pixmap)
    cache_key = label.pixmap().cacheKey()
    notifications = []

    def apply_style(scheme):
        colors = ui_theme.panel_theme_colors(scheme)
        label.setStyleSheet(f"background: {colors.preview_bg}; color: {colors.preview_text};")
        notifications.append(scheme)

    def unexpected_data_read(*_args, **_kwargs):
        raise AssertionError("theme changes must not load images or metadata")

    from app_common import thumb_stream
    from app_common import exif_io

    monkeypatch.setattr(thumb_stream, "load_thumbnail_rgb", unexpected_data_read)
    monkeypatch.setattr(exif_io, "read_batch_metadata", unexpected_data_read)
    style_calls = []
    monkeypatch.setattr(app, "setStyle", lambda value: style_calls.append(value))
    manager.add_listener(apply_style)
    target = "light" if manager.scheme == "dark" else "dark"
    manager.refresh(target)
    manager.refresh(target)
    app.processEvents()

    assert notifications == [target]
    assert style_calls == []
    assert ui_theme.current_color_scheme() == target
    assert ui_theme.current_panel_colors() == manager.colors()
    assert label.pixmap().cacheKey() == cache_key
    manager.remove_listener(apply_style)
    label.close()


def test_system_signal_changes_palette_once_and_disconnects_on_close(app, monkeypatch) -> None:
    hints = _StyleHints()
    hints.value = 2
    monkeypatch.setattr(app, "styleHints", lambda: hints)
    manager = ui_theme.install_app_theme(app)
    notifications = []
    manager.add_listener(notifications.append)
    hints.value = 1
    hints.colorSchemeChanged.emit(1)
    hints.colorSchemeChanged.emit(1)
    app.processEvents()
    assert manager.scheme == "light"
    assert notifications == ["light"]
    assert hints.receivers(hints.colorSchemeChanged) == 1

    manager.close()
    manager.close()
    assert hints.receivers(hints.colorSchemeChanged) == 0
    assert ui_theme.get_ui_theme_manager() is None
    hints.value = 2
    hints.colorSchemeChanged.emit(2)
    assert notifications == ["light"]
    assert manager.refresh("dark") == "light"


def test_old_qt_application_palette_event_follows_system_but_widget_palette_does_not(app) -> None:
    manager = ui_theme.install_app_theme(app)
    notifications = []
    manager.add_listener(notifications.append)
    widget = QLabel()
    target = "light" if manager.scheme == "dark" else "dark"
    palette = ui_theme.build_palette(target)
    widget.setPalette(palette)
    app.processEvents()
    assert notifications == []

    app.setPalette(palette)
    QApplication.sendEvent(widget, QEvent(QEvent.Type.ApplicationPaletteChange))
    app.processEvents()
    assert manager.scheme == target
    assert notifications == [target]
    widget.close()


def test_weak_listeners_do_not_retain_closed_panel_objects(app) -> None:
    manager = ui_theme.install_app_theme(app)
    notifications = []

    class _Panel:
        def apply_theme(self, scheme):
            notifications.append(scheme)

    panel = _Panel()
    reference = weakref.ref(panel)
    manager.add_listener(panel.apply_theme)
    del panel
    gc.collect()
    assert reference() is None
    manager.refresh("light" if manager.scheme == "dark" else "dark")
    assert notifications == []


def test_application_quit_closes_manager() -> None:
    # Use a separate QApplication so its real quit signal cannot stop panels
    # that other test modules share in this pytest process.
    code = """
from PyQt6.QtWidgets import QApplication
from SuperViewer.superviewer.ui_theme import install_app_theme, get_ui_theme_manager
app = QApplication([])
manager = install_app_theme(app)
app.aboutToQuit.emit()
assert manager.closed
assert get_ui_theme_manager() is None
assert manager._connections == []
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
