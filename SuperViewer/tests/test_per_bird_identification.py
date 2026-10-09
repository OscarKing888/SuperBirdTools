"""真实图片/中文 XMP 回读，服务与模型使用离线替身。"""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from bird_sharpness.image_source import AnalysisImage
from bird_sharpness.params import AnalysisParams
from SuperViewer.superviewer.bird_identification import BirdIDClient, BirdIDOptions
from SuperViewer.superviewer import per_bird_identification as core


class Analyzer:
    def __init__(self, boxes, *, crop=None):
        rgb = np.zeros((240, 320, 3), dtype=np.uint8)
        rgb[:, :160, 0] = 255
        rgb[:, 160:, 1] = 255
        self.image = AnalysisImage(rgb, rgb[..., 0].astype(np.float32) / 255, False, crop)
        self.params = AnalysisParams()
        self.boxes = boxes
        self.calls = 0

    def load(self): pass
    def image_loader(self): return lambda path: self.image
    def analyze(self, path, *, image_loader, cancelled):
        assert image_loader(path) is self.image
        self.calls += 1
        return SimpleNamespace(ok=True, version="test", birds=[
            {"index": i, "box": box, "confidence": .8} for i, box in enumerate(self.boxes)])


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "中文鸟片.jpg"
    Image.new("RGB", (320, 240), "white").save(path)
    return path


def response(name="白鹭", score=95):
    return {"success": True, "results": [{"cn_name": name, "en_name": "Egret", "confidence": score}]}


def client(monkeypatch, fn=None, **options):
    obj = BirdIDClient(BirdIDOptions(**options))
    monkeypatch.setattr(obj, "recognize_crop", fn or (lambda _: response()))
    return obj


def test_each_crop_boundaries_filter_and_unicode_roundtrip(photo, monkeypatch):
    analyzer = Analyzer([(0, 0, 63, 100), (0, 0, 100, 63), (0, 0, 64, 64), (160, 80, 320, 240)])
    seen = []
    def recognize(path):
        with Image.open(path) as image:
            seen.append((path, image.size, image.getpixel((5, 5))))
        return response("白鹭" if len(seen) == 1 else "苍鹭", 95 if len(seen) == 1 else 30)
    store = PhotoMetaDataXMP()
    assert store.write(str(photo), {"XMP-dc:Title": "整图鸟名", "XMP-dc:Description": "中文说明", "XMP-xmp:Rating": 4})
    before = photo.read_bytes()
    result = core.identify_individuals(photo, client(monkeypatch, recognize), analyzer)
    assert result.status == "success", result.message
    meta = store.read(str(photo))
    birds = core.read_individuals(meta)
    assert [b["status"] for b in birds] == ["skipped", "skipped", "confirmed", "candidate"]
    assert [v[1] for v in seen] == [(64, 64), (160, 160)]
    assert seen[0][2][0] > 240 and seen[1][2][1] > 240
    assert birds[2]["cn_name"] == "白鹭" and birds[3]["cn_name"] == "苍鹭"
    assert birds[3]["box"] == pytest.approx([.5, 1/3, 1, 1])
    assert meta["Title"] == "整图鸟名" and meta["Description"] == "中文说明" and meta["rating"] == 4
    assert photo.read_bytes() == before
    assert all(not Path(v[0]).parent.exists() for v in seen)
    assert "白鹭" in photo.with_suffix(".xmp").read_text(encoding="utf-8")
    # 通过文件浏览器正常元数据读取后仍能呈现列表。
    from app_common.file_browser._workers import MetadataLoader
    assert core.read_individuals(MetadataLoader([str(photo)], meta_proxy=object())._parse_rec(meta)) == birds


def test_padding_clipped_and_raw_camera_mapping(photo, monkeypatch):
    seen = []
    def recognize(path):
        with Image.open(path) as image: seen.append(image.size)
        return response()
    analyzer = Analyzer([(0, 0, 80, 100)], crop=(.1, .1, .9, .9))
    result = core.identify_individuals(photo, client(monkeypatch, recognize), analyzer, core.PerBirdOptions(padding_percent=10))
    assert result.status == "success", result.message
    record = core.read_individuals(result.updates)[0]
    assert seen == [(88, 110)]
    assert record["box_px"] == [0, 0, 80, 100]
    assert record["box"] == pytest.approx([-.125, -.125, .1875, (100/240-.1)/.8])
    from app_common.raw_preview_geometry import map_camera_focus_box
    assert map_camera_focus_box(record["box"], analyzer.image.camera_crop) == pytest.approx([0, 0, .25, 100/240])


@pytest.mark.parametrize("extension", [".HIF", ".HEIC", ".heif", ".png", ".tiff", ".jpg"])
def test_source_formats_are_decoded_once_and_sent_as_jpg(tmp_path, monkeypatch, extension):
    from bird_sharpness.image_source import load_embedded_jpeg

    source = tmp_path / f"中文鸟片{extension}"
    with Image.new("RGB", (320, 240), "red") as image:
        if extension.lower() in {".hif", ".heic", ".heif"}:
            pillow_heif = pytest.importorskip("pillow_heif")
            pillow_heif.from_pillow(image).save(source, quality=95)
        else:
            image.save(source)
    original = source.read_bytes()
    analyzer = Analyzer([(0, 0, 80, 100), (160, 80, 320, 240)])
    decoded, requested = [], []

    def load(path):
        decoded.append(path)
        analyzer.image = load_embedded_jpeg(path)
        return analyzer.image

    def recognize(path):
        requested.append(Path(path))
        assert Path(path).suffix == ".jpg"
        assert Path(path) != source
        with Image.open(path) as crop:
            assert crop.format == "JPEG" and crop.mode == "RGB"
            assert crop.getpixel((5, 5))[0] > 240
        return response()

    monkeypatch.setattr(analyzer, "image_loader", lambda: load)
    assert core.collect_paths([str(source)]) == [str(source)]
    result = core.identify_individuals(source, client(monkeypatch, recognize), analyzer)
    assert result.status == "success", result.message
    assert decoded == [str(source)] and analyzer.calls == 1
    assert len(requested) == 2
    assert all(not path.parent.exists() for path in requested)
    assert source.read_bytes() == original
    metadata = PhotoMetaDataXMP().read(str(source))
    assert [bird["cn_name"] for bird in core.read_individuals(metadata)] == ["白鹭", "白鹭"]
    assert json.loads(metadata[core.INFO_FIELD])["source_extension"] == extension.lower()


@pytest.mark.parametrize("filename, message", [
    ("DSC09382.HIF", "找不到照片原文件"),
    ("unsupported.txt", "不支持的照片格式"),
])
def test_invalid_source_reports_reason_and_full_path(tmp_path, monkeypatch, filename, message):
    source = tmp_path / filename
    if source.suffix == ".txt":
        source.write_text("not a photo", encoding="utf-8")
    analyzer = Analyzer([])
    result = core.identify_individuals(source, client(monkeypatch), analyzer)
    assert result.status == "failed"
    assert message in result.message and str(source) in result.message
    assert analyzer.calls == 0 and not source.with_suffix(".xmp").exists()


def test_species_snapshot_selected_candidate_and_legacy_roundtrip(photo, monkeypatch):
    selected = {"cn_name": "白鹭", "en_name": "Egret", "confidence": 40,
                "scientific_name": "Egretta garzetta", "description": "白色鹭鸟",
                "gbif_rarity_100": 0, "iucn_category": "LC", "pinyin_name": "bái lù"}
    payload = {"success": True, "results": [response("苍鹭", 20)["results"][0], selected],
               "model_version": "离线测试", "all_results": [selected]}
    result = core.identify_individuals(photo, client(monkeypatch, lambda _: payload),
                                      Analyzer([(0, 0, 100, 100)]))
    assert result.status == 'success', result.message
    meta = PhotoMetaDataXMP().read(str(photo))
    item = core.read_individuals(meta)[0]
    assert item['status'] == 'candidate' and item['selected_candidate_index'] == 1
    assert item['response'] == payload
    assert item['description'] == '白色鹭鸟'
    assert item['gbif_rarity_100'] == 0 and item['iucn_category'] == 'LC'
    assert item['china_protection_level'] is None
    info = json.loads(meta[core.INFO_FIELD])
    assert info['recognized_at'].endswith('+00:00')
    assert info['recognition_request']['use_yolo'] is False
    legacy = {k: v for k, v in item.items() if k not in core.species_snapshot(selected)}
    assert core.individual_species_metadata(legacy) == core.species_snapshot(selected)
    # 显式缺失不能回填；旧响应中另一鸟种的属性也不能串到本行。
    legacy['gbif_rarity_100'] = None
    assert core.individual_species_metadata(legacy)['gbif_rarity_100'] is None
    legacy['cn_name'] = '其它鸟'
    assert core.individual_species_metadata(legacy)['iucn_category'] is None


def test_rotated_original_is_cropped_in_display_coordinates(photo, monkeypatch):
    from bird_sharpness.image_source import load_analysis_image
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (240, 320), 'red').save(photo, exif=exif)
    analyzer = Analyzer([(160, 80, 320, 240)])
    analyzer.image = load_analysis_image(str(photo))
    assert analyzer.image.rgb8.shape[:2] == (240, 320)
    sizes = []
    def recognize(path):
        with Image.open(path) as crop:
            sizes.append(crop.size)
            assert crop.getexif().get(274, 1) == 1
        return response()
    result = core.identify_individuals(photo, client(monkeypatch, recognize), analyzer)
    assert result.status == 'success', result.message
    assert sizes == [(160, 160)]
    assert core.read_individuals(result.updates)[0]['box'] == pytest.approx([.5, 1/3, 1, 1])


@pytest.mark.parametrize("change", ["cancel", "edit", "replace", "rename", "write_failure"])
def test_cancel_stale_and_write_failure_preserve_previous(photo, monkeypatch, change):
    store = PhotoMetaDataXMP()
    assert store.write_superpicky_fields(str(photo), {core.FIELD: '[{"old":"旧结果"}]'})
    sidecar = photo.with_suffix(".xmp")
    old = sidecar.read_bytes()
    obj = client(monkeypatch)
    paths = []
    def recognize(path):
        paths.append(path)
        if change == "cancel": obj.cancel()
        if change == "edit": store.write_title(str(photo), "用户编辑")
        if change == "replace": photo.write_bytes(b"replacement")
        if change == "rename": photo.rename(photo.with_name("已移动.jpg"))
        return response()
    monkeypatch.setattr(obj, "recognize_crop", recognize)
    if change == "write_failure":
        monkeypatch.setattr(PhotoMetaDataXMP, "_write_tree_atomic", staticmethod(lambda *_: False))
    result = core.identify_individuals(photo, obj, Analyzer([(0, 0, 100, 100)]))
    assert result.status == ("cancelled" if change == "cancel" else "failed" if change == "write_failure" else "skipped")
    if change != "edit": assert sidecar.read_bytes() == old
    else: assert store.read(str(photo))["Title"] == "用户编辑"
    assert all(not Path(p).parent.exists() for p in paths)


def test_one_bird_failure_continues_and_all_failure_preserves(photo, monkeypatch):
    calls = []
    def recognize(path):
        calls.append(path)
        if len(calls) == 1: raise RuntimeError("离线服务错误")
        return response()
    analyzer = Analyzer([(0, 0, 80, 100), (160, 80, 320, 240)])
    result = core.identify_individuals(photo, client(monkeypatch, recognize), analyzer)
    assert result.status == "partial", result.message
    assert [b["status"] for b in core.read_individuals(result.updates)] == ["failed", "confirmed"]
    assert result.response["individuals"] == core.read_individuals(result.updates)
    old = photo.with_suffix(".xmp").read_bytes()
    result = core.identify_individuals(photo, client(monkeypatch, lambda _: {"success": False, "error": "失败"}), analyzer)
    assert result.status == "failed" and photo.with_suffix(".xmp").read_bytes() == old
    assert [bird["status"] for bird in result.response["individuals"]] == ["failed", "failed"]


def test_no_birds_replaces_old_list_skip_and_corrupt_xmp(photo, monkeypatch):
    obj = client(monkeypatch)
    assert core.identify_individuals(photo, obj, Analyzer([(0, 0, 100, 100)])).status == "success"
    analyzer = Analyzer([])
    result = core.identify_individuals(photo, client(monkeypatch, skip_existing=True), analyzer)
    assert result.status == "skipped" and analyzer.calls == 0
    assert result.response["individuals"][0]["cn_name"] == "白鹭"
    result = core.identify_individuals(photo, obj, analyzer)
    assert result.status == "success" and json.loads(PhotoMetaDataXMP().read(str(photo))[core.FIELD]) == []
    assert result.response["individuals"] == []
    photo.with_suffix(".xmp").write_text("<broken", encoding="utf-8")
    assert core.identify_individuals(photo, obj, analyzer).status == "failed"
    assert photo.with_suffix(".xmp").read_text() == "<broken"


def test_crop_request_disables_redetection_and_gps(monkeypatch):
    obj = BirdIDClient(BirdIDOptions())
    seen = []
    monkeypatch.setattr(obj, "_request", lambda endpoint, payload: seen.append((endpoint, payload)))
    obj.recognize_crop("临时图.jpg")
    assert seen[0][0] == "/recognize"
    assert seen[0][1]["use_yolo"] is False and seen[0][1]["use_gps"] is False
    assert seen[0][1]["top_k"] == 3


def test_reuses_formal_analyzer_and_keeps_detection_params():
    analyzer = core.make_analyzer({"max_birds": 3, "min_bird_side": 200, "image_source": "jpeg", "enh_mode": "nobird"})
    assert analyzer.max_birds == 0 and analyzer.params.min_bird_side == 0
    assert analyzer.params.image_source == "jpeg" and analyzer.params.enhanced.mode == "nobird"


@pytest.mark.parametrize("value", ["[]", "null", "bad", '{"schema":2}', '{"schema":1}'])
def test_bad_saved_metadata_is_not_a_highlight(value):
    assert core.read_individuals({core.FIELD: value, core.INFO_FIELD: value}) == []


def test_cli_uses_same_core(photo, monkeypatch, capsys):
    monkeypatch.setattr(BirdIDClient, "health", lambda _: {})
    monkeypatch.setattr(BirdIDClient, "recognize_crop", lambda *_: response())
    monkeypatch.setattr(core, "make_analyzer", lambda *_: Analyzer([(0, 0, 100, 100)]))
    assert core.main([str(photo)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"
