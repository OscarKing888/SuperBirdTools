# -*- coding: utf-8 -*-
"""Base classes for SuperViewer image information tabs."""
from __future__ import annotations

import os
from abc import ABCMeta, abstractmethod

from .qt_compat import QWidget


class _ImageInfoTabPanelMeta(type(QWidget), ABCMeta):
    """Qt QWidget + ABC compatible metaclass."""


class ImageInfoTabPanel(QWidget, metaclass=_ImageInfoTabPanelMeta):
    """Base class for right-side image information tab panels."""

    tab_title = "信息"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_photo_path = ""
        self.create_ui()

    def current_photo_path(self) -> str:
        return self._current_photo_path

    def on_photo_selected(self, path: str):
        self._current_photo_path = os.path.normpath(path) if path else ""
        return self.refresh_ui()

    def refresh_current_photo(self):
        return self.refresh_ui()

    @abstractmethod
    def create_ui(self) -> None:
        """Create child widgets and layout."""

    @abstractmethod
    def refresh_ui(self):
        """Refresh the panel for ``current_photo_path``."""


__all__ = [
    "ImageInfoTabPanel",
]
