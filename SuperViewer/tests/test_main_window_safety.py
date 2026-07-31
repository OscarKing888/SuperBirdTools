from __future__ import annotations

import importlib
import os
import threading
import time
from pathlib import Path

import pytest

from SuperViewer.main import MainWindow
from SuperViewer.superviewer.qt_compat import QApplication, QThread, pyqtSignal
from SuperViewer.superviewer.tagged_file_list import SuperViewerTaggedFileListPanel


main_module = importlib.import_module("SuperViewer.main")


class _LabelProbe:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = ""

    def setText(self, value: str) -> None:
        self.text = value

    def setToolTip(self, value: str) -> None:
        self.tooltip = value


class _PreviewProbe:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.focus_boxes: list[object] = []

    def set_image(self, path: str) -> None:
        self.paths.append(path)

    def set_focus_box(self, focus_box) -> None:
        self.focus_boxes.append(focus_box)


class _RenameFileListProbe:
    def __init__(self, directory: Path) -> None:
        self.directory = os.path.normpath(str(directory))
        self.pending: list[str] = []
        self.loaded: list[str] = []

    def get_current_dir(self) -> str:
        return self.directory

    def set_pending_selection(self, paths, **_kwargs) -> None:
        self.pending = list(paths)

    def load_directory(self, path: str, **_kwargs) -> None:
        self.loaded.append(path)


class _RenameHarness:
    _same_filesystem_key = staticmethod(MainWindow._same_filesystem_key)
    _case_safe_rename_path = staticmethod(MainWindow._case_safe_rename_path)
    _rename_target_path = staticmethod(MainWindow._rename_target_path)

    def __init__(self, directory: Path) -> None:
        self.file_label = _LabelProbe()
        self.preview_panel = _PreviewProbe()
        self._file_list = _RenameFileListProbe(directory)
        self._current_exif_path = ""

    @staticmethod
    def _file_writes_allowed(_path: str | None = None) -> bool:
        return True

    @staticmethod
    def _file_writes_disabled_message(*_args) -> str:
        return ""


def _rename(harness: _RenameHarness, path: Path, name: str) -> Path:
    return Path(MainWindow._rename_photo_from_info_panel(harness, str(path), name))


def test_rename_uses_only_strict_same_directory_sidecar(tmp_path: Path) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    parent_sidecar = tmp_path / "Bird.xmp"
    parent_sidecar.write_text("source metadata", encoding="utf-8")
    derived = export_dir / "Bird-DxO_DeepPRIME.jpg"
    derived.write_bytes(b"derived")

    renamed = _rename(_RenameHarness(export_dir), derived, "Renamed")

    assert renamed == export_dir / "Renamed.jpg"
    assert renamed.read_bytes() == b"derived"
    assert parent_sidecar.read_text(encoding="utf-8") == "source metadata"
    assert not (export_dir / "Renamed.xmp").exists()


def test_rename_moves_same_stem_xmp_and_preserves_suffix_case(tmp_path: Path) -> None:
    photo = tmp_path / "鸟.JPG"
    sidecar = tmp_path / "鸟.XMP"
    photo.write_bytes(b"photo")
    sidecar.write_text("中文元数据", encoding="utf-8")

    renamed = _rename(_RenameHarness(tmp_path), photo, "隼")

    assert renamed == tmp_path / "隼.JPG"
    assert renamed.read_bytes() == b"photo"
    assert (tmp_path / "隼.XMP").read_text(encoding="utf-8") == "中文元数据"
    assert not sidecar.exists()


@pytest.mark.parametrize("conflict_kind", ["photo", "sidecar"])
def test_rename_preflights_all_conflicts_without_partial_changes(
    tmp_path: Path,
    conflict_kind: str,
) -> None:
    photo = tmp_path / "Photo.jpg"
    sidecar = tmp_path / "Photo.xmp"
    photo.write_bytes(b"source photo")
    sidecar.write_text("source metadata", encoding="utf-8")
    if conflict_kind == "photo":
        (tmp_path / "Target.jpg").write_bytes(b"target photo")
    else:
        (tmp_path / "Target.xmp").write_text("target metadata", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _rename(_RenameHarness(tmp_path), photo, "Target")

    assert photo.read_bytes() == b"source photo"
    assert sidecar.read_text(encoding="utf-8") == "source metadata"


def test_rename_rolls_back_photo_when_sidecar_rename_fails(tmp_path: Path) -> None:
    photo = tmp_path / "Photo.jpg"
    sidecar = tmp_path / "Photo.xmp"
    photo.write_bytes(b"source photo")
    sidecar.write_text("source metadata", encoding="utf-8")

    class _FailingSidecarHarness(_RenameHarness):
        @staticmethod
        def _case_safe_rename_path(source: Path, target: Path) -> None:
            if source.suffix.lower() == ".xmp":
                raise OSError("simulated sidecar failure")
            MainWindow._case_safe_rename_path(source, target)

    with pytest.raises(OSError, match="simulated sidecar failure"):
        _rename(_FailingSidecarHarness(tmp_path), photo, "Target")

    assert photo.read_bytes() == b"source photo"
    assert sidecar.read_text(encoding="utf-8") == "source metadata"
    assert not (tmp_path / "Target.jpg").exists()
    assert not (tmp_path / "Target.xmp").exists()


def test_rename_is_case_only_safe_for_photo_and_sidecar(tmp_path: Path) -> None:
    photo = tmp_path / "Bird.jpg"
    sidecar = tmp_path / "Bird.xmp"
    photo.write_bytes(b"photo")
    sidecar.write_text("metadata", encoding="utf-8")

    renamed = _rename(_RenameHarness(tmp_path), photo, "bird")

    names = {entry.name for entry in tmp_path.iterdir()}
    assert renamed.name == "bird.jpg"
    assert "bird.jpg" in names
    assert "bird.xmp" in names
    assert not any(".rename-" in name for name in names)


def test_rename_preserves_image_format_and_supports_dots_in_stem(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    assert MainWindow._rename_target_path(str(source), "bird.v2") == tmp_path / "bird.v2.jpg"
    assert MainWindow._rename_target_path(str(source), "bird.JPG") == tmp_path / "bird.jpg"
    with pytest.raises(ValueError, match="不能改变图片格式"):
        MainWindow._rename_target_path(str(source), "bird.png")


class _FocusIndexHarness:
    def __init__(self) -> None:
        self._focus_source_index: dict[tuple[str, str], str] = {}

    _rebuild_focus_source_index = SuperViewerTaggedFileListPanel._rebuild_focus_source_index
    focus_source_for_sibling = SuperViewerTaggedFileListPanel.focus_source_for_sibling


def test_focus_source_lookup_uses_directory_index_without_scandir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    photo = tmp_path / "Bird.jpg"
    arw = tmp_path / "Bird.ARW"
    cr3 = tmp_path / "Bird.cr3"
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other_arw = other_dir / "Bird.ARW"
    for path in (photo, arw, cr3, other_arw):
        path.write_bytes(b"x")
    index = _FocusIndexHarness()
    index._rebuild_focus_source_index([str(photo), str(cr3), str(other_arw), str(arw)])

    monkeypatch.setattr(os, "scandir", lambda *_args, **_kwargs: pytest.fail("GUI lookup scanned a directory"))

    assert index.focus_source_for_sibling(str(photo)) == os.path.normpath(str(arw))
    main_harness = type("MainHarness", (), {"_file_list": index})()
    assert MainWindow._find_source_file_by_stem(main_harness, str(photo)) == os.path.normpath(str(arw))


class _SignalProbe:
    def __init__(self) -> None:
        self.callbacks: list = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self.callbacks = [existing for existing in self.callbacks if existing != callback]

    def emit(self, *args) -> None:
        for callback in list(self.callbacks):
            callback(*args)


class _FocusLoaderProbe:
    instances: list["_FocusLoaderProbe"] = []
    active = 0
    max_active = 0

    def __init__(
        self,
        request_id: int,
        _photo_cache_key: str,
        preview_path: str,
        source_path: str,
        _width: int,
        _height: int,
        _parent=None,
    ) -> None:
        self.request_id = request_id
        self.preview_path = preview_path
        self.source_path = source_path
        self.focus_loaded = _SignalProbe()
        self.finished = _SignalProbe()
        self.running = False
        self.interrupted = False
        self.deleted = False
        self.wait_calls: list[int | None] = []
        self.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.active = 0
        cls.max_active = 0

    def start(self) -> None:
        self.running = True
        type(self).active += 1
        type(self).max_active = max(type(self).max_active, type(self).active)

    def isRunning(self) -> bool:
        return self.running

    def requestInterruption(self) -> None:
        self.interrupted = True

    def wait(self, timeout: int | None = None) -> bool:
        self.wait_calls.append(timeout)
        return not self.running

    def deleteLater(self) -> None:
        self.deleted = True

    def finish(self, focus_box=None) -> None:
        if not self.running:
            return
        self.focus_loaded.emit(self.request_id, focus_box, self.source_path or self.preview_path)
        self.running = False
        type(self).active -= 1
        self.finished.emit()


class _FocusHarness:
    _queue_focus_loader_request = MainWindow._queue_focus_loader_request
    _start_focus_loader = MainWindow._start_focus_loader
    _on_focus_box_loaded = MainWindow._on_focus_box_loaded
    _on_focus_loader_finished = MainWindow._on_focus_loader_finished
    _finalize_focus_loader = MainWindow._finalize_focus_loader
    _stop_focus_loader = MainWindow._stop_focus_loader

    def __init__(self) -> None:
        self._shutdown_requested = False
        self._focus_loader = None
        self._focus_pending_request = None
        self._focus_request_sequence = 0
        self._focus_display_request_id = 0
        self.preview_panel = _PreviewProbe()


def test_focus_loader_is_single_flight_and_runs_only_latest_pending(monkeypatch) -> None:
    _FocusLoaderProbe.reset()
    monkeypatch.setattr(main_module, "FocusBoxLoader", _FocusLoaderProbe)
    harness = _FocusHarness()
    first = (1, "first.jpg", "first.arw", 100, 80)
    second = (2, "second.jpg", "second.arw", 100, 80)
    latest = (3, "latest.jpg", "latest.arw", 100, 80)

    harness._focus_display_request_id = 1
    harness._queue_focus_loader_request(first)
    first_loader = _FocusLoaderProbe.instances[0]
    harness._focus_display_request_id = 2
    harness._queue_focus_loader_request(second)
    harness._focus_display_request_id = 3
    harness._queue_focus_loader_request(latest)

    assert len(_FocusLoaderProbe.instances) == 1
    assert first_loader.interrupted
    assert harness._focus_pending_request == latest

    first_loader.finish(("stale",))
    assert len(_FocusLoaderProbe.instances) == 2
    latest_loader = _FocusLoaderProbe.instances[1]
    assert latest_loader.request_id == 3
    assert harness.preview_panel.focus_boxes == []
    assert _FocusLoaderProbe.max_active == 1

    latest_loader.finish(("latest",))
    assert harness.preview_panel.focus_boxes == [("latest",)]
    assert harness._focus_loader is None
    assert first_loader.deleted and latest_loader.deleted


def _process_events_until(app: QApplication, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


def test_main_window_defers_close_without_blocking_or_destroying_running_focus_loader(
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    _FocusLoaderProbe.reset()
    monkeypatch.setattr(main_module, "FocusBoxLoader", _FocusLoaderProbe)
    window = MainWindow(initial_received_files=["skip-restore"])
    window.show()
    app.processEvents()
    window._focus_display_request_id = 1
    window._start_focus_loader((1, "first.jpg", "first.arw", 100, 80))
    loader = _FocusLoaderProbe.instances[-1]
    try:
        started = time.perf_counter()
        window.close()
        elapsed = time.perf_counter() - started

        assert elapsed < 0.5
        assert window._shutdown_requested
        assert not window._shutdown_finalized
        assert window._focus_loader is loader
        assert loader.running
        assert loader.interrupted
        assert loader.wait_calls and all(call is not None for call in loader.wait_calls)

        loader.finish()
        assert _process_events_until(app, lambda: window._shutdown_finalized)
        assert window._focus_loader is None
        assert window.image_info_tabs._shutdown_requested
        assert window.preview_panel._shutdown_requested
    finally:
        loader.finish()
        window.close()
        app.processEvents()


def test_deferred_hidden_close_explicitly_quits_application_after_finalizing(
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    _FocusLoaderProbe.reset()
    monkeypatch.setattr(main_module, "FocusBoxLoader", _FocusLoaderProbe)
    window = MainWindow(initial_received_files=["skip-restore"])
    window.show()
    app.processEvents()
    window._focus_display_request_id = 1
    window._start_focus_loader((1, "first.jpg", "first.arw", 100, 80))
    loader = _FocusLoaderProbe.instances[-1]
    quit_requested = threading.Event()

    class _ApplicationProbe:
        @staticmethod
        def instance():
            return type("Instance", (), {"quit": staticmethod(quit_requested.set)})()

    try:
        window.close()
        assert window._shutdown_requested
        assert not window._shutdown_finalized
        assert not window.isVisible()

        # The window is already hidden, so Qt's lastWindowClosed fallback can
        # no longer be relied upon when the retry finally accepts closeEvent.
        monkeypatch.setattr(main_module, "QApplication", _ApplicationProbe)
        loader.finish()

        assert _process_events_until(
            app,
            lambda: window._shutdown_finalized and quit_requested.is_set(),
        )
    finally:
        loader.finish()
        window.close()
        app.processEvents()


class _BlockingFocusLoader(QThread):
    focus_loaded = pyqtSignal(int, object, str)

    def __init__(
        self,
        request_id: int,
        _photo_cache_key: str,
        preview_path: str,
        source_path: str,
        _width: int,
        _height: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.request_id = request_id
        self.preview_path = preview_path
        self.source_path = source_path
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self) -> None:
        self.started.set()
        self.release.wait(2.0)
        if not self.isInterruptionRequested():
            self.focus_loaded.emit(
                self.request_id,
                ("focus",),
                self.source_path or self.preview_path,
            )


def test_deferred_close_retains_real_qthread_until_slow_focus_work_finishes(
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(main_module, "FocusBoxLoader", _BlockingFocusLoader)
    window = MainWindow(initial_received_files=["skip-restore"])
    window.show()
    app.processEvents()
    window._focus_display_request_id = 1
    window._start_focus_loader((1, "first.jpg", "first.arw", 100, 80))
    loader = window._focus_loader
    assert isinstance(loader, _BlockingFocusLoader)
    assert loader.started.wait(1.0)
    try:
        started = time.perf_counter()
        window.close()

        assert (time.perf_counter() - started) < 0.5
        assert not window._shutdown_finalized
        assert window._focus_loader is loader
        assert loader.isRunning()

        loader.release.set()
        assert _process_events_until(app, lambda: window._shutdown_finalized)
        assert window._focus_loader is None
        try:
            assert not loader.isRunning()
        except RuntimeError:
            # deleteLater may already have released the completed QThread.
            pass
    finally:
        loader.release.set()
        try:
            loader.wait(2000)
        except RuntimeError:
            pass
        window.close()
        app.processEvents()


def test_exiftool_teardown_is_deferred_off_the_gui_thread(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    close_started = threading.Event()
    release_close = threading.Event()

    def slow_close_exiftool() -> None:
        close_started.set()
        release_close.wait(2.0)

    monkeypatch.setattr(main_module, "close_exiftool_process", slow_close_exiftool)
    window = MainWindow(initial_received_files=["skip-restore"])
    window.show()
    app.processEvents()
    try:
        started = time.perf_counter()
        window.close()

        assert (time.perf_counter() - started) < 0.5
        assert close_started.wait(1.0)
        assert not window._shutdown_finalized
        assert not window._exiftool_shutdown_done.is_set()

        release_close.set()
        assert _process_events_until(app, lambda: window._shutdown_finalized)
        assert window._exiftool_shutdown_done.is_set()
    finally:
        release_close.set()
        window.close()
        app.processEvents()
