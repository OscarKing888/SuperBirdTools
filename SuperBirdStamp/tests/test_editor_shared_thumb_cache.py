from pathlib import Path
import os

import pytest

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


def test_missing_scope_is_created_automatically_without_dialog_or_report(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("must not ask"))
    path = _source(tmp_path / "照片")
    scope = SharedThumbnailScope()
    assert scope.ensure(path)
    assert scope.ensure(path)
    assert (path.parent / ".superpicky").is_dir()
    assert not (path.parent / ".superpicky" / "report.db").exists()
    with Image.new("RGB", (640, 400), "blue") as image:
        assert write_thumbnail(path, image)
    with read_thumbnail(path) as cached:
        assert cached.getpixel((0, 0))[2] > 240


def test_scope_creation_failure_is_session_scoped_and_uses_local_cache(tmp_path, monkeypatch, caplog):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("must not ask"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail("must not interrupt preview"))
    path = _source(tmp_path / "photos")
    target = path.parent / ".superpicky"
    calls = []
    original_mkdir = Path.mkdir
    def fail_scope(directory, *args, **kwargs):
        if directory == target:
            calls.append(directory)
            raise PermissionError("read only")
        return original_mkdir(directory, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail_scope)
    scope = SharedThumbnailScope()
    assert not scope.ensure(path)
    assert not scope.ensure(path)
    assert calls == [target]
    assert "回退本地缓存" in caplog.text
    with Image.new("RGB", (640, 400), "red") as image:
        assert write_thumbnail(path, image)
    assert not target.exists()
    with read_thumbnail(path) as cached:
        assert cached.size == (256, 160)
    # 下一窗口允许重试；外部创建成功后当前窗口也能立即复用。
    monkeypatch.setattr(Path, "mkdir", original_mkdir)
    assert SharedThumbnailScope().ensure(path)
    assert scope.ensure(path)


def test_birdstamp_reuses_ancestor_cache_without_creating_leaf_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    root = tmp_path / "海湾森林公园"
    scope = root / ".superpicky"
    scope.mkdir(parents=True)
    path = _source(root / "日期" / "优选" / "鸟种" / "连拍" / "照片")
    assert SharedThumbnailScope().ensure(path)
    assert not (path.parent / ".superpicky").exists()
    with Image.new("RGB", (640, 400), "red") as image:
        assert write_thumbnail(path, image)
    expected = _persistent_thumb_cache_path_for_file(str(path), str(root), 256, selected_dir=str(root))
    assert Path(expected).is_file()
    with read_thumbnail(path) as cached:
        assert cached.size == (256, 160)


@pytest.mark.parametrize("raises", [False, True])
def test_shared_write_failure_falls_back_to_readable_local_cache(tmp_path, monkeypatch, raises):
    from birdstamp.gui import editor_shared_thumb_cache as cache
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    path = _source(tmp_path / "photos")
    assert SharedThumbnailScope().ensure(path)
    shared = _persistent_thumb_cache_path_for_file(str(path), str(path.parent), 256, selected_dir=str(path.parent))
    local = cache.local_thumbnail_path(path)
    original_write = cache._write_persistent_thumb_cache_image
    calls = []
    def fail_shared(target, image, stamp):
        calls.append(target)
        if target == shared:
            if raises:
                raise PermissionError("read only")
            return False
        return original_write(target, image, stamp)
    monkeypatch.setattr(cache, "_write_persistent_thumb_cache_image", fail_shared)
    with Image.new("RGB", (640, 400), "red") as image:
        assert write_thumbnail(path, image)
    assert calls == [shared, str(local)]
    assert not Path(shared).exists()
    assert local.is_file()
    with read_thumbnail(path) as cached:
        assert cached.size == (256, 160)
    monkeypatch.setattr(cache, "_write_persistent_thumb_cache_image", lambda *args: False)
    with Image.new("RGB", (640, 400), "red") as image:
        assert not write_thumbnail(path, image)


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
