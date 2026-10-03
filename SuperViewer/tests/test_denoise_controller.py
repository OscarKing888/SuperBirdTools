"""降噪菜单、配置、取消和真实 Qt 完成信号生命周期。"""
from dataclasses import replace
from pathlib import Path
import threading
import time

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

import image_denoise.batch as batch_module
from image_denoise.types import DenoiseOptions, DenoiseResult
from SuperViewer.superviewer import denoise_controller as dc
from SuperViewer.superviewer.super_viewer_user_options_dialog import SuperViewerUserOptionsDialog

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class FileList:
    def __init__(self):
        self.extenders = []
        self.sources = {}

    def add_file_context_menu_extender(self, callback):
        self.extenders.append(callback)

    def _resolve_source_path_for_action(self, path):
        return self.sources.get(path, path)


class Engine:
    closed = False

    def load(self, cancelled=None):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app_common.file_browser._work_pool import BrowserWorkPool

    window, file_list = QWidget(), FileList()
    pool = BrowserWorkPool(5, 2, analysis_workers=2)
    file_list.background_work_pool = lambda: pool
    controller = dc.DenoiseController(window, file_list)
    engine = Engine()
    controller._engine = engine
    monkeypatch.setattr(controller, "engine", lambda _device: engine)
    monkeypatch.setattr(dc, "current_denoise_options", lambda: DenoiseOptions())
    inputs = []
    for index in range(5):
        path = tmp_path / f"鸟{index}.jpg"
        Image.new("RGB", (24, 16)).save(path)
        inputs.append(str(path))
    state = {"gate": None, "active": 0, "peak": 0, "seen": []}
    lock = threading.Lock()

    def processor(source, destination, options, cancelled=None, **kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            state["seen"].append(source)
        try:
            if state["gate"] is not None:
                state["gate"].wait(5)
            if cancelled():
                return DenoiseResult(source, destination, "cancelled")
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            Path(destination).write_bytes(b"completed")
            return DenoiseResult(source, destination)
        finally:
            with lock:
                state["active"] -= 1

    actual_batch = batch_module.run_batch
    monkeypatch.setattr(batch_module, "run_batch", lambda *a, **kw: actual_batch(*a, **kw,
        processor=processor, probe=lambda _p: (24, 16), memory_provider=lambda: (32 * batch_module.GIB, 24 * batch_module.GIB)))
    yield controller, file_list, inputs, state, engine, pool
    controller.request_shutdown()
    if state["gate"] is not None:
        state["gate"].set()
    assert wait_for(controller.is_shutdown_done)
    pool.shutdown(timeout=5)
    if controller._dialog is not None:
        controller._dialog.close()
    window.deleteLater()
    _APP.processEvents()


def test_single_multi_and_directory_menus_and_resolved_sources(env):
    controller, file_list, paths, state, engine, pool = env
    assert file_list.extenders == [controller.extend_file_menu]
    menu = QMenu()
    controller.extend_file_menu(menu, paths[:2])
    assert menu.actions()[0].text() == "批量降噪（2 张）"
    single = QMenu()
    controller.extend_file_menu(single, paths[:1])
    assert single.actions()[0].text() == "降噪"
    directory = QMenu()
    controller.extend_directory_menu(directory, str(Path(paths[0]).parent))
    sub = directory.actions()[0].menu()
    assert [action.text() for action in sub.actions()] == ["降噪当前目录", "降噪目录及子目录"]
    file_list.sources["preview.jpg"] = paths[0]
    assert controller.start_for_paths(["preview.jpg"])
    assert wait_for(lambda: not controller.busy)
    assert state["seen"] == paths[:1]
    assert controller._dialog.label.text() == "降噪完成。"
    assert "成功 1" in controller._dialog.summary.text()


@pytest.mark.parametrize("missing", [False, True])
def test_source_resolution_failure_never_processes_cached_preview(env, monkeypatch, missing):
    controller, file_list, paths, state, engine, pool = env
    messages = []

    def resolve(_path):
        if missing:
            return None
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(file_list, "_resolve_source_path_for_action", resolve)
    monkeypatch.setattr(dc.QMessageBox, "information", lambda *args: messages.append(args[-1]))
    assert not controller.start_for_paths(paths)
    assert not controller.busy and state["seen"] == []
    assert messages and "无法解析照片原文件" in messages[0]


def test_ask_directory_cancel_starts_nothing(env, monkeypatch):
    controller, _, paths, state, engine, pool = env
    monkeypatch.setattr(dc, "current_denoise_options", lambda: DenoiseOptions(output_mode="ask"))
    monkeypatch.setattr(dc.QFileDialog, "getExistingDirectory", lambda *_args: "")
    assert not controller.start_for_paths(paths)
    assert not controller.busy and state["seen"] == []
    assert not (Path(paths[0]).parent / "denoised").exists()


def test_cancel_keeps_worker_owned_until_all_photos_stop(env):
    controller, _, paths, state, engine, pool = env
    state["gate"] = threading.Event()
    assert controller.start_for_paths(paths)
    assert wait_for(lambda: state["active"] == 2)
    assert state["peak"] == 2
    old_worker = controller._worker
    assert not controller.start_for_paths(paths)
    controller.stop()
    assert controller.busy and controller._worker is old_worker
    state["gate"].set()
    assert wait_for(lambda: not controller.busy)
    assert len(state["seen"]) == 2 and "已停止" in controller._dialog.label.text()
    assert not any(Path(paths[0]).parent.glob("denoised/*"))
    count = controller._counts.copy()
    controller._on_result(old_worker, DenoiseResult(paths[0], status="failed"))
    assert controller._counts == count


def test_shutdown_rejects_late_results_and_waits_for_running_pool_actions(env):
    controller, _, paths, state, engine, pool = env
    state["gate"] = threading.Event()
    assert controller.start_for_paths(paths)
    assert wait_for(lambda: state["active"] == 2)
    pool.request_shutdown()
    controller.request_shutdown()
    assert not controller.is_shutdown_done() and not engine.closed
    controller._on_result(controller._worker, DenoiseResult(paths[0]))
    assert controller._counts == {}
    state["gate"].set()
    assert wait_for(controller.is_shutdown_done)
    assert engine.closed and controller._dialog is None
    assert not controller.start_for_paths(paths)


def test_denoise_settings_round_trip_and_output_mode_controls():
    options = {"denoise_output_mode": "fixed", "denoise_output_directory": "/tmp/降噪",
               "denoise_format": "jpeg", "denoise_strength": 65, "denoise_workers": 3,
               "denoise_device": "cpu", "denoise_subdir": "鸟片"}
    dialog = SuperViewerUserOptionsDialog(options=options)
    try:
        chosen = dialog.selected_options()
        assert all(chosen[key] == value for key, value in options.items())
        assert dialog._edit_denoise_directory.isEnabled()
        assert not dialog._edit_denoise_subdir.isEnabled()
        dialog._combo_denoise_mode.setCurrentIndex(dialog._combo_denoise_mode.findData("ask"))
        assert not dialog._edit_denoise_directory.isEnabled()
        assert dialog.selected_options()["denoise_output_mode"] == "ask"
    finally:
        dialog.deleteLater()
