"""SuperViewer 鸟清晰度检测：菜单、后台任务写 XMP 并刷新列表、跳过已检测、关闭时停止。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

import bird_sharpness.models as bs_models
from app_common import bird_sharpness_fields as bsf
from app_common.exif_io.exiftool_path import get_exiftool_executable_path
from app_common.exif_io.photo_meta import PhotoMetaDataReportDB, PhotoMetaDataXMP
from bird_sharpness.analyzer import BirdSharpnessResult
from bird_sharpness.scoring import ALGORITHM_VERSION
from SuperViewer.superviewer.bird_sharpness_controller import BirdSharpnessController, BirdSharpnessJob

_APP = QApplication.instance() or QApplication([])


class _FakeFileList:
    def __init__(self):
        self.synced: dict[str, dict] = {}
        self.extenders = []

    def add_file_context_menu_extender(self, callback):
        self.extenders.append(callback)

    def _resolve_source_path_for_action(self, path):
        return path

    def sync_metadata_edit_for_path(self, path, *, meta_updates=None, report_fields=None):
        self.synced[os.path.normpath(path)] = dict(meta_updates or {})
        return True


class _FakeAnalyzer:
    def __init__(self, gate: threading.Event | None = None):
        self.gate = gate
        self.analyzed: list[str] = []
        self.loaded = self.released = False

    def load(self):
        self.loaded = True

    def release(self):
        self.released = True

    def analyze(self, path):
        if self.gate is not None:
            self.gate.wait(5)
        self.analyzed.append(os.path.normpath(path))
        verdict = "sharp" if path.endswith("a.jpg") else "soft"
        sigma = 0.7 if verdict == "sharp" else 1.3
        return BirdSharpnessResult(path=path, verdict=verdict, score=520 if verdict == "sharp" else 180,
                                   head_sigma=sigma, body_sigma=0.9, eye_visibility=0.99)


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
    if not get_exiftool_executable_path():
        pytest.skip("ExifTool is unavailable")
    monkeypatch.setattr(PhotoMetaDataReportDB, "_row_for", lambda *_args: None)
    monkeypatch.setattr(bs_models, "check_runtime", lambda: None)
    folder = tmp_path / "鸟片目录"
    folder.mkdir()
    paths = []
    for name in ("a.jpg", "b.jpg"):
        path = folder / name
        Image.new("RGB", (16, 12), "gray").save(path)
        paths.append(str(path))
    window = QWidget()
    file_list = _FakeFileList()
    controller = BirdSharpnessController(window, file_list)
    analyzer = _FakeAnalyzer()
    monkeypatch.setattr(controller, "analyzer", lambda: analyzer)
    messages = []
    monkeypatch.setattr(controller, "_show_message", messages.append)
    controller.messages = messages
    yield controller, file_list, analyzer, folder, paths
    controller.request_shutdown()
    _wait(controller.is_shutdown_done)
    if controller._dialog is not None:
        controller._dialog.close()
    window.deleteLater()
    _APP.processEvents()


def test_file_job_writes_xmp_and_refreshes_list(env) -> None:
    controller, file_list, analyzer, _folder, paths = env
    assert file_list.extenders == [controller.extend_file_menu]
    controller.start_for_paths(paths)
    assert _wait(lambda: not controller.busy)
    assert analyzer.analyzed == [os.path.normpath(p) for p in paths]
    rec = PhotoMetaDataXMP().read(paths[0])
    assert rec["bird_sharpness_verdict"] == "sharp"
    assert rec["bird_sharpness_version"] == ALGORITHM_VERSION
    assert float(rec["XMP-photoshop:City"]) == pytest.approx(520)
    updates = file_list.synced[os.path.normpath(paths[1])]
    assert bsf.bird_sharpness_from_meta(updates).text() == "失焦 1.30"
    assert updates["XMP:City"] == "180.00"
    assert "清晰 1" in controller._dialog.summary.text() and "失焦 1" in controller._dialog.summary.text()
    assert controller._dialog.button.text() == "关闭"


def test_directory_job_skips_photos_already_analyzed_with_same_version(env) -> None:
    controller, file_list, analyzer, folder, paths = env
    assert PhotoMetaDataXMP().write(paths[0], {
        "XMP-superpicky:bird_sharpness_verdict": "sharp",
        "XMP-superpicky:bird_sharpness_version": ALGORITHM_VERSION,
    })
    controller.start(BirdSharpnessJob(title="t", directory=str(folder), skip_existing=True))
    assert _wait(lambda: not controller.busy)
    assert analyzer.analyzed == [os.path.normpath(paths[1])]
    assert "已跳过 1" in controller._dialog.summary.text()


def test_menus_offer_start_then_stop_while_busy(env) -> None:
    controller, file_list, analyzer, folder, paths = env
    menu = QMenu()
    controller.extend_file_menu(menu, paths)
    assert [a.text() for a in menu.actions()] == ["检测鸟清晰度（2 张）"]
    dir_menu = QMenu()
    controller.extend_directory_menu(dir_menu, str(folder))
    sub = dir_menu.actions()[0].menu()
    assert [a.text() for a in sub.actions()][0] == "检测本目录（跳过已检测）"

    gate = threading.Event()
    analyzer.gate = gate
    controller.start_for_paths(paths)
    busy_menu = QMenu()
    controller.extend_file_menu(busy_menu, paths)
    assert [a.text() for a in busy_menu.actions()] == ["停止鸟清晰度检测"]
    assert not controller.start(BirdSharpnessJob(title="again", items=[(paths[0], paths[0])]))
    assert controller.messages == ["已有鸟清晰度检测任务在运行。"]
    busy_menu.actions()[0].trigger()
    gate.set()
    assert _wait(lambda: not controller.busy)
    assert len(analyzer.analyzed) == 1  # stopped after the in-flight photo
    assert controller._dialog.label.text() == "已停止。"


def test_shutdown_waits_for_worker_and_releases_models(env) -> None:
    controller, file_list, analyzer, _folder, paths = env
    gate = threading.Event()
    analyzer.gate = gate
    controller.start_for_paths(paths)
    assert _wait(lambda: controller._worker is not None and controller._worker.isRunning())
    controller.request_shutdown()
    assert not controller.is_shutdown_done()
    gate.set()
    assert _wait(controller.is_shutdown_done)
    assert controller._dialog is None
    # late results after shutdown must not touch the list
    assert len(file_list.synced) <= 1


class _PeakAnalyzer(_FakeAnalyzer):
    def __init__(self, gate=None, delay=0.05):
        super().__init__(gate)
        self.delay = delay
        self.lock = threading.Lock()
        self.active = self.peak = 0

    def analyze(self, path):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            return super().analyze(path)
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def pooled(env, monkeypatch, tmp_path):
    from app_common.file_browser._work_pool import BrowserWorkPool

    controller, file_list, _analyzer, folder, paths = env
    for i in range(6):
        extra = folder / f"c{i}.jpg"
        Image.new("RGB", (16, 12), "gray").save(extra)
        paths.append(str(extra))
    pool = BrowserWorkPool(6, 2, analysis_workers=3)
    file_list.background_work_pool = lambda: pool
    analyzer = _PeakAnalyzer()
    monkeypatch.setattr(controller, "analyzer", lambda: analyzer)
    yield controller, file_list, analyzer, folder, paths, pool
    pool.shutdown(timeout=5)


def test_job_runs_actions_in_parallel_on_shared_pool(pooled) -> None:
    controller, file_list, analyzer, folder, paths, pool = pooled
    controller.start(BirdSharpnessJob(title="t", directory=str(folder)))
    assert _wait(lambda: not controller.busy)
    assert sorted(analyzer.analyzed) == sorted(os.path.normpath(p) for p in paths)
    assert 1 < analyzer.peak <= 3
    assert len(file_list.synced) == len(paths)
    assert all(PhotoMetaDataXMP().read(p).get("bird_sharpness_verdict") for p in paths)
    assert controller._dialog.label.text() == "检测完成。"
    snap = pool.snapshot()
    assert snap["analysis_active"] == 0 and snap["analysis_queued"] == 0


def test_stop_withdraws_queued_actions_and_waits_for_running_ones(pooled) -> None:
    controller, file_list, analyzer, folder, paths, pool = pooled
    gate = threading.Event()
    analyzer.gate = gate
    controller.start(BirdSharpnessJob(title="t", directory=str(folder)))
    assert _wait(lambda: analyzer.active == 3)
    controller.stop()
    assert _wait(lambda: pool.snapshot()["analysis_queued"] == 0)
    assert controller.busy  # running photos still own the job
    gate.set()
    assert _wait(lambda: not controller.busy)
    assert len(analyzer.analyzed) == 3
    assert file_list.synced == {}  # results finished after stop are dropped, not written
    assert not any(PhotoMetaDataXMP().read(p).get("bird_sharpness_verdict") for p in paths)
    assert controller._dialog.label.text() == "已停止。"


def test_browser_pool_shutdown_ends_job_and_releases_models(pooled) -> None:
    controller, file_list, analyzer, folder, paths, pool = pooled
    gate = threading.Event()
    analyzer.gate = gate
    controller.start(BirdSharpnessJob(title="t", directory=str(folder)))
    assert _wait(lambda: analyzer.active == 3)
    pool.request_shutdown()  # browser closes first, as in MainWindow.closeEvent
    controller.request_shutdown()
    gate.set()
    assert _wait(controller.is_shutdown_done)
    assert len(analyzer.analyzed) == 3
    assert controller._dialog is None
