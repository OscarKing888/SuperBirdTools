from __future__ import annotations

import struct
from pathlib import Path

from birdstamp.constants import PIL_EXTENSIONS, RAW_EXTENSIONS, SUPPORTED_EXTENSIONS
from birdstamp.decoders.image_decoder import decode_image


def _write_minimal_rgb_psd(path: Path) -> None:
    width = 2
    height = 2
    channels = 3
    pixels = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 255),
    ]
    channel_bytes = b"".join(
        bytes(pixel[channel] for pixel in pixels)
        for channel in range(channels)
    )
    path.write_bytes(
        b"".join(
            [
                b"8BPS",
                struct.pack(">H", 1),
                b"\x00" * 6,
                struct.pack(">HIIHH", channels, height, width, 8, 3),
                struct.pack(">I", 0),
                struct.pack(">I", 0),
                struct.pack(">I", 0),
                struct.pack(">H", 0),
                channel_bytes,
            ]
        )
    )


def test_decode_psd_uses_pillow_path(tmp_path: Path) -> None:
    psd_path = tmp_path / "minimal.psd"
    _write_minimal_rgb_psd(psd_path)

    image = decode_image(psd_path)

    assert ".psd" in SUPPORTED_EXTENSIONS
    assert ".psd" in PIL_EXTENSIONS
    assert ".psd" not in RAW_EXTENSIONS
    assert image.mode == "RGB"
    assert image.size == (2, 2)
    assert image.getpixel((0, 0)) == (255, 0, 0)
