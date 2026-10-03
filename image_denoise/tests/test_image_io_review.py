"""独立审阅补充：灰度透明、预乘通道与高位深颜色变换。"""
import numpy as np
import pytest
import tifffile
import errno
import hashlib
import json
from pathlib import Path
import struct
import zlib
from PIL import Image

from image_denoise.image_io import decode_image, probe_image, linear_to_srgb
from image_denoise.types import UnsupportedImage


def test_white_is_zero_tiff_keeps_unassociated_alpha(tmp_path):
    source = tmp_path / "反相灰度带透明.tif"
    pixels = np.zeros((4, 7, 2), dtype=np.uint16)
    pixels[..., 0] = 16384
    pixels[..., 1] = np.arange(28, dtype=np.uint16).reshape(4, 7) * 2300
    tifffile.imwrite(source, pixels, photometric="miniswhite", extrasamples="unassalpha", metadata=None)
    result = decode_image(source)
    np.testing.assert_allclose(result.rgb, (65535 - 16384) / 65535, atol=1e-6)
    np.testing.assert_allclose(result.alpha, pixels[..., 1] / 65535, atol=1e-6)


def test_white_is_zero_associated_alpha_unpremultiplies_before_inversion(tmp_path):
    source = tmp_path / "反相灰度预乘透明.tif"
    pixels = np.empty((4, 7, 2), dtype=np.uint16)
    pixels[..., 0] = 8192
    pixels[..., 1] = 32768
    tifffile.imwrite(source, pixels, photometric="miniswhite", extrasamples="assocalpha", metadata=None)
    result = decode_image(source)
    np.testing.assert_allclose(result.rgb, .75, atol=1e-6)
    np.testing.assert_allclose(result.alpha, 32768 / 65535, atol=1e-6)


def test_associated_rgb_alpha_is_unpremultiplied_without_losing_precision(tmp_path):
    source = tmp_path / "预乘透明.tif"
    pixels = np.empty((4, 7, 4), dtype=np.uint16)
    pixels[..., :3] = 8192
    pixels[..., 3] = 32768
    pixels[0, 0] = 0
    tifffile.imwrite(source, pixels, photometric="rgb", extrasamples="assocalpha", metadata=None)
    result = decode_image(source)
    np.testing.assert_allclose(result.rgb[1:], .25, atol=1e-6)
    np.testing.assert_array_equal(result.rgb[0, 0], 0)
    np.testing.assert_allclose(result.alpha[1:], 32768 / 65535, atol=1e-6)


def test_heif_premultiplied_alpha_is_unpremultiplied(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    source = tmp_path / "预乘透明.heic"
    pixels = np.empty((32, 48, 4), dtype=np.uint8)
    pixels[..., :3] = 64
    pixels[..., 3] = 128
    image = pillow_heif.from_bytes("RGBa", (48, 32), pixels.tobytes())
    image.save(source, quality=-1, chroma=444)
    assert pillow_heif.open_heif(source).premultiplied_alpha
    result = decode_image(source)
    np.testing.assert_allclose(result.rgb, .5, atol=1e-4)
    np.testing.assert_allclose(result.alpha, 128 / 255, atol=1e-4)


def test_tiff_orientation_rotates_rgb_and_alpha_together(tmp_path):
    source = tmp_path / "需要旋转.tif"
    pixels = np.arange(3 * 5 * 4, dtype=np.uint16).reshape(3, 5, 4) * 1000
    tifffile.imwrite(source, pixels, photometric="rgb", extrasamples="unassalpha",
                     metadata=None, extratags=[(274, "H", 1, 6, False)])
    result = decode_image(source)
    expected = np.rot90(pixels, -1)
    np.testing.assert_allclose(result.rgb, expected[..., :3] / 65535, atol=1e-6)
    np.testing.assert_allclose(result.alpha, expected[..., 3] / 65535, atol=1e-6)


def test_wrong_colourspace_icc_fails_without_mutating_input(tmp_path):
    import imagecodecs

    source = tmp_path / "灰度描述不匹配.tif"
    pixels = np.zeros((4, 7, 3), dtype=np.uint16)
    tifffile.imwrite(source, pixels, photometric="rgb", metadata=None,
                     iccprofile=imagecodecs.cms_profile("gray"))
    before = source.read_bytes()
    with pytest.raises(Exception):
        decode_image(source)
    assert source.read_bytes() == before


@pytest.mark.parametrize("bits", [4, 12])
def test_packed_tiff_sample_depth_is_not_silently_misread_as_8_or_16(tmp_path, bits):
    source = tmp_path / f"打包{bits}位.tif"
    maximum = (1 << bits) - 1
    pixels = np.array([[0, maximum]] * 3, dtype=np.uint8 if bits == 4 else np.uint16)
    tifffile.imwrite(source, pixels, photometric="minisblack", bitspersample=bits, metadata=None)
    # 首版只支持实际 8/16 位；dtype 只是容器宽度，不能代替 BitsPerSample。
    with pytest.raises(UnsupportedImage):
        decode_image(source)


def test_publish_fallback_never_replaces_an_external_file_after_identity_check(tmp_path, monkeypatch):
    from image_denoise import export

    staged, target = tmp_path / "staged.jpg", tmp_path / "output.jpg"
    staged.write_bytes(b"our denoised result")
    external = b"another application's complete photo"
    injected = False
    real_stat = Path.stat

    def unsupported_link(*args):
        raise OSError(errno.EOPNOTSUPP, "filesystem has no hardlink support")

    def stat_then_external_replacement(path, *args, **kwargs):
        nonlocal injected
        current = real_stat(path, *args, **kwargs)
        if path == target and not injected:
            injected = True
            target.unlink()
            target.write_bytes(external)
        return current

    monkeypatch.setattr(export.os, "link", unsupported_link)
    monkeypatch.setattr(Path, "stat", stat_then_external_replacement)
    try:
        export._publish_new(staged, target)
    except FileExistsError:
        pass
    assert injected
    assert target.read_bytes() == external


def test_hardlink_publication_reports_owned_inode_not_external_replacement(tmp_path, monkeypatch):
    from image_denoise import export

    staged, target = tmp_path / "staged.jpg", tmp_path / "output.jpg"
    staged.write_bytes(b"our result")
    expected = staged.stat()
    real_link = export.os.link

    def link_then_replace(source, destination):
        real_link(source, destination)
        destination.unlink()
        destination.write_bytes(b"foreign result")

    monkeypatch.setattr(export.os, "link", link_then_replace)
    identity = export._publish_new(staged, target)
    assert identity == (expected.st_dev, expected.st_ino)
    assert target.read_bytes() == b"foreign result"
    assert identity != (target.stat().st_dev, target.stat().st_ino)


def test_fallback_failed_copy_cleans_owned_file_and_preserves_staged(tmp_path, monkeypatch):
    from image_denoise import export

    staged, target = tmp_path / "staged.jpg", tmp_path / "output.jpg"
    staged.write_bytes(b"complete staged result")

    def unsupported_link(*args):
        raise OSError(errno.EOPNOTSUPP, "no links")

    def failed_copy(source, output, **kwargs):
        output.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(export.os, "link", unsupported_link)
    monkeypatch.setattr(export.shutil, "copyfileobj", failed_copy)
    with pytest.raises(OSError, match="disk full"):
        export._publish_new(staged, target)
    assert not target.exists()
    assert staged.read_bytes() == b"complete staged result"


def _write_cicp_png(path, transfer, primaries=9):
    Image.new("RGB", (16, 17), (64, 64, 64)).save(path)
    original = path.read_bytes()
    chunk = b"cICP" + bytes([primaries, transfer, 0, 1])
    # Pillow 不保留这个新标准块，需要直接写入合法的 PNG 元数据。
    encoded = struct.pack(">I", 4) + chunk + struct.pack(">I", zlib.crc32(chunk))
    path.write_bytes(original[:33] + encoded + original[33:])


@pytest.mark.parametrize("transfer", [16, 18])
@pytest.mark.parametrize("reader", [decode_image, probe_image])
def test_hdr_png_is_rejected_before_pillow_decode(tmp_path, monkeypatch, transfer, reader):
    source = tmp_path / "HDR.png"
    _write_cicp_png(source, transfer)
    original = source.read_bytes()
    monkeypatch.setattr(Image, "open", lambda *a, **k: pytest.fail("HDR 必须在解码前跳过"))
    with pytest.raises(UnsupportedImage, match="HDR PNG"):
        reader(source)
    assert source.read_bytes() == original


def test_sdr_png_cicp_linear_transfer_is_converted_without_icc(tmp_path):
    source = tmp_path / "线性SDR.png"
    _write_cicp_png(source, 8, primaries=1)
    result = decode_image(source)
    expected = linear_to_srgb(np.full((17, 16, 3), 64 / 255, np.float32))
    np.testing.assert_allclose(result.rgb, expected, atol=1e-6)


@pytest.mark.parametrize("reader", [decode_image, probe_image])
def test_avif_outside_shared_supported_formats_is_not_silently_decoded(tmp_path, reader):
    source = tmp_path / "HDR.avif"
    source.write_bytes(b"format is outside the shared extension list")
    with pytest.raises(UnsupportedImage, match="avif"):
        reader(source)


@pytest.mark.parametrize("format,suffix", [("tiff", ".tif"), ("jpeg", ".jpg")])
def test_real_sony_raw_metadata_exports_without_hidden_makernote_offsets(tmp_path, format, suffix):
    from app_common.exif_io import get_exiftool_executable_path
    from app_common.exif_io.exiftool_runner import run_exiftool
    from image_denoise import export, DenoiseOptions

    source = Path(__file__).resolve().parents[2] / "benchmark" / "DSC01182.ARW"
    if not source.exists():
        pytest.skip("可选真实 Sony ARW 回归样本未安装")
    executable = get_exiftool_executable_path()
    assert executable
    tags = ["Make", "Model", "ISO", "ExposureTime", "FNumber", "FocalLength", "LensModel", "DateTimeOriginal"]

    def read_tags(path):
        result = run_exiftool(executable, ["-j", "-n", "-s", "-MakerNotes", *[f"-{tag}" for tag in tags], str(path)])
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)[0]

    before = hashlib.sha256(source.read_bytes()).hexdigest()
    source_tags = read_tags(source)
    assert any(tag.startswith("MakerNote") for tag in source_tags)
    target = tmp_path / ("真实RAW拍摄信息" + suffix)
    # 使用小像素只测真实 RAW 元数据迁移；完整 RAW 推理另外做耗时烟测。
    export.export_image(source, target, np.full((16, 19, 3), .4, np.float32), None,
                        DenoiseOptions(format=format))
    output_tags = read_tags(target)
    assert not any(tag.startswith("MakerNote") for tag in output_tags)
    assert all(output_tags[tag] == source_tags[tag] for tag in tags if tag in source_tags)
    assert {"Make", "Model", "ISO", "ExposureTime", "FNumber", "LensModel"} <= output_tags.keys()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
