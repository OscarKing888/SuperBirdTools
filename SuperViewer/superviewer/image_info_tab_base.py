# -*- coding: utf-8 -*-
"""Base classes for SuperViewer image information tabs."""
from __future__ import annotations

import os
from abc import ABCMeta, abstractmethod

from .qt_compat import QEvent, QWidget
from .ui_theme import PanelThemeColors, current_panel_colors


class _ImageInfoTabPanelMeta(type(QWidget), ABCMeta):
    """Qt QWidget + ABC compatible metaclass."""


class ImageInfoTabPanel(QWidget, metaclass=_ImageInfoTabPanelMeta):
    """Base class for right-side image information tab panels."""

    tab_title = "信息"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_photo_path = ""
        self.create_ui()
        self.apply_theme()

    def current_photo_path(self) -> str:
        return self._current_photo_path

    def set_current_photo_path(self, path: str) -> str:
        """Update the selected path without refreshing the panel.

        Inactive tabs use this helper to stay logically in sync while
        deferring their file I/O until the user opens them.
        """
        self._current_photo_path = os.path.normpath(path) if path else ""
        return self._current_photo_path

    def on_photo_selected(self, path: str):
        self.set_current_photo_path(path)
        return self.refresh_ui()

    def refresh_current_photo(self):
        return self.refresh_ui()

    def apply_theme(self, colors: PanelThemeColors | None = None) -> None:
        """Refresh stylesheet colors without reloading photo metadata."""
        _ = colors or current_panel_colors()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event is None:
            return
        event_type = event.type()
        theme_change = getattr(QEvent, "ThemeChange", None)
        palette_change = getattr(QEvent, "PaletteChange", None)
        type_value = getattr(event_type, "value", event_type)
        watched = {
            getattr(theme_change, "value", theme_change) if theme_change is not None else None,
            getattr(palette_change, "value", palette_change) if palette_change is not None else None,
            # Fallback numeric values used by Qt6 when enum aliases differ.
            214,  # QEvent.Type.ThemeChange
            39,   # QEvent.Type.PaletteChange
        }
        if type_value in watched or event_type in watched:
            self.apply_theme()

    @abstractmethod
    def create_ui(self) -> None:
        """Create child widgets and layout."""

    @abstractmethod
    def refresh_ui(self):
        """Refresh the panel for ``current_photo_path``."""


__all__ = [
    "ImageInfoTabPanel",
]
