import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from app_common.file_browser._browser_core import _persistent_thumb_cache_path_for_file
from SuperViewer.superviewer.preview_panel import PreviewPanel
from SuperViewer.superviewer.qt_compat import QApplication, QColor, QPixmap
from SuperViewer.main import MainWindow
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


class _FastResolverProbe:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[tuple[str, bool]] = []

    def _resolve_existing_sized_preview_image_path(
        self,
        path: str,
        *,
        exact_size_only: bool = False,
    ) -> str:
        self.calls.append((path, bool(exact_size_only)))
        return self.result


class _SignalProbe:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def emit(self, *args) -> None:
        self.calls.append(tuple(args))


class _UncachedPlaybackProbe:
    enable_in_memory_fast_preview = True
    skip_uncached_fast_preview = True

    def __init__(self) -> None:
        self.file_fast_preview_requested = _SignalProbe()
        self.file_fast_preview_pixmap_requested = _SignalProbe()
        self.prioritized: list[str] = []
        self._selected_display_path = ""
        self._thumb_size = 2048

    def _resolve_source_path_for_action(self, path: str, *, log_resolution: bool = True) -> str:
        return path

    def resolve_preview_path(self, path: str, prefer_fast_preview: bool = False) -> str:
        return path

    def _current_thumbnail_fast_preview_pixmap(self, _path: str):
        return None

    def _prioritize_fast_preview_thumbnail(self, path: str) -> None:
        self.prioritized.append(path)

    def _request_actual_path_lookup(self, _path: str) -> None:
        raise AssertionError("an existing source must not start path lookup")

    def _materialize_current_thumbnail_fast_preview(self, _path: str) -> str:
        raise AssertionError("uncached playback must not synchronously decode/materialize the source")


class _MemoryFirstPlaybackProbe(_UncachedPlaybackProbe):
    def __init__(self, preview_path: str, pixmap: QPixmap) -> None:
        super().__init__()
        self._thumb_size = 128
        self.preview_path = preview_path
        self.pixmap = pixmap

    def resolve_preview_path(self, _path: str, prefer_fast_preview: bool = False) -> str:
        return self.preview_path

    def _current_thumbnail_fast_preview_pixmap(self, _path: str):
        return self.pixmap


def test_superviewer_fast_resolver_requires_exact_selected_tier() -> None:
    panel = _FastResolverProbe("selected-tier.jpg")

    resolved = SuperViewerTaggedFileListPanel.resolve_preview_path(
        panel,
        "folder/photo.ARW",
        prefer_fast_preview=True,
    )

    assert resolved == "selected-tier.jpg"
    assert panel.calls == [(os.path.normpath("folder/photo.ARW"), True)]


def test_superviewer_normal_resolver_keeps_source_path() -> None:
    panel = _FastResolverProbe("must-not-be-used.jpg")

    resolved = SuperViewerTaggedFileListPanel.resolve_preview_path(
        panel,
        "folder/photo.ARW",
        prefer_fast_preview=False,
    )

    assert resolved == os.path.normpath("folder/photo.ARW")
    assert panel.calls == []


def test_superviewer_resolves_each_exact_persistent_tier(tmp_path: Path) -> None:
    _app = QApplication.instance() or QApplication([])
    image_dir = tmp_path / "images"
    (image_dir / ".superpicky").mkdir(parents=True)
    source = image_dir / "photo.jpg"
    Image.new("RGB", (2400, 1600), (20, 40, 60)).save(source, "JPEG")
    panel = SuperViewerTaggedFileListPanel()
    panel._current_dir = str(image_dir)
    try:
        for tier in (128, 256, 512, 1024, 2048):
            cache_path = Path(
                _persistent_thumb_cache_path_for_file(
                    str(source),
                    str(image_dir),
                    tier,
                    selected_dir=str(image_dir),
                )
            )
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (tier, max(1, tier * 2 // 3)), (40, 60, 80)).save(
                cache_path,
                "JPEG",
            )
            future = time.time() + 1.0
            os.utime(cache_path, (future, future))
            panel._thumb_size = tier
            assert panel.resolve_preview_path(str(source), True) == str(cache_path)
    finally:
        panel.close()


def test_superviewer_explicitly_opts_into_fast_playback() -> None:
    assert SuperViewerTaggedFileListPanel.enable_key_navigation_playback is True
    assert SuperViewerTaggedFileListPanel.enable_in_memory_fast_preview is True
    assert SuperViewerTaggedFileListPanel.skip_uncached_fast_preview is True


def test_uncached_raw_playback_prioritizes_background_cache_without_sync_fallback() -> None:
    probe = _UncachedPlaybackProbe()
    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / "uncached.ARW"
        source.write_bytes(b"raw-placeholder")

        SuperViewerTaggedFileListPanel._emit_fast_preview_for_path(probe, str(source))

        assert probe.prioritized == [str(source)]
        assert probe.file_fast_preview_requested.calls == []
        assert probe.file_fast_preview_pixmap_requested.calls == []


def test_decoded_exact_tier_pixmap_wins_over_disk_cache() -> None:
    _app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(128, 96)
    pixmap.fill(QColor(10, 20, 30))
    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / "source.ARW"
        cached = Path(temp_dir) / "cached-128.jpg"
        source.write_bytes(b"raw-placeholder")
        cached.write_bytes(b"jpeg-placeholder")
        probe = _MemoryFirstPlaybackProbe(str(cached), pixmap)

        SuperViewerTaggedFileListPanel._emit_fast_preview_for_path(probe, str(source))

        assert probe.file_fast_preview_requested.calls == []
        assert len(probe.file_fast_preview_pixmap_requested.calls) == 1
        emitted_path, emitted_pixmap, emitted_size = probe.file_fast_preview_pixmap_requested.calls[0]
        assert emitted_path == str(source)
        assert emitted_pixmap is pixmap
        assert emitted_size == 128


def test_fast_focus_clear_returns_before_source_resolution() -> None:
    calls: list[tuple[str, object]] = []

    class _Preview:
        def set_focus_box(self, value) -> None:
            calls.append(("clear", value))

    class _WindowProbe:
        preview_panel = _Preview()

        def _stop_focus_loader(self) -> None:
            calls.append(("stop", None))

        def _resolve_focus_metadata_source_path(self, _path: str) -> str:
            raise AssertionError("fast preview must not scan for a focus metadata source")

    MainWindow._update_preview_focus_box(
        _WindowProbe(),
        "cache/hashed-thumb.jpg",
        allow_async_load=False,
    )

    assert calls == [("stop", None), ("clear", None)]


def test_in_memory_fast_frame_keeps_composition_grid_in_export() -> None:
    _app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(120, 90)
    pixmap.fill(QColor(0, 0, 0))
    preview = PreviewPanel()
    preview.set_composition_grid_mode("thirds")

    preview.set_quick_pixmap("source.jpg", pixmap, quick_size=128)
    rendered = preview.canvas.render_source_pixmap_with_overlays()

    assert rendered is not None and not rendered.isNull()
    grid_pixel = rendered.toImage().pixelColor(40, 45)
    assert grid_pixel.red() > 0 or grid_pixel.green() > 0 or grid_pixel.blue() > 0
    preview.shutdown()
    preview.close()
