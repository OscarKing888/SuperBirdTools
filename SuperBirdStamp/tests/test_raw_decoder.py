from pathlib import Path

import pytest

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

