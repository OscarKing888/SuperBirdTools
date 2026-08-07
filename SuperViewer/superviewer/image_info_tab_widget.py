# -*- coding: utf-8 -*-
"""Tab widget container for SuperViewer image information panels."""
from __future__ import annotations

import time as _time

from app_common.log import get_logger
from app_common.perf_probe import perf_log

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import QEvent, QTabWidget
from .ui_theme import ColorSchemeName, get_ui_theme_manager, panel_colors


_log = get_logger("superviewer.image_info_tabs")


class ImageInfoTabWidget(QTabWidget):
    """Container that dispatches image-selection events to all info tabs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._panels: list[ImageInfoTabPanel] = []
        self._pending_panels: set[ImageInfoTabPanel] = set()
        self._shutdown_requested = False
        self._shutdown_complete = False
        self._theme_listener_attached = False
        self.currentChanged.connect(self._on_current_tab_changed)
        self._attach_theme_listener()

    def add_info_panel(self, panel: ImageInfoTabPanel) -> None:
        self._attach_theme_listener()
        self._panels.append(panel)
        self.addTab(panel, panel.tab_title)
        panel.apply_theme(panel_colors())

    def panels(self) -> list[ImageInfoTabPanel]:
        return list(self._panels)

    def apply_theme(self, scheme: ColorSchemeName | None = None) -> None:
        """Broadcast theme colors to all panels without metadata I/O."""
        colors = panel_colors(scheme)
        for panel in self._panels:
            try:
                panel.apply_theme(colors)
            except Exception:
                pass

    def on_photo_selected(self, path: str) -> dict[str, object]:
        if self._shutdown_requested:
            return {}
        total_t0 = _time.perf_counter()
        perf_log(_log, "[PERF][image_switch][ImageInfoTabWidget] START path=%r panels=%s", path, len(self._panels))
        results: dict[str, object] = {}
        active_panel = self.currentWidget()
        for panel in self._panels:
            if panel is not active_panel:
                # 非活动页只同步路径；切换到该页时再读取文件或 sidecar。
                panel.set_current_photo_path(path)
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

    def _attach_theme_listener(self) -> None:
        manager = get_ui_theme_manager()
        if manager is None or self._theme_listener_attached:
            return
        manager.add_listener(self.apply_theme)
        self._theme_listener_attached = True

    def _detach_theme_listener(self) -> None:
        manager = get_ui_theme_manager()
        if manager is None or not self._theme_listener_attached:
            return
        manager.remove_listener(self.apply_theme)
        self._theme_listener_attached = False

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event is None:
            return
        # Ensure listener is attached once the app theme manager exists.
        self._attach_theme_listener()
        event_type = event.type()
        theme_change = getattr(QEvent, "ThemeChange", None)
        palette_change = getattr(QEvent, "PaletteChange", None)
        type_value = getattr(event_type, "value", event_type)
        watched = {
            getattr(theme_change, "value", theme_change) if theme_change is not None else None,
            getattr(palette_change, "value", palette_change) if palette_change is not None else None,
            214,
            39,
        }
        if type_value in watched or event_type in watched:
            self.apply_theme()

    def request_shutdown(self) -> None:
        """Ask child panels to stop without waiting on the GUI thread."""
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
        """Boundedly finish child shutdown; return whether every panel stopped."""
        if self._shutdown_complete:
            return True
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
        if complete:
            self._shutdown_complete = True
        return complete

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)


__all__ = [
    "ImageInfoTabWidget",
]
