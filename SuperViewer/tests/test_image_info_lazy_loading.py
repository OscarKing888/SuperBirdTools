from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image

from SuperViewer.superviewer.image_info_tab_base import ImageInfoTabPanel
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
from SuperViewer.superviewer.image_info_tab_widget import ImageInfoTabWidget
from SuperViewer.superviewer.qt_compat import QApplication, QPixmap


class _CountingPanel(ImageInfoTabPanel):
    tab_title = "probe"

    def __init__(self) -> None:
        self.refresh_count = 0
        self.request_shutdown_count = 0
        self.shutdown_count = 0
        super().__init__()

    def create_ui(self) -> None:
        return

    def refresh_ui(self):
        self.refresh_count += 1
        return self.current_photo_path()

    def request_shutdown(self) -> None:
        self.request_shutdown_count += 1

    def shutdown(self, *, wait_timeout_ms=None) -> bool:
        self.shutdown_count += 1
        return True


def test_image_info_tabs_refresh_only_active_panel() -> None:
    app = QApplication.instance() or QApplication([])
    tabs = ImageInfoTabWidget()
    first = _CountingPanel()
    second = _CountingPanel()
    try:
        tabs.add_info_panel(first)
        tabs.add_info_panel(second)
        tabs.setCurrentIndex(0)

        result = tabs.on_photo_selected("first.jpg")
        assert list(result) == ["_CountingPanel"]
        assert first.refresh_count == 1
        assert second.refresh_count == 0
        assert second.current_photo_path().endswith("first.jpg")

        tabs.setCurrentIndex(1)
        app.processEvents()
        assert first.refresh_count == 1
        assert second.refresh_count == 1

        tabs.on_photo_selected("second.jpg")
        assert first.refresh_count == 1
        assert second.refresh_count == 2
        tabs.setCurrentIndex(0)
        app.processEvents()
        assert first.refresh_count == 2

        tabs.request_shutdown()
        assert tabs.shutdown(wait_timeout_ms=1)
        assert first.request_shutdown_count == 1
        assert second.request_shutdown_count == 1
        assert first.shutdown_count == 1
        assert second.shutdown_count == 1
    finally:
        tabs.close()
        first.close()
        second.close()


def test_image_info_preview_is_provider_only_and_size_ignores_quick_pixmap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "header.jpg"
    Image.new("RGB", (321, 123), (20, 30, 40)).save(image_path, "JPEG")
    psd_path = tmp_path / "header.psd"
    psd_path.write_bytes(
        struct.pack(
            ">4sH6sHIIHHIIIH",
            b"8BPS",
            1,
            b"\x00" * 6,
            3,
            45,
            123,
            16,
            3,
            0,
            0,
            0,
            0,
        )
    )
    quick_pixmap = QPixmap(64, 48)
    quick_pixmap.fill()
    provider_calls: list[str] = []

    def pixmap_provider(path: str):
        provider_calls.append(path)
        return quick_pixmap

    panel = ImageInfoTabPanel_ImageInfo(
        lambda: [],
        lambda path: set(),
        lambda paths, tag, enabled: None,
        lambda path, name: path,
        metadata_provider=lambda path: {},
        preview_pixmap_provider=pixmap_provider,
    )
    try:
        panel._load_preview(str(image_path))
        assert provider_calls == [str(image_path)]
        assert panel._preview_pixmap is quick_pixmap
        assert panel._image_size(str(image_path), metadata={}) == (321, 123)
        assert panel._image_size(str(psd_path), metadata={}) == (123, 45)
        assert panel._image_size(
            "missing.jpg",
            metadata={"EXIF:ExifImageWidth": "5616", "EXIF:ExifImageLength": 3744},
        ) == (5616, 3744)

        import SuperViewer.superviewer.image_info_tab_image_info as image_info_module

        pixmap_args: list[tuple] = []
        original_qpixmap = image_info_module.QPixmap

        def recording_qpixmap(*args):
            pixmap_args.append(args)
            return original_qpixmap(*args)

        monkeypatch.setattr(image_info_module, "QPixmap", recording_qpixmap)
        panel._preview_pixmap_provider = lambda path: None
        panel._load_preview(str(image_path))
        assert all(not args for args in pixmap_args)
    finally:
        panel.close()
        app.processEvents()
