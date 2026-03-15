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
    assert metadata.location == "39.12345, 116.12345"

