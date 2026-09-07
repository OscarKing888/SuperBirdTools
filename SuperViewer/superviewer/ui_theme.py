# -*- coding: utf-8 -*-
"""Cross-platform Fusion dark/light theme helpers for SuperViewer UI panels."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import logging
import weakref

try:
    from PyQt6.QtCore import QEvent, QObject
    from PyQt6.QtGui import QColor, QPalette
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QEvent, QObject
    from PyQt5.QtGui import QColor, QPalette
    from PyQt5.QtWidgets import QApplication

from app_common.qt_theme import (
    ColorSchemeName,
    detect_color_scheme,
    is_theme_change_event,
    scheme_from_qt_value,
)


_LOG = logging.getLogger(__name__)

_THEME_MANAGER: "UiThemeManager | None" = None


@dataclass(frozen=True)
class PanelThemeColors:
    """Semantic colors used by titled image-info panels."""

    section_title: str
    muted_text: str
    secondary_text: str
    label_text: str
    value_text: str
    separator: str
    input_border: str
    preview_bg: str
    preview_border: str
    preview_text: str
    chip_bg: str
    chip_border: str
    chip_text: str
    chip_btn: str
    button_bg: str
    button_border: str
    button_text: str
    button_hover: str


_DARK_PANEL = PanelThemeColors(
    section_title="#b8b8b8",
    muted_text="#888888",
    secondary_text="#aaaaaa",
    label_text="#d7d7d7",
    value_text="#cfcfcf",
    separator="#303238",
    input_border="#303238",
    preview_bg="#202124",
    preview_border="#36383d",
    preview_text="#888888",
    chip_bg="#2b2d31",
    chip_border="#4a4c52",
    chip_text="#f0f0f0",
    chip_btn="#aaaaaa",
    button_bg="#2a2c30",
    button_border="#34363b",
    button_text="#e6e6e6",
    button_hover="#34373d",
)

_LIGHT_PANEL = PanelThemeColors(
    section_title="#5f6368",
    muted_text="#70757a",
    secondary_text="#5f6368",
    label_text="#3c4043",
    value_text="#202124",
    separator="#dadce0",
    input_border="#dadce0",
    preview_bg="#f1f3f4",
    preview_border="#dadce0",
    preview_text="#70757a",
    chip_bg="#e8eaed",
    chip_border="#dadce0",
    chip_text="#202124",
    chip_btn="#5f6368",
    button_bg="#f8f9fa",
    button_border="#dadce0",
    button_text="#202124",
    button_hover="#e8eaed",
)


def _role(name: str):
    roles = getattr(QPalette, "ColorRole", QPalette)
    return getattr(roles, name)


def _group(name: str):
    groups = getattr(QPalette, "ColorGroup", QPalette)
    return getattr(groups, name)


def _set_palette_color(palette: QPalette, role_name: str, color: QColor, *, disabled: QColor | None = None) -> None:
    role = _role(role_name)
    palette.setColor(role, color)
    if disabled is not None:
        palette.setColor(_group("Disabled"), role, disabled)


def build_palette(scheme: ColorSchemeName) -> QPalette:
    """Build a complete Fusion-friendly palette for dark or light mode."""
    palette = QPalette()
    if scheme == "light":
        window = QColor(245, 245, 245)
        window_text = QColor(32, 33, 36)
        base = QColor(255, 255, 255)
        alternate = QColor(240, 240, 240)
        text = QColor(32, 33, 36)
        button = QColor(240, 240, 240)
        button_text = QColor(32, 33, 36)
        highlight = QColor(0, 120, 215)
        highlighted_text = QColor(255, 255, 255)
        link = QColor(0, 102, 204)
        mid = QColor(200, 200, 200)
        dark = QColor(160, 160, 160)
        light = QColor(255, 255, 255)
        disabled_text = QColor(140, 140, 140)
        disabled_button_text = QColor(140, 140, 140)
        placeholder = QColor(130, 130, 130)
    else:
        window = QColor(45, 45, 45)
        window_text = QColor(220, 220, 220)
        base = QColor(35, 35, 35)
        alternate = QColor(50, 50, 50)
        text = QColor(220, 220, 220)
        button = QColor(53, 53, 53)
        button_text = QColor(220, 220, 220)
        highlight = QColor(42, 130, 218)
        highlighted_text = QColor(255, 255, 255)
        link = QColor(100, 180, 255)
        mid = QColor(70, 70, 70)
        dark = QColor(30, 30, 30)
        light = QColor(80, 80, 80)
        disabled_text = QColor(140, 140, 140)
        disabled_button_text = QColor(140, 140, 140)
        placeholder = QColor(140, 140, 140)

    _set_palette_color(palette, "Window", window)
    _set_palette_color(palette, "WindowText", window_text, disabled=disabled_text)
    _set_palette_color(palette, "Base", base)
    _set_palette_color(palette, "AlternateBase", alternate)
    _set_palette_color(palette, "Text", text, disabled=disabled_text)
    _set_palette_color(palette, "Button", button)
    _set_palette_color(palette, "ButtonText", button_text, disabled=disabled_button_text)
    _set_palette_color(palette, "BrightText", QColor(255, 255, 255))
    _set_palette_color(palette, "Highlight", highlight)
    _set_palette_color(palette, "HighlightedText", highlighted_text)
    _set_palette_color(palette, "Link", link)
    _set_palette_color(palette, "Mid", mid)
    _set_palette_color(palette, "Dark", dark)
    _set_palette_color(palette, "Light", light)
    _set_palette_color(palette, "Shadow", QColor(20, 20, 20) if scheme == "dark" else QColor(160, 160, 160))
    _set_palette_color(palette, "ToolTipBase", base)
    _set_palette_color(palette, "ToolTipText", text)
    try:
        _set_palette_color(palette, "PlaceholderText", placeholder)
    except AttributeError:
        pass
    return palette


def panel_theme_colors(scheme: ColorSchemeName | None = None) -> PanelThemeColors:
    """Return semantic colors without reading or refreshing photo data."""
    resolved = scheme or current_color_scheme()
    return _LIGHT_PANEL if resolved == "light" else _DARK_PANEL


def apply_app_palette(app: QApplication, scheme: ColorSchemeName | None = None) -> ColorSchemeName:
    """Install Fusion and apply the requested or detected application palette."""
    resolved = scheme or detect_color_scheme(app)
    app.setStyle("Fusion")
    app.setPalette(build_palette(resolved))
    return resolved


class UiThemeManager(QObject):
    """Manage palette changes and styling listeners, with no photo-data work.

    Listeners should update styles only. Bound methods are held weakly; callers
    using closures should remove them explicitly when their widgets close.
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._closed = False
        self._applying = False
        self._listeners: list[Callable | weakref.WeakMethod] = []
        self._connections: list[tuple[object, Callable]] = []
        self._scheme: ColorSchemeName = apply_app_palette(app)
        self._connect_system_signals()
        app.installEventFilter(self)

    @property
    def scheme(self) -> ColorSchemeName:
        return self._scheme

    @property
    def closed(self) -> bool:
        return self._closed

    def colors(self) -> PanelThemeColors:
        return panel_theme_colors(self._scheme)

    @staticmethod
    def _listener_callback(listener):
        return listener() if isinstance(listener, weakref.WeakMethod) else listener

    def add_listener(self, callback: Callable[[ColorSchemeName], None]) -> None:
        if self._closed:
            return
        if any(self._listener_callback(item) == callback for item in self._listeners):
            return
        try:
            listener = weakref.WeakMethod(callback)
        except TypeError:
            listener = callback
        self._listeners.append(listener)

    def remove_listener(self, callback: Callable[[ColorSchemeName], None]) -> None:
        self._listeners = [
            item for item in self._listeners
            if self._listener_callback(item) is not None
            and self._listener_callback(item) != callback
        ]

    def refresh(self, scheme: ColorSchemeName | None = None) -> ColorSchemeName:
        if self._closed or self._applying:
            return self._scheme
        resolved = scheme or detect_color_scheme(self._app)
        if resolved not in ("dark", "light"):
            raise ValueError(f"unsupported color scheme: {resolved!r}")
        if resolved == self._scheme:
            return self._scheme
        self._scheme = resolved
        self._applying = True
        try:
            # Fusion is selected once at installation. Subsequent changes only
            # replace the palette, preserving widget/layout and photo state.
            self._app.setPalette(build_palette(resolved))
        finally:
            self._applying = False
        self._notify(resolved)
        return resolved

    def _notify(self, scheme: ColorSchemeName) -> None:
        for listener in list(self._listeners):
            if self._closed:
                break
            if listener not in self._listeners:
                continue
            callback = self._listener_callback(listener)
            if callback is None:
                self._listeners.remove(listener)
                continue
            try:
                callback(scheme)
            except Exception:
                _LOG.exception("Theme style listener failed")

    def _connect(self, signal, callback) -> None:
        if signal is None:
            return
        try:
            signal.connect(callback)
        except (AttributeError, TypeError, RuntimeError):
            return
        self._connections.append((signal, callback))

    def _connect_system_signals(self) -> None:
        style_hints = getattr(self._app, "styleHints", None)
        hints = style_hints() if callable(style_hints) else None
        self._connect(getattr(hints, "colorSchemeChanged", None), self._on_color_scheme_changed)
        self._connect(getattr(self._app, "aboutToQuit", None), self.close)
        self._connect(getattr(self._app, "destroyed", None), self.close)

    def _on_color_scheme_changed(self, value=None) -> None:
        if self._closed:
            return
        self.refresh(scheme_from_qt_value(value) or detect_color_scheme(self._app))

    def eventFilter(self, watched, event) -> bool:
        if self._closed or self._applying or not is_theme_change_event(event):
            return False
        # Older Qt/PyQt5 lacks colorSchemeChanged. Application palette events
        # arrive on widgets too; a widget's own PaletteChange must not select
        # a new application theme from that widget's custom colors.
        event_types = getattr(QEvent, "Type", QEvent)
        application_palette = getattr(event_types, "ApplicationPaletteChange", None)
        theme_change = getattr(event_types, "ThemeChange", None)
        if watched is self._app or event.type() in (application_palette, theme_change):
            self.refresh()
        return False

    def close(self, *_args) -> None:
        """Disconnect system listeners and release styling callbacks once."""
        global _THEME_MANAGER
        if self._closed:
            return
        self._closed = True
        try:
            self._app.removeEventFilter(self)
        except RuntimeError:
            pass
        for signal, callback in self._connections:
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError):
                pass
        self._connections.clear()
        self._listeners.clear()
        if _THEME_MANAGER is self:
            _THEME_MANAGER = None
        self.deleteLater()


def install_app_theme(app: QApplication) -> UiThemeManager:
    """Install once per application; close a replaced manager first."""
    global _THEME_MANAGER
    if _THEME_MANAGER is not None:
        if not _THEME_MANAGER.closed and _THEME_MANAGER._app is app:
            return _THEME_MANAGER
        _THEME_MANAGER.close()
    _THEME_MANAGER = UiThemeManager(app)
    return _THEME_MANAGER


def get_ui_theme_manager() -> UiThemeManager | None:
    return _THEME_MANAGER


def current_color_scheme() -> ColorSchemeName:
    manager = get_ui_theme_manager()
    return manager.scheme if manager is not None else detect_color_scheme()


def current_panel_colors() -> PanelThemeColors:
    return panel_theme_colors()


# Keep the source branch's public names for incremental UI integration.
install_ui_theme = install_app_theme
panel_colors = panel_theme_colors


__all__ = [
    "ColorSchemeName",
    "PanelThemeColors",
    "UiThemeManager",
    "apply_app_palette",
    "build_palette",
    "current_color_scheme",
    "current_panel_colors",
    "detect_color_scheme",
    "get_ui_theme_manager",
    "install_app_theme",
    "install_ui_theme",
    "panel_colors",
    "panel_theme_colors",
]
