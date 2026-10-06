"""鸟名拼音：只读 report 兼容、带声调查表、XMP 优先及模板/导出一致性。"""
from __future__ import annotations

import json
import sqlite3

import pytest
from PIL import Image

from birdstamp.config import resolve_bundled_path
from birdstamp.gui import template_context as context
from birdstamp.meta import pinyin_names


@pytest.fixture(autouse=True)
def isolate_caches(monkeypatch):
    monkeypatch.setattr(context, "_REPORT_DB_ROW_RESOLVER", None)
    pinyin_names.reset_cache()
    yield
    pinyin_names.reset_cache()


@pytest.mark.parametrize("name,expected", [
    (" 家燕 ", "jiā yàn"),
    ("黑喉小䴙䴘", "hēi hóu xiǎo pì tī"),
    ("绿林戴胜", "lǜ lín dài shèng"),
    ("中杓鹬", "zhōng sháo yù"),
    ("秘鲁企鹅", "bì lǔ qǐ é"),
    ("未收录的鸟名", ""),
    (None, ""),
])
def test_shipped_pinyin_preserves_tones_and_bird_specific_readings(name, expected):
    assert pinyin_names.pinyin_for(name) == expected


@pytest.mark.parametrize("contents", [None, "broken JSON", "[]", '{"家燕": 12}'])
def test_missing_or_bad_table_is_cached_and_does_not_break_rendering(tmp_path, monkeypatch, contents):
    path = tmp_path / "拼音表.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    paths = []

    def resource(*parts):
        paths.append(parts)
        return path

    monkeypatch.setattr(pinyin_names, "resolve_bundled_path", resource)
    assert pinyin_names.pinyin_for("家燕") == ""
    assert pinyin_names.pinyin_for("家燕") == ""
    assert paths == [("config", "pinyin_toned.json")]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_pinyin_resource_lookup_in_frozen_windows_and_macos_layouts(tmp_path, monkeypatch, platform):
    from birdstamp import config

    if platform == "darwin":
        executable = tmp_path / "SuperBirdStamp.app" / "Contents" / "MacOS" / "SuperBirdStamp"
        resources = executable.parent.parent / "Resources"
    else:
        executable = tmp_path / "SuperBirdStamp" / "SuperBirdStamp.exe"
        resources = executable.parent / "_internal"
    table = resources / "config" / "pinyin_toned.json"
    table.parent.mkdir(parents=True)
    table.write_text('{"家燕": "jiā yàn"}', encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(config.sys, "frozen", True, raising=False)
        patch.setattr(config.sys, "platform", platform)
        patch.setattr(config.sys, "executable", str(executable))
        patch.setattr(config.sys, "_MEIPASS", str(tmp_path / "frameworks"), raising=False)
        resolved = config.resolve_bundled_path("config", "pinyin_toned.json")
        text = pinyin_names.pinyin_for("家燕")
    assert resolved == table
    assert text == "jiā yàn"


@pytest.mark.parametrize("column", [None, "bird_species_pinyin", "bird_pinyin", "pinyin_name", "pinyin"])
@pytest.mark.parametrize("stem", ["鸟", "DSC_001"])
def test_legacy_report_and_optional_pinyin_columns_are_read_only(tmp_path, monkeypatch, column, stem):
    from app_common.exif_io import PhotoMetaDataReportDB
    from app_common.report_db import ReportDB

    folder = tmp_path / "中文照片"
    report = folder / ".superpicky" / "report.db"
    report.parent.mkdir(parents=True)
    path = folder / f"{stem}.jpg"
    path.write_bytes(b"metadata only")
    with sqlite3.connect(report) as db:
        db.execute("CREATE TABLE photos (filename TEXT, current_path TEXT, bird_species_cn TEXT)")
        db.execute("INSERT INTO photos VALUES (?, ?, ?)", (stem, path.name, "家燕"))
        if column:
            # 列名来自上面的固定参数，模拟其他版本已保存拼音的 report。
            db.execute(f"ALTER TABLE photos ADD COLUMN {column} TEXT")
            db.execute(f"UPDATE photos SET {column} = ?", ("  yǐ yǒu pīn yīn  ",))
    before = report.read_bytes()
    readonly = ReportDB.open_db_path_if_exists(str(report))
    assert readonly is not None
    try:
        rows = readonly.get_all_photos()
    finally:
        readonly.close()
    provider = PhotoMetaDataReportDB(cache={
        str(i): dict(row, _report_root_dir=str(folder)) for i, row in enumerate(rows)
    })
    monkeypatch.setattr(context, "_REPORT_DB_ROW_RESOLVER", lambda p: provider.row_for(str(p)))
    photo = context.PhotoInfo(path, raw_metadata={}, metadata_is_snapshot=True)
    expected = "yǐ yǒu pīn yīn" if column else "jiā yàn"
    assert context.AutoProxyTemplateContextProvider("bird_species_pinyin").get_text_content(photo) == expected
    assert context.build_template_context(photo)["bird_pinyin"] == expected
    assert report.read_bytes() == before
    assert not path.with_suffix(".xmp").exists()


def test_xmp_species_correction_changes_lookup_and_explicit_pinyin_wins(tmp_path, monkeypatch):
    row = {"bird_species_cn": "家燕"}
    monkeypatch.setattr(context, "_REPORT_DB_ROW_RESOLVER", lambda _: row)
    original = context.PhotoInfo(tmp_path / "photo.jpg", raw_metadata={"XMP-superpicky:bird_species_cn": "绿林戴胜"})
    provider = context.AutoProxyTemplateContextProvider("bird_pinyin")
    photo = context.preview_photo_info(original)
    assert provider.get_text_content(photo) == "lǜ lín dài shèng"
    assert context.build_template_context(photo)["pinyin"] == "lǜ lín dài shèng"
    assert context.AutoProxyTemplateContextProvider.build_context_entries(photo)["pinyin_name"] == "lǜ lín dài shèng"
    original.raw_metadata["XMP-superpicky:bird_species_cn"] = "秘鲁企鹅"
    assert provider.get_text_content(context.preview_photo_info(original)) == "bì lǔ qǐ é"
    row["bird_species_pinyin"] = "report 拼音"
    assert provider.get_text_content(context.preview_photo_info(original)) == "report 拼音"
    original.raw_metadata["XMP-superpicky:bird_species_pinyin"] = "显式 pīn yīn"
    monkeypatch.setattr(context, "pinyin_for", lambda _: pytest.fail("explicit pinyin must not query the table"))
    latest = context.preview_photo_info(original)
    assert provider.get_text_content(latest) == "显式 pīn yīn"
    assert context.build_template_context(latest)["bird_pinyin"] == "显式 pīn yīn"


def test_real_utf8_sidecar_pinyin_overrides_report_and_file(tmp_path, monkeypatch):
    path = tmp_path / "绿林戴胜.jpg"
    with Image.new("RGB", (8, 6)) as image:
        image.save(path)
    sidecar = path.with_suffix(".xmp")
    sidecar.write_text('''<x:xmpmeta xmlns:x="adobe:ns:meta/">
      <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
        <rdf:Description rdf:about=""
          xmlns:superpicky="https://superbirdtools.local/xmp/superpicky/1.0/"
          superpicky:bird_species_cn="绿林戴胜"
          superpicky:bird_species_pinyin="lǜ lín dài shèng" />
      </rdf:RDF></x:xmpmeta>''', encoding="utf-8")
    before = path.read_bytes(), sidecar.read_bytes()
    monkeypatch.setattr(context, "_REPORT_DB_ROW_RESOLVER", lambda _: {"bird_species_pinyin": "旧拼音"})
    photo = context.PhotoInfo.from_path(path, raw_metadata={"bird_species_pinyin": "file pinyin"})
    assert context.AutoProxyTemplateContextProvider("bird_pinyin").get_text_content(photo) == "lǜ lín dài shèng"
    assert context.build_template_context(photo)["bird_pinyin"] == "lǜ lín dài shèng"
    assert (path.read_bytes(), sidecar.read_bytes()) == before


def test_field_selector_aliases_placeholder_missing_and_diagnostic(tmp_path, monkeypatch):
    assert ("auto", "bird_species_pinyin", "鸟种拼音") in context.get_template_context_field_options()
    for alias in ("bird_species_pinyin", "bird_pinyin", "pinyin_name", "pinyin", "report.pinyin_name"):
        assert context.normalize_template_selector_option("report_db", alias) == ("auto", "bird_species_pinyin")
    routes = json.loads(resolve_bundled_path("config", "template_context_routes.json").read_text(encoding="utf-8"))
    assert routes["bird_species_pinyin"] == context._FALLBACK_AUTO_PROXY_ROUTE_CONFIG["bird_species_pinyin"]
    monkeypatch.setattr(context, "_REPORT_DB_ROW_RESOLVER", lambda _: {"bird_species_cn": "家燕"})
    photo = context.PhotoInfo(tmp_path / "photo.jpg", raw_metadata={}, metadata_is_snapshot=True)
    provider = context.AutoProxyTemplateContextProvider("bird_pinyin")
    assert context.FromFileTemplateContextProvider("{bird} / {bird_pinyin}").get_text_content(photo) == "家燕 / jiā yàn"
    assert provider.inspect_candidates(photo)[-1].provider_id == "bird_name_lookup"
    assert provider.inspect_candidates(photo)[-1].text_content == provider.get_text_content(photo) == "jiā yàn"
    monkeypatch.setattr(context, "_REPORT_DB_ROW_RESOLVER", None)
    unknown = context.PhotoInfo(tmp_path / "unknown.jpg", raw_metadata={"Title": "未收录的鸟名"}, metadata_is_snapshot=True)
    assert provider.get_text_content(unknown) == context.MISSING_TEMPLATE_TEXT
    assert context.build_template_context(unknown)["bird_pinyin"] == ""


def test_pinyin_metadata_overlay_matches_literal_render_and_template_roundtrip(tmp_path):
    from birdstamp.gui.editor_template import load_template_payload, render_template_overlay, save_template_payload
    from birdstamp.overlays.model import new_item

    item = new_item("text", metadata=True)
    item.update(text_source={"type": "auto", "key": "bird_species_pinyin"}, font_size=100)
    payload = {"overlay_version": 1, "overlays": [item]}
    template_path = tmp_path / "鸟种拼音.json"
    save_template_payload(template_path, payload)
    loaded = load_template_payload(template_path)
    photo = context.PhotoInfo(tmp_path / "photo.jpg", raw_metadata={"Title": "绿林戴胜"}, metadata_is_snapshot=True)
    with Image.new("RGB", (1200, 800), "#456789") as source:
        actual = render_template_overlay(source, template_payload=loaded, raw_metadata=photo.raw_metadata, metadata_context={}, photo_info=photo)
        loaded["overlays"][0].update(text_mode="literal", text="lǜ lín dài shèng")
        expected = render_template_overlay(source, template_payload=loaded, raw_metadata=photo.raw_metadata, metadata_context={}, photo_info=photo)
        try:
            assert actual.tobytes() == expected.tobytes()
            assert actual.tobytes() != source.tobytes()
        finally:
            actual.close()
            expected.close()


def test_cli_inspects_pinyin_fallback_without_gui(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from birdstamp import cli

    path = tmp_path / "家燕.jpg"
    path.write_bytes(b"metadata only")
    monkeypatch.setattr(cli, "extract_metadata_with_xmp_priority", lambda *a, **kw: {"Title": "家燕"})
    result = CliRunner().invoke(cli.app, ["inspect-auto-proxy", str(path), "bird_species_pinyin", "--use-exiftool", "off"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["text_content"] == "jiā yàn"
    assert payload["candidates"][-1]["provider_id"] == "bird_name_lookup"
