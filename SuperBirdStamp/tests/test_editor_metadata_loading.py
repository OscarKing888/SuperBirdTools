from __future__ import annotations

import os
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo
import pytest

from app_common.exif_io import close_exiftool_process, inject_metadata_cache, invalidate_metadata_cache
from app_common.exif_io import reader
from birdstamp.gui.editor_photo_metadata_loader import EditorPhotoListMetadataLoader
from birdstamp.gui.template_context import (
    AutoProxyTemplateContextProvider,
    PhotoInfo,
    preview_photo_info,
)


@pytest.fixture(autouse=True)
def close_metadata_reader():
    yield
    close_exiftool_process()


def _photo_with_sidecar(tmp_path: Path) -> Path:
    path = tmp_path / "月亮.png"
    Image.new("RGB", (24, 24)).save(path)
    path.with_suffix(".xmp").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
    xmlns:exif="http://ns.adobe.com/exif/1.0/"
    xmlns:aux="http://ns.adobe.com/exif/1.0/aux/"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    tiff:Model="ILCE-1M2" aux:Lens="FE 300mm F2.8 GM OSS + 1.4X Teleconverter"
    exif:DateTimeOriginal="2026-09-25T22:11:52.417+08:00"
    exif:ExposureTime="1/640" exif:FNumber="4/1" exif:FocalLength="4200/10">
   <exif:ISOSpeedRatings><rdf:Seq><rdf:li>50</rdf:li></rdf:Seq></exif:ISOSpeedRatings>
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">中文月亮</rdf:li></rdf:Alt></dc:title>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>''', encoding="utf-8")
    return path


def _template_values(path: Path, raw: dict, *, snapshot: bool) -> dict[str, str]:
    photo = PhotoInfo.from_path(path, raw_metadata=raw)
    if snapshot:
        photo = preview_photo_info(photo)
    return {
        key: AutoProxyTemplateContextProvider(key).get_text_content(photo)
        for key in ("camera_model", "lens_model", "iso", "aperture", "shutter_speed",
                    "focal_length", "capture_text", "title")
    }


def test_background_metadata_ignores_partial_browser_cache(tmp_path: Path) -> None:
    path = _photo_with_sidecar(tmp_path)
    # 共享列表缓存可只有尺寸或 report 字段，不能代表模板的完整 EXIF/XMP。
    inject_metadata_cache(str(path), {"SourceFile": str(path), "File:ImageWidth": 24})
    try:
        raw = EditorPhotoListMetadataLoader([path])._read_chunk([str(path)])[os.path.normpath(path)]
    finally:
        invalidate_metadata_cache(str(path))

    expected = {
        "camera_model": "ILCE-1M2",
        "lens_model": "FE 300mm F2.8 GM OSS + 1.4X Teleconverter",
        "iso": "50", "aperture": "f/4", "shutter_speed": "1/640",
        "focal_length": "420 毫米", "capture_text": "2026-09-25 22:11",
        "title": "中文月亮",
    }
    assert _template_values(path, raw, snapshot=True) == expected
    assert _template_values(path, raw, snapshot=False) == expected


def test_background_metadata_keeps_all_embedded_fields(tmp_path: Path) -> None:
    path = tmp_path / "camera.png"
    exif = Image.Exif()
    exif[272] = "Embedded camera"
    exif[42036] = "Embedded lens"
    exif[37386] = 420.0
    Image.new("RGB", (24, 24)).save(path, exif=exif)
    raw = EditorPhotoListMetadataLoader([path])._read_chunk([str(path)])[os.path.normpath(path)]
    values = _template_values(path, raw, snapshot=True)
    assert values["lens_model"] == "Embedded lens"
    assert values["focal_length"] == "420 毫米"


def test_sidecar_overrides_embedded_fields_even_with_embedded_title(tmp_path: Path) -> None:
    path = _photo_with_sidecar(tmp_path)
    # 文件已有标题时也必须合并 sidecar，不能以“已有任意 XMP 字段”跳过。
    png_info = PngInfo()
    png_info.add_itxt("XML:com.adobe.xmp", '''<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:tiff="http://ns.adobe.com/tiff/1.0/" tiff:Model="Old camera">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">embedded</rdf:li></rdf:Alt></dc:title>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>''')
    Image.new("RGB", (24, 24)).save(path, pnginfo=png_info)
    try:
        raw = EditorPhotoListMetadataLoader([path])._read_chunk([str(path)])[os.path.normpath(path)]
    finally:
        invalidate_metadata_cache(str(path))
    values = _template_values(path, raw, snapshot=True)
    assert values["title"] == "中文月亮"
    assert values["camera_model"] == "ILCE-1M2"


def test_background_metadata_falls_back_to_pillow_and_xmp(tmp_path: Path, monkeypatch) -> None:
    path = _photo_with_sidecar(tmp_path)
    monkeypatch.setattr(reader, "get_exiftool_executable_path", lambda: None)
    raw = EditorPhotoListMetadataLoader([path])._read_chunk([str(path)])[os.path.normpath(path)]
    values = _template_values(path, raw, snapshot=True)
    assert values["camera_model"] == "ILCE-1M2"
    assert values["capture_text"] == "2026-09-25 22:11"


def test_export_metadata_is_not_overwritten_by_browser_cache(tmp_path: Path) -> None:
    from birdstamp.gui.editor import BirdStampEditorWindow

    path = _photo_with_sidecar(tmp_path)

    class ExportHarness:
        raw_metadata_cache = {}
        photo_list_metadata_cache = {}
        _photo_list_metadata_pending_keys = set()

    # 未经后台列表加载的直接导出也不能被旧缓存覆盖 XMP 字段。
    inject_metadata_cache(str(path), {"SourceFile": str(path), "XMP-tiff:Model": "Old camera"})
    try:
        raw = BirdStampEditorWindow._load_raw_metadata(ExportHarness(), path)
    finally:
        invalidate_metadata_cache(str(path))
    assert _template_values(path, raw, snapshot=True)["camera_model"] == "ILCE-1M2"


@pytest.mark.parametrize("key,raw_value,expected", [
    ("aperture", "4/1", "f/4"),
    ("aperture", "28/10", "f/2.8"),
    ("focal_length", "4200/10", "420 毫米"),
    ("focal_length", "35.5 mm", "35.5 毫米"),
    ("aperture", "4/0", "4/0"),
    ("focal_length", "420/0", "420/0"),
])
def test_template_formats_camera_rationals(key: str, raw_value: str, expected: str) -> None:
    tag = "FNumber" if key == "aperture" else "FocalLength"
    photo = preview_photo_info(PhotoInfo.from_path("missing.png", raw_metadata={tag: raw_value}))
    assert AutoProxyTemplateContextProvider(key).get_text_content(photo) == expected
