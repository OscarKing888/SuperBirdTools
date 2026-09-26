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


def test_embedded_raw_preview_uses_draft_and_keeps_fit_size_and_orientation(monkeypatch, tmp_path):
    import io as _io

    from PIL import Image as _Image

    from birdstamp.decoders import image_decoder as _decoder

    embedded = _Image.new("RGB", (5616, 3744), (200, 30, 30))
    embedded.paste((30, 30, 200), (2808, 0, 5616, 3744))
    exif = _Image.Exif()
    exif[0x0112] = 6
    buffer = _io.BytesIO()
    embedded.save(buffer, format="JPEG", quality=90, exif=exif)
    monkeypatch.setattr("app_common.thumb_stream.get_raw_preview_jpeg", lambda path: buffer.getvalue())

    from PIL import JpegImagePlugin as _JpegImagePlugin

    drafts = []
    original_draft = _JpegImagePlugin.JpegImageFile.draft

    def _tracking_draft(self, mode, size):
        drafts.append(size)
        return original_draft(self, mode, size)

    monkeypatch.setattr(_JpegImagePlugin.JpegImageFile, "draft", _tracking_draft)
    result = _decoder._decode_embedded_raw_preview(tmp_path / "bird.arw", 2048)

    assert result is not None
    assert result.size == (1365, 2048)  # same size as the old full decode + fit, rotated to portrait
    assert drafts and drafts[0] == (2048, 1365)
    top = result.getpixel((680, 20))
    bottom = result.getpixel((680, 2020))
    assert top[0] > 150 and bottom[2] > 150  # orientation 6: stored left half ends up on top


def test_decode_standard_matches_transpose_convert_copy_for_all_orientations(tmp_path):
    from PIL import Image as _Image, ImageOps as _ImageOps

    from birdstamp.decoders import image_decoder as _decoder

    base = _Image.new("RGB", (60, 40), (200, 30, 30))
    base.paste((30, 30, 200), (30, 0, 60, 40))
    for orientation in range(1, 9):
        path = tmp_path / f"o{orientation}.jpg"
        exif = _Image.Exif()
        exif[0x0112] = orientation
        base.save(path, quality=95, exif=exif)
        with _Image.open(path) as image:
            expected = _ImageOps.exif_transpose(image).convert("RGB").copy()
        actual = _decoder._decode_standard(path)
        assert actual.mode == "RGB"
        assert actual.size == expected.size
        assert actual.tobytes() == expected.tobytes(), orientation
        actual.load()  # still usable after the source file was closed
