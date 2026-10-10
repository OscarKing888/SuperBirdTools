"""Real source headers and Viewer flows with ExifTool launch forbidden."""
import io
import os
import subprocess
import struct
import time
from types import SimpleNamespace

import pytest
from PIL import Image, ImageFile, PngImagePlugin
from piexif.helper import UserComment

from app_common.exif_io import PhotoMetaDataJSON, meta_disk_cache
from app_common.file_browser._browser_core import _meta_disk_cache_db_path_for_file
from SuperViewer.superviewer import light_metadata as light
from SuperViewer.tests.test_preview_info_sync import _APP, window


XMP = '''<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:xmp="http://ns.adobe.com/xap/1.0/"
 xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/"
 xmp:Rating="4" xmpDM:pick="1">
<dc:description>内嵌中文备注</dc:description>
<dc:subject><rdf:Bag><rdf:li>白鹭</rdf:li><rdf:li>飞行</rdf:li></rdf:Bag></dc:subject>
</rdf:Description></rdf:RDF></x:xmpmeta>'''.encode("utf-8")


@pytest.fixture(autouse=True)
def no_exiftool(monkeypatch, tmp_path):
    calls = []
    original = subprocess.Popen

    def guarded(args, *more, **kwargs):
        if "exiftool" in str(args).casefold():
            calls.append(args)
            raise AssertionError("Viewer attempted to launch ExifTool")
        return original(args, *more, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded)
    from app_common.file_browser import _workers
    def forbid_fallback(*args, **kwargs):
        calls.append("shared ExifTool fallback")
        raise AssertionError("Viewer requested generic metadata fallback")
    monkeypatch.setattr(_workers, "read_batch_metadata", forbid_fallback)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    yield
    meta_disk_cache.close_all()
    assert calls == []  # Detect attempts even when worker error handling caught them.


def make_photo(path, *, xmp=True):
    image = Image.new("RGB", (48, 32), "green")
    exif = Image.Exif()
    exif[40092] = "EXIF中文备注".encode("utf-16-le") + b"\0\0"
    exif[18246] = 3
    exif[34665] = {37510: UserComment.dump("用户中文备注", encoding="unicode"),
                   33434: 1 / 250, 33437: 5.6, 34855: 800}
    kwargs = {"exif": exif}
    if path.suffix == ".png":
        info = PngImagePlugin.PngInfo()
        if xmp:
            info.add_itxt("XML:com.adobe.xmp", XMP.decode("utf-8"))
        kwargs["pnginfo"] = info
    elif path.suffix == ".tif":
        if xmp:
            exif[700] = XMP
    elif xmp:
        kwargs["xmp"] = XMP
    if path.suffix == ".hif":
        import pillow_heif
        heif = pillow_heif.from_pillow(image)
        heif.save(path, exif=exif.tobytes(), xmp=XMP if xmp else None)
    else:
        kwargs["exif"] = exif.tobytes()
        image.save(path, **kwargs)


@pytest.mark.parametrize("suffix", [".jpg", ".png", ".webp", ".tif", ".hif"])
def test_embedded_annotations_without_pixel_decode(tmp_path, monkeypatch, suffix):
    photo = tmp_path / ("白鹭" + suffix)
    make_photo(photo)

    def forbid(*args, **kwargs):
        pytest.fail("metadata reader decoded image pixels")

    monkeypatch.setattr(ImageFile.ImageFile, "load", forbid)
    from pillow_heif.heif import BaseImage
    monkeypatch.setattr(BaseImage, "load", forbid)
    record = light.read_light_metadata(str(photo))
    loader = light.ViewerMetadataLoader([], object())
    parsed = loader._parse_rec(record)
    assert (parsed["width"], parsed["height"]) == (48, 32)
    assert parsed["comment"] == "内嵌中文备注"
    assert parsed["tags"] == ["白鹭", "飞行"]
    assert (parsed["rating"], parsed["pick"]) == (4, 1)
    assert parsed["iso"] == "800"
    assert record["ExifIFD:UserComment"] == "用户中文备注"
    assert record["IFD0:XPComment"] == "EXIF中文备注"


def test_exif_only_and_incomplete_headers_do_not_require_capture_fields(tmp_path):
    photo = tmp_path / "exif.jpg"
    make_photo(photo, xmp=False)
    parsed = light.ViewerMetadataLoader([], object())._parse_rec(light.read_light_metadata(str(photo)))
    assert parsed["comment"] == "用户中文备注"
    assert parsed["rating"] == 3
    empty = tmp_path / "plain.png"
    Image.new("RGB", (12, 8)).save(empty)
    loader = light.ViewerMetadataLoader([str(empty)], object())
    rec = loader._read_metadata_batch([str(empty)])[str(empty)]
    assert rec["width"] == 12
    assert loader._parse_rec(rec)["comment"] == ""


def test_png_annotations_after_pixels_are_read_without_load(tmp_path, monkeypatch):
    photo = tmp_path / "late.png"
    Image.new("RGB", (12, 8)).save(photo)
    payload = b"XML:com.adobe.xmp\0\0\0\0\0" + XMP
    chunk = io.BytesIO()
    PngImagePlugin.putchunk(chunk, b"iTXt", payload)
    data = photo.read_bytes()
    photo.write_bytes(data[:-12] + chunk.getvalue() + data[-12:])
    monkeypatch.setattr(Image.Image, "load", lambda *_: pytest.fail("pixel decode"))
    rec = light.read_light_metadata(str(photo))
    assert rec["Description"] == "内嵌中文备注"


def test_raw_size_is_source_geometry_without_unpack(tmp_path, monkeypatch):
    # RAW's IFD0 is often just the preview. Simulate that header with a real TIFF.
    photo = tmp_path / "source.arw"
    Image.new("RGB", (48, 32)).save(photo, format="TIFF")
    import rawpy
    calls = []

    class RawHeader:
        sizes = SimpleNamespace(width=6000, height=4000)
        def __enter__(self):
            return self
        def __exit__(self, *_):
            calls.append("closed")
        def open_file(self, path):
            calls.append(path)

    monkeypatch.setattr(rawpy, "RawPy", RawHeader)
    monkeypatch.setattr(rawpy, "imread", lambda *_: pytest.fail("RAW unpack"))
    rec = light.read_light_metadata(str(photo))
    assert (rec["width"], rec["height"]) == (6000, 4000)
    assert calls == [str(photo), "closed"]


def test_16_bit_psd_metadata_needs_no_pixel_decoder(tmp_path, monkeypatch):
    photo = tmp_path / "中文.psd"
    from app_common.tests.test_psd_composite import _write_raw_16_bit_psd
    _write_raw_16_bit_psd(photo)
    data = photo.read_bytes()
    resource = b"8BIM" + struct.pack(">H", 1060) + b"\0\0" + struct.pack(">I", len(XMP)) + XMP
    resource += b"\0" if len(XMP) % 2 else b""
    photo.write_bytes(data[:30] + struct.pack(">I", len(resource)) + resource + data[34:])
    monkeypatch.setattr(Image, "open", lambda *_: pytest.fail("PSD pixel decoder"))
    rec = light.read_light_metadata(str(photo))
    assert (rec["width"], rec["height"]) == (4, 2)
    assert rec["Description"] == "内嵌中文备注"


def test_real_dng_source_header(tmp_path, monkeypatch):
    import numpy as np
    import rawpy
    from PIL import TiffImagePlugin
    photo = tmp_path / "源图.dng"
    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    for tag, value in {
        262: 32803, 271: "Test", 272: "Synthetic", 277: 1, 284: 1,
        33421: (2, 2), 33422: bytes([0, 1, 1, 2]),
        50706: bytes([1, 4, 0, 0]), 50707: bytes([1, 1, 0, 0]),
        50708: "Synthetic", 50714: 0, 50717: 65535,
        50721: (1, 0, 0, 0, 1, 0, 0, 0, 1), 50728: (1, 1, 1), 50778: 21,
    }.items():
        ifd[tag] = value
    for tag in (33422, 50706, 50707):
        ifd.tagtype[tag] = 1
    for tag in (50721, 50728):
        ifd.tagtype[tag] = 5
    Image.fromarray(np.full((96, 128), 2000, dtype=np.uint16)).save(photo, format="TIFF", tiffinfo=ifd)
    class HeaderOnlyRaw(rawpy.RawPy):
        def unpack(self):
            pytest.fail("RAW unpack")
        def postprocess(self, **kwargs):
            pytest.fail("RAW demosaic")
    monkeypatch.setattr(rawpy, "RawPy", HeaderOnlyRaw)
    rec = light.read_light_metadata(str(photo))
    assert (rec["width"], rec["height"]) == (128, 96)


def test_cache_is_separate_and_sidecar_edits_override_embedded_values(tmp_path, monkeypatch):
    (tmp_path / ".superpicky").mkdir()
    photo = tmp_path / "中文.jpg"
    make_photo(photo)
    path = str(photo)
    loader = light.ViewerMetadataLoader([path], object(), selected_dir=str(tmp_path))
    shared_db = _meta_disk_cache_db_path_for_file(path)
    stat = photo.stat()
    meta_disk_cache.put_many(shared_db, {path: (stat.st_mtime, stat.st_size, {"Description": "旧缓存"})})
    assert loader._metadata_cache_db_path(path) != shared_db
    assert loader._parse_rec(loader._read_metadata_batch([path])[path])["comment"] == "内嵌中文备注"
    monkeypatch.setattr(light, "read_light_metadata", lambda *_: pytest.fail("cache miss"))
    photo.with_suffix(".xmp").write_bytes(XMP.replace("内嵌中文备注".encode(), "侧车中文备注".encode()))
    assert loader._parse_rec(loader._read_metadata_batch([path])[path])["comment"] == "侧车中文备注"
    writer = PhotoMetaDataJSON()
    for comment, rating, pick in [("JSON中文修改", 2, -1), ("", 0, 0)]:
        assert writer.write(path, {"XMP-dc:Description": comment, "XMP-xmp:Rating": rating,
                                   "XMP-xmpDM:pick": pick, "XMP-dc:Subject": []})
        rec = loader._parse_rec(loader._read_metadata_batch([path])[path])
        assert (rec["comment"], rec["rating"], rec["pick"]) == (comment, rating, pick)
        assert rec["tags"] == []


def test_bad_or_missing_source_still_loads_sidecars_without_exiftool(tmp_path):
    photo = tmp_path / "bad.jpg"
    photo.write_bytes(b"not an image")
    assert PhotoMetaDataJSON().write(str(photo), {"XMP-dc:Description": "侧车仍可读取"})
    missing = tmp_path / "missing.png"
    loader = light.ViewerMetadataLoader([], object())
    batch = loader._read_metadata_batch([str(photo), str(missing)])
    assert loader._parse_rec(batch[str(photo)])["comment"] == "侧车仍可读取"
    assert "width" not in batch[str(missing)]
    assert loader._parse_rec(batch[str(missing)])["comment"] == ""
    from SuperViewer.superviewer.photo_tags import PhotoTagSidecarStore
    from app_common.exif_io import json_sidecar_path_for
    result = PhotoTagSidecarStore().apply_tag_states({str(missing): {"飞行": True}})
    assert str(missing) in result.failed_paths
    assert not json_sidecar_path_for(str(missing)).exists()


@pytest.mark.parametrize("mode", ["list", "thumb"])
def test_real_viewer_pipeline_and_cached_ui_never_fall_back(window, tmp_path, monkeypatch, mode):
    directory = tmp_path / "library"
    (directory / ".superpicky").mkdir(parents=True)
    for index in range(20):
        photo = directory / f"白鹭{index}.png"
        Image.new("RGB", (48, 32), "green").save(photo)
        assert PhotoMetaDataJSON().write(str(photo), {"XMP-dc:Description": "中文检索",
                                                     "XMP-xmp:Rating": 4})
    panel = window._file_list
    panel._view_mode = panel._MODE_LIST if mode == "list" else panel._MODE_THUMB
    attempts = []
    monkeypatch.setattr(panel._meta_proxy, "read", lambda *a: attempts.append(a) or {})
    monkeypatch.setattr(panel._meta_proxy, "read_exposure_settings", lambda *a: attempts.append(a) or ("", "", ""))
    window._on_directory_selected(str(directory))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _APP.processEvents()
        if len(panel._meta_cache) == 20 and all(m.get("comment") == "中文检索" for m in panel._meta_cache.values()):
            break
        time.sleep(0.003)
    assert len(panel._meta_cache) == 20
    assert all(m.get("width") == 48 and m.get("comment") == "中文检索" for m in panel._meta_cache.values())
    for path in panel._all_files:
        assert panel.get_photo_metadata_for_path(path, allow_slow_read=True)["rating"] == 4
        panel.get_photo_exposure_settings_for_path(path, allow_slow_read=True)
    assert attempts == []
    panel._filter_edit.setText("中文检索")
    assert all(panel._path_matches_active_filters(path) for path in panel._all_files)
