from SuperViewer.superviewer.image_info_tab_image_info import _metadata_comment
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo
import pytest


def test_metadata_comment_accepts_parsed_file_list_comment_field() -> None:
    assert _metadata_comment({"comment": "sidecar note"}) == "sidecar note"


def test_basic_info_includes_camera_and_analysis_metadata(tmp_path) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"not an actual image")

    class DummyPanel:
        def _image_size(self, path: str, *, metadata=None):
            return (4000, 3000)

        def _load_metadata(self, path: str) -> dict:
            return {}

    info = ImageInfoTabPanel_ImageInfo._load_basic_info(
        DummyPanel(),
        str(photo),
        metadata={
            "rating": 4,
            "report.shutter_speed": "0.0005",
            "XMP-superpicky:aperture": "5.6",
            "EXIF:ISO": "800",
            "FocalLength": "600",
            "XMP-tiff:Model": "Alpha 1",
            "XMP-aux:LensModel": "FE 600mm F4 GM OSS",
            "EXIF:DateTimeOriginal": "2026:02:16 16:23:00",
            "burst_position": 3,
            "burst_id": 12,
            "report.adj_sharpness": 0.96,
            "XMP-superpicky:adj_topiq": "0.83",
            "focus_status": "BEST",
        },
    )

    assert info["评分"] == "★★★★☆"
    assert info["尺寸"] == "4000 × 3000"
    assert info["拍摄时间"] == "2026/02/16 16:23"
    assert info["快门"] == "1/2000s"
    assert info["光圈"] == "f/5.6"
    assert info["ISO"] == "800"
    assert info["焦距"] == "600mm"
    assert info["相机"] == "Alpha 1"
    assert info["镜头"] == "FE 600mm F4 GM OSS"
    assert info["连拍"] == "(3/12)"
    assert info["锐度"] == "0.96"
    assert info["美学"] == "0.83"
    assert info["对焦"] == "精焦"


def test_report_metadata_rows_include_superpicky_v10_fields() -> None:
    from SuperViewer.superviewer.exif_helpers import build_report_metadata_rows

    report = {
        "picked": 1,
        "aesthetic_index": 78.5,
        "alt_species_cn": "白鹭",
        "alt_species_en": "Little Egret",
        "alt_confidence": 0.63,
    }
    rows = {row[3]: row[5] for row in build_report_metadata_rows(report)}
    for key, value in report.items():
        assert rows[key] == value


@pytest.mark.parametrize("values, expected", [
    ((923, 500, 711.5), ("923", "500", "711.5")),
    ((0, 0, 0), ("0", "0", "0.0")),
    ((None, 450, None), ("—", "450", "—")),
    ((None, None, None), ("—", "—", "—")),
])
def test_part_scores_follow_shared_metadata_display_without_inventing_missing_scores(tmp_path, values, expected):
    photo = tmp_path / "bird.jpg"
    photo.write_bytes(b"photo")
    panel = type("Panel", (), {"_image_size": lambda *_args, **_kwargs: (4000, 3000)})()
    metadata = {"bird_sharpness_verdict": "sharp", "bird_sharpness_sigma": 0.6,
                **{f"XMP-superpicky:bird_sharpness_{name}_score": value
                   for name, value in zip(("head", "body", "mean"), values)}}
    info = ImageInfoTabPanel_ImageInfo._load_basic_info(panel, str(photo), metadata=metadata)
    assert tuple(info[key] for key in ("头部清晰度", "身体清晰度", "平均清晰度")) == expected
    keys = list(info)
    pos = keys.index("鸟清晰度")
    assert keys[pos:pos + 5] == ["鸟清晰度", "头部清晰度", "身体清晰度", "平均清晰度", "美学"]
    assert info["鸟清晰度"] == "清晰（模糊半径 0.60px）"


def test_part_scores_refresh_in_place_and_clear_stale_values_without_losing_drafts(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from app_common import superviewer_user_options as options

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(options, "_RUNTIME_OPTIONS", dict(options.get_runtime_user_options()))
    source = tmp_path / "bird.jpg"
    source.write_bytes(b"photo")
    metadata = {"bird_sharpness_verdict": "sharp", "bird_sharpness_head_score": 0,
                "bird_sharpness_body_score": 840, "bird_sharpness_mean_score": 420.0}
    panel = ImageInfoTabPanel_ImageInfo(lambda: [], lambda _p: set(), lambda *_args: None,
                                      lambda p, _name: p, metadata_provider=lambda _p: dict(metadata),
                                      preview_pixmap_provider=lambda _p: None)
    monkeypatch.setattr(panel, "_image_size", lambda *_args, **_kwargs: (4000, 3000))
    try:
        assert panel.basic_rows["平均清晰度"].text() == "—"
        panel._current_photo_path = str(source)
        panel.comment_edit.setPlainText("未保存备注")
        panel.location_edit.setText("未保存地点")
        panel.filename_edit.setText("未保存文件名")
        before = [panel.basic_rows[key] for key in ("头部清晰度", "身体清晰度", "平均清晰度")]
        panel.refresh_metadata_fields()
        assert [label.text() for label in before] == ["0", "840", "420.0"]
        metadata.clear()  # 旧算法结果或尚无结果，不能留住上一张照片的三项分数。
        metadata.update(bird_sharpness_verdict="no_bird", bird_sharpness_sigma=1.2)
        panel.refresh_metadata_fields()
        assert [label.text() for label in before] == ["—", "—", "—"]
        assert panel.comment_edit.toPlainText() == "未保存备注"
        assert panel.location_edit.text() == "未保存地点"
        assert panel.filename_edit.text() == "未保存文件名"
        assert before == [panel.basic_rows[key] for key in ("头部清晰度", "身体清晰度", "平均清晰度")]
    finally:
        panel.close()
        app.processEvents()
