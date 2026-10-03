"""SuperViewer 目录右键「计算连拍信息」：菜单、后台写 XMP 并批量刷新列表、停止与关闭。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import pytest
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from app_common import burst_info as bi
from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
from SuperViewer.superviewer.burst_info_controller import BurstInfoController, BurstInfoJob

_APP = QApplication.instance() or QApplication([])


class _FakeDirBrowser:
    def __init__(self):
        self.extenders = []

    def add_context_menu_extender(self, callback):
        self.extenders.append(callback)


class _FakeFileList:
    def __init__(self):
        self.batches: list[dict] = []

    def sync_metadata_edits_for_paths(self, updates):
        self.batches.append(dict(updates))
        return len(updates)


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    folder = tmp_path / "连拍目录"
    folder.mkdir()
    times = {}
    for index in range(6):
        path = folder / f"鹰鹃_{index}.jpg"
        path.write_bytes(b"")
        # 前 4 张 50ms 连拍，后 2 张单拍
        offset = index * 0.05 if index < 4 else 10.0 + index * 5
        times[os.path.normpath(str(path))] = bi.CaptureTime(1_700_000_000.0 + offset, True)
    gate = threading.Event()
    gate.set()

    def fake_read(paths, *, cancel_event=None):
        gate.wait(5)
        return {os.path.normpath(p): times.get(os.path.normpath(p)) for p in paths}

    monkeypatch.setattr(bi, "read_capture_times", fake_read)
    window = QWidget()
    files = _FakeFileList()
    browser = _FakeDirBrowser()
    controller = BurstInfoController(window, files, browser)
    yield controller, files, browser, str(folder), sorted(times), gate
    controller.request_shutdown()
    gate.set()
    _wait(controller.is_shutdown_done)
    window.deleteLater()


def test_directory_job_writes_bursts_and_refreshes_list_in_batch(env) -> None:
    controller, files, _browser, folder, paths, _gate = env
    assert controller.start(BurstInfoJob(title="t", directory=folder))
    assert _wait(lambda: not controller.busy)

    xmp = PhotoMetaDataXMP()
    assert [xmp.read(p).get(bi.BURST_POSITION_FIELD) for p in paths[:4]] == ["1", "2", "3", "4"]
    assert {xmp.read(p).get(bi.BURST_ID_FIELD) for p in paths[:4]} == {"1"}
    # 单拍且没有任何旧连拍值：不创建 sidecar
    assert not os.path.exists(os.path.splitext(paths[4])[0] + ".xmp")
    synced = {path: meta for batch in files.batches for path, meta in batch.items()}
    assert set(synced) == set(paths[:4])
    assert synced[paths[2]]["burst_position"] == "3" and synced[paths[2]]["report.burst_id"] == "1"
    assert "连拍组 1" in controller._dialog.summary.text()


def test_menu_offers_start_then_stop_while_busy(env) -> None:
    controller, _files, browser, folder, _paths, gate = env
    assert browser.extenders == [controller.extend_directory_menu]
    menu = QMenu()
    controller.extend_directory_menu(menu, folder)
    sub = menu.actions()[0].menu()
    assert menu.actions()[0].text() == "计算连拍信息"
    assert [a.text() for a in sub.actions()] == ["计算本目录…", "计算本目录及子目录…"]

    gate.clear()
    assert controller.start(BurstInfoJob(title="t", directory=folder))
    busy_menu = QMenu()
    controller.extend_directory_menu(busy_menu, folder)
    assert [a.text() for a in busy_menu.actions()[0].menu().actions()] == ["停止连拍计算"]
    controller.stop()
    gate.set()
    assert _wait(lambda: not controller.busy)
    assert controller._dialog.label.text() == "已停止。"


def test_shutdown_waits_for_worker_and_skips_list_refresh(env) -> None:
    controller, files, _browser, folder, _paths, gate = env
    gate.clear()
    assert controller.start(BurstInfoJob(title="t", directory=folder))
    controller.request_shutdown()
    assert not controller.is_shutdown_done()
    gate.set()
    assert _wait(controller.is_shutdown_done)
    assert files.batches == []
    assert not controller.start(BurstInfoJob(title="t", directory=folder))
