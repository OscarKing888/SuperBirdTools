"""Selection, save conflicts and real QThread ownership; isolated app state."""
import threading
import time

import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from app_common.photo_edit.model import Recipe
from app_common.photo_edit.session import EditSession
from app_common.photo_edit.storage import RecipeStore, EditConflict, RecoveryStore
from SuperViewer.superviewer.photo_edit_controller import PhotoEditController

_APP = QApplication.instance() or QApplication([])


def wait_for(predicate,timeout=6):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        _APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return bool(predicate())


@pytest.fixture
def photo(tmp_path):
    path = tmp_path/"中文照片.png"
    Image.new("RGB",(24,16),"gray").save(path)
    return str(path)


@pytest.fixture
def controllers(tmp_path):
    live = []
    def create(**kwargs):
        controller = PhotoEditController(directory=tmp_path/"user-state",**kwargs)
        live.append(controller)
        return controller
    yield create
    for controller in live:
        controller.request_shutdown()
    assert wait_for(lambda:all(c.is_shutdown_done() for c in live))
    for controller in live:
        controller.deleteLater()
    _APP.processEvents()


def adjust(controller,value,commit=True):
    recipe = controller.session().state.current.recipe.validated()
    recipe.camera["exposure"] = value
    controller.edit(recipe,commit=commit)


def test_file_operation_flushes_gesture_and_blocks_edits_until_finished(photo,controllers):
    controller = controllers()
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    adjust(controller,.6,commit=False)
    assert controller.file_operation_reason([photo],prepare=True)
    assert wait_for(lambda:not controller.session().dirty and not controller._saving)
    assert not controller.file_operation_reason([photo])
    assert RecipeStore().load(photo).current.recipe.camera["exposure"]==.6
    controller.begin_file_operation()
    adjust(controller,1.2)
    controller.undo()
    assert controller.session().state.current.recipe.camera["exposure"]==.6
    controller.end_file_operation()
    adjust(controller,1.2)
    assert controller.session().state.current.recipe.camera["exposure"]==1.2


def test_same_stem_sibling_waits_for_xmp_and_inflight_operation(photo,controllers):
    from pathlib import Path
    controller = controllers()
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    adjust(controller,.2)
    sibling = str(Path(photo).with_suffix('.arw'))
    assert controller.file_operation_reason([sibling],prepare=True)
    assert wait_for(lambda:not controller.session().dirty and not controller._saving)
    finish = threading.Event()
    controller.run_operation('long-import',lambda *_:finish.wait(2))
    assert controller.file_operation_reason([sibling])
    finish.set()
    assert wait_for(lambda:not controller._operations)
    assert not controller.file_operation_reason([sibling])


def test_file_operation_waits_for_owned_render_and_pauses_new_reads(photo,controllers):
    started = threading.Event()
    def render(_state,*,cancelled,**kwargs):
        started.set()
        while not cancelled():
            time.sleep(.002)
        from app_common.image_processing_types import ImageProcessingCancelled
        raise ImageProcessingCancelled()
    controller = controllers(renderer=render)
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    controller.request_render(photo,'viewport')
    assert wait_for(started.is_set)
    assert controller.file_operation_reason([photo],prepare=True)
    assert wait_for(lambda:controller._render_active is None)
    assert not controller.file_operation_reason([photo])
    controller.begin_file_operation()
    controller.request_render(photo,'viewport')
    assert not controller._render_pending
    controller.end_file_operation([photo])


def test_session_gesture_undo_is_shared_and_inputs_are_independent(photo,tmp_path):
    session = EditSession(RecipeStore().load(photo))
    for value in (.1,.2,.3):
        recipe = session.state.current.recipe.validated()
        recipe.camera["exposure"] = value
        session.edit(recipe,commit=False)
    session.finish_gesture()
    assert session.undo() and session.state.current.recipe.camera["exposure"]==0
    assert not session.history.can_undo
    assert session.redo() and session.state.current.recipe.camera["exposure"]==.3
    derivative = tmp_path/"denoised.tif"
    derivative.write_bytes(b"a version")
    session.add_denoised(derivative)
    assert session.state.current.recipe.camera["exposure"]==0
    assert not session.history.can_undo
    session.select_input("original")
    assert session.state.current.recipe.camera["exposure"]==.3 and session.history.can_undo


def test_load_is_background_and_held_navigation_starts_no_io(photo,controllers):
    calls = []
    class Store(RecipeStore):
        def load(self,source):
            calls.append(threading.get_ident())
            return super().load(source)
    controller = controllers(store=Store())
    controller.set_suspended(True)
    controller.select(photo)
    controller.request_render(photo,"A")
    _APP.processEvents()
    assert not calls and not controller._workers and not controller._render_pending
    controller.set_suspended(False)
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    assert calls and all(t!=threading.get_ident() for t in calls)


def test_edit_during_save_keeps_new_changes_dirty_and_saves_latest(photo,controllers):
    started,release = threading.Event(),threading.Event()
    class Store(RecipeStore):
        saves = 0
        def save(self,state):
            self.saves += 1
            if self.saves==1:
                started.set()
                assert release.wait(4)
            return super().save(state)
    store = Store()
    controller = controllers(store=store)
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    adjust(controller,1)
    controller.flush()
    try:
        assert wait_for(started.is_set)
        adjust(controller,2)
        controller.flush()
        assert controller.session().dirty and store.saves==1
    finally:
        release.set()
    assert wait_for(lambda:store.saves==2 and not controller.session().dirty)
    assert store.load(photo).current.recipe.camera["exposure"]==2
    assert wait_for(lambda:not controller._workers)
    assert controller.recovery.load(photo) is None


def test_external_edit_conflict_retains_draft_and_does_not_overwrite(photo,controllers):
    controller = controllers()
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    adjust(controller,1)
    outside = RecipeStore().load(photo)
    outside.current.recipe.camera["exposure"] = 3
    RecipeStore().save(outside)
    controller.flush()
    assert wait_for(lambda:controller.session().conflict)
    assert RecipeStore().load(photo).current.recipe.camera["exposure"]==3
    assert controller.recovery.load(photo).current.recipe.camera["exposure"]==1
    adjust(controller,2)
    controller.flush()
    assert wait_for(lambda:not controller._saving)
    assert controller.recovery.load(photo).current.recipe.camera["exposure"]==2
    assert not controller.unsafe_drafts()


def test_stale_render_never_replaces_newer_consumer_request(photo,controllers):
    started,release = threading.Event(),threading.Event()
    calls = []
    def render(snapshot,**kwargs):
        calls.append(snapshot.current.recipe.camera["exposure"])
        if len(calls)==1:
            started.set()
            assert release.wait(4)  # Emulate a decoder that cannot cancel mid-call.
        return calls[-1]
    controller = controllers(renderer=render)
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    results = []
    controller.render_ready.connect(lambda *value:results.append(value))
    old_id = controller.request_render(photo,"A")
    try:
        assert wait_for(started.is_set)
        adjust(controller,1)
        new_id = controller.request_render(photo,"A")
        assert new_id>old_id
    finally:
        release.set()
    assert wait_for(lambda:len(results)==1)
    assert results[0][2:]==(new_id,1)


def test_shutdown_retains_workers_until_finished_and_flushes_pending_edit(photo,controllers):
    started,release = threading.Event(),threading.Event()
    def render(*args,**kwargs):
        started.set()
        assert release.wait(4)
        return "stale"
    controller = controllers(renderer=render)
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    adjust(controller,1.5,commit=False)
    controller.request_render(photo,"A")
    try:
        assert wait_for(started.is_set)
        controller.request_shutdown()
        assert not controller.is_shutdown_done()
        assert controller._workers
    finally:
        release.set()
    assert wait_for(controller.is_shutdown_done)
    assert not controller.unsafe_drafts()
    assert RecipeStore().load(photo).current.recipe.camera["exposure"]==1.5


def test_recovery_cleanup_cannot_delete_a_newer_draft(photo,tmp_path):
    recovery = RecoveryStore(tmp_path/"recover")
    old = RecipeStore().load(photo)
    recovery.save(old)
    newer = RecipeStore().load(photo)
    newer.current.recipe.camera["exposure"] = 1
    recovery.save(newer)
    recovery.remove_if_matches(old)
    assert recovery.load(photo).current.recipe.camera["exposure"]==1


def test_journal_failure_is_reported_as_unsafe_to_close(photo,controllers,monkeypatch):
    controller = controllers()
    controller.select(photo)
    assert wait_for(lambda:controller.session() is not None)
    adjust(controller,1)
    monkeypatch.setattr(controller.recovery,"save",lambda _:(_ for _ in ()).throw(OSError("磁盘已满")))
    controller.flush()
    assert wait_for(lambda:bool(controller.session().error))
    assert "当前窗口" in controller.session().error
    assert controller.unsafe_drafts()==[photo]


@pytest.mark.parametrize("during", ["edit", "input", "selection", "shutdown", "failure"])
def test_auto_adjust_worker_captures_source_and_rejects_stale_edits(photo, controllers, monkeypatch, tmp_path, during):
    from app_common.photo_edit.auto_adjust import AutoAdjustmentResult
    from app_common.photo_edit.model import AdjustmentStep
    started, release = threading.Event(), threading.Event()
    threads = []
    def analyze(snapshot, mode, **kwargs):
        threads.append(threading.get_ident())
        started.set()
        assert release.wait(4)
        if during == "failure":
            raise ValueError("自动调节测试读取失败")
        recipe = snapshot.current.recipe.validated()
        recipe.steps.append(AdjustmentStep("levels", id="auto-levels-tone-test"))
        return AutoAdjustmentResult(recipe.validated(), mode, True)
    monkeypatch.setattr("app_common.photo_edit.auto_adjust.prepare_auto_adjustment", analyze)
    controller = controllers()
    controller.select(photo)
    assert wait_for(lambda: controller.session() is not None)
    original = controller.session()
    errors = []
    controller.error_changed.connect(lambda _, error: errors.append(error))
    try:
        assert controller.auto_adjust()
        assert not controller.auto_adjust()  # No duplicate worker on double click.
        assert wait_for(started.is_set)
        assert controller._workers and threads[0] != threading.get_ident()
        if during == "edit":
            adjust(controller, 1.25)
        elif during == "input":
            path = tmp_path/"denoised.tif"
            path.write_bytes(b"input identity")
            original.add_denoised(path)
        elif during == "selection":
            other = tmp_path/"other.png"
            Image.new("RGB", (32, 32)).save(other)
            controller.select(str(other))
            assert wait_for(lambda: controller.session() is not None)
        elif during == "shutdown":
            controller.request_shutdown()
            assert not controller.is_shutdown_done()
    finally:
        release.set()
    assert wait_for(lambda: not controller._operations)
    if during == "selection":
        assert original.state.current.recipe.steps
        assert controller.session().state.current.recipe.is_identity()
    else:
        assert not original.state.current.recipe.steps
    if during in {"edit", "input"}:
        assert any("未覆盖" in error for error in errors)
    elif during == "failure":
        assert any("读取失败" in error for error in errors)
        assert not original.dirty and not original.history.can_undo


def test_real_auto_adjust_undo_redo_and_save(photo, controllers):
    import numpy as np
    from app_common.photo_edit.auto_adjust import auto_step_mode
    pytest.importorskip("app_common.photo_edit._native")
    pixels = np.tile(np.arange(50, 181, dtype=np.uint8)[None, :, None], (32, 1, 3))
    Image.fromarray(pixels).save(photo)
    controller = controllers()
    controller.select(photo)
    assert wait_for(lambda: controller.session() is not None)
    assert controller.auto_adjust()
    assert wait_for(lambda: not controller._operations)
    recipe = controller.session().state.current.recipe.validated()
    assert {step.kind for step in recipe.steps} == {"levels"}
    assert all(auto_step_mode(step) == "all" for step in recipe.steps)
    controller.undo()
    assert controller.session().state.current.recipe.is_identity()
    assert not controller.session().history.can_undo
    controller.redo()
    assert controller.session().state.current.recipe == recipe
    controller.flush()
    assert wait_for(lambda: not controller.session().dirty)
    assert RecipeStore().load(photo).current.recipe == recipe
    assert controller.auto_adjust()
    assert wait_for(lambda: not controller._operations)
    assert not controller.session().dirty
    controller.undo()
    assert controller.session().state.current.recipe.is_identity()


def test_auto_adjust_starts_no_work_during_navigation_or_file_operation(photo, controllers):
    controller = controllers()
    controller.select(photo)
    assert wait_for(lambda: controller.session() is not None)
    controller.set_suspended(True)
    assert not controller.auto_adjust()
    controller.set_suspended(False)
    controller.begin_file_operation()
    assert not controller.auto_adjust()
    controller.end_file_operation()
