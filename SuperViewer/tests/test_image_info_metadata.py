from SuperViewer.superviewer.image_info_tab_image_info import _metadata_comment
from SuperViewer.superviewer.image_info_tab_image_info import ImageInfoTabPanel_ImageInfo


def test_metadata_comment_accepts_parsed_file_list_comment_field() -> None:
    assert _metadata_comment({"comment": "sidecar note"}) == "sidecar note"


def test_basic_info_includes_camera_and_analysis_metadata(tmp_path) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"not an actual image")

    class DummyPanel:
        def _image_size(self, path: str):
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
