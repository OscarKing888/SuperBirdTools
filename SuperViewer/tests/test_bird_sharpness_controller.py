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
        self.entered = threading.Event()

    def load(self):
        self.loaded = True

    def release(self):
        self.released = True

    def analyze(self, path, on_stage=None, cancelled=None):
        if on_stage is not None:
            on_stage("decode")
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        self.analyzed.append(os.path.normpath(path))
        verdict = "sharp" if path.endswith("a.jpg") else "soft"
        sigma = 0.7 if verdict == "sharp" else 1.3
        return BirdSharpnessResult(path=path, verdict=verdict, score=520 if verdict == "sharp" else 180,
                                   head_sigma=sigma, body_sigma=0.9, eye_visibility=0.99,
                                   stage_s={"decode": 0.1, "detect": 0.2, "measure": 0.3}, elapsed_s=0.6)


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
    assert "清晰 1" in controller._dialog.summary_text() and "失焦 1" in controller._dialog.summary_text()
    assert controller._dialog.button.text() == "关闭"
    # every stage is timed per photo and totalled in the progress window (准备/写入 come from the action)
    stats = controller._timing
    assert stats.photos == 2 and stats.skipped == 0 and stats.setup_s is not None
    assert stats.stages["decode"].total_s == pytest.approx(0.2) and stats.stages["measure"].mean_s == pytest.approx(0.3)
    assert {"check", "decode", "detect", "measure", "write"} <= set(stats.stages)
    text = controller._dialog.timing_text()
    assert "2 张" in text and "解码：2 次" in text and "写入：2 次" in text and "总用时" in text
    assert not controller._dialog.timing.isHidden()


def test_directory_job_skips_photos_already_analyzed_with_same_version(env) -> None:
    controller, file_list, analyzer, folder, paths = env
    assert PhotoMetaDataXMP().write(paths[0], {
        "XMP-superpicky:bird_sharpness_verdict": "sharp",
        "XMP-superpicky:bird_sharpness_version": ALGORITHM_VERSION,
    })
    controller.start(BirdSharpnessJob(title="t", directory=str(folder), skip_existing=True))
    assert _wait(lambda: not controller.busy)
    assert analyzer.analyzed == [os.path.normpath(paths[1])]
    assert "已跳过 1" in controller._dialog.summary_text()


def test_menus_offer_start_then_stop_while_busy(env) -> None:
    controller, file_list, analyzer, folder, paths = env
    menu = QMenu()
    controller.extend_file_menu(menu, paths)
    # one entry; the image source is chosen inside the trace window
    assert [a.text() for a in menu.actions()] == ["查看清晰度计算过程…", "检测鸟清晰度（2 张）"]
    assert menu.actions()[0].menu() is None
    dir_menu = QMenu()
    controller.extend_directory_menu(dir_menu, str(folder))
    sub = dir_menu.actions()[0].menu()
    assert [a.text() for a in sub.actions()][0] == "检测本目录（跳过已检测）"

    gate = threading.Event()
    analyzer.gate = gate
    controller.start_for_paths(paths)
    assert _wait(analyzer.entered.is_set)
    busy_menu = QMenu()
    controller.extend_file_menu(busy_menu, paths)
    # the read-only trace viewer stays available while a batch job runs
    assert [a.text() for a in busy_menu.actions()] == ["查看清晰度计算过程…", "停止鸟清晰度检测"]
    assert not controller.start(BirdSharpnessJob(title="again", items=[(paths[0], paths[0])]))
    assert controller.messages == ["已有鸟清晰度检测任务在运行。"]
    next(a for a in busy_menu.actions() if a.text() == "停止鸟清晰度检测").trigger()
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

    def analyze(self, path, on_stage=None, cancelled=None):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            return super().analyze(path, on_stage, cancelled)
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


def test_job_runs_actions_in_parallel_on_shared_pool(pooled, monkeypatch) -> None:
    from SuperViewer.superviewer.bird_sharpness_progress import BirdSharpnessProgressDialog

    controller, file_list, analyzer, folder, paths, pool = pooled
    loads = []
    original = BirdSharpnessProgressDialog.set_load
    monkeypatch.setattr(BirdSharpnessProgressDialog, "set_load",
                        lambda self, load: (loads.append(load), original(self, load)))
    controller.start(BirdSharpnessJob(title="t", directory=str(folder)))
    assert _wait(lambda: not controller.busy)
    assert sorted(analyzer.analyzed) == sorted(os.path.normpath(p) for p in paths)
    assert 1 < analyzer.peak <= 3
    assert len(file_list.synced) == len(paths)
    assert all(PhotoMetaDataXMP().read(p).get("bird_sharpness_verdict") for p in paths)
    assert controller._dialog.label.text() == "检测完成。"
    snap = pool.snapshot()
    assert snap["analysis_active"] == 0 and snap["analysis_queued"] == 0
    # Live load: every snapshot uses the pool's capacity, lanes are stable slots, the last one is idle.
    assert loads and all(load.capacity == 3 and len(load.lanes) == 3 for load in loads)
    assert max(load.busy for load in loads) > 1
    assert any(load.queued for load in loads)
    assert loads[-1].busy == 0
    assert all(load.pool_threads == 6 for load in loads)


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


def test_missing_eye_model_is_reported_in_progress_window(env) -> None:
    controller, file_list, analyzer, _folder, paths = env

    class _NoEyeModels:
        has_keypoints = False

    analyzer.models = _NoEyeModels()
    controller.start_for_paths(paths)
    assert _wait(lambda: not controller.busy)
    assert "未找到鸟眼关键点模型" in controller._dialog.warning.text()
    assert not controller._dialog.warning.isHidden()


def test_denoised_source_without_denoising_fails_the_job_once(env) -> None:
    from bird_sharpness.params import AnalysisParams

    controller, _file_list, analyzer, _folder, paths = env
    analyzer.params = AnalysisParams(image_source="denoised")
    analyzer.denoised_lookup = None
    controller.start_for_paths(paths)
    assert _wait(lambda: not controller.busy)
    assert analyzer.analyzed == [] and "降噪功能不可用" in controller._dialog.label.text()
    analyzer.params = AnalysisParams(image_source="jpeg")  # the analyzer decodes the other sources itself
    controller.start_for_paths(paths)
    assert _wait(lambda: not controller.busy)
    assert len(analyzer.analyzed) == 2


def test_bird_limit_option_reaches_the_analyzer_and_the_options_dialog():
    from app_common import superviewer_user_options as opts
    from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog

    key = opts.KEY_BIRD_SHARPNESS_MAX_BIRDS
    window = QWidget()
    controller = BirdSharpnessController(window, _FakeFileList())
    try:
        est = opts.KEY_BIRD_SHARPNESS_EDGE_ESTIMATOR
        assert controller.analyzer().max_birds == 0  # default: every bird
        assert controller.analyzer().edge_estimator == "standard"
        # default image source: the camera's embedded JPEG, tagged so it never passes for a RAW result
        assert controller.analyzer().tile_options.mf_center and controller.analyzer().version.endswith("v15-jpeg")
        opts.apply_runtime_user_options({key: 6, est: "dense", opts.KEY_BIRD_SHARPNESS_MF_TILE: 128,
                                         opts.KEY_BIRD_SHARPNESS_IMAGE_SOURCE: "raw"})
        assert controller.analyzer().max_birds == 6  # picked up at the next job start
        assert controller.analyzer().edge_estimator == "dense"
        assert controller.analyzer().tile_options.mf_tile == 128
        assert controller.analyzer().version.endswith("v15-dense-mf50-128-10")  # RAW decode: no source tag
        dialog = SuperViewerUserOptionsDialog(options={key: 6, est: "dense"})
        try:
            assert dialog._spin_bird_sharpness_max_birds.value() == 6
            assert dialog._combo_bird_sharpness_estimator.currentData() == "dense"
            form = dialog._bird_sharpness_form
            assert not form.image_source.isHidden() and form.image_source.currentData() == "jpeg"
            assert "-jpeg" in form.image_source_note.text()
            form.image_source.setCurrentIndex(form.image_source.findData("denoised"))
            assert dialog.selected_options()[opts.KEY_BIRD_SHARPNESS_IMAGE_SOURCE] == "denoised"
            dialog._spin_bird_sharpness_max_birds.setValue(0)
            assert dialog._spin_bird_sharpness_max_birds.text() == "不限制"
            dialog._combo_bird_sharpness_estimator.setCurrentIndex(0)
            assert dialog.selected_options()[key] == 0
            assert dialog.selected_options()[est] == "standard"
            tiles = dialog._bird_sharpness_tiles
            assert (tiles.full_tile.value(), tiles.mf_center.isChecked(), tiles.mf_tile.value()) == (1024, True, 256)
            tiles.full_tile.setValue(512)
            tiles.mf_center.setChecked(False)
            form = dialog._bird_sharpness_form
            form.enh_mode.setCurrentIndex(form.enh_mode.findData("nobird"))
            form.sam_model.setCurrentIndex(form.sam_model.findData("sam2.1_b.pt"))
            form.sam_scope.setCurrentIndex(form.sam_scope.findData("all"))
            chosen = dialog.selected_options()
            assert chosen[opts.KEY_BIRD_SHARPNESS_FULL_TILE] == 512 and chosen[opts.KEY_BIRD_SHARPNESS_MF_CENTER] == 0
            assert chosen[opts.KEY_BIRD_SHARPNESS_MF_TILE] == 256
            assert chosen[opts.KEY_BIRD_SHARPNESS_ENH_MODE] == "nobird" and chosen[opts.KEY_BIRD_SHARPNESS_ENH_LIFT] == 1
            assert (chosen[opts.KEY_BIRD_SHARPNESS_SAM_MODEL], chosen[opts.KEY_BIRD_SHARPNESS_SAM_SCOPE]) == \
                ("sam2.1_b.pt", "all")
            assert opts.normalize_user_options(chosen)[opts.KEY_BIRD_SHARPNESS_SAM_MODEL] == "sam2.1_b.pt"
        finally:
            dialog.deleteLater()
    finally:
        opts.apply_runtime_user_options(None)
        controller.request_shutdown()
        _wait(controller.is_shutdown_done)
        window.deleteLater()
