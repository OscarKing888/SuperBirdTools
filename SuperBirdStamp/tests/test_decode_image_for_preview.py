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


def test_large_tiff_is_reduced_before_orientation_and_color_copy(tmp_path, monkeypatch):
    from birdstamp.decoders import image_decoder
    path = tmp_path / "large.tif"
    Image.new("RGBA", (2400, 1600), (100, 120, 140, 255)).save(path)
    original_transpose = image_decoder.ImageOps.exif_transpose
    def transpose(image, **kwargs):
        if not kwargs.get("in_place"):
            assert max(image.size) <= 256
        return original_transpose(image, **kwargs)
    monkeypatch.setattr(image_decoder.ImageOps, "exif_transpose", transpose)
    image = decode_image_for_preview(path, max_long_edge=256)
    try:
        assert image.mode == "RGB"
        assert image.info["birdstamp_source_properties"] == {
            "size": (2400, 1600), "width": 2400, "height": 1600, "has_alpha": True,
        }
    finally:
        image.close()


def test_preview_retains_oriented_source_dimensions(tmp_path):
    path = tmp_path / "portrait.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (600, 400), "red").save(path, exif=exif)
    image = decode_image_for_preview(path, max_long_edge=120)
    try:
        assert image.size == (80, 120)
        assert image.info["birdstamp_source_properties"]["size"] == (400, 600)
    finally:
        image.close()


def test_rotated_tiff_preview_and_header_size_match(tmp_path):
    from birdstamp.decoders.image_decoder import read_decoded_image_size
    for orientation in (5, 6, 7, 8):
        for compression in ("raw", "tiff_lzw"):
            path = tmp_path / f"portrait_{orientation}_{compression}.tif"
            exif = Image.Exif()
            exif[274] = orientation
            Image.new("RGB", (600, 400), "red").save(path, exif=exif, compression=compression)
            image = decode_image_for_preview(path, max_long_edge=120)
            try:
                assert image.size == (80, 120)
                assert read_decoded_image_size(path) == (400, 600)
                assert image.info["birdstamp_source_properties"]["size"] == (400, 600)
            finally:
                image.close()
