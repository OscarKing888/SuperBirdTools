from datetime import datetime
from pathlib import Path

from birdstamp.models import NormalizedMetadata
from birdstamp.naming import build_output_name


def test_build_output_name_with_tokens() -> None:
    metadata = NormalizedMetadata(source=Path("灰喜鹊_001.ARW"), stem="灰喜鹊_001")
    metadata.capture_dt = datetime(2026, 2, 16, 12, 30)
    metadata.camera = "Sony ILCE-1M2"
    metadata.bird = "灰喜鹊"

    name = build_output_name(
        "{date}_{camera}_{stem}_{bird}.{ext}",
        Path("灰喜鹊_001.ARW"),
        metadata,
        extension="jpg",
    )
    assert name.endswith(".jpg")
    assert "20260216_1230" in name
    assert "Sony_ILCE-1M2" in name

