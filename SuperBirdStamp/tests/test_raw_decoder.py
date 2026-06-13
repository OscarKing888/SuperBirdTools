from pathlib import Path
from io import BytesIO

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


def test_decode_preview_raw_prefers_embedded_jpeg(monkeypatch, tmp_path: Path) -> None:
    raw_path = tmp_path / "sample.ARW"
    raw_path.write_bytes(b"raw")
    embedded = Image.new("RGB", (12, 8), "#336699")
    payload = BytesIO()
    embedded.save(payload, format="JPEG")

    def _fail_full_decode(_path: Path, decoder: str = "auto"):
        raise AssertionError("preview RAW decode should use embedded JPEG before full RAW decode")

    monkeypatch.setattr(image_decoder, "get_raw_preview_jpeg", lambda _path: payload.getvalue())
    monkeypatch.setattr(image_decoder, "_decode_raw", _fail_full_decode)
    monkeypatch.setattr(image_decoder, "_read_orientation_from_file", lambda _path: 1)

    image, source = image_decoder.decode_preview_image_with_source(raw_path)

    assert source == "raw_embedded_preview"
    assert image.mode == "RGB"
    assert image.size == (12, 8)
