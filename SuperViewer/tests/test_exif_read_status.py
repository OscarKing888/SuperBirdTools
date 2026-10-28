"""Display-profile fallback must distinguish absent EXIF from failed reads."""
import pytest
from PIL import Image

from app_common.exif_io import metadata_store
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import exif_helpers


@pytest.fixture
def store(tmp_path, monkeypatch):
    instance = metadata_store.MetadataStore(tmp_path / "store.sqlite3", start_scheduler=False)
    monkeypatch.setattr(metadata_store, "get_store", lambda: instance)
    yield instance
    instance.close()


def test_failed_display_fallback_with_xmp_retries_without_complete_cache(tmp_path, store, monkeypatch):
    photo = (tmp_path / "中文.png").resolve()
    Image.new("RGB", (8, 6)).save(photo)
    assert PhotoMetaDataXMP().write_title(str(photo), "侧车白鹭")
    monkeypatch.setattr(exif_helpers, "get_exiftool_executable_path", lambda: None)
    monkeypatch.setattr(exif_helpers, "load_exif_piexif", lambda *a: None)
    pillow_read = exif_helpers.load_exif_pillow
    monkeypatch.setattr(exif_helpers, "load_exif_pillow", lambda *a, **k: [])
    rows = exif_helpers.load_all_exif(str(photo))
    assert next(row[4] for row in rows if row[:2] == ("Meta", "Title")) == "侧车白鹭"
    assert store.peek(str(photo), profile="full-display") is None

    # The exact same source/signature becomes readable on the next request.
    monkeypatch.setattr(exif_helpers, "load_exif_pillow", pillow_read)
    exif_helpers.load_all_exif(str(photo))
    cached = store.peek(str(photo), profile="full-display")
    assert cached["_metadata_read_ok"] is True
    assert cached["_metadata_complete"] is True
    monkeypatch.setattr(exif_helpers, "_load_all_exif_fallback",
                        lambda *a, **k: pytest.fail("successful empty EXIF was not cached"))
    assert exif_helpers.load_all_exif(str(photo))


def test_exiftool_error_with_file_fields_is_not_a_complete_display_snapshot(tmp_path, store, monkeypatch):
    photo = (tmp_path / "temporary.ARW").resolve()
    photo.write_bytes(b"source")
    monkeypatch.setattr(exif_helpers, "get_exiftool_executable_path", lambda: "stub")
    monkeypatch.setattr(exif_helpers, "run_exiftool_json", lambda path: [{
        "SourceFile": path, "File:FileType": "ARW", "ExifTool:Error": "temporary failure",
    }])
    exif_helpers.load_all_exif(str(photo))
    assert store.peek(str(photo), profile="full-display") is None
    monkeypatch.setattr(exif_helpers, "run_exiftool_json", lambda path: [{
        "SourceFile": path, "IFD0:Model": "恢复后的相机",
    }])
    rows = exif_helpers.load_all_exif(str(photo))
    assert any(row[4] == "恢复后的相机" for row in rows)
    assert store.peek(str(photo), profile="full-display")["_metadata_read_ok"] is True


def test_pillow_empty_exif_is_success_and_unreadable_image_is_failure(tmp_path):
    photo = tmp_path / "empty.png"
    Image.new("RGB", (8, 6)).save(photo)
    success, failure = {}, {}
    assert exif_helpers.load_exif_pillow(str(photo), read_status=success) == []
    assert success == {"ok": True}
    assert exif_helpers.load_exif_pillow(str(tmp_path / "missing.png"), read_status=failure) == []
    assert not failure.get("ok", False)
