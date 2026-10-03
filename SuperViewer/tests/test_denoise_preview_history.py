"""自选成片目录可跨重启找到，索引失败不损坏上一次记录。"""
from SuperViewer.superviewer.denoise_preview_history import DenoisePreviewHistory
from SuperViewer.superviewer import denoise_preview_history


def test_history_keeps_recent_sources_and_roundtrips_chinese_paths(tmp_path):
    path = tmp_path / "state" / "denoise_previews.json"
    history = DenoisePreviewHistory(path, limit=2)
    history.record("鸟一.ARW", "自选目录/鸟一.tif")
    history.record("鸟二.ARW", "自选目录/鸟二.tif")
    history.record("鸟一.ARW", "新目录/鸟一.tif")
    history.record("鸟三.ARW", "自选目录/鸟三.tif")
    assert DenoisePreviewHistory(path).entries() == (
        ("鸟一.ARW", "新目录/鸟一.tif"), ("鸟三.ARW", "自选目录/鸟三.tif"),
    )


def test_history_failed_atomic_replace_preserves_last_complete_index(tmp_path, monkeypatch):
    path = tmp_path / "history.json"
    history = DenoisePreviewHistory(path)
    history.record("原图.ARW", "成片.tif")
    original = path.read_bytes()

    def fail(*_args):
        raise OSError("read only")

    monkeypatch.setattr(denoise_preview_history.os, "replace", fail)
    history.record("另一张.ARW", "another.tif")
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_history_cleanup_permission_failure_does_not_fail_published_photo(tmp_path, monkeypatch):
    history = DenoisePreviewHistory(tmp_path / "history.json")

    def fail(*_args):
        raise PermissionError("temporary file in use")

    monkeypatch.setattr(denoise_preview_history.os, "replace", fail)
    monkeypatch.setattr(denoise_preview_history.os, "unlink", fail)
    history.record("原图.ARW", "已成功发布.tif")
    assert history.entries() == (("原图.ARW", "已成功发布.tif"),)
