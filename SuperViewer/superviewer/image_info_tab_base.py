# -*- coding: utf-8 -*-
"""Base classes for SuperViewer image information tabs."""
from __future__ import annotations

import os
from abc import ABCMeta, abstractmethod

from app_common.qt_theme import is_theme_change_event

from .qt_compat import QWidget
from .ui_theme import PanelThemeColors, current_panel_colors


class _ImageInfoTabPanelMeta(type(QWidget), ABCMeta):
    """Qt QWidget + ABC compatible metaclass."""


class ImageInfoTabPanel(QWidget, metaclass=_ImageInfoTabPanelMeta):
    """Base class for right-side image information tab panels."""

    tab_title = "信息"

    def __init__(self, parent=None) -> None:
        self._theme_ready = False
        self._theme_update_in_progress = False
        super().__init__(parent)
        self._current_photo_path = ""
        self.create_ui()
        self._theme_ready = True
        self.apply_theme()

    def current_photo_path(self) -> str:
        return self._current_photo_path

    def on_photo_selected(self, path: str):
        self._current_photo_path = os.path.normpath(path) if path else ""
        return self.refresh_ui()

    def refresh_current_photo(self):
        return self.refresh_ui()

    def apply_theme(self, colors: PanelThemeColors | None = None) -> None:
        """Restyle existing widgets without refreshing photo data."""
        _ = colors or current_panel_colors()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if not self._theme_ready or self._theme_update_in_progress:
            return
        if is_theme_change_event(event):
            self._theme_update_in_progress = True
            try:
                self.apply_theme()
            finally:
                self._theme_update_in_progress = False

    @abstractmethod
    def create_ui(self) -> None:
        """Create child widgets and layout."""

    @abstractmethod
    def refresh_ui(self):
        """Refresh the panel for ``current_photo_path``."""


__all__ = [
    "ImageInfoTabPanel",
]
