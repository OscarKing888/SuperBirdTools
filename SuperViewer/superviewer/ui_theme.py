# -*- coding: utf-8 -*-
"""Cross-platform Fusion dark/light theme helpers for SuperViewer UI panels."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .qt_compat import QApplication, QColor, QPalette


ColorSchemeName = Literal["dark", "light"]

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


def _scheme_from_qt_value(value) -> ColorSchemeName | None:
    if value is None:
        return None
    name_attr = getattr(value, "name", None)
    if callable(name_attr):
        text = str(name_attr())
    elif name_attr is not None:
        text = str(name_attr)
    else:
        text = str(value)
    lowered = text.lower()
    if "dark" in lowered:
        return "dark"
    if "light" in lowered:
        return "light"
    # Qt ColorScheme enum values: Unknown=0, Light=1, Dark=2
    try:
        numeric = int(value)
    except Exception:
        return None
    if numeric == 2:
        return "dark"
    if numeric == 1:
        return "light"
    return None


def _scheme_from_palette(palette: QPalette | None) -> ColorSchemeName:
    if palette is None:
        return "dark"
    try:
        color = palette.color(_role("Window"))
        # Perceived luminance; below midpoint => dark theme.
        luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255.0
        return "dark" if luminance < 0.5 else "light"
    except Exception:
        return "dark"


def detect_color_scheme(app: QApplication | None = None) -> ColorSchemeName:
    """Detect the preferred color scheme from Qt style hints or palette."""
    application = app or QApplication.instance()
    if application is not None:
        style_hints = getattr(application, "styleHints", None)
        if callable(style_hints):
            hints = style_hints()
            color_scheme = getattr(hints, "colorScheme", None)
            if callable(color_scheme):
                detected = _scheme_from_qt_value(color_scheme())
                if detected is not None:
                    return detected
        return _scheme_from_palette(application.palette())
    return "dark"


def panel_colors(scheme: ColorSchemeName | None = None) -> PanelThemeColors:
    """Return semantic panel colors for the given or current scheme."""
    resolved = scheme or detect_color_scheme()
    return _LIGHT_PANEL if resolved == "light" else _DARK_PANEL


def apply_app_palette(app: QApplication, scheme: ColorSchemeName | None = None) -> ColorSchemeName:
    """Apply Fusion palette for the given or detected scheme."""
    resolved = scheme or detect_color_scheme(app)
    if hasattr(app, "setStyle"):
        app.setStyle("Fusion")
    app.setPalette(build_palette(resolved))
    return resolved


class UiThemeManager:
    """Owns app palette application and theme-change fan-out."""

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._listeners: list[Callable[[ColorSchemeName], None]] = []
        self._scheme: ColorSchemeName = apply_app_palette(app)
        self._connect_system_signals()

    @property
    def scheme(self) -> ColorSchemeName:
        return self._scheme

    def colors(self) -> PanelThemeColors:
        return panel_colors(self._scheme)

    def add_listener(self, callback: Callable[[ColorSchemeName], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[ColorSchemeName], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def refresh(self, scheme: ColorSchemeName | None = None) -> ColorSchemeName:
        resolved = apply_app_palette(self._app, scheme or detect_color_scheme(self._app))
        self._scheme = resolved
        self._notify(resolved)
        return resolved

    def _notify(self, scheme: ColorSchemeName) -> None:
        for callback in list(self._listeners):
            try:
                callback(scheme)
            except Exception:
                pass

    def _connect_system_signals(self) -> None:
        style_hints = getattr(self._app, "styleHints", None)
        if not callable(style_hints):
            return
        hints = style_hints()
        signal = getattr(hints, "colorSchemeChanged", None)
        if signal is None:
            return
        try:
            signal.connect(self._on_color_scheme_changed)
        except Exception:
            pass

    def _on_color_scheme_changed(self, *_args) -> None:
        self.refresh(detect_color_scheme(self._app))


def install_ui_theme(app: QApplication) -> UiThemeManager:
    """Install (or reuse) the process-wide theme manager."""
    global _THEME_MANAGER
    if _THEME_MANAGER is not None and _THEME_MANAGER._app is app:
        return _THEME_MANAGER
    _THEME_MANAGER = UiThemeManager(app)
    return _THEME_MANAGER


def get_ui_theme_manager() -> UiThemeManager | None:
    return _THEME_MANAGER


def current_panel_colors() -> PanelThemeColors:
    manager = get_ui_theme_manager()
    if manager is not None:
        return manager.colors()
    return panel_colors()


__all__ = [
    "ColorSchemeName",
    "PanelThemeColors",
    "UiThemeManager",
    "apply_app_palette",
    "build_palette",
    "current_panel_colors",
    "detect_color_scheme",
    "get_ui_theme_manager",
    "install_ui_theme",
    "panel_colors",
]
