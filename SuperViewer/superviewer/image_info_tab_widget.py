# -*- coding: utf-8 -*-
"""Tab widget container for SuperViewer image information panels."""
from __future__ import annotations

import os
import time as _time

from app_common.log import get_logger
from app_common.perf_probe import perf_log
from app_common.qt_theme import is_theme_change_event

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import QTabWidget
from .ui_theme import ColorSchemeName, get_ui_theme_manager, panel_theme_colors


_log = get_logger("superviewer.image_info_tabs")


class ImageInfoTabWidget(QTabWidget):
    """Container that dispatches image-selection events to all info tabs."""

    def __init__(self, parent=None) -> None:
        self._theme_ready = False
        self._theme_broadcast_in_progress = False
        self._theme_manager = None
        super().__init__(parent)
        self._panels: list[ImageInfoTabPanel] = []
        self._pending_panels: set[ImageInfoTabPanel] = set()
        self._shutdown_requested = False
        self.currentChanged.connect(self._on_current_tab_changed)
        self._theme_ready = True
        self._attach_theme_listener()

    def add_info_panel(self, panel: ImageInfoTabPanel) -> None:
        self._attach_theme_listener()
        self._panels.append(panel)
        self.addTab(panel, panel.tab_title)
        panel.apply_theme(panel_theme_colors())

    def panels(self) -> list[ImageInfoTabPanel]:
        return list(self._panels)

    def apply_theme(self, scheme: ColorSchemeName | None = None) -> None:
        """Restyle all tabs without consuming their pending metadata refresh."""
        if self._shutdown_requested or self._theme_broadcast_in_progress:
            return
        self._attach_theme_listener()
        colors = panel_theme_colors(scheme)
        self._theme_broadcast_in_progress = True
        try:
            for panel in self._panels:
                try:
                    panel.apply_theme(colors)
                except Exception:
                    _log.exception("Theme update failed for info panel %s", type(panel).__name__)
        finally:
            self._theme_broadcast_in_progress = False

    def _attach_theme_listener(self) -> None:
        if self._shutdown_requested:
            return
        manager = get_ui_theme_manager()
        if manager is self._theme_manager:
            return
        self._detach_theme_listener()
        if manager is not None and not manager.closed:
            manager.add_listener(self.apply_theme)
            self._theme_manager = manager

    def _detach_theme_listener(self) -> None:
        manager = self._theme_manager
        self._theme_manager = None
        if manager is not None:
            manager.remove_listener(self.apply_theme)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if self._theme_ready and is_theme_change_event(event):
            self.apply_theme()

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

    def request_shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._pending_panels.clear()
        self._detach_theme_listener()
        for panel in self._panels:
            request_shutdown = getattr(panel, "request_shutdown", None)
            if not callable(request_shutdown):
                continue
            try:
                request_shutdown()
            except Exception:
                pass

    def shutdown(self, *, wait_timeout_ms: int | None = None) -> bool:
        self.request_shutdown()
        complete = True
        for panel in self._panels:
            shutdown = getattr(panel, "shutdown", None)
            if not callable(shutdown):
                continue
            try:
                if wait_timeout_ms is None:
                    result = shutdown()
                else:
                    try:
                        result = shutdown(wait_timeout_ms=wait_timeout_ms)
                    except TypeError:
                        result = shutdown()
                if result is False:
                    complete = False
            except Exception:
                complete = False
        return complete

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)


__all__ = [
    "ImageInfoTabWidget",
]
