from datetime import datetime
from pathlib import Path
import tempfile

from birdstamp.gui.template_context import (
    AutoProxyTemplateContextProvider,
    EditorPhotoInfo,
    MISSING_TEMPLATE_TEXT,
    PhotoInfo,
    TEMPLATE_SOURCE_AUTO,
    TEMPLATE_SOURCE_EDITOR,
    TEMPLATE_SOURCE_EXIF,
    TEMPLATE_SOURCE_FROM_FILE,
    TEMPLATE_SOURCE_REPORT_DB,
    build_template_context,
    build_template_context_provider,
    canonical_meta_field_key,
    get_template_context_field_options,
    normalize_template_selector_option,
    report_db_lookup_keys_for_path,
    report_db_lookup_keys_for_value,
    set_report_db_row_resolver,
)


def test_report_db_lookup_keys_cover_filename_and_stem() -> None:
    keys = report_db_lookup_keys_for_value(r"folder\\sample.jpg")

    assert keys == (r"folder\\sample.jpg", "sample.jpg", "sample")


def test_build_template_context_reads_species_from_report_db_filename_key() -> None:
    row = {
        "filename": "sample.jpg",
        "bird_species_cn": "红胁蓝尾鸲",
        "bird_species_en": "Red-flanked Bluetail",
    }
    cache = {
        key: dict(row)
        for key in report_db_lookup_keys_for_value(row["filename"])
    }

    def _resolver(path: Path) -> dict | None:
        for key in report_db_lookup_keys_for_path(path):
            hit = cache.get(key)
            if isinstance(hit, dict):
                return dict(hit)
        return None

    set_report_db_row_resolver(_resolver)
    try:
        context = build_template_context(Path("/tmp/sample.jpg"), {})
    finally:
        set_report_db_row_resolver(None)

    assert context["bird"] == "红胁蓝尾鸲"
    assert context["bird_common"] == "红胁蓝尾鸲"
    assert context["bird_latin"] == "Red-flanked Bluetail"
    assert context["bird_scientific"] == "Red-flanked Bluetail"
    assert context["report.bird_species_cn"] == "红胁蓝尾鸲"
    assert context["report.bird_species_en"] == "Red-flanked Bluetail"


def test_template_context_provider_supports_all_text_sources() -> None:
    photo = PhotoInfo.from_path(
        "/tmp/sample.jpg",
        raw_metadata={
            "EXIF:Model": "Sony ILCE-1M2",
            "EXIF:DateTimeOriginal": "2026:02:16 09:14:00",
            "XMP-dc:Creator": "Oscar",
            "SourceFile": "/tmp/sample.jpg",
        },
    )
    row = {
        "filename": "sample.jpg",
        "bird_species_cn": "黑脸琵鹭",
    }

    def _resolver(path: Path) -> dict | None:
        if path.name == "sample.jpg":
            return dict(row)
        return None

    set_report_db_row_resolver(_resolver)
    try:
        exif_provider = build_template_context_provider("exif", "EXIF:Model", display_label="机身型号")
        from_file_provider = build_template_context_provider("from_file", "{filename}", display_label="文件名")
        from_file_author_provider = build_template_context_provider("from_file", "author", display_label="作者")
        from_file_capture_provider = build_template_context_provider("from_file", "capture_text", display_label="拍摄时间")
        report_provider = build_template_context_provider("report_db", "bird_species_cn", display_label="鸟种中文名")

        assert exif_provider.id == TEMPLATE_SOURCE_EXIF
        assert from_file_provider.id == TEMPLATE_SOURCE_FROM_FILE
        assert report_provider.id == TEMPLATE_SOURCE_REPORT_DB
        assert exif_provider.get_text_content(photo) == "Sony ILCE-1M2"
        assert from_file_provider.get_text_content(photo) == "sample.jpg"
        assert from_file_author_provider.get_text_content(photo) == "Oscar"
        assert from_file_capture_provider.get_text_content(photo) == "2026-02-16 09:14"
        assert report_provider.get_text_content(photo) == "黑脸琵鹭"
        assert exif_provider.get_display_caption(photo) == "机身型号"
        assert from_file_provider.get_display_caption(photo) == "文件名"
        assert report_provider.get_display_caption(photo) == "鸟种中文名"
    finally:
        set_report_db_row_resolver(None)


def test_template_context_field_options_come_from_provider_definitions() -> None:
    options = get_template_context_field_options()

    assert ("auto", "aperture", "光圈") in options
    assert ("auto", "aesthetic", "美学评分") in options
    assert ("auto", "rating", "星级") in options
    assert ("auto", "pick", "标记") in options
    assert ("auto", "title", "标题") in options
    assert ("auto", "file_created_time", "创建时间") in options
    assert ("auto", "file_modified_time", "修改时间") in options
    assert ("auto", "content_created_time", "内容创建时间") in options
    assert ("auto", "camera_model", "相机型号") in options
    assert ("auto", "resolution_dpi", "分辨率") in options
    assert ("auto", "flash", "闪光灯") in options
    assert ("auto", "white_balance", "白平衡") in options
    assert ("auto", "creator_tool", "内容创作者") in options
    assert ("auto", "file_size", "文件大小") in options
    assert ("auto", "author", "作者") in options
    assert ("editor", "row_number", "列表编号") in options
    assert all(
        data_source in {"auto", "editor"}
        for data_source, _key, _display_label in options
    )
    assert not any(
        data_source in {"report_db", "from_file", "exif"}
        for data_source, _key, _display_label in options
    )


def test_legacy_source_keys_map_to_canonical_meta_fields() -> None:
    assert canonical_meta_field_key("EXIF:Model") == "camera_model"
    assert canonical_meta_field_key("EXIF:LensModel") == "lens_model"
    assert canonical_meta_field_key("EXIF:ExposureTime") == "shutter_speed"
    assert canonical_meta_field_key("report.adj_sharpness") == "sharpness"
    assert canonical_meta_field_key("report.adj_topiq") == "aesthetic"
    assert canonical_meta_field_key("{capture_text}") == "capture_text"


def test_selector_options_keep_editor_provider_as_exception() -> None:
    assert normalize_template_selector_option("auto", "EXIF:Model") == ("auto", "camera_model")
    assert normalize_template_selector_option("exif", "EXIF:LensModel") == ("auto", "lens_model")
    assert normalize_template_selector_option("report_db", "report.adj_topiq") == ("auto", "aesthetic")
    assert normalize_template_selector_option("auto", "bird") == ("auto", "bird_species_cn")
    assert normalize_template_selector_option("auto", "row_number") == ("editor", "row_number")
    assert normalize_template_selector_option("editor", "editor.index") == ("editor", "row_number")


def test_auto_proxy_and_editor_provider_both_support_editor_row_number() -> None:
    photo = EditorPhotoInfo.from_path("/tmp/sample.jpg", editor_row_number=7)
    auto_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "row_number")
    editor_provider = build_template_context_provider(TEMPLATE_SOURCE_EDITOR, "editor.index")
    context = build_template_context(photo)

    assert auto_provider.get_text_content(photo) == "7"
    assert editor_provider.get_text_content(photo) == "7"
    assert context["row_number"] == "7"


def test_from_file_provider_exposes_photo_file_info_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "sample.jpg"
        path.write_bytes(b"x" * 1536)
        modified_timestamp = datetime(2026, 2, 16, 18, 25).timestamp()

        import os

        os.utime(path, (modified_timestamp, modified_timestamp))
        path_stats = path.stat()
        path_created_timestamp = getattr(path_stats, "st_birthtime", path_stats.st_ctime)
        path_created_text = datetime.fromtimestamp(path_created_timestamp).strftime("%Y-%m-%d %H:%M")
        path_modified_text = datetime.fromtimestamp(modified_timestamp).strftime("%Y-%m-%d %H:%M")

        photo = PhotoInfo.from_path(
            path,
            raw_metadata={
                "XMP-dc:Title": "黑脸琵鹭",
                "ImageWidth": 5615,
                "ImageHeight": 3744,
                "XResolution": 350,
                "YResolution": 350,
                "Make": "SONY",
                "Model": "ILCE-1M2",
                "ColorSpace": "RGB",
                "ICC_Profile:ProfileDescription": "Adobe RGB (1998)",
                "FocalLength": 600,
                "EXIF:ISO": 400,
                "Flash": 0,
                "WhiteBalance": 0,
                "XMP-xmp:CreatorTool": "DxO PureRAW 6",
                "Description": "",
                "AlphaChannel": 0,
                "RedEye": False,
                "MeteringMode": "矩阵测光",
                "FNumber": 5.6,
                "ExposureProgram": "快门优先",
                "ExposureTime": "1/1250",
                "LensModel": "Sony FE 600mm F5.6 OSS",
                "EXIF:DateTimeOriginal": "2026:02:16 16:23:00",
            },
        )

        context = build_template_context(photo)
        title_provider = build_template_context_provider("from_file", "title", display_label="标题")
        size_provider = build_template_context_provider("from_file", "dimensions", display_label="尺寸")
        make_provider = build_template_context_provider("from_file", "device_make", display_label="设备制造商")
        model_provider = build_template_context_provider("from_file", "device_model", display_label="设备型号")
        legacy_model_provider = build_template_context_provider("from_file", "camera", display_label="设备型号")
        exposure_provider = build_template_context_provider("from_file", "exposure_time", display_label="曝光时间")
        creator_tool_provider = build_template_context_provider("from_file", "creator_tool", display_label="内容创作者")
        file_size_provider = build_template_context_provider("from_file", "file_size", display_label="文件大小")

        assert context["title"] == "黑脸琵鹭"
        assert context["file_created_time"] == path_created_text
        assert context["file_modified_time"] == path_modified_text
        assert context["content_created_time"] == "2026-02-16 16:23"
        assert context["dimensions"] == "5615x3744"
        assert context["resolution_dpi"] == "350x350"
        assert context["device_make"] == "SONY"
        assert context["device_model"] == "ILCE-1M2"
        assert context["color_space"] == "RGB"
        assert context["profile_description"] == "Adobe RGB (1998)"
        assert context["focal_length"] == "600 毫米"
        assert context["iso"] == "400"
        assert context["flash"] == "否"
        assert context["white_balance"] == "自动"
        assert context["creator_tool"] == "DxO PureRAW 6"
        assert context["file_size"] == "1.5 KB"
        assert context["alpha_channel"] == "否"
        assert context["red_eye"] == "否"
        assert context["metering_mode"] == "矩阵测光"
        assert context["aperture"] == "f/5.6"
        assert context["exposure_program"] == "快门优先"
        assert context["exposure_time"] == "1/1250"
        assert context["lens"] == "Sony FE 600mm F5.6 OSS"

        assert title_provider.get_text_content(photo) == "黑脸琵鹭"
        assert size_provider.get_text_content(photo) == "5615x3744"
        assert make_provider.get_text_content(photo) == "SONY"
        assert model_provider.get_text_content(photo) == "ILCE-1M2"
        assert legacy_model_provider.get_text_content(photo) == "ILCE-1M2"
        assert exposure_provider.get_text_content(photo) == "1/1250"
        assert creator_tool_provider.get_text_content(photo) == "DxO PureRAW 6"
        assert file_size_provider.get_text_content(photo) == "1.5 KB"


def test_auto_proxy_maps_bird_species_cn_to_exif_title_when_report_db_missing() -> None:
    photo = PhotoInfo.from_path(
        "/tmp/sample.jpg",
        raw_metadata={
            "XMP-dc:Title": "黑脸琵鹭",
            "IFD0:XPTitle": "黑脸琵鹭",
        },
    )
    provider = build_template_context_provider("auto", "bird_species_cn")

    assert provider.get_display_caption(photo) == "鸟种中文名"
    assert provider.get_text_content(photo) == "黑脸琵鹭"


def test_auto_proxy_prefers_exif_then_from_file_then_report_db() -> None:
    photo = PhotoInfo.from_path(
        "/tmp/sample.jpg",
        raw_metadata={
            "XMP-dc:Title": "EXIF鸟名",
            "EXIF:ExposureTime": "1/2000",
            "EXIF:ISO": 800,
            "EXIF:FNumber": 8,
            "XMP-xmp:Rating": 5,
            "XMP:City": "0.91",
            "XMP:State": "0.88",
        },
    )
    row = {
        "filename": "sample",
        "bird_species_cn": "Report鸟名",
        "shutter_speed": "1/1250",
        "iso": 400,
        "aperture": "5.6",
        "rating": 3,
        "pick": 1,
        "adj_sharpness": 0.96,
        "adj_topiq": 0.93,
    }

    def _resolver(path: Path) -> dict | None:
        if path.name == "sample.jpg":
            return dict(row)
        return None

    set_report_db_row_resolver(_resolver)
    try:
        bird_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "bird_species_cn")
        shutter_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "shutter_speed")
        iso_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "iso")
        aperture_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "aperture")
        rating_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "rating")
        pick_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "pick")
        sharpness_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "sharpness")
        aesthetic_provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "aesthetic")
        context = build_template_context(photo)

        assert bird_provider.get_text_content(photo) == "EXIF鸟名"
        assert shutter_provider.get_text_content(photo) == "1/2000"
        assert iso_provider.get_iso_text(photo) == "800"
        assert aperture_provider.get_aperture_text(photo) == "f/8"
        assert rating_provider.get_rating_text(photo) == "5"
        assert pick_provider.get_flag_text(photo) == "1"
        assert sharpness_provider.get_sharpness_text(photo) == "0.91"
        assert aesthetic_provider.get_aesthetic_text(photo) == "0.88"
        assert context["bird_species_cn"] == "EXIF鸟名"
        assert context["bird"] == "EXIF鸟名"
        assert context["shutter_speed"] == "1/2000"
        assert context["iso"] == "800"
        assert context["rating"] == "5"
        assert context["filename"] == "sample.jpg"
        assert context["report.filename"] == "sample"
    finally:
        set_report_db_row_resolver(None)


def test_auto_proxy_prefers_from_file_before_report_db_when_exif_missing() -> None:
    row = {
        "filename": "report-stem",
    }

    def _resolver(path: Path) -> dict | None:
        if path.name == "sample.jpg":
            return dict(row)
        return None

    set_report_db_row_resolver(_resolver)
    try:
        photo = PhotoInfo.from_path("/tmp/sample.jpg", raw_metadata={})
        provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "filename")
        context = build_template_context(photo)

        assert provider.get_text_content(photo) == "sample.jpg"
        assert context["filename"] == "sample.jpg"
        assert context["report.filename"] == "report-stem"
    finally:
        set_report_db_row_resolver(None)


def test_auto_proxy_prefers_sidecar_before_report_db() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "sample.jpg"
        path.write_bytes(b"x")
        (Path(tmp_dir) / "sample.xmp").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about="">
      <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">
        <rdf:Alt>
          <rdf:li xml:lang="x-default">Sidecar 鸟名</rdf:li>
        </rdf:Alt>
      </dc:title>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
""",
            encoding="utf-8",
        )
        row = {
            "filename": "sample",
            "bird_species_cn": "Report 鸟名",
        }

        def _resolver(current_path: Path) -> dict | None:
            if current_path.name == "sample.jpg":
                return dict(row)
            return None

        set_report_db_row_resolver(_resolver)
        try:
            photo = PhotoInfo.from_path(
                path,
                raw_metadata={"XMP-dc:Title": "Embedded 鸟名"},
            )
            provider = build_template_context_provider(TEMPLATE_SOURCE_AUTO, "bird_species_cn")
            context = build_template_context(photo)

            assert provider.get_text_content(photo) == "Sidecar 鸟名"
            assert context["bird_species_cn"] == "Sidecar 鸟名"
            assert context["bird"] == "Sidecar 鸟名"
            assert context["report.bird_species_cn"] == "Report 鸟名"
        finally:
            set_report_db_row_resolver(None)


def test_exif_provider_prefers_sidecar_xmp_before_embedded_exif() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "sample.jpg"
        path.write_bytes(b"x")
        xmp_path = Path(tmp_dir) / "sample.xmp"
        xmp_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description
      rdf:about=""
      xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
      tiff:Model="Sidecar Camera">
      <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">
        <rdf:Alt>
          <rdf:li xml:lang="x-default">Sidecar Title</rdf:li>
        </rdf:Alt>
      </dc:title>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
""",
            encoding="utf-8",
        )
        photo = PhotoInfo.from_path(
            path,
            raw_metadata={
                "EXIF:Model": "Embedded Camera",
                "XMP-dc:Title": "Embedded Title",
            },
        )

        canonical_provider = build_template_context_provider("exif", "camera_model")
        direct_provider = build_template_context_provider("exif", "EXIF:Model")
        auto_title_provider = build_template_context_provider("auto", "title")

        assert canonical_provider.get_text_content(photo) == "Sidecar Camera"
        assert direct_provider.get_text_content(photo) == "Sidecar Camera"
        assert auto_title_provider.get_text_content(photo) == "Sidecar Title"


def test_exif_provider_reads_superpicky_sidecar_report_only_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "sample.jpg"
        path.write_bytes(b"x")
        (Path(tmp_dir) / "sample.xmp").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description
      rdf:about=""
      xmlns:superpicky="https://superbirdtools.local/xmp/superpicky/1.0/"
      superpicky:has_bird="0"
      superpicky:confidence="0.88"
      superpicky:burst_id="12"
      superpicky:rarity_index="8.5"
      superpicky:iucn_category="LC"
      superpicky:gbif_rarity_100="72.25" />
  </rdf:RDF>
</x:xmpmeta>
""",
            encoding="utf-8",
        )
        photo = PhotoInfo.from_path(path)
        context = build_template_context(photo)

        assert context["has_bird"] == "0"
        assert context["confidence"] == "0.88"
        assert context["burst_id"] == "12"
        assert context["rarity_index"] == "8.5"
        assert context["iucn_category"] == "LC"
        assert context["gbif_rarity_100"] == "72.25"
        assert context["report.has_bird"] == "0"
        assert context["report.confidence"] == "0.88"
        assert context["report.rarity_index"] == "8.5"
        assert context["report.iucn_category"] == "LC"
        assert context["report.gbif_rarity_100"] == "72.25"
        assert build_template_context_provider("auto", "has_bird").get_text_content(photo) == "0"
        assert build_template_context_provider("auto", "confidence").get_text_content(photo) == "0.88"
        assert build_template_context_provider("auto", "burst_id").get_text_content(photo) == "12"
        assert build_template_context_provider("auto", "rarity_index").get_text_content(photo) == "8.5"
        assert build_template_context_provider("auto", "iucn_category").get_text_content(photo) == "LC"
        assert build_template_context_provider("auto", "gbif_rarity_100").get_text_content(photo) == "72.25"


def test_auto_proxy_returns_na_when_all_sources_are_missing() -> None:
    photo = PhotoInfo.from_path(
        "/tmp/sample.jpg",
        raw_metadata={"SourceFile": "/tmp/sample.jpg"},
    )
    provider = build_template_context_provider("auto", "not_a_real_meta_field")

    assert provider.get_text_content(photo) == MISSING_TEMPLATE_TEXT


def test_auto_proxy_route_definitions_are_loaded_from_resource_json() -> None:
    routes = AutoProxyTemplateContextProvider.route_definitions()

    assert "bird_species_cn" in routes
    assert routes["bird_species_cn"][0].provider_id == "exif"
    assert routes["bird_species_cn"][1].provider_id == "from_file"
    assert routes["bird_species_cn"][2].provider_id == "report_db"
    assert "XMP-superpicky:bird_species_cn" in routes["bird_species_cn"][0].candidate_keys
    assert "XMP-dc:Title" in routes["bird_species_cn"][0].candidate_keys
