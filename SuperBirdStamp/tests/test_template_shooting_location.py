"""拍摄地点须与 Viewer 的中文文本一致，不把清晰度或 GPS 当作地点。"""
from pathlib import Path

import pytest
from PIL import Image

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.shooting_location import LOCATION_TAG, write_shooting_location
from birdstamp.gui import editor_template, template_context as context
from birdstamp.meta.normalize import normalize_metadata
from birdstamp.overlays.model import new_item
from birdstamp.overlays.render import build_scene


ANALYSIS_METADATA = {
    "XMP-photoshop:City": "650.00",
    "XMP-photoshop:State": "87.50",
    "XMP-photoshop:Country": "1",
    "GPSLatitude": 31.22,
    "GPSLongitude": 121.48,
}


@pytest.mark.parametrize("value", ["上海·崇明东滩（保护区） & 湿地", "123号观鸟点", "123", "", None])
def test_snapshot_providers_separate_location_analysis_and_gps(value):
    raw = dict(ANALYSIS_METADATA)
    if value is not None:
        raw[LOCATION_TAG] = value
    info = context.PhotoInfo(Path("白鹭.jpg"), raw_metadata=raw, metadata_is_snapshot=True)
    for source in ("auto", "exif", "from_file"):
        provider = context.build_template_context_provider(source, "location")
        assert provider.get_text_content(info) == (value or ("N/A" if source == "auto" else ""))
    assert context.build_template_context(info)["location"] == (value or "")
    normalized = normalize_metadata(
        info.path, raw, bird_arg=None, bird_priority=["meta"], bird_regex="",
    )
    assert normalized.location == (value or None)
    assert normalized.gps_text == "31.22000, 121.48000"
    assert context.build_template_context_provider("auto", "sharpness").get_text_content(info) == "650.00"


def test_real_xmp_location_edit_clear_and_overlay_render(tmp_path, monkeypatch):
    source = tmp_path / "白鹭.jpg"
    Image.new("RGB", (32, 24), "white").save(source)
    original = source.read_bytes()
    source.with_suffix(".xmp").write_text('''<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">
<photoshop:City>650.00</photoshop:City><photoshop:State>87.50</photoshop:State>
</rdf:Description></rdf:RDF></x:xmpmeta>''', encoding="utf-8")
    # 同一个导出 PhotoInfo 带旧缓存，侧车修改和清空都必须立即胜出。
    raw = dict(ANALYSIS_METADATA, shooting_location="旧地点", location="650.00")
    info = context.PhotoInfo.from_path(source, raw_metadata=raw)
    item = new_item("text")
    item.update(layout_mode="manual", text_mode="metadata", text_source={"type": "auto", "key": "location"})
    drawn = []
    original_draw = editor_template.styled_text_layer

    def capture_text(text, *args, **kwargs):
        drawn.append(text)
        return original_draw(text, *args, **kwargs)

    monkeypatch.setattr(editor_template, "styled_text_layer", capture_text)
    for value in ("上海东滩", "云南·高黎贡山 & 湿地", ""):
        write_shooting_location(str(source), value)
        saved = PhotoMetaDataXMP().read(str(source))
        assert saved.get(LOCATION_TAG, "") == value
        assert saved["XMP-photoshop:City"] == "650.00"
        assert source.read_bytes() == original
        assert context.build_template_context(info)["location"] == value
        provider = context.build_template_context_provider("auto", "location")
        assert editor_template._resolve_template_field_text(provider, info) == (value or "N/A")
        drawn.clear()
        scene = build_scene({"overlays": [item]}, (800, 600), photo_info=info, raw_metadata=raw)
        try:
            assert drawn == [value or "N/A"]
            assert len(scene.layers) == 1
        finally:
            scene.close()


def test_clear_last_sidecar_field_does_not_restore_cached_location(tmp_path):
    source = tmp_path / "翠鸟.jpg"
    Image.new("RGB", (8, 8)).save(source)
    write_shooting_location(str(source), "旧地点")
    info = context.PhotoInfo.from_path(source, raw_metadata={LOCATION_TAG: "旧地点"})
    assert context.build_template_context(info)["location"] == "旧地点"
    write_shooting_location(str(source), "")
    assert context.build_template_context(info)["location"] == ""
    assert context.build_template_context_provider("auto", "location").get_text_content(info) == "N/A"
