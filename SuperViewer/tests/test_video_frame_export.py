"""Real FFmpeg full-frame export, failure retention and worker ownership."""
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from app_common.video import find_ffmpeg, run_video_tool
from SuperViewer.superviewer import video_frame_export as core
from SuperViewer.superviewer import video_frame_export_controller as ui

_APP = QApplication.instance() or QApplication([])


def wait_until(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "中文 100% 视频.mkv"
    code, _, error = run_video_tool([
        find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc=size=96x64:rate=12", "-frames:v", "12",
        "-c:v", "ffv1", str(path),
    ])
    assert code == 0, error
    return path


def test_all_frames_original_size_pixels_unicode_and_repeat(clip, tmp_path):
    before = hashlib.sha256(clip.read_bytes()).digest()
    progress = []
    result = core.export_video_frames(clip, tmp_path / "输出 50%",
                                     on_progress=lambda n, d: progress.append((n, d)))
    assert not result.error and not result.cancelled
    assert result.frames == 12
    images = sorted(Path(result.output_dir).glob("*.png"))
    assert [p.name for p in images] == [f"frame_{n:08d}.png" for n in range(1, 13)]
    code, raw, error = run_video_tool([
        find_ffmpeg(), "-loglevel", "error", "-i", str(clip), "-map", "0:V:0",
        "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ])
    assert code == 0, error
    pixels = []
    for path in images:
        with Image.open(path) as frame:
            assert frame.format == "PNG" and frame.size == (96, 64)
            pixels.append(frame.convert("RGB").tobytes())
    assert b"".join(pixels) == raw
    again = core.export_video_frames(clip, tmp_path / "输出 50%")
    assert not again.error and again.frames == 12
    assert again.output_dir != result.output_dir
    assert len(list(Path(result.output_dir).iterdir())) == 12
    assert hashlib.sha256(clip.read_bytes()).digest() == before
    assert progress[0][0] == 0 and progress[-1][0] == 12


def test_vfr_keeps_every_frame_without_duplicates(clip, tmp_path):
    vfr = tmp_path / "变帧率.mkv"
    code, _, error = run_video_tool([
        find_ffmpeg(), "-loglevel", "error", "-i", str(clip),
        "-vf", r"setpts=if(lt(N\,6)\,N\,6+(N-6)*3)/(12*TB)",
        "-vsync", "0", "-c:v", "ffv1", str(vfr),
    ])
    assert code == 0, error
    result = core.export_video_frames(vfr, tmp_path / "vfr")
    assert not result.error and result.frames == 12


def test_missing_corrupt_and_precancelled_do_not_report_success(tmp_path):
    missing = core.export_video_frames(tmp_path / "missing.mp4", tmp_path / "out")
    assert missing.error and not missing.output_dir
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    result = core.export_video_frames(broken, tmp_path / "out")
    assert result.error and result.frames == 0
    stopped = core.export_video_frames(broken, tmp_path / "untouched", cancelled=lambda: True)
    assert stopped.cancelled and not stopped.error
    assert not (tmp_path / "untouched").exists()


def test_cancellation_reaps_process_and_retains_complete_frames(clip, tmp_path, monkeypatch):
    long_clip = tmp_path / "long.mkv"
    code, _, error = run_video_tool([
        find_ffmpeg(), "-loglevel", "error", "-stream_loop", "19", "-i", str(clip),
        "-c", "copy", str(long_clip),
    ])
    assert code == 0, error
    processes = []
    popen = subprocess.Popen
    stop = threading.Event()

    def slow_popen(args, **kwargs):
        # Real-time input makes cancellation deterministic before EOF.
        args = list(args)
        args.insert(args.index("-i"), "-re")
        process = popen(args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(core.subprocess, "Popen", slow_popen)
    result = core.export_video_frames(
        long_clip, tmp_path / "cancel", cancelled=stop.is_set,
        on_progress=lambda count, _: stop.set() if count >= 1 else None,
    )
    assert result.cancelled and not result.error
    assert 0 < result.frames < 240
    assert all(process.poll() is not None for process in processes)
    images = list(Path(result.output_dir).iterdir())
    assert len(images) == result.frames
    for path in images:
        with Image.open(path) as frame:
            frame.load()


class Files:
    def add_file_context_menu_extender(self, callback):
        self.extender = callback

    def _resolve_source_path_for_action(self, path):
        return path


@pytest.fixture
def controller():
    window = QWidget()
    result = ui.VideoFrameExportController(window, Files())
    yield result
    result.request_shutdown()
    wait_until(result.is_shutdown_done)
    if result._dialog:
        result._dialog.close()
    window.close()
    window.deleteLater()
    _APP.processEvents()


def test_menu_filter_destination_cancel_and_real_batch(controller, clip, tmp_path, monkeypatch):
    from app_common import superviewer_user_options as options
    monkeypatch.setitem(options._RUNTIME_OPTIONS, "video_frame_output_mode", "ask")
    menu = QMenu()
    controller.extend_file_menu(menu, ["photo.jpg"])
    assert not menu.actions()
    controller.extend_file_menu(menu, ["photo.jpg", str(clip)])
    assert menu.actions()[0].text() == "提取全部帧为 PNG…"
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *args: "")
    menu.actions()[0].trigger()
    assert controller._worker is None
    assert controller.start([str(tmp_path / "missing.mp4"), str(clip), str(clip)], tmp_path / "ui")
    assert not controller.start([str(clip)], tmp_path / "second")
    wait_until(controller.is_shutdown_done)
    assert controller._done == 2 and controller._failed == 1 and controller._frames == 12
    assert "_Frames" in controller._dialog.details.toPlainText()
    assert not controller._dialog.running


def test_shutdown_waits_for_actual_finished(controller, clip, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delayed(source, destination, **kwargs):
        entered.set()
        assert release.wait(5)
        return core.FrameExportResult(source, cancelled=True)

    monkeypatch.setattr(ui, "export_video_frames", delayed)
    assert controller.start([str(clip)], tmp_path / "shutdown")
    wait_until(entered.is_set)
    worker = controller._worker
    try:
        controller.request_shutdown()
        assert worker.cancelled.is_set()
        assert not controller.is_shutdown_done()
        assert not controller.start([str(clip)], tmp_path / "restart")
    finally:
        release.set()
    wait_until(controller.is_shutdown_done)
    assert not controller._dialog.isVisible()


def test_cli_uses_same_export(clip, tmp_path, capsys):
    import json
    assert core.main([str(clip), "--output", str(tmp_path / "cli")]) == 0
    assert json.loads(capsys.readouterr().out)["frames"] == 12


@pytest.mark.parametrize("mode", ["source_subdir", "fixed", "ask"])
def test_output_policy_multiple_sources_custom_suffix(controller, clip, tmp_path, monkeypatch, mode):
    import shutil
    from app_common import superviewer_user_options as options
    second = tmp_path / "other" / clip.name
    second.parent.mkdir()
    shutil.copyfile(clip, second)
    destination = tmp_path / "chosen"
    monkeypatch.setitem(options._RUNTIME_OPTIONS, "video_frame_output_mode", mode)
    monkeypatch.setitem(options._RUNTIME_OPTIONS, "video_frame_output_directory", str(destination))
    monkeypatch.setitem(options._RUNTIME_OPTIONS, "video_frame_suffix", "_逐帧")
    prompts = []
    def choose(*args):
        prompts.append(args)
        return str(destination)
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", choose)
    controller.choose_destination([str(clip), str(second)])
    # Options belong to the submitted job, not a subsequent settings edit.
    monkeypatch.setitem(options._RUNTIME_OPTIONS, "video_frame_suffix", "_changed")
    wait_until(controller.is_shutdown_done)
    assert controller._frames == 24 and not controller._failed
    assert len(prompts) == (1 if mode == "ask" else 0)
    if mode == "source_subdir":
        folders = [clip.parent / (clip.stem + "_逐帧"), second.parent / (second.stem + "_逐帧")]
    else:
        folders = [destination / (clip.stem + "_逐帧"), destination / (clip.stem + "_逐帧 (2)")]
    assert all(len(list(folder.glob("*.png"))) == 12 for folder in folders)


def test_invalid_suffix_does_not_create_output_and_default_is_source(clip, tmp_path):
    result = core.export_video_frames(clip, tmp_path / "output", suffix="../escape")
    assert result.error and not result.output_dir and not (tmp_path / "output").exists()
    result = core.export_video_frames(clip)
    assert Path(result.output_dir) == clip.parent / (clip.stem + "_Frames")
    assert result.frames == 12


def test_unconfigured_fixed_directory_does_not_start(controller, clip, monkeypatch):
    from app_common import superviewer_user_options as options
    monkeypatch.setitem(options._RUNTIME_OPTIONS, "video_frame_output_mode", "fixed")
    monkeypatch.setitem(options._RUNTIME_OPTIONS, "video_frame_output_directory", "")
    messages = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *a: messages.append(a))
    controller.choose_destination([str(clip)])
    assert messages and controller._worker is None


@pytest.mark.parametrize("theme,point_size", [("light", 10), ("dark", 16)])
def test_progress_layout_and_escape(controller, clip, tmp_path, monkeypatch, theme, point_size):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtTest import QTest
    from SuperViewer.superviewer.ui_theme import build_palette
    entered, release = threading.Event(), threading.Event()

    def delayed(source, destination, **kwargs):
        entered.set()
        assert release.wait(5)
        return core.FrameExportResult(source, cancelled=True)

    monkeypatch.setattr(ui, "export_video_frames", delayed)
    assert controller.start([str(clip)], tmp_path / "layout")
    wait_until(entered.is_set)
    dialog = controller._dialog
    try:
        dialog.setPalette(build_palette(theme))
        font = dialog.font()
        # Windows offscreen has no system font discovery; load one for visual QA.
        if os.name == "nt":
            font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
            if font_path.exists():
                font_id = QFontDatabase.addApplicationFont(str(font_path))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    font.setFamily(families[0])
        font.setPointSize(point_size)
        dialog.setFont(font)
        dialog.resize(600, 380)
        controller._drain()
        _APP.processEvents()
        for widget in (dialog.label, dialog.bar, dialog.summary, dialog.details, dialog.button):
            assert dialog.rect().contains(widget.geometry())
        assert dialog.details.height() >= 50
        assert dialog.label.height() >= dialog.label.heightForWidth(dialog.label.width())
        qa = os.environ.get("SUPERBIRD_FRAME_EXPORT_QA")
        if qa:
            Path(qa).mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(Path(qa) / f"progress-{theme}.png"))
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert controller._worker.cancelled.is_set()
        assert not controller.is_shutdown_done()
    finally:
        release.set()
    wait_until(controller.is_shutdown_done)


@pytest.mark.parametrize("mode", ["list", "thumbnail"])
def test_main_window_video_menu_exports_and_closes(clip, tmp_path, monkeypatch, mode):
    import importlib
    from app_common import superviewer_user_options
    from SuperViewer.superviewer import paths_settings
    from SuperViewer.superviewer.file_context_menu import build_file_context_menu
    from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel
    main = importlib.import_module("SuperViewer.main")
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / paths_settings.CONFIG_FILENAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths_settings, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(paths_settings, "_get_user_state_dir", lambda: str(settings / "state"))
    monkeypatch.setattr(main, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "_get_app_dir", lambda: str(settings))
    monkeypatch.setattr(superviewer_user_options, "get_user_config_dir", lambda: str(settings))
    monkeypatch.setenv("LOCALAPPDATA", str(settings / "cache"))
    tags = settings / "tags.cfg"
    tags.write_text("", encoding="utf-8")
    monkeypatch.setattr(main, "SuperViewerTaggedFileListPanel",
                        lambda: SuperViewerTaggedFileListPanel(tag_config_path=tags))
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path / "output"))
    window = main.MainWindow(initial_received_files=["skip-restore"])
    window.show()
    menu = None
    try:
        files = window._file_list
        files._set_view_mode(files._MODE_LIST if mode == "list" else files._MODE_THUMB)
        source = str(clip)
        files.load_directory(str(clip.parent))
        wait_until(lambda: source in files._all_files)
        menu = build_file_context_menu(files, [source], source, log_prefix="frame-export-test")
        action = next(a for a in menu.actions() if a.text() == "提取全部帧为 PNG…")
        action.trigger()
        controller = window._video_frame_export
        wait_until(controller.is_shutdown_done)
        assert controller._frames == 12 and controller._failed == 0
    finally:
        if menu is not None:
            menu.deleteLater()
        window.close()
        wait_until(lambda: window._shutdown_finalized)
        window.deleteLater()
        _APP.processEvents()
        # A cancelled background health probe can cache False; do not leak that
        # process-wide result into later metadata tests in this pytest process.
        from app_common.exif_io.exiftool_path import _is_usable_exiftool
        _is_usable_exiftool.cache_clear()
