"""EXIF 页的相机对焦距离展示。"""

import importlib

from SuperViewer.superviewer import exif_helpers as exif


def test_main_module_imports_current_exif_api():
    assert importlib.import_module("SuperViewer.main").MainWindow is not None


def _read_exiftool_rows(monkeypatch, metadata):
    monkeypatch.setattr(exif, "load_exif_tag_hidden_from_settings", lambda: set())
    return exif.exif_rows_from_metadata(metadata, tag_label_chinese=True)


def test_canon_distance_bounds_keep_both_raw_tags(monkeypatch):
    rows = _read_exiftool_rows(monkeypatch, {
        "Canon:FocusDistanceUpper": "2.5 m",
        "Canon:FocusDistanceLower": "1.5 m",
        "ExifIFD:FNumber": "f/4",
    })
    summary = next(row for row in rows if row[:2] == (exif.META_IFD_NAME, exif.FOCUS_DISTANCE_TAG_ID))
    assert summary[4] == "Canon: FocusDistanceLower 1.5 m; FocusDistanceUpper 2.5 m"
    assert {row[6] for row in rows if row[6]} >= {
        "Canon:FocusDistanceUpper", "Canon:FocusDistanceLower",
    }
    assert not any(row[0] == "Calc" for row in rows)


def test_sony_distance_keeps_original_value_and_tag(monkeypatch):
    rows = _read_exiftool_rows(monkeypatch, {"Sony:FocusDistance": "0.8 m"})
    summary = next(row for row in rows if row[:2] == (exif.META_IFD_NAME, exif.FOCUS_DISTANCE_TAG_ID))
    assert summary[4] == "Sony: 0.8 m"
    assert any(row[6] == "Sony:FocusDistance" and row[4] == "0.8 m" for row in rows)


def test_sony_composite_distance_is_marked_as_estimate(monkeypatch):
    rows = _read_exiftool_rows(monkeypatch, {
        "Sony:FocusPosition2": 198,
        "Composite:FocusDistance2": "105.2 m",
        "Composite:HyperfocalDistance": "2201.61 m",
    })
    summary = next(row for row in rows if row[:2] == (exif.META_IFD_NAME, exif.FOCUS_DISTANCE_TAG_ID))
    assert summary[4] == "ExifTool 估算 (FocusDistance2，可能不准确): 105.2 m"
    assert any(row[6] == "Sony:FocusPosition2" for row in rows)
    assert any(row[6] == "Composite:FocusDistance2" for row in rows)
    assert not any(row[6] == "Composite:HyperfocalDistance" for row in rows)


def test_missing_distance_has_no_computed_placeholder(monkeypatch):
    rows = _read_exiftool_rows(monkeypatch, {"ExifIFD:FNumber": "f/4"})
    assert not any(row[:2] == (exif.META_IFD_NAME, exif.FOCUS_DISTANCE_TAG_ID) for row in rows)
    assert not any(row[0] == "Calc" for row in rows)


def test_full_display_sqlite_snapshot_retains_camera_distance_tags(tmp_path, monkeypatch):
    from app_common.exif_io.metadata_store import get_store

    photo = tmp_path / "中文对焦距离.CR3"
    photo.write_bytes(b"camera fixture")
    metadata = {"Canon:FocusDistanceUpper": "2.5 m", "Canon:FocusDistanceLower": "1.5 m"}
    calls = []
    monkeypatch.setattr(exif, "get_exiftool_executable_path", lambda: "fixture-exiftool")
    monkeypatch.setattr(exif, "run_exiftool_json", lambda path: calls.append(path) or [metadata])
    monkeypatch.setattr(exif, "load_exif_tag_hidden_from_settings", lambda: set())
    first = exif.load_all_exif_exiftool(str(photo), tag_label_chinese=True)
    cached = get_store().read_many([str(photo)], profile="full-display")[str(photo)]
    assert {key: cached[key] for key in metadata} == metadata
    second = exif.load_all_exif_exiftool(str(photo), tag_label_chinese=True)
    assert first == second
    assert calls == [str(photo)]
    assert any(row[:2] == (exif.META_IFD_NAME, exif.FOCUS_DISTANCE_TAG_ID) for row in second)


def test_old_hyperfocal_priority_is_discarded(monkeypatch):
    monkeypatch.setattr(exif, "_load_settings", lambda: {
        "exif_tag_priority": ["Calc:HyperfocalDistance", "Exif:33437"],
    })
    priority = exif.load_tag_priority_from_settings()
    assert "Calc:HyperfocalDistance" not in priority
    assert exif.FOCUS_DISTANCE_PRIORITY_KEY in priority
