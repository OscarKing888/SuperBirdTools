# -*- coding: utf-8 -*-
"""Tab widget container for SuperViewer image information panels."""
from __future__ import annotations

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import QTabWidget


class ImageInfoTabWidget(QTabWidget):
    """Container that dispatches image-selection events to all info tabs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._panels: list[ImageInfoTabPanel] = []

    def add_info_panel(self, panel: ImageInfoTabPanel) -> None:
        self._panels.append(panel)
        self.addTab(panel, panel.tab_title)

    def panels(self) -> list[ImageInfoTabPanel]:
        return list(self._panels)

    def on_photo_selected(self, path: str) -> dict[str, object]:
        results: dict[str, object] = {}
        for panel in self._panels:
            results[panel.__class__.__name__] = panel.on_photo_selected(path)
        return results


__all__ = [
    "ImageInfoTabWidget",
]
