"""Measuring other pixels than the RAW decode: embedded JPEG, denoised rendering."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from bird_sharpness import image_source as src


def test_sixteen_bit_tiff_keeps_full_precision_for_measurement(tmp_path) -> None:
    tifffile = pytest.importorskip("tifffile")
    pixels = np.zeros((40, 60, 3), np.uint16)
    pixels[..., 1] = np.arange(60, dtype=np.uint16)[None, :] * 1000 + 7
    path = tmp_path / "照片_denoised.tif"
    tifffile.imwrite(path, pixels, photometric="rgb")
    image = src.load_image_file(str(path), source=src.SOURCE_DENOISED, camera_crop=(0.1, 0.1, 0.9, 0.9))
    assert image.gray.dtype == np.float32 and image.gray[0, 5] == pytest.approx((5007) / 65535.0)
    assert image.rgb8.dtype == np.uint8 and image.rgb8[0, 5, 1] == 5007 >> 8
    assert image.source == src.SOURCE_DENOISED and image.camera_crop == (0.1, 0.1, 0.9, 0.9)
    assert image.source_path == str(path)


def test_embedded_jpeg_of_a_bitmap_is_the_file_itself(tmp_path) -> None:
    path = tmp_path / "鸟.jpg"
    Image.new("RGB", (30, 20), (10, 120, 30)).save(path)
    image = src.load_embedded_jpeg(str(path))
    assert image.source == src.SOURCE_JPEG and image.gray.shape == (20, 30) and image.camera_crop is None


def test_source_loader_choices() -> None:
    assert src.source_loader(src.SOURCE_RAW) is None
    assert src.source_loader(src.SOURCE_JPEG) is src.load_embedded_jpeg
    with pytest.raises(ValueError):
        src.source_loader(src.SOURCE_DENOISED)
    with pytest.raises(src.DenoisedImageMissing):
        src.source_loader(src.SOURCE_DENOISED, denoised_lookup=lambda path: None)("a.ARW")
    with pytest.raises(ValueError):
        src.source_loader("png")


def test_image_source_is_an_analysis_parameter_tagged_into_the_version() -> None:
    from bird_sharpness.params import AnalysisParams
    from bird_sharpness.scoring import ALGORITHM_VERSION
    from bird_sharpness.analyzer import BirdSharpnessAnalyzer

    assert AnalysisParams().image_source == src.SOURCE_RAW  # library / CLI default: the calibrated decode
    assert AnalysisParams.from_params({"image_source": "png"}).image_source == src.SOURCE_RAW
    for source in (src.SOURCE_JPEG, src.SOURCE_DENOISED):
        params = AnalysisParams.from_params({"image_source": source})
        assert params.as_params()["image_source"] == source
        analyzer = BirdSharpnessAnalyzer(models=object(), params=params)
        # "skip analysed" must never take a JPEG / denoised result for a RAW one (or the reverse)
        assert analyzer.version == f"{ALGORITHM_VERSION}-{source}"
    assert BirdSharpnessAnalyzer(models=object()).version == ALGORITHM_VERSION
    grey_jpeg = AnalysisParams.from_params({"grey_fill": True, "image_source": "jpeg"})
    assert grey_jpeg.version_tags() == ["grey", "jpeg"]  # the source tag goes last


def test_analyzer_decodes_the_chosen_image_source(monkeypatch) -> None:
    from bird_sharpness import analyzer as analyzer_mod
    from bird_sharpness.analyzer import BirdSharpnessAnalyzer
    from bird_sharpness.params import AnalysisParams

    def stub(name):
        def load(path):
            raise RuntimeError(f"{name}:{path}")
        return load

    monkeypatch.setattr(analyzer_mod, "load_analysis_image", stub("raw"))
    monkeypatch.setattr(src, "load_embedded_jpeg", stub("jpeg"))
    analyzer = BirdSharpnessAnalyzer(models=object(), focus_provider=lambda *a: None)
    assert analyzer.analyze("a.ARW").error == "RuntimeError: raw:a.ARW"
    analyzer.params = AnalysisParams(image_source=src.SOURCE_JPEG)
    assert analyzer.analyze("a.ARW").error == "RuntimeError: jpeg:a.ARW"
    analyzer.params = AnalysisParams(image_source=src.SOURCE_DENOISED)
    assert "没有降噪成片" in analyzer.analyze("a.ARW").error  # no lookup: a clear per-photo error
    found = type("Found", (), {"path": "a_denoised.tif", "camera_crop": None})()
    monkeypatch.setattr(src, "load_image_file", lambda path, **kw: (_ for _ in ()).throw(RuntimeError(f"tif:{path}")))
    sibling = analyzer.with_options(params=analyzer.params)
    analyzer.denoised_lookup = lambda path: found
    assert analyzer.analyze("a.ARW").error == "RuntimeError: tif:a_denoised.tif"
    assert analyzer.with_options().denoised_lookup is analyzer.denoised_lookup and sibling.denoised_lookup is None


def test_runtime_check_says_restart_when_the_program_was_replaced(monkeypatch) -> None:
    from bird_sharpness import models

    monkeypatch.setattr(models, "RUNTIME_MODULES", ("bird_sharpness_no_such_module",))
    monkeypatch.setattr(models.os, "getcwd", lambda: (_ for _ in ()).throw(FileNotFoundError(2, "gone")))
    assert "重新打开" in models.check_runtime()
    monkeypatch.setattr(models.os, "getcwd", lambda: "/tmp")
    assert models.check_runtime().startswith("缺少运行依赖 bird_sharpness_no_such_module")


@pytest.mark.parametrize("preview_side", [None, 1616])
def test_raw_without_a_full_size_jpeg_is_decoded_instead(monkeypatch, preview_side) -> None:
    import io

    from app_common import thumb_stream

    data = None
    if preview_side:
        buffer = io.BytesIO()
        Image.new("RGB", (preview_side, 1080), (40, 90, 30)).save(buffer, "JPEG")
        data = buffer.getvalue()
    decoded = src.AnalysisImage(np.zeros((4000, 6000, 3), np.uint8), np.zeros((4000, 6000), np.float32), True)
    monkeypatch.setattr(thumb_stream, "get_raw_preview_jpeg", lambda path: data)
    monkeypatch.setattr(src, "_load_raw", lambda path: decoded)
    image = src.load_embedded_jpeg("a.ARW")
    assert image is decoded and image.source == src.SOURCE_RAW and "改用 RAW 解码" in image.note
    assert ("1616 × 1080" in image.note) if preview_side else ("没有内嵌 JPEG" in image.note)


def test_raw_with_a_full_size_jpeg_measures_it(monkeypatch) -> None:
    import io

    from app_common import thumb_stream

    buffer = io.BytesIO()
    Image.new("RGB", (src.EMBEDDED_JPEG_MIN_LONG_EDGE, 2000), (40, 90, 30)).save(buffer, "JPEG")
    monkeypatch.setattr(thumb_stream, "get_raw_preview_jpeg", lambda path: buffer.getvalue())
    monkeypatch.setattr(src, "_load_raw", lambda path: pytest.fail("decoded the RAW"))
    image = src.load_embedded_jpeg("a.ARW")
    assert image.source == src.SOURCE_JPEG and image.gray.shape == (2000, src.EMBEDDED_JPEG_MIN_LONG_EDGE)
    assert image.note == "" and image.source_path == "a.ARW"
