from pathlib import Path

from PIL import Image

from birdstamp.decoders.image_decoder import decode_image_for_preview


def test_decode_image_for_preview_respects_max_long_edge(tmp_path: Path) -> None:
    path = tmp_path / "large.jpg"
    Image.new("RGB", (6000, 4000), color=(128, 64, 32)).save(path, format="JPEG")

    image = decode_image_for_preview(path, max_long_edge=2048)

    assert max(image.size) <= 2048


def test_decode_image_for_preview_keeps_small_images(tmp_path: Path) -> None:
    path = tmp_path / "small.jpg"
    Image.new("RGB", (800, 600), color=(10, 20, 30)).save(path, format="JPEG")

    image = decode_image_for_preview(path, max_long_edge=2048)

    assert image.size == (800, 600)
