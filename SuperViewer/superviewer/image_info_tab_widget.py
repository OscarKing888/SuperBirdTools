# -*- coding: utf-8 -*-
"""Tab widget container for SuperViewer image information panels."""
from __future__ import annotations

import time as _time

from app_common.log import get_logger

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import QTabWidget


_log = get_logger("superviewer.image_info_tabs")


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
        total_t0 = _time.perf_counter()
        _log.info("[PERF][image_switch][ImageInfoTabWidget] START path=%r panels=%s", path, len(self._panels))
        results: dict[str, object] = {}
        for panel in self._panels:
            panel_t0 = _time.perf_counter()
            results[panel.__class__.__name__] = panel.on_photo_selected(path)
            _log.info(
                "[PERF][image_switch][ImageInfoTabWidget] panel=%s path=%r elapsed_ms=%.1f",
                panel.__class__.__name__,
                path,
                (_time.perf_counter() - panel_t0) * 1000.0,
            )
        _log.info(
            "[PERF][image_switch][ImageInfoTabWidget] END path=%r total_ms=%.1f",
            path,
            (_time.perf_counter() - total_t0) * 1000.0,
        )
        return results


__all__ = [
    "ImageInfoTabWidget",
]
