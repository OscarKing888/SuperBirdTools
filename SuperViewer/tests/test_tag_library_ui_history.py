import os
import threading
import time

import pytest

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from SuperViewer.superviewer import tagged_file_list as tagged
from SuperViewer.superviewer.image_info_tab_tags import ImageInfoTabPanel_Tags
from SuperViewer.superviewer.qt_compat import QApplication, QMessageBox, QTimer
from SuperViewer.superviewer.tag_library_controller import TagLibraryCommand
from SuperViewer.superviewer.tag_library_dialog import TagLibraryDialog, TagTaskDialog, run_tag_task
from SuperViewer.superviewer.tag_library_model import TagLibraryDraft
from SuperViewer.superviewer.tag_library_transaction import TagEditCancelled, prepare_tag_edit, recovery_directories


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app, tmp_path, monkeypatch):
    cfg = tmp_path / 'tags.cfg'
    cfg.write_text('飞行\n捕食\n', encoding='utf-8')
    widget = tagged.SuperViewerTaggedFileListPanel(tag_config_path=cfg)
    widget._current_dir = str(tmp_path)
    photo = tmp_path / '鸟.jpg'
    photo.write_bytes(b'photo')
    widget._all_files = [str(photo)]
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(widget, '_refresh_metadata_state_for_paths', lambda paths: None)
    widget.errors = errors
    try:
        yield widget
    finally:
        widget.shutdown()
        widget.close()
        app.processEvents()


def rename_plan(panel):
    draft = TagLibraryDraft(panel._tag_config.read_bytes())
    draft.rename(draft.roots[0], '盘旋')
    return prepare_tag_edit(panel._tag_config, draft.original, draft.serialize(), panel.get_current_dir(), draft.migration())


def immediate(parent, title, operation, **kwargs):
    return operation(cancel=lambda: False, progress=lambda *args: None)


def test_library_and_photo_commands_share_history_without_losing_old_entries(panel):
    panel.tag_library.task_runner = immediate
    path = panel._all_files[0]
    panel.set_photo_tag_for_paths([path], '飞行', True)
    panel._active_tag_filters = {'飞行'}
    panel._command_history.add_command(TagLibraryCommand(panel.tag_library, rename_plan(panel)))
    assert panel.available_photo_tags() == ['盘旋', '捕食']
    assert panel._active_tag_filters == {'盘旋'}
    assert panel._photo_tag_cache[path] == {'盘旋'}
    panel.set_photo_tag_for_paths([path], '捕食', True)
    panel.undo()
    assert panel.photo_tags_for_path(path) == {'盘旋'}
    panel.undo()
    assert panel.available_photo_tags() == ['飞行', '捕食']
    assert panel.photo_tags_for_path(path) == {'飞行'}
    assert panel._active_tag_filters == {'飞行'}
    panel.undo()
    assert panel.photo_tags_for_path(path) == set()
    panel.redo()
    panel.redo()
    panel.redo()
    assert panel.photo_tags_for_path(path) == {'盘旋', '捕食'}
    assert panel.errors == []


def test_failed_undo_keeps_stack_and_retry_succeeds(panel, monkeypatch):
    panel.tag_library.task_runner = immediate
    path = panel._all_files[0]
    panel.set_photo_tag_for_paths([path], '飞行', True)
    panel._command_history.add_command(TagLibraryCommand(panel.tag_library, rename_plan(panel)))
    original = PhotoMetaDataXMP.write_subjects
    monkeypatch.setattr(PhotoMetaDataXMP, 'write_subjects', lambda *args: False)
    panel.undo()
    assert panel.can_undo and not panel.can_redo
    assert panel.available_photo_tags() == ['盘旋', '捕食']
    assert '已全部回滚' in panel.errors[-1]
    monkeypatch.setattr(PhotoMetaDataXMP, 'write_subjects', original)
    panel.undo()
    assert panel.available_photo_tags() == ['飞行', '捕食']


def test_editor_cancel_only_changes_draft_and_button_works_without_photo(panel):
    before = panel._tag_config.read_bytes()
    calls = []
    tab = ImageInfoTabPanel_Tags(lambda: [], lambda p: set(), lambda *args: None,
                                 lambda *args: None, edit_tags_callback=lambda: calls.append(True))
    tab.refresh_ui()
    tab.btn_edit.click()
    assert calls == [True]
    assert not tab.btn_clear.isEnabled()
    dialog = TagLibraryDialog(panel.tag_library)
    try:
        dialog.name_edit.setText('鸟类')
        dialog._try(lambda: dialog._add(True))
        dialog.name_edit.setText('白鹭')
        dialog._try(lambda: dialog._add(False))
        assert dialog.draft.roots[-1].children[0].name == '白鹭'
        dialog.reject()
        assert panel._tag_config.read_bytes() == before
        assert not panel.can_undo
    finally:
        dialog.close()
        tab.close()


def test_save_dialog_confirmation_real_worker_and_empty_library(panel, monkeypatch):
    yes = getattr(QMessageBox, 'StandardButton', QMessageBox).Yes
    confirmations = []
    def confirm(parent, title, message):
        confirmations.append(message)
        return yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    panel._tag_config.path.write_bytes(b'')
    panel._load_tag_config_if_changed(force=True)
    dialog = TagLibraryDialog(panel.tag_library)
    try:
        dialog.name_edit.setText('白鹭')
        dialog._try(lambda: dialog._add(False))
        dialog._save()
        assert panel.available_photo_tags() == ['白鹭']
        assert panel.can_undo
        assert '同步 0 张照片' in confirmations[0]
        panel.undo()
        assert panel.available_photo_tags() == []
    finally:
        dialog.close()


def test_worker_keeps_event_loop_responsive_and_reports_exception(app):
    main_thread = threading.get_ident()
    ticks = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    def operation(**kwargs):
        assert threading.get_ident() != main_thread
        time.sleep(.025)
        raise ValueError('worker failure')
    try:
        with pytest.raises(ValueError, match='worker failure'):
            run_tag_task(None, 'test', operation)
        assert ticks
    finally:
        timer.stop()


def test_cancel_waits_for_worker_cleanup(app):
    cleaned = []
    def operation(cancel, progress):
        while not cancel():
            time.sleep(.001)
        time.sleep(.02)
        cleaned.append(True)
        raise TagEditCancelled()
    dialog = TagTaskDialog(None, 'cancel', operation)
    QTimer.singleShot(10, dialog.reject)
    with pytest.raises(TagEditCancelled):
        dialog.run()
    assert cleaned and not dialog.worker.isRunning()
    dialog.deleteLater()


def test_old_metadata_and_tag_worker_results_cannot_restore_renamed_tag(panel, monkeypatch):
    panel.tag_library.task_runner = immediate
    path = panel._all_files[0]
    panel.set_photo_tag_for_paths([path], '飞行', True)
    panel._command_history.add_command(TagLibraryCommand(panel.tag_library, rename_plan(panel)))
    assert panel._merge_metadata_batch_with_photo_tag_cache({path: {'tags': ['飞行']}})[path]['tags'] == ['盘旋']
    stale = type('Worker', (), {'_tag_generation_snapshot': {os.path.normcase(path): 0}})()
    real = panel._photo_tag_loader
    panel._photo_tag_loader = stale
    try:
        panel._on_photo_tag_cache_batch_ready(stale, {path: {'飞行'}})
        assert panel.photo_tags_for_path(path) == {'盘旋'}
    finally:
        panel._photo_tag_loader = real


def test_recovery_block_prevents_regular_tag_writes(panel):
    path = panel._all_files[0]
    panel.tag_library.blocked = True
    panel.set_photo_tag_for_paths([path], '飞行', True)
    assert not panel.can_undo
    assert PhotoMetaDataXMP().read_subjects(path) == []
