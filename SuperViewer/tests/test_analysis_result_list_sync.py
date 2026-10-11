"""后台分析（鸟清晰度、连拍）写 XMP 后，缩略图模式下也要同步文件列表的内存元数据。"""
import os
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QWidget

from app_common.file_browser import FileListPanel
from bird_sharpness.analyzer import BirdSharpnessResult
from SuperViewer.superviewer.bird_sharpness_controller import BirdSharpnessController
from SuperViewer.superviewer.burst_info_controller import BurstInfoController
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def thumb_list(tmp_path):
    folder = tmp_path / "鸟片目录"
    folder.mkdir()
    listed, other = str(folder / "白鹭.jpg"), str(tmp_path / "其它目录.jpg")
    raw, unrelated = str(folder / "白鹭.ARW"), str(folder / "苍鹭.jpg")
    for path in (listed, other, unrelated):
        Image.new("RGB", (16, 12), "gray").save(path)
    with open(raw, "wb") as handle:
        handle.write(b"raw")
    config = tmp_path / "tags.cfg"
    config.write_text("飞行\n", encoding="utf-8")
    files = SuperViewerTaggedFileListPanel(tag_config_path=config)
    files._current_dir = str(folder)
    files._all_files = [listed, raw, unrelated]
    files._view_mode = FileListPanel._MODE_THUMB
    # The lazily built list-view model has no rows while the thumbnail view is shown.
    assert files._file_table_model.row_for_path(listed) is None
    window = QWidget()
    files.raw, files.unrelated = raw, unrelated
    yield files, window, listed, other
    files.shutdown()
    files.deleteLater()
    window.deleteLater()
    _APP.processEvents()


def test_bird_sharpness_result_updates_memory_metadata_in_thumbnail_mode(thumb_list) -> None:
    files, window, listed, other = thumb_list
    controller = BirdSharpnessController(window, files)
    worker = controller._worker = object()
    controller._counts = Counter()
    for path in (listed, other):
        result = BirdSharpnessResult(path=path, verdict="sharp", score=523, head_sigma=0.62, body_sigma=0.7)
        controller._on_result(worker, path, result, True)
    # RAW+JPEG of one shot read the same sidecar, so both rows show the new verdict.
    for path in (listed, files.raw):
        meta = files.cached_photo_metadata_for_path(path)
        assert meta["bird_sharpness_verdict"] == "sharp"
        assert meta["sharpness"] == "523.00"
    assert files.unrelated not in files._meta_cache
    assert other not in files._meta_cache


def test_bird_sharpness_result_of_unlisted_source_updates_listed_sibling(thumb_list) -> None:
    files, window, listed, _other = thumb_list
    files._all_files = [listed, files.unrelated]  # only the JPEG is shown; the RAW was analysed
    controller = BirdSharpnessController(window, files)
    worker = controller._worker = object()
    controller._counts = Counter()
    result = BirdSharpnessResult(path=files.raw, verdict="soft", score=180, head_sigma=1.3, body_sigma=0.9)
    controller._on_result(worker, files.raw, result, True)
    assert files.cached_photo_metadata_for_path(listed)["bird_sharpness_verdict"] == "soft"
    assert files.raw not in files._meta_cache


def test_burst_result_updates_memory_metadata_in_thumbnail_mode(thumb_list) -> None:
    files, window, listed, other = thumb_list
    controller = BurstInfoController(window, files)
    worker = controller._worker = object()
    updates = {"burst_id": "7", "burst_position": "2"}
    controller._on_written(worker, {listed: dict(updates), other: dict(updates)})
    assert files.cached_photo_metadata_for_path(listed)["burst_id"] == "7"
    assert other not in files._meta_cache
