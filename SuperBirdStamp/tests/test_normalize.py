from pathlib import Path

from birdstamp.meta.normalize import normalize_metadata


def test_normalize_metadata_prefers_datetimeoriginal_and_filename_bird() -> None:
    raw = {
        "DateTimeOriginal": "2026:02:16 09:14:33",
        "Make": "Sony",
        "Model": "ILCE-1M2",
        "LensModel": "FE 600mm F4 GM OSS",
        "FNumber": 4.0,
        "ExposureTime": "1/2000",
        "ISO": 800,
        "FocalLength": 600,
        "GPSLatitude": 39.12345,
        "GPSLongitude": 116.12345,
    }
    metadata = normalize_metadata(
        Path("灰喜鹊_20260216_001.ARW"),
        raw,
        bird_arg=None,
        bird_priority=["filename", "meta"],
        bird_regex=r"(?P<bird>[^_]+)_",
    )
    assert metadata.capture_text == "2026-02-16 09:14"
    assert metadata.bird == "灰喜鹊"
    assert metadata.camera == "Sony ILCE-1M2"
    assert metadata.settings_text == "f/4  1/2000s  ISO800  600mm"
    assert metadata.location is None
    assert metadata.gps_text == "39.12345, 116.12345"


def test_snapshot_normalization_does_not_probe_missing_capture_time(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("snapshot normalization queried the source volume")

    monkeypatch.setattr(Path, "stat", forbidden)
    metadata = normalize_metadata(
        Path("白鹭_001.ARW"), {}, bird_arg=None, bird_priority=["filename", "meta"],
        bird_regex=r"(?P<bird>[^_]+)_", allow_stat=False,
    )
    assert metadata.capture_text is None
    assert metadata.bird == "白鹭"
    assert calls == []
