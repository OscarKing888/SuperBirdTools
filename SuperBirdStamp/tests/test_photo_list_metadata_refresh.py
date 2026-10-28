from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QPushButton

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from birdstamp import config
from birdstamp.gui import editor
from birdstamp.gui import template_context
from birdstamp.gui.editor_photo_list import PHOTO_COL_NAME, PHOTO_COL_ROW, PHOTO_COL_TITLE, PHOTO_LIST_PHOTO_INFO_ROLE
from birdstamp.gui.editor_utils import path_key


_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    for name in (
        "_start_bird_detector_preload", "_run_deferred_startup_tasks",
        "_schedule_workspace_photo_selection", "_on_photo_selected",
    ):
        monkeypatch.setattr(editor.BirdStampEditorWindow, name, lambda *args, **kwargs: None)
    instance = editor.BirdStampEditorWindow()
    try:
        yield instance
    finally:
        deadline = time.monotonic() + 5
        accepted = instance.close()
        while not accepted and time.monotonic() < deadline:
            _APP.processEvents()
            accepted = instance.close()
        assert accepted, "编辑器后台任务未及时结束"
        instance.deleteLater()
        _APP.processEvents()


def _wait_metadata_loaded(window, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        # A finished first source attempt may leave failed rows for the watch's
        # bounded retry. Wait for a complete applied snapshot, not just QThread.
        if (window._photo_list_metadata_loader is None
                and not window._photo_list_metadata_loading
                and not window._photo_list_metadata_updates
                and not window._photo_list_metadata_pending_keys):
            return
        time.sleep(0.01)
    raise AssertionError("照片信息后台读取未在限定时间内完成")


def _add_photo(window, path):
    window._append_photo_path_to_list(
        path, existing_keys=set(), default_settings=window._build_current_render_settings(),
    )


def test_refresh_button_and_action_are_exposed(window):
    action = window.photo_list.refresh_metadata_action
    assert action.shortcut().toString() == "F5"
    assert action.shortcutContext() == Qt.ShortcutContext.WidgetShortcut
    assert action in window.photo_list._tree_widget.actions()
    buttons = [btn for btn in window.findChildren(QPushButton) if btn.text() == "刷新信息"]
    assert len(buttons) == 1
    assert buttons[0].toolTip() == action.toolTip()


def test_refresh_rereads_changed_xmp_without_touching_files(window, tmp_path):
    photo = tmp_path / "翠鸟.png"
    Image.new("RGB", (16, 12), "green").save(photo)
    original = photo.read_bytes()
    xmp = PhotoMetaDataXMP()
    assert xmp.write_title(str(photo), "普通翠鸟")
    _add_photo(window, photo)
    window._restart_photo_list_metadata_loader()
    _wait_metadata_loaded(window)
    item = window._find_photo_item_by_path(photo)
    assert item.text(PHOTO_COL_TITLE) == "普通翠鸟", {
        "raw": window.raw_metadata_cache.get(path_key(photo)),
        "pending": window._photo_list_metadata_pending_keys,
        "queued": window._photo_list_metadata_updates,
    }

    # 外部工具（SuperViewer/SuperPicky）修改侧车后，列表需要手动刷新才能看到。
    assert xmp.write_title(str(photo), "斑鱼狗（雌）")
    sidecar_after_edit = xmp.sidecar_path_for(str(photo)).read_bytes()
    window.photo_list.refresh_metadata_action.trigger()
    assert path_key(photo) in window._photo_list_metadata_pending_keys or window._photo_list_metadata_loader
    _wait_metadata_loaded(window)

    assert item.text(PHOTO_COL_TITLE) == "斑鱼狗（雌）"
    assert path_key(photo) not in window._photo_list_metadata_pending_keys
    assert not window._photo_list_metadata_forced_refresh_keys
    assert path_key(photo) in window._photo_export_dirty_keys
    # 刷新只读：原图与侧车均不被改写。
    assert photo.read_bytes() == original
    assert xmp.sidecar_path_for(str(photo)).read_bytes() == sidecar_after_edit
    assert xmp.read(str(photo)).get("XMP-dc:Title") == "斑鱼狗（雌）"


def test_refresh_forces_current_photo_context_rebuild(window, tmp_path, monkeypatch):
    photo = tmp_path / "白鹭.png"
    Image.new("RGB", (16, 12), "white").save(photo)
    _add_photo(window, photo)
    metadata = {"SourceFile": str(photo), "XMP-dc:Title": "白鹭", "ImageWidth": 16, "ImageHeight": 12}
    window.current_path = photo
    window.current_raw_metadata = dict(metadata)
    applied = []
    monkeypatch.setattr(window, "_restart_photo_list_metadata_loader", lambda: None)
    monkeypatch.setattr(window, "render_preview", lambda *a, **k: applied.append("render"))
    monkeypatch.setattr(window, "_refresh_preview_label", lambda *a, **k: applied.append("label"))

    # 未刷新时，相同元数据不重建上下文。
    window._apply_photo_list_metadata_batch({str(photo): dict(metadata)})
    assert applied == []

    # 刷新后即使 EXIF/XMP 未变（例如仅 report.db 变化）也要重建当前预览。
    window._refresh_photo_list_metadata()
    assert path_key(photo) in window._photo_list_metadata_pending_keys
    window._apply_photo_list_metadata_batch({str(photo): dict(metadata)})
    assert applied == ["label"]
    assert not window._photo_list_metadata_forced_refresh_keys


@pytest.mark.parametrize("selected", ["none", "pending", "decoded"])
def test_metadata_queue_apply_and_item_lookup_never_resolve_photo_paths(window, tmp_path, monkeypatch, selected):
    from app_common.exif_io.metadata_store import get_store

    photo = (tmp_path / "MixedCase白鹭.png").resolve()
    Image.new("RGB", (16, 12), "white").save(photo)
    _add_photo(window, photo)
    get_store().prepare_paths([str(photo)])
    # App-bundled routing is loaded once independently of photo-volume reads.
    template_context.AutoProxyTemplateContextProvider.route_definitions()
    item = window._find_photo_item_by_path(photo)
    window._photo_item_map.clear()  # Exercise the Qt-role lookup fallback too.
    window._metadata_store_watch._timer.stop()
    if selected != "none":
        window.current_path = photo
        window.current_raw_metadata = {"SourceFile": str(photo), "Title": "旧鸟名", "_metadata_revision": 0}
    render_requests = []
    if selected == "decoded":
        window.current_source_image = Image.new("RGB", (16, 12), "white")
        window.current_source_full_size = (16, 12)
        # The metadata callback hands already-decoded pixels to the renderer;
        # its image pipeline is covered separately by preview regressions.
        monkeypatch.setattr(window, "render_preview", lambda **kwargs:
                            render_requests.append((window.current_raw_metadata["Title"], kwargs)))
    metadata = {"SourceFile": str(photo), "Title": "新白鹭", "XMP-dc:Title": "新白鹭",
                "ImageWidth": 16, "ImageHeight": 12, "_metadata_read_ok": True,
                "_metadata_complete": True, "_metadata_revision": 1}
    probes = []

    def forbidden(*args, **kwargs):
        frame, callers = sys._getframe(1), []
        while frame is not None and len(callers) < 8:
            callers.append(f"{frame.f_code.co_name}:{frame.f_lineno}")
            frame = frame.f_back
        probes.append((args, callers))
        raise AssertionError(f"metadata delivery queried the source filesystem: {args!r}; {callers}")

    def protect_photo_volume(original):
        def guarded(path, *args, **kwargs):
            candidate = Path(os.path.abspath(os.fspath(path)))
            if candidate == photo.parent or candidate.parent == photo.parent:
                return forbidden(path, *args, **kwargs)
            # Rendering can inspect its local template/font resources; those
            # are separate from the source-volume metadata delivery contract.
            return original(path, *args, **kwargs)
        return guarded

    with monkeypatch.context() as blocked:
        blocked.setattr(Path, "resolve", protect_photo_volume(Path.resolve))
        blocked.setattr(Path, "stat", protect_photo_volume(Path.stat))
        blocked.setattr(os.path, "realpath", protect_photo_volume(os.path.realpath))
        window._queue_photo_list_metadata_batch({str(photo): metadata})
        window._process_photo_list_metadata_updates()
    assert not probes, "\n".join(str(probe) for probe in probes[:8])
    assert item.text(PHOTO_COL_TITLE) == "新白鹭"
    assert not window._photo_list_metadata_updates
    assert not item.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE).metadata_is_snapshot
    if selected != "none":
        assert window.current_raw_metadata["Title"] == "新白鹭"
    if selected == "decoded":
        assert render_requests == [("新白鹭", {"sync_transport": False})]


def test_refresh_ignored_for_empty_list_and_during_restore(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "_restart_photo_list_metadata_loader", lambda: started.append(True))
    window._refresh_photo_list_metadata()
    assert started == []
    monkeypatch.setattr(window, "_workspace_restore_in_progress", lambda: True)
    window._refresh_photo_list_metadata()
    assert started == []


def test_hot_received_raw_rows_never_probe_images_or_reread_sidecars(window, tmp_path, monkeypatch):
    paths = [tmp_path / f"bird_{index:03d}.ARW" for index in range(20)]
    for path in paths:
        path.write_bytes(b"RAW test fixture; pixel decode must stay off the list callback")
    image_opens = []

    def forbidden_image_open(path, *args, **kwargs):
        image_opens.append(path)
        raise RuntimeError("Simulated unsupported RAW / Ultralytics PIL hook")

    def read_chunk(loader, chunk):
        return {
            path: {"SourceFile": path, "File:FileName": Path(path).name,
                   "XMP-superpicky:filename": "侧车-" + Path(path).name,
                   "XMP-dc:Title": "普通翠鸟", "EXIF:ImageWidth": 8640, "EXIF:ImageHeight": 5760}
            for path in chunk
        }

    monkeypatch.setattr(Image, "open", forbidden_image_open)
    monkeypatch.setattr(template_context, "_read_sidecar_metadata_cached",
                        lambda *args: pytest.fail("GUI reread sidecar instead of using worker snapshot"))
    monkeypatch.setattr(editor.EditorPhotoListMetadataLoader, "_read_chunk", read_chunk)
    window.add_received_file_paths([str(path) for path in paths])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _APP.processEvents()
        if (window.photo_list.topLevelItemCount() == len(paths)
                and not window._photo_list_metadata_pending_keys
                and not window._photo_list_metadata_loading):
            break
        time.sleep(0.001)
    assert window.photo_list.topLevelItemCount() == len(paths)
    assert not window._photo_list_metadata_pending_keys
    assert not window._photo_list_metadata_updates
    assert not image_opens
    for path in paths:
        item = window._find_photo_item_by_path(path)
        assert item.text(PHOTO_COL_NAME) == "侧车-" + path.name
        assert item.text(PHOTO_COL_TITLE) == "普通翠鸟"
        # Display snapshots must not change later export/metadata-read policy.
        assert not item.data(PHOTO_COL_ROW, PHOTO_LIST_PHOTO_INFO_ROLE).metadata_is_snapshot


@pytest.mark.parametrize("max_rows,budget,expected", [(2, 10.0, 2), (20, 0.025, 3)])
def test_metadata_ui_tick_obeys_item_and_time_limits(window, tmp_path, monkeypatch, max_rows, budget, expected):
    applied = []
    clock = [0.0]

    def apply(batch):
        applied.extend(batch)
        clock[0] += 0.01

    monkeypatch.setattr(editor, "_PHOTO_LIST_METADATA_APPLY_MAX", max_rows)
    monkeypatch.setattr(editor, "_PHOTO_LIST_METADATA_APPLY_BUDGET_S", budget)
    monkeypatch.setattr(editor.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(window, "_apply_photo_list_metadata_batch", apply)
    # This test isolates scheduling after the list-membership guard.
    monkeypatch.setattr(window, "_find_photo_item_by_path", lambda path: object())
    window._queue_photo_list_metadata_batch({str(tmp_path / f"{i}.ARW"): {"ISO": i} for i in range(20)})
    assert applied == []
    window._process_photo_list_metadata_updates()
    assert len(applied) == expected
    assert len(window._photo_list_metadata_updates) == 20 - expected
    assert window.photo_list.updatesEnabled()


def test_metadata_queue_yields_and_finishes_only_after_rows_are_applied(window, tmp_path, monkeypatch):
    applied, event_observations = [], []
    monkeypatch.setattr(editor, "_PHOTO_LIST_METADATA_APPLY_MAX", 1)

    def apply(batch):
        applied.extend(batch)
        if len(applied) == 1:
            QTimer.singleShot(0, lambda: event_observations.append(len(applied)))

    monkeypatch.setattr(window, "_apply_photo_list_metadata_batch", apply)
    # This test isolates scheduling after the list-membership guard.
    monkeypatch.setattr(window, "_find_photo_item_by_path", lambda path: object())
    window._photo_list_metadata_loading = True
    window._queue_photo_list_metadata_batch({str(tmp_path / f"{i}.ARW"): {"ISO": i} for i in range(20)})
    window._finish_photo_list_metadata_loading()
    assert window._photo_list_metadata_loading
    _wait_metadata_loaded(window)
    assert len(applied) == 20
    assert event_observations and event_observations[0] < 20
    assert not window._photo_list_metadata_apply_timer.isActive()


@pytest.mark.parametrize("cancel", ["clear", "refresh", "close"])
def test_metadata_queue_discards_pending_and_late_results(window, tmp_path, monkeypatch, cancel):
    photo = tmp_path / "bird.ARW"
    photo.write_bytes(b"raw")
    _add_photo(window, photo)
    batch = {str(photo): {"SourceFile": str(photo), "XMP-dc:Title": "旧结果"}}
    window._queue_photo_list_metadata_batch(batch)
    assert window._photo_list_metadata_updates
    monkeypatch.setattr(window, "_restart_photo_list_metadata_loader", lambda: None)
    if cancel == "clear":
        window._clear_photos_state(show_placeholder=False)
    elif cancel == "refresh":
        window._refresh_photo_list_metadata()
    else:
        window.close()
    window._queue_photo_list_metadata_batch(batch)
    assert not window._photo_list_metadata_updates
    assert not window._photo_list_metadata_apply_timer.isActive()
    assert window.raw_metadata_cache.get(path_key(photo), {}).get("XMP-dc:Title") != "旧结果"


def test_metadata_loader_handoff_waits_for_finished_and_keeps_queued_rows(window, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    loads = []
    first = tmp_path / "first.ARW"
    second = tmp_path / "second.ARW"
    for photo in (first, second):
        photo.write_bytes(b"raw")
        _add_photo(window, photo)

    class Loader(QThread):
        metadata_batch_ready = pyqtSignal(object)
        progress_updated = pyqtSignal(int, int)

        def __init__(self, paths, parent=None):
            super().__init__(parent)
            self.paths = paths
            loads.append(self)

        def stop(self):
            pass

        def run(self):
            entered.set()
            assert release.wait(5)
            self.metadata_batch_ready.emit({p: {"SourceFile": p, "File:FileName": Path(p).name} for p in self.paths})

    monkeypatch.setattr(editor, "EditorPhotoListMetadataLoader", Loader)
    # The first photo already has a queued result; the next worker must only read second.
    window._queue_photo_list_metadata_batch({str(first): {"File:FileName": first.name, "ISO": 100}})
    window._restart_photo_list_metadata_loader()
    first_loader = window._photo_list_metadata_loader
    assert first_loader.paths == [str(second)]
    try:
        assert entered.wait(3)
        release.set()
        assert first_loader.wait(3000)
        # run() returned but GUI has not dispatched the real finished signal yet.
        window._restart_photo_list_metadata_loader()
        assert window._photo_list_metadata_loader is first_loader
        assert len(loads) == 1
        _wait_metadata_loaded(window)
        assert not window._photo_list_metadata_pending_keys
        assert len(loads) == 1
    finally:
        release.set()
        if window._photo_list_metadata_loader is first_loader:
            first_loader.wait(3000)


def test_birdstamp_entry_disables_dependency_autoinstall_before_detector_import():
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root), str(root / "SuperBirdStamp"))),
               YOLO_AUTOINSTALL="true", ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS="0")
    result = subprocess.run(
        [sys.executable, "-c", "import birdstamp, os, sys; "
         "assert os.environ['YOLO_AUTOINSTALL'] == 'false'; "
         "assert os.environ['ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS'] == '1'; "
         "assert 'ultralytics' not in sys.modules"],
        cwd=root, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_pending_edit_stays_visible_while_full_source_snapshot_is_unavailable(window, tmp_path):
    from app_common.exif_io.metadata_store import get_store
    from birdstamp.gui.editor_photo_list import PHOTO_COL_ISO, PHOTO_COL_RATING

    photo = (tmp_path / "待同步白鹭.png").resolve()
    with Image.new("RGB", (16, 12), "white") as image:
        image.save(photo)
    _add_photo(window, photo)
    window._metadata_store_watch._timer.stop()
    store = get_store()
    old = store.remember_many({str(photo): {
        "SourceFile": str(photo), "Title": "旧鸟名", "ISO": 640,
    }}, profile="full").result(5)[str(photo)]
    window._apply_photo_list_metadata_batch({str(photo): old})
    item = window._find_photo_item_by_path(photo)
    assert item.text(PHOTO_COL_ISO) == "640"

    # Invalidate the full source row, as after an external sidecar write. The
    # next accepted edit is durable even if a RAW/network-volume read must retry.
    store.note_sidecar_write(str(photo)).result(5)
    committed = store.submit_edit(str(photo), {"title": "最新白鹭", "rating": 4}).result(5).record
    assert committed["_metadata_complete"] is False
    window._queue_photo_list_metadata_batch({str(photo): committed})
    window._process_photo_list_metadata_updates()

    assert item.text(PHOTO_COL_TITLE) == "最新白鹭"
    assert item.text(PHOTO_COL_RATING) == window._format_rating_display(4)
    assert item.text(PHOTO_COL_ISO) == "640"
    assert path_key(photo) in window._photo_list_metadata_pending_keys
    assert window.raw_metadata_cache[path_key(photo)]["_metadata_complete"] is False
