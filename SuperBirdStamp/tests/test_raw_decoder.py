import io
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from PIL import Image

from birdstamp.decoders import image_decoder


def test_decode_raw_auto_reports_clear_error_when_backends_unavailable(monkeypatch) -> None:
    def _rawpy_fail(_: Path):
        raise RuntimeError("rawpy backend missing")

    def _darktable_fail(_: Path):
        raise RuntimeError("darktable-cli not found")

    monkeypatch.setattr(image_decoder, "_decode_raw_rawpy", _rawpy_fail)
    monkeypatch.setattr(image_decoder, "_decode_raw_darktable", _darktable_fail)

    with pytest.raises(RuntimeError) as exc_info:
        image_decoder._decode_raw(Path("test.arw"), decoder="auto")

    message = str(exc_info.value)
    assert "No RAW decoder is available" in message
    assert "pip install rawpy" in message
    assert "darktable-cli" in message


def test_decode_raw_preview_prefers_embedded_jpeg(monkeypatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 800), "#336699").save(buffer, format="JPEG")
    monkeypatch.setattr(
        "app_common.thumb_stream.get_raw_preview_jpeg",
        lambda _path: buffer.getvalue(),
    )
    monkeypatch.setattr(
        image_decoder,
        "_decode_raw",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("RAW demosaic should not run")),
    )

    preview = image_decoder.decode_image_for_preview(Path("sample.arw"), max_long_edge=100)
    try:
        assert preview.size == (100, 50)
        assert preview.info["birdstamp_source_properties"]["size"] == (1600, 800)
    finally:
        preview.close()


def test_decode_raw_preview_uses_full_raw_fallback_for_small_embedded_image(monkeypatch) -> None:
    monkeypatch.setattr(image_decoder, "_decode_embedded_raw_preview", lambda *_args: None)
    monkeypatch.setattr(
        image_decoder,
        "_decode_raw",
        lambda *_args, **_kwargs: Image.new("RGB", (900, 600), "#123456"),
    )

    preview = image_decoder.decode_image_for_preview(Path("sample.nef"), max_long_edge=100)
    try:
        assert preview.size == (100, 66)
        assert preview.info["birdstamp_source_properties"]["size"] == (900, 600)
    finally:
        preview.close()


def test_auto_decode_and_source_size_use_embedded_pixels_but_raw_toggle_uses_demosaic(monkeypatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (1800, 1200), "#336699").save(buffer, format="JPEG")
    monkeypatch.setattr("app_common.thumb_stream.get_raw_preview_jpeg", lambda _path: buffer.getvalue())
    monkeypatch.setattr(image_decoder, "_decode_raw", lambda *_args, **_kwargs: Image.new("RGB", (2400, 1600), "red"))
    path = Path("sample.nef")
    assert image_decoder.read_decoded_image_size(path) == (1800, 1200)
    with image_decoder.decode_image(path) as exported:
        assert exported.size == (1800, 1200)
        assert exported.getpixel((0, 0))[2] > exported.getpixel((0, 0))[0]
    with image_decoder.decode_image_for_preview(path, max_long_edge=100, show_raw=True) as raw_preview:
        assert raw_preview.size == (100, 66)
        assert raw_preview.info["birdstamp_source_properties"]["size"] == (2400, 1600)
        assert raw_preview.getpixel((0, 0))[0] > raw_preview.getpixel((0, 0))[2]


def test_small_embedded_image_falls_back_for_export_and_size(monkeypatch) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 120), "blue").save(buffer, format="JPEG")
    monkeypatch.setattr("app_common.thumb_stream.get_raw_preview_jpeg", lambda _path: buffer.getvalue())
    monkeypatch.setattr(image_decoder, "_read_raw_exif_size", lambda _path: (2000, 1400))
    monkeypatch.setattr(image_decoder, "_decode_raw", lambda *_args, **_kwargs: Image.new("RGB", (2000, 1400), "red"))
    path = Path("sample.arw")
    assert image_decoder.read_decoded_image_size(path) == (2000, 1400)
    with image_decoder.decode_image(path) as exported:
        assert exported.size == (2000, 1400)
        assert exported.getpixel((0, 0)) == (255, 0, 0)


def test_read_decoded_raw_size_uses_rawpy_orientation(monkeypatch) -> None:
    class _FakeRaw:
        sizes = SimpleNamespace(width=6000, height=4000, iwidth=6000, iheight=4000, flip=6)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(image_decoder, "_read_raw_exif_size", lambda _path: None)
    monkeypatch.setitem(sys.modules, "rawpy", SimpleNamespace(imread=lambda _path: _FakeRaw()))

    assert image_decoder.read_decoded_image_size(Path("sample.cr3")) == (4000, 6000)


def test_read_decoded_raw_size_prefers_exif_without_full_decode(monkeypatch) -> None:
    monkeypatch.setattr(image_decoder, "_read_raw_exif_size", lambda _path: (4000, 6000))
    monkeypatch.setattr(
        image_decoder,
        "_decode_raw",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full RAW decode should not run")),
    )

    assert image_decoder.read_decoded_image_size(Path("sample.cr3")) == (4000, 6000)


def test_read_decoded_raw_size_never_uses_pillow_fallback(monkeypatch) -> None:
    pillow_called = False

    def fail_pillow_open(*_args, **_kwargs):
        nonlocal pillow_called
        pillow_called = True
        raise AssertionError("Pillow must not be used for RAW source dimensions")

    monkeypatch.setattr(image_decoder, "_read_raw_exif_size", lambda _path: None)
    monkeypatch.setitem(sys.modules, "rawpy", None)
    monkeypatch.setattr(image_decoder.Image, "open", fail_pillow_open)

    with pytest.raises(RuntimeError, match="EXIF or rawpy headers"):
        image_decoder.read_decoded_image_size(Path("sample.cr3"))

    assert pillow_called is False
