from pathlib import Path

from birdstamp.gui.template_context import (
    AutoProxyTemplateContextProvider,
    PhotoInfo,
    TEMPLATE_SOURCE_AUTO,
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

        assert exif_provider.id == TEMPLATE_SOURCE_AUTO
        assert from_file_provider.id == TEMPLATE_SOURCE_AUTO
        assert report_provider.id == TEMPLATE_SOURCE_AUTO
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

    assert ("auto", "bird_species_cn", "鸟种中文名") in options
    assert ("auto", "{author}", "作者") in options
    assert ("auto", "EXIF:Model", "机身型号 (EXIF)") in options


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
