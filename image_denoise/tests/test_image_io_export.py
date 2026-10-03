"""真实高位深编解码、中文 EXIF/XMP 与失败原子性回归。"""
from dataclasses import replace
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import imagecodecs
import numpy as np
from PIL import Image
import pytest
import tifffile

from app_common.exif_io import get_exiftool_executable_path
from app_common.exif_io.exiftool_runner import run_exiftool
from image_denoise import DenoiseOptions, denoise_file
from image_denoise import export
from image_denoise.image_io import decode_image, linear_to_srgb, probe_image, srgb_profile
from image_denoise.types import DenoiseCancelled


def exiftool(*args):
    executable = get_exiftool_executable_path()
    assert executable, "项目 ExifTool 必须可用"
    result = run_exiftool(executable, list(args), timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "中文 原片.jpg"
    Image.new("RGB", (64, 48), "#4876ab").save(path)
    text = tmp_path / "中文.txt"
    text.write_text("原始中文备注，白鹭", encoding="utf-8")
    exiftool("-overwrite_original", "-Orientation#=6", "-Make=SONY", "-Model=ILCE-1M2",
             "-ISO=3200", "-ExposureTime=1/2000", f"-UserComment<={text}", str(path))
    path.with_suffix(".XMP").write_text('''<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Orientation="6"
 xmlns:s="https://superbirdtools.local/xmp/superpicky/1.0/" s:bird_species_cn="白鹭">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">侧车中的中文新标题</rdf:li></rdf:Alt></dc:title>
<dc:subject><rdf:Bag><rdf:li>飞行</rdf:li><rdf:li>陌生标签</rdf:li></rdf:Bag></dc:subject>
</rdf:Description>
<rdf:Description rdf:about="https://example.org/unrelated" xmlns:c="urn:custom" c:value="不丢失"/>
</rdf:RDF></x:xmpmeta>''', encoding="utf-8")
    return path


@pytest.mark.parametrize("format,suffix", [("tiff", ".tif"), ("jpeg", ".jpg")])
def test_real_chinese_metadata_orientation_and_source_unchanged(photo, format, suffix):
    originals = {p: p.read_bytes() for p in (photo, photo.with_suffix(".XMP"))}
    target = photo.parent / ("降噪成片" + suffix)
    result = denoise_file(photo, target, DenoiseOptions(strength=0, format=format))
    assert result.status == "success", result.error
    metadata = json.loads(exiftool("-j", "-s", "-G1", "-n", str(target)))[0]
    assert metadata["ExifIFD:ISO"] == 3200
    assert metadata["IFD0:Make"] == "SONY"
    assert metadata["ExifIFD:UserComment"] == "原始中文备注，白鹭"
    assert metadata["XMP-dc:Title"] == "侧车中的中文新标题"
    assert metadata["IFD0:Orientation"] == metadata["XMP-tiff:Orientation"] == 1
    assert metadata["ExifIFD:ExifImageWidth"] == 48
    assert metadata["ExifIFD:ExifImageHeight"] == 64
    tree = ET.parse(target.with_suffix(".xmp"))
    descriptions = tree.findall(".//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
    assert descriptions[0].attrib["{https://superbirdtools.local/xmp/superpicky/1.0/}bird_species_cn"] == "白鹭"
    assert descriptions[1].attrib["{urn:custom}value"] == "不丢失"
    for path, data in originals.items():
        assert path.read_bytes() == data
    assert probe_image(target) == (48, 64)
    if format == "tiff":
        with tifffile.TiffFile(target) as tiff:
            assert tiff.asarray().dtype == np.uint16
            assert len(tiff.pages) == 1
            assert tiff.asarray().shape == (64, 48, 3)


@pytest.mark.parametrize("extension", ["tif", "png"])
def test_16bit_gradient_does_not_roundtrip_through_rgb8(tmp_path, extension):
    gradient = np.broadcast_to(np.arange(4096, dtype=np.uint16)[None, :, None] * 16, (3, 4096, 3)).copy()
    source = tmp_path / ("渐变." + extension)
    if extension == "tif":
        tifffile.imwrite(source, gradient, photometric="rgb", metadata=None, iccprofile=srgb_profile())
    else:
        source.write_bytes(imagecodecs.png_encode(gradient))
    decoded = decode_image(source)
    assert np.unique(decoded.rgb[0, :, 0]).size > 4000
    target = tmp_path / "结果.tif"
    result = denoise_file(source, target, DenoiseOptions(strength=0))
    assert result.status == "success", result.error
    actual = tifffile.imread(target)
    assert actual.dtype == np.uint16 and np.unique(actual[0, :, 0]).size > 4000
    np.testing.assert_allclose(actual.astype(float), gradient, atol=4)


def test_alpha_preserved_in_tiff_and_jpeg_rejected(tmp_path):
    source = tmp_path / "透明.png"
    pixels = np.zeros((4, 5, 4), np.uint16)
    pixels[..., :3] = 12345
    pixels[..., 3] = np.arange(20, dtype=np.uint16).reshape(4, 5) * 3000
    source.write_bytes(imagecodecs.png_encode(pixels))
    target = tmp_path / "透明结果.tif"
    result = denoise_file(source, target, DenoiseOptions(strength=0))
    assert result.status == "success", result.error
    np.testing.assert_array_equal(tifffile.imread(target)[..., 3], pixels[..., 3])
    jpeg = denoise_file(source, tmp_path / "alpha.jpg", DenoiseOptions(strength=0, format="jpeg"))
    assert jpeg.status == "failed" and "透明" in jpeg.error
    assert not (tmp_path / "alpha.jpg").exists()


def test_raw_pipeline_is_full_16bit_and_srgb_encoded(tmp_path, monkeypatch):
    import rawpy
    source = tmp_path / "原始中文.ARW"
    source.write_bytes(b"synthetic LibRaw fixture")
    seen = {}
    class Raw:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def postprocess(self, **kwargs):
            seen.update(kwargs)
            return np.full((4, 6, 3), round(.18 * 65535), np.uint16)
    def imread(stream):
        assert hasattr(stream, "read")
        return Raw()
    monkeypatch.setattr(rawpy, "imread", imread)
    result = decode_image(source)
    assert seen["output_bps"] == 16 and seen["half_size"] is False
    assert seen["gamma"] == (1, 1) and seen["use_camera_wb"] is True
    assert result.rgb[0, 0, 0] == pytest.approx(.46135, abs=.0001)


def test_cancel_before_publication_leaves_no_output(photo, monkeypatch):
    before = {p.name for p in photo.parent.iterdir()}
    monkeypatch.setattr(export, "copy_output_metadata", lambda *a, **k: (_ for _ in ()).throw(DenoiseCancelled()))
    result = denoise_file(photo, photo.parent / "结果.tif", DenoiseOptions(strength=0))
    assert result.status == "cancelled"
    assert {p.name for p in photo.parent.iterdir()} == before


def test_failed_second_publication_rolls_back_sidecar(photo, monkeypatch):
    before = {p.name for p in photo.parent.iterdir()}
    publish = export._publish_new
    def fail_image(staged, target):
        if target.suffix == ".tif":
            raise OSError("模拟磁盘写入失败")
        return publish(staged, target)
    monkeypatch.setattr(export, "_publish_new", fail_image)
    result = denoise_file(photo, photo.parent / "结果.tif", DenoiseOptions(strength=0))
    assert result.status == "failed" and "磁盘" in result.error
    assert {p.name for p in photo.parent.iterdir()} == before


def test_existing_sidecar_or_original_never_overwritten(photo):
    target = photo.parent / "Already.tif"
    sidecar = target.with_suffix(".XMP")
    sidecar.write_bytes(b"previous complete sidecar")
    result = denoise_file(photo, target, DenoiseOptions(strength=0))
    assert result.status == "failed"
    assert sidecar.read_bytes() == b"previous complete sidecar" and not target.exists()
    before = photo.read_bytes()
    result = denoise_file(photo, photo, DenoiseOptions(strength=0, format="jpeg"))
    assert result.status == "failed" and photo.read_bytes() == before


def test_invalid_sidecar_is_not_silently_discarded(photo):
    sidecar = photo.with_suffix(".XMP")
    sidecar.write_bytes(b"<malformed")
    target = photo.parent / "result.tif"
    result = denoise_file(photo, target, DenoiseOptions(strength=0))
    assert result.status == "failed" and not target.exists()
    assert sidecar.read_bytes() == b"<malformed"


def test_static_scope_and_bad_profile(tmp_path):
    source = tmp_path / "animated.webp"
    images = [Image.new("RGB", (4, 4), color) for color in ("red", "blue")]
    images[0].save(source, save_all=True, append_images=images[1:])
    result = denoise_file(source, tmp_path / "out.tif", DenoiseOptions(strength=0))
    assert result.status == "skipped"
    broken = tmp_path / "profile.png"
    images[0].save(broken, icc_profile=b"broken profile")
    result = denoise_file(broken, tmp_path / "out.tif", DenoiseOptions(strength=0))
    assert result.status == "failed"
