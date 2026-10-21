"""Batch color edits use real temporary XMPs and controlled owned workers."""
import hashlib
from pathlib import Path
import threading
import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest, QSignalSpy
from PyQt6.QtWidgets import QApplication, QScrollArea

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.photo_edit.assets import _publish, asset_path
from app_common.photo_edit.model import AdjustmentStep
from app_common.photo_edit.storage import RecipeStore
from SuperViewer.superviewer.photo_edit_controller import PhotoEditController
from SuperViewer.superviewer.image_info_tab_color_adjust import ImageInfoTabPanel_ColorAdjust
from SuperViewer.superviewer.image_info_tab_camera_raw import ImageInfoTabPanel_CameraRaw

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(.003)
    return bool(predicate())


@pytest.fixture
def setup(tmp_path):
    controllers = []

    def create(*, store=None):
        controller = PhotoEditController(directory=tmp_path / "state", store=store)
        controllers.append(controller)
        return controller

    yield create
    for controller in controllers:
        controller.request_shutdown()
    assert wait_for(lambda: all(c.is_shutdown_done() for c in controllers))
    for controller in controllers:
        controller.deleteLater()
    _APP.processEvents()


def photo(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (30, 20), "#426896").save(path, format="PNG")
    return str(path)


def select(controller, source):
    controller.select(source)
    assert wait_for(lambda: controller.session(source) is not None)
    return controller.session(source)


def color_recipe(controller, source):
    session = select(controller, source)
    recipe = session.state.current.recipe.validated()
    recipe.steps = [AdjustmentStep("exposure", {"exposure": .75}),
                    AdjustmentStep("invert", enabled=False),
                    AdjustmentStep("exposure", {"exposure": -.25})]
    controller.edit(recipe)
    return recipe.validated()


def test_applies_captured_steps_preserves_camera_inputs_chinese_metadata_and_originals(tmp_path, setup):
    source = photo(tmp_path, "参考.png")
    first = photo(tmp_path, "白鹭.ARW")
    second = photo(tmp_path, "白鹭.JPG")
    denoised = photo(tmp_path, "降噪.tif")
    store = RecipeStore()
    first_state = store.load(first)
    first_state.current.recipe.camera["exposure"] = 2
    first_state.use_denoised(denoised)
    first_state.current.recipe.camera["exposure"] = -1
    first_state.current.recipe.camera_enabled = False
    first_state.current.recipe.steps = [AdjustmentStep("invert")]
    store.save(first_state)
    PhotoMetaDataXMP().write_title(first, "池塘边的白鹭")
    originals = {path: Path(path).read_bytes() for path in (source, first, second, denoised)}
    controller = setup()
    target = select(controller, first)
    before = target.state.current.recipe.validated()
    expected = color_recipe(controller, source)
    assert controller.batch.start(source, [source, first, second, first, "视频.mp4"])
    assert wait_for(lambda: not controller.batch.busy)
    assert (controller.batch.applied, controller.batch.unchanged, controller.batch.failed) == (2, 1, 0)
    loaded = store.load(first)
    assert loaded.active == first_state.active
    assert loaded.current.recipe.camera == before.camera
    assert not loaded.current.recipe.camera_enabled
    assert loaded.inputs["original"].recipe == first_state.inputs["original"].recipe
    assert loaded.current.recipe.steps == expected.steps == store.load(second).current.recipe.steps
    assert PhotoMetaDataXMP().read(first)["XMP-dc:Title"] == "池塘边的白鹭"
    assert all(Path(path).read_bytes() == content for path, content in originals.items())
    select(controller, first)
    controller.undo()
    assert target.state.current.recipe == before
    controller.redo()
    assert target.state.current.recipe.steps == expected.steps


def test_empty_steps_reset_targets_and_noop_does_not_create_sidecars(tmp_path, setup):
    source, target, empty = [photo(tmp_path, name + ".png") for name in ("参考", "已调色", "空白")]
    controller = setup()
    color_recipe(controller, target)
    select(controller, source)
    assert controller.batch.start(source, [target, empty])
    assert wait_for(lambda: not controller.batch.busy)
    assert not RecipeStore().load(target).current.recipe.steps
    assert not Path(empty).with_suffix(".xmp").exists()
    assert controller.batch.applied == 1 and controller.batch.unchanged == 1


def test_bad_missing_and_conflicted_photos_fail_individually_without_losing_metadata(tmp_path, setup):
    source, bad, conflict, good = [photo(tmp_path, name + ".png") for name in ("参考", "损坏", "冲突", "正常")]
    sidecar = Path(bad).with_suffix(".xmp")
    sidecar.write_bytes(b"<broken")
    controller = setup()
    select(controller, conflict)
    external = RecipeStore().load(conflict)
    external.current.recipe.camera["exposure"] = 3
    RecipeStore().save(external)
    expected = color_recipe(controller, source)
    results = []
    controller.batch.item_finished.connect(lambda *values: results.append(values))
    assert controller.batch.start(source, [bad, str(tmp_path / "missing.png"), conflict, good])
    assert wait_for(lambda: not controller.batch.busy)
    assert controller.batch.failed == 3 and controller.batch.applied == 1
    assert len(results) == 4 and all("失败" in result[1] for result in results[:3])
    assert sidecar.read_bytes() == b"<broken"
    assert RecipeStore().load(conflict).current.recipe.camera["exposure"] == 3
    assert controller.recovery.load(conflict).current.recipe.steps == expected.steps
    assert RecipeStore().load(good).current.recipe.steps == expected.steps


def test_lut_copied_before_save_and_missing_lut_preserves_target(tmp_path, setup, monkeypatch):
    from SuperViewer.superviewer import photo_edit_batch as module
    source, target, missing = [photo(tmp_path, name + "/白鹭.png") for name in ("原目录", "另一目录", "失败目录")]
    cube = b"LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"
    digest = hashlib.sha256(cube).hexdigest()
    reference = {"sha256": digest, "path": f".superbird-assets/{digest}.cube"}
    _publish(Path(source), reference, cube)
    controller = setup()
    session = select(controller, source)
    recipe = session.state.current.recipe.validated()
    recipe.steps = [AdjustmentStep("colorLookup", {"asset": reference, "builtin": ""})]
    controller.edit(recipe)
    threads = []
    original = module.copy_recipe_assets

    def copy(*args):
        threads.append(threading.get_ident())
        return original(*args)

    monkeypatch.setattr(module, "copy_recipe_assets", copy)
    assert controller.batch.start(source, [target])
    assert wait_for(lambda: not controller.batch.busy)
    assert controller.batch.applied == 1
    assert asset_path(target, reference).read_bytes() == cube
    assert RecipeStore().load(target).current.recipe.steps == recipe.validated().steps
    assert threads and all(thread != threading.get_ident() for thread in threads)
    revision = RecipeStore().load(target).revision
    asset_path(target, reference).unlink()
    assert controller.batch.start(source, [target])
    assert wait_for(lambda: not controller.batch.busy)
    assert controller.batch.unchanged == 1
    assert asset_path(target, reference).read_bytes() == cube
    assert RecipeStore().load(target).revision == revision
    asset_path(source, reference).unlink()
    assert controller.batch.start(source, [missing])
    assert wait_for(lambda: not controller.batch.busy)
    assert controller.batch.failed == 1
    assert not Path(missing).with_suffix(".xmp").exists()


@pytest.mark.parametrize("stage", ["load", "prepare", "save"])
@pytest.mark.parametrize("shutdown", [False, True])
def test_stop_or_shutdown_retains_worker_and_only_completed_writes(tmp_path, setup, monkeypatch, stage, shutdown):
    from SuperViewer.superviewer import photo_edit_batch as module
    source, target, later = [photo(tmp_path, name + ".png") for name in ("参考", "当前", "后续")]
    started, release = threading.Event(), threading.Event()

    def block():
        started.set()
        assert release.wait(5)

    class Store(RecipeStore):
        def load(self, path):
            if str(path) == target and stage == "load":
                block()
            return super().load(path)

        def save(self, state):
            if state.source == target and stage == "save":
                block()
            return super().save(state)

    if stage == "prepare":
        monkeypatch.setattr(module, "copy_recipe_assets", lambda *_: block())
    controller = setup(store=Store())
    recipe = color_recipe(controller, source)
    try:
        assert controller.batch.start(source, [target, later])
        assert wait_for(started.is_set)
        if shutdown:
            controller.request_shutdown()
            assert not controller.is_shutdown_done()
        else:
            controller.batch.stop()
        assert controller._workers  # No early QThread deletion.
    finally:
        release.set()
    assert wait_for(lambda: not controller.batch.busy and not controller._workers)
    assert RecipeStore().load(target).current.recipe.steps == (recipe.steps if stage == "save" else [])
    assert not Path(later).with_suffix(".xmp").exists()
    assert wait_for(lambda: not controller.unsafe_drafts())


def test_navigation_pauses_dispatch_and_parameter_file_guards_are_scoped(tmp_path, setup, monkeypatch):
    from SuperViewer.superviewer import photo_edit_batch as module
    source, target, other = [photo(tmp_path, name + ".png") for name in ("参考", "目标", "范围外")]
    started, release = threading.Event(), threading.Event()

    def copy(*_):
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(module, "copy_recipe_assets", copy)
    controller = setup()
    expected = color_recipe(controller, source)
    try:
        assert controller.batch.start(source, [target])
        assert not controller.batch.start(source, [target])
        assert wait_for(started.is_set)
        assert not controller.edit(expected)
        assert not controller.reload(source)
        assert not controller.run_operation("test", lambda *_: None)
        assert controller.file_operation_reason([str(Path(target).with_suffix(".xmp"))])
        assert not controller.file_operation_reason([other])
        controller.set_suspended(True)
    finally:
        release.set()
    assert wait_for(lambda: controller.batch._phase == "ready")
    assert not Path(target).with_suffix(".xmp").exists()
    controller.set_suspended(False)
    select(controller, other)
    assert wait_for(lambda: not controller.batch.busy)
    assert controller.current_source == other
    assert RecipeStore().load(target).current.recipe.steps == expected.steps
    assert not controller.file_operation_reason([target])


def test_stale_preparation_keeps_newer_session_and_save_failures_report_unsafe_drafts(tmp_path, setup, monkeypatch):
    from SuperViewer.superviewer import photo_edit_batch as module
    source, target = [photo(tmp_path, name + ".png") for name in ("参考", "目标")]
    started, release = threading.Event(), threading.Event()

    def copy(*_):
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(module, "copy_recipe_assets", copy)
    controller = setup()
    target_session = select(controller, target)
    color_recipe(controller, source)
    try:
        assert controller.batch.start(source, [target])
        assert wait_for(started.is_set)
        latest = target_session.state.current.recipe.validated()
        latest.camera["exposure"] = 2.5
        target_session.edit(latest)  # Simulates a completion outside the widget guards.
    finally:
        release.set()
    assert wait_for(lambda: not controller.batch.busy)
    assert controller.batch.failed == 1 and target_session.state.current.recipe == latest
    controller.flush()
    assert wait_for(lambda: not target_session.dirty)

    save = controller.recovery.save

    def fail(state):
        if state.source == target:
            raise OSError("恢复目录已满")
        return save(state)

    monkeypatch.setattr(controller.recovery, "save", fail)
    assert controller.batch.start(source, [target])
    assert wait_for(lambda: not controller.batch.busy)
    assert controller.batch.failed == 1
    assert target in controller.unsafe_drafts()
    assert RecipeStore().load(target).current.recipe == latest
    monkeypatch.setattr(controller.recovery, "save", save)


def test_many_photos_stay_bounded_and_leave_selection_unchanged(tmp_path, setup):
    source = photo(tmp_path, "参考.png")
    paths = [photo(tmp_path, f"目标{i}.png") for i in range(30)]
    controller = setup()
    expected = color_recipe(controller, source)
    sizes = []
    controller.batch.progress.connect(lambda *_: sizes.append(len(controller.sessions)))
    assert controller.batch.start(source, paths)
    assert wait_for(lambda: not controller.batch.busy, timeout=20)
    assert controller.batch.applied == 30 and controller.batch.failed == 0
    assert controller.current_source == source
    assert max(sizes) <= 24 and len(controller.sessions) <= 24
    assert RecipeStore().load(paths[-1]).current.recipe.steps == expected.steps


def test_button_uses_complete_filtered_snapshot_keyboard_and_empty_filter(tmp_path, setup):
    from app_common.file_browser._browser_core import _set_cached_actual_path
    source, visible, hidden = [photo(tmp_path, name + ".png") for name in ("参考", "列表图片", "被筛掉")]
    stale = str(tmp_path / "旧目录" / "列表图片.png")
    _set_cached_actual_path(stale, visible)

    class Files:
        _filtered_files = [source, stale, "视频.mp4"]
        _all_files = [source, visible, hidden]

    controller = setup()
    expected = color_recipe(controller, source)
    panel = ImageInfoTabPanel_ColorAdjust(controller, files=Files())
    camera = ImageInfoTabPanel_CameraRaw(controller)
    panel.on_photo_selected(source)
    camera.on_photo_selected(source)
    panel.resize(360, 640)
    panel.show()
    try:
        assert panel.apply_all_button.isEnabled()
        assert panel.apply_all_button.parent() is panel
        scroll = panel.findChild(QScrollArea)
        panel.set_groups_expanded(False)
        editing = QSignalSpy(panel.editing_started)
        panel.apply_all_button.setFocus()
        QTest.keyClick(panel.apply_all_button, Qt.Key.Key_Space)
        assert controller.batch.busy
        assert len(editing) == 1 and editing[0] == [source]
        assert not panel.content.isEnabled() and not camera.content.isEnabled()
        assert not panel.auto_button.isEnabled()
        Files._filtered_files = [hidden]  # Directory/filter changes do not retarget a running batch.
        assert wait_for(lambda: not controller.batch.busy)
        assert RecipeStore().load(visible).current.recipe.steps == expected.steps
        assert not Path(hidden).with_suffix(".xmp").exists()
        assert panel.content.isEnabled() and camera.content.isEnabled()
        assert all(not group.is_expanded() for group in panel.groups)
        assert panel.apply_all_button.geometry().top() >= scroll.geometry().bottom()
        assert panel._batch_dialog.bar.value() == 2
        assert "已应用 1 张" in panel._batch_dialog.label.text()
        Files._filtered_files = []
        panel.apply_all_button.click()
        assert not controller.batch.busy
        assert "没有可应用" in panel.status.text()
    finally:
        if panel._batch_dialog:
            panel._batch_dialog.close()
        panel.close()
        camera.close()
        panel.deleteLater()
        camera.deleteLater()
