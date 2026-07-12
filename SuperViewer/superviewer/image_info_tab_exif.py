# -*- coding: utf-8 -*-
"""EXIF image information tab for SuperViewer."""
from __future__ import annotations

import os
from typing import Callable

from .image_info_tab_base import ImageInfoTabPanel
from .qt_compat import (
    QCheckBox,
    QHBoxLayout,
    QPushButton,
    QThread,
    QVBoxLayout,
    pyqtSignal,
)


class _ExifRowsLoader(QThread):
    loaded = pyqtSignal(int, str, object)

    def __init__(
        self,
        request_token: int,
        path: str,
        tag_label_chinese: bool,
        rows_loader: Callable[[str, bool], list[tuple]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._request_token = int(request_token)
        self._path = os.path.normpath(path) if path else ""
        self._tag_label_chinese = bool(tag_label_chinese)
        self._rows_loader = rows_loader

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        try:
            rows = list(self._rows_loader(self._path, self._tag_label_chinese) or [])
        except Exception:
            rows = []
        if self.isInterruptionRequested():
            return
        self.loaded.emit(self._request_token, self._path, rows)


class ImageInfoTabPanel_EXIF(ImageInfoTabPanel):
    """EXIF metadata tab."""

    tab_title = "EXIF"

    def __init__(
        self,
        metadata_rows_loader: Callable[[str, bool], list[tuple]],
        save_callback: Callable[[str, object, str, object, object], None],
        parent=None,
    ) -> None:
        self._metadata_rows_loader = metadata_rows_loader
        self._save_callback = save_callback
        self._last_rows: list[tuple] = []
        self._request_sequence = 0
        self._display_request_token = 0
        self._loader: _ExifRowsLoader | None = None
        self._pending_request: tuple[int, str, bool] | None = None
        self._shutdown_requested = False
        super().__init__(parent)

    def create_ui(self) -> None:
        from .exif_helpers import load_tag_label_chinese_from_settings
        from .exif_table import ExifTable
        from .qt_compat import QLineEdit

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.exif_filter = QLineEdit()
        self.exif_filter.setPlaceholderText("按分组、标签或值过滤…")
        self.exif_filter.setClearButtonEnabled(True)
        self.exif_filter.setStyleSheet("QLineEdit { padding: 6px; font-size: 13px; }")
        self.exif_filter.textChanged.connect(self._on_exif_filter_changed)
        top_row.addWidget(self.exif_filter, stretch=1)

        self.check_tag_chinese = QCheckBox("中文标签")
        self.check_tag_chinese.setChecked(load_tag_label_chinese_from_settings())
        self.check_tag_chinese.setToolTip("勾选显示汉字标签名，否则显示英文")
        self.check_tag_chinese.toggled.connect(self._on_tag_label_lang_toggled)
        top_row.addWidget(self.check_tag_chinese)

        self.btn_config_order = QPushButton("配置显示顺序")
        self.btn_config_order.setToolTip("设置优先显示的 EXIF 标签及顺序")
        self.btn_config_order.clicked.connect(self._open_tag_order_config)
        top_row.addWidget(self.btn_config_order)
        layout.addLayout(top_row)

        self.exif_table = ExifTable(self)
        self.exif_table.set_save_callback(self._save_callback)
        layout.addWidget(self.exif_table, stretch=1)

    def refresh_ui(self) -> list[tuple]:
        from .exif_helpers import load_tag_label_chinese_from_settings

        path = self.current_photo_path()
        if not path or not os.path.isfile(path):
            self._invalidate_requests()
            self._last_rows = []
            self.exif_table.set_exif([])
            return []
        self._request_sequence += 1
        request_token = self._request_sequence
        self._display_request_token = request_token
        request = (
            request_token,
            os.path.normpath(path),
            bool(load_tag_label_chinese_from_settings()),
        )
        self._last_rows = []
        self.exif_table.set_exif([])
        loader = self._loader
        if loader is not None and loader.isRunning():
            loader.requestInterruption()
            self._pending_request = request
            return []
        self._pending_request = None
        self._launch_request(*request)
        return []

    def _launch_request(self, request_token: int, path: str, tag_label_chinese: bool) -> None:
        if self._shutdown_requested:
            return
        if int(request_token) != int(self._display_request_token):
            return
        if not path or os.path.normcase(path) != os.path.normcase(self.current_photo_path()):
            return
        loader = _ExifRowsLoader(
            request_token,
            path,
            tag_label_chinese,
            self._metadata_rows_loader,
            self,
        )
        loader.loaded.connect(self._on_rows_loaded)
        loader.finished.connect(lambda l=loader: self._on_loader_finished(l))
        self._loader = loader
        loader.start()

    def _on_rows_loaded(self, request_token: int, path: str, rows) -> None:
        if int(request_token) != int(self._display_request_token):
            return
        current_path = os.path.normpath(self.current_photo_path()) if self.current_photo_path() else ""
        if not current_path or os.path.normcase(path) != os.path.normcase(current_path):
            return
        self._last_rows = list(rows or [])
        self.exif_table.set_exif(self._last_rows)

    def _on_loader_finished(self, loader: _ExifRowsLoader) -> None:
        if self._loader is loader:
            self._loader = None
        try:
            loader.deleteLater()
        except Exception:
            pass
        if self._shutdown_requested:
            self._pending_request = None
            return
        pending = self._pending_request
        self._pending_request = None
        if pending is not None:
            self._launch_request(*pending)

    def _invalidate_requests(self) -> None:
        self._request_sequence += 1
        self._display_request_token = self._request_sequence
        self._pending_request = None
        loader = self._loader
        if loader is not None and loader.isRunning():
            loader.requestInterruption()

    def shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._invalidate_requests()
        loader = self._loader
        if loader is None:
            return
        try:
            loader.requestInterruption()
            loader.wait()
        except Exception:
            pass
        if self._loader is loader:
            self._loader = None
        try:
            loader.deleteLater()
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def last_rows(self) -> list[tuple]:
        return list(self._last_rows)

    def _on_exif_filter_changed(self, text: str) -> None:
        self.exif_table.set_filter_text(text)

    def _on_tag_label_lang_toggled(self, checked: bool) -> None:
        from .exif_helpers import save_tag_label_chinese_to_settings

        save_tag_label_chinese_to_settings(checked)
        # 如果当前没有选中照片，则只更新后续加载的显示语言。
        if not self.current_photo_path():
            return
        self.refresh_current_photo()

    def _open_tag_order_config(self) -> None:
        from .exif_helpers import load_tag_label_chinese_from_settings
        from .exif_tag_order_dialog import ExifTagOrderDialog

        use_chinese = load_tag_label_chinese_from_settings()
        dialog = ExifTagOrderDialog(self, use_chinese=use_chinese)
        if dialog.exec():
            self.refresh_current_photo()


__all__ = [
    "ImageInfoTabPanel_EXIF",
]
