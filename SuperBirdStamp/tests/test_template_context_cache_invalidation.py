from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

import app_common.exif_io
from birdstamp.gui import template_context


def test_path_only_metadata_cache_invalidates_when_sidecar_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_path = tmp_path / "sample.jpg"
    Image.new("RGB", (8, 6), "#ffffff").save(source_path)
    sidecar_path = source_path.with_suffix(".xmp")
    sidecar_path.write_text("旧标题", encoding="utf-8")
    calls: list[str] = []

    def _fake_extract(_path: Path) -> dict[str, str]:
        title = sidecar_path.read_text(encoding="utf-8")
        calls.append(title)
        return {"SourceFile": str(source_path), "XMP-dc:Title": title}

    monkeypatch.setattr(app_common.exif_io, "extract_metadata_with_xmp_priority", _fake_extract)
    template_context._read_file_metadata_with_xmp_priority_cached.cache_clear()
    photo_info = template_context.PhotoInfo.from_path(source_path, sidecar_path=sidecar_path)

    first = template_context._metadata_with_xmp_priority(photo_info)
    prior_stat = sidecar_path.stat()
    sidecar_path.write_text("新标题", encoding="utf-8")
    os.utime(
        sidecar_path,
        ns=(prior_stat.st_atime_ns, prior_stat.st_mtime_ns + 1_000_000_000),
    )
    second = template_context._metadata_with_xmp_priority(photo_info)

    assert first["XMP-dc:Title"] == "旧标题"
    assert second["XMP-dc:Title"] == "新标题"
    assert calls == ["旧标题", "新标题"]
