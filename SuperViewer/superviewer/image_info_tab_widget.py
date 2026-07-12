# -*- coding: utf-8 -*-
"""Tab widget container for SuperViewer image information panels."""
from __future__ import annotations

import os
import time as _time

from app_common.log import get_logger
from app_common.perf_probe import perf_log

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import QTabWidget


_log = get_logger("superviewer.image_info_tabs")


class ImageInfoTabWidget(QTabWidget):
    """Container that dispatches image-selection events to all info tabs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._panels: list[ImageInfoTabPanel] = []
        self._pending_panels: set[ImageInfoTabPanel] = set()
        self._shutdown_requested = False
        self.currentChanged.connect(self._on_current_tab_changed)

    def add_info_panel(self, panel: ImageInfoTabPanel) -> None:
        self._panels.append(panel)
        self.addTab(panel, panel.tab_title)

    def panels(self) -> list[ImageInfoTabPanel]:
        return list(self._panels)

    def on_photo_selected(self, path: str) -> dict[str, object]:
        total_t0 = _time.perf_counter()
        perf_log(_log, "[PERF][image_switch][ImageInfoTabWidget] START path=%r panels=%s", path, len(self._panels))
        results: dict[str, object] = {}
        active_panel = self.currentWidget()
        norm_path = os.path.normpath(path) if path else ""
        for panel in self._panels:
            if panel is not active_panel:
                # Keep inactive tabs logically in sync without performing any
                # file I/O.  They refresh lazily when the user opens the tab.
                panel._current_photo_path = norm_path
                self._pending_panels.add(panel)
                continue
            panel_t0 = _time.perf_counter()
            results[panel.__class__.__name__] = panel.on_photo_selected(path)
            self._pending_panels.discard(panel)
            perf_log(
                _log,
                "[PERF][image_switch][ImageInfoTabWidget] panel=%s path=%r elapsed_ms=%.1f",
                panel.__class__.__name__,
                path,
                (_time.perf_counter() - panel_t0) * 1000.0,
            )
        perf_log(
            _log,
            "[PERF][image_switch][ImageInfoTabWidget] END path=%r total_ms=%.1f",
            path,
            (_time.perf_counter() - total_t0) * 1000.0,
        )
        return results

    def _on_current_tab_changed(self, index: int) -> None:
        if self._shutdown_requested or index < 0:
            return
        panel = self.widget(index)
        if not isinstance(panel, ImageInfoTabPanel) or panel not in self._pending_panels:
            return
        self._pending_panels.discard(panel)
        panel.refresh_current_photo()

    def shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._pending_panels.clear()
        for panel in self._panels:
            shutdown = getattr(panel, "shutdown", None)
            if not callable(shutdown):
                continue
            try:
                shutdown()
            except Exception:
                pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)


__all__ = [
    "ImageInfoTabWidget",
]
