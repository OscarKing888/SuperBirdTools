"""Measuring other pixels than the RAW decode: embedded JPEG, denoised rendering."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from bird_sharpness import image_source as src
from bird_sharpness.__main__ import main as cli_main


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


def test_cli_refuses_to_write_xmp_from_other_sources(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["--source", "jpeg", "--write-xmp", "a.ARW"])
    assert exc.value.code == 2 and "--write-xmp" in capsys.readouterr().err


def test_runtime_check_says_restart_when_the_program_was_replaced(monkeypatch) -> None:
    from bird_sharpness import models

    monkeypatch.setattr(models, "RUNTIME_MODULES", ("bird_sharpness_no_such_module",))
    monkeypatch.setattr(models.os, "getcwd", lambda: (_ for _ in ()).throw(FileNotFoundError(2, "gone")))
    assert "重新打开" in models.check_runtime()
    monkeypatch.setattr(models.os, "getcwd", lambda: "/tmp")
    assert models.check_runtime().startswith("缺少运行依赖 bird_sharpness_no_such_module")
