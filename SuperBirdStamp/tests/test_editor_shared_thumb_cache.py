from pathlib import Path
import os

from PIL import Image

from app_common.file_browser._browser_core import (
    _existing_persistent_thumb_cache_path_for_exact_size,
    _persistent_thumb_cache_path_for_file,
    _thumb_disk_cache_path,
)
from birdstamp import config
from birdstamp.gui.editor_shared_thumb_cache import (
    SharedThumbnailScope, read_thumbnail, write_thumbnail,
)
from birdstamp.gui.editor_source_quick_loader import SourceQuickAction
from birdstamp.gui.editor_preview_decode_worker import EditorPreviewAction


def _source(directory: Path, color: str = "red") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "same.jpg"
    Image.new("RGB", (640, 400), color).save(path)
    return path


def test_birdstamp_writes_viewers_exact_256_cache_for_each_file_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    for name, color in (("one", "red"), ("two", "blue")):
        directory = tmp_path / name
        (directory / ".superpicky").mkdir(parents=True)
        path = _source(directory, color)
        with Image.new("RGB", (640, 400), color) as image:
            assert write_thumbnail(path, image)
        expected = _persistent_thumb_cache_path_for_file(
            str(path), str(directory), 256, selected_dir=str(directory))
        assert expected and Path(expected).is_file()
        assert _existing_persistent_thumb_cache_path_for_exact_size(
            str(path), str(directory), 256, selected_dir=str(directory)) == expected
        with read_thumbnail(path) as cached:
            assert cached.size == (256, 160)
            assert cached.getpixel((0, 0))[0 if color == "red" else 2] > 240
        os.utime(expected, (path.stat().st_mtime - 60,) * 2)
        assert read_thumbnail(path) is None


def test_missing_scope_uses_birdstamp_local_cache_and_creation_is_session_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    path = _source(tmp_path / "photos")
    scope = SharedThumbnailScope()
    asked = []
    monkeypatch.setattr(scope, "_ask", lambda target, parent: asked.append(target) or False)
    assert not scope.ensure(path)
    assert not scope.ensure(path)
    assert len(asked) == 1
    with Image.new("RGB", (640, 400), "red") as image:
        assert write_thumbnail(path, image)
    assert not (path.parent / ".superpicky").exists()
    with read_thumbnail(path) as cached:
        assert cached.size == (256, 160)

    accepted = SharedThumbnailScope()
    monkeypatch.setattr(accepted, "_ask", lambda target, parent: True)
    assert accepted.ensure(path)
    assert (path.parent / ".superpicky").is_dir()
    assert not (path.parent / ".superpicky" / "report.db").exists()
    with Image.new("RGB", (640, 400), "blue") as image:
        assert write_thumbnail(path, image)
    with read_thumbnail(path) as cached:
        assert cached.getpixel((0, 0))[2] > 240


def test_corrupt_shared_cache_falls_back_and_rebuilds(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    path = _source(tmp_path / "photos")
    (path.parent / ".superpicky").mkdir()
    shared = Path(_persistent_thumb_cache_path_for_file(
        str(path), str(path.parent), 256, selected_dir=str(path.parent)))
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"broken")
    assert read_thumbnail(path) is None
    with Image.new("RGB", (640, 400), "green") as image:
        assert write_thumbnail(path, image)
    with read_thumbnail(path) as cached:
        assert cached.getpixel((0, 0))[1] > 100


def test_reads_viewer_local_fallback_without_shared_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "viewer_local"))
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "birdstamp_local")
    path = _source(tmp_path / "photos")
    viewer_path = Path(_thumb_disk_cache_path(
        str(path), path.stat().st_mtime, 256, str(path.parent)))
    viewer_path.parent.mkdir(parents=True)
    Image.new("RGB", (256, 160), "blue").save(viewer_path)
    with read_thumbnail(path) as cached:
        assert cached.getpixel((0, 0))[2] > 240


def test_source_quick_action_populates_viewer_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    path = _source(tmp_path / "photos")
    (path.parent / ".superpicky").mkdir()
    action = SourceQuickAction("source-signature", path, tmp_path / "user" / "source_preview",
                               cancelled=lambda: False)
    image, size = action.execute()
    try:
        assert size == (640, 400)
        assert image.size == (256, 160)
        assert _existing_persistent_thumb_cache_path_for_exact_size(
            str(path), str(path.parent), 256, selected_dir=str(path.parent))
    finally:
        image.close()


def test_source_quick_image_survives_cache_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    path = _source(tmp_path / "photos")
    action = SourceQuickAction("source-signature", path, tmp_path / "local", cancelled=lambda: False)
    monkeypatch.setattr(action, "_write_cache", lambda *args: (_ for _ in ()).throw(OSError("read only")))
    image, size = action.execute()
    try:
        assert image.size == (256, 160) and size == (640, 400)
    finally:
        image.close()


def test_selection_thresholds_match_viewer(monkeypatch, tmp_path):
    from birdstamp.gui import editor_preview_policy
    monkeypatch.setattr(editor_preview_policy, "read_decoded_image_size", lambda path: (5000, 3000))
    assert editor_preview_policy.load_full_synchronously(tmp_path / "image.jpg")
    assert not editor_preview_policy.load_full_synchronously(tmp_path / "image.heic")
    assert not editor_preview_policy.load_full_synchronously(tmp_path / "image.cr3")
    monkeypatch.setenv("SuperViewer_SYNC_FULL_PREVIEW_HEIF_MAX_MP", "16")
    assert editor_preview_policy.load_full_synchronously(tmp_path / "image.heic")


def test_large_selection_emits_256_then_native_pixels(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (900, 600), "red").save(path)
    quick, clear = [], []
    action = EditorPreviewAction(
        path, 0, False,
        lambda image, size: quick.append((image, size)),
        lambda image, size: clear.append((image, size)),
        cancelled=lambda: False,
    )
    action.execute()
    try:
        assert len(quick) == len(clear) == 1
        assert quick[0][0].size == (256, 171)
        assert quick[0][1] == (900, 600)
        assert clear[0][0].size == clear[0][1] == (900, 600)
    finally:
        for image, _ in quick + clear:
            image.close()
