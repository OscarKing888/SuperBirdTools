from pathlib import Path

from birdstamp.meta.pillow_fallback import extract_pillow_metadata


def test_extract_pillow_metadata_is_safe_on_unidentified_files(tmp_path: Path) -> None:
    raw_like = tmp_path / "sample.ARW"
    raw_like.write_bytes(b"not-a-real-image")

    metadata = extract_pillow_metadata(raw_like)

    assert metadata["SourceFile"] == str(raw_like)

