from datetime import datetime
from pathlib import Path
import tempfile

from birdstamp.gui.template_context import (
    AutoProxyTemplateContextProvider,
    PhotoInfo,
    TEMPLATE_SOURCE_EXIF,
    TEMPLATE_SOURCE_FROM_FILE,
    TEMPLATE_SOURCE_REPORT_DB,
    build_template_context,
    build_template_context_provider,
    get_template_context_field_options,
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

    assert ("report_db", "bird_species_cn", "鸟种中文名") in options
    assert ("from_file", "title", "标题") in options
    assert ("from_file", "file_created_time", "创建时间") in options
    assert ("from_file", "file_modified_time", "修改时间") in options
    assert ("from_file", "content_created_time", "内容创建时间") in options
    assert ("from_file", "device_model", "设备型号") in options
    assert ("from_file", "resolution_dpi", "分辨率") in options
    assert ("from_file", "iso", "ISO感光度") in options
    assert ("from_file", "flash", "闪光灯") in options
    assert ("from_file", "white_balance", "白平衡") in options
    assert ("from_file", "creator_tool", "内容创作者") in options
    assert ("from_file", "file_size", "文件大小") in options
    assert ("from_file", "author", "作者") in options
    assert ("exif", "EXIF:Model", "机身型号 (EXIF)") in options
    assert not any(
        data_source == "from_file" and key in {"bird", "{bird}"}
        for data_source, key, _display_label in options
    )


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


def test_auto_proxy_route_definitions_are_loaded_from_resource_json() -> None:
    routes = AutoProxyTemplateContextProvider.route_definitions()

    assert "bird_species_cn" in routes
    assert routes["bird_species_cn"][0].provider_id == "exif"
    assert "XMP-dc:Title" in routes["bird_species_cn"][0].candidate_keys
