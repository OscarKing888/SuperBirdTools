from pathlib import Path

from SuperViewer.superviewer import preview_panel


def test_sync_full_preview_policy_uses_pixel_threshold(monkeypatch, tmp_path: Path) -> None:
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"placeholder")
    monkeypatch.setattr(preview_panel, "_SYNC_FULL_PREVIEW_MAX_PIXELS", 40_000_000)

    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 12_000_000)
    assert preview_panel._should_load_full_preview_sync(str(photo))

    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 50_000_000)
    assert not preview_panel._should_load_full_preview_sync(str(photo))


def test_sync_full_preview_policy_never_syncs_raw(monkeypatch, tmp_path: Path) -> None:
    raw = tmp_path / "a.arw"
    raw.write_bytes(b"placeholder")
    monkeypatch.setattr(preview_panel, "_SYNC_FULL_PREVIEW_MAX_PIXELS", 40_000_000)
    monkeypatch.setattr(preview_panel, "_expected_image_pixel_count", lambda path: 1)

    assert not preview_panel._should_load_full_preview_sync(str(raw))


def test_quick_preview_target_uses_requested_thumbnail_size() -> None:
    assert preview_panel._quick_preview_target_size(None, 1024) == 1024
    assert preview_panel._quick_preview_target_size(None, 0) == preview_panel._QUICK_PREVIEW_SIZE
