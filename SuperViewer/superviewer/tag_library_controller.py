"""Tag editor/history orchestration; all Qt updates stay on the GUI thread."""
from __future__ import annotations

import os
from time import perf_counter

from app_common.log import get_logger

from .qt_compat import QMessageBox, QTimer
from .tag_library_dialog import TagLibraryDialog, TagTaskDialog, exec_dialog
from .tag_library_transaction import (
    TagEditCancelled, TagLibraryFailure, apply_tag_edit, prepare_tag_edit,
    recover_tag_edits, recovery_directories,
)


_log = get_logger("superviewer.tag_library")


class TagLibraryCommand:
    def __init__(self, controller, plan, *, filters=None):
        self.controller = controller
        self.plan = plan
        self.filters = filters

    def execute(self):
        return self.controller.execute(self)


class TagLibraryController:
    def __init__(self, panel):
        self.panel = panel
        self.busy = False
        self.refreshing = False
        self.blocked = False
        self.dirty_paths = set()
        self._refresh_token = 0
        self._active_task = None
        self.task_runner = self._run_task

    def _run_task(self, parent, title, operation, *, cancellable=True):
        dialog = TagTaskDialog(parent, title, operation, cancellable)
        self._active_task = dialog
        try:
            return dialog.run()
        finally:
            self._active_task = None
            dialog.deleteLater()

    def request_shutdown(self):
        self._refresh_token += 1
        if self._active_task is not None:
            self._active_task.reject()

    @property
    def available(self):
        return not (self.busy or self.refreshing or self.blocked)

    def check_recovery(self):
        self.blocked = bool(recovery_directories(self.panel._tag_config))

    def edit(self):
        if self.busy or self.refreshing or self.panel._tag_shutdown_requested:
            return
        try:
            self.panel._load_tag_config_if_changed()
            self.check_recovery()
            dialog = TagLibraryDialog(self, self.panel.window())
            try:
                exec_dialog(dialog)
            finally:
                dialog.deleteLater()
        except Exception as exc:
            QMessageBox.warning(self.panel, "编辑标签", str(exc))

    def save_draft(self, draft, parent):
        if not self.available:
            raise ValueError("请等待当前操作完成，或先重试恢复。")
        after = draft.serialize()
        if not draft.changed:
            return True
        self.busy = True
        self.panel.command_history_changed.emit()
        try:
            config = self.panel._tag_config
            directory = self.panel.get_current_dir()
            migration = draft.migration()
            plan = self.task_runner(parent, "检查标签同步范围", lambda **kw: prepare_tag_edit(
                config, draft.original, after, directory, migration, **kw))
            answer = QMessageBox.question(parent, "保存标签修改",
                f"{draft.summary()}\n\n同步 {plan.affected_count} 张照片的标签。\n"
                "删除分组会同时删除其子树；本次配置与照片修改可整体撤销。\n是否保存？")
            yes = getattr(QMessageBox, "StandardButton", QMessageBox).Yes
            if answer != yes:
                return False
            self.panel._command_history.add_command(TagLibraryCommand(self, plan))
            return True
        except TagEditCancelled:
            return False
        finally:
            self.busy = False
            self.panel.command_history_changed.emit()

    def execute(self, command):
        panel = self.panel
        if panel._tag_shutdown_requested or self.blocked:
            raise ValueError("当前不能修改标签，请先完成恢复。")
        if os.path.normcase(os.path.abspath(command.plan.config_path)) != os.path.normcase(os.path.abspath(panel._tag_config.path)):
            raise ValueError("标签配置范围已改变。")
        was_busy = self.busy
        self.busy = True
        panel.command_history_changed.emit()
        old_filters = set(panel._active_tag_filters)
        panel._stop_photo_tag_cache_loader()
        try:
            current = self.task_runner(panel.window(), "保存标签配置与照片", lambda **kw: apply_tag_edit(command.plan, **kw))
        except Exception as exc:
            self.check_recovery()
            self.refresh({}, preserve_history=True)
            _log.warning("Tag library transaction failed: %s", exc)
            message = str(exc)
            if not self.blocked:
                message += "\n本次操作未生效或已全部回滚，历史记录保持不变。"
            raise TagLibraryFailure(message) from exc
        else:
            filters = command.filters
            if filters is None:
                filters = {command.plan.filter_mapping.get(tag, tag) for tag in old_filters} - {None}
            # Disk commit has succeeded; presentation errors must not discard its inverse.
            try:
                self.refresh(current, filters=filters, preserve_history=True)
            except Exception:
                _log.exception("Tag library saved; UI refresh failed")
            return TagLibraryCommand(self, command.plan.inverse(), filters=old_filters)
        finally:
            self.busy = was_busy
            panel.command_history_changed.emit()

    def recover(self, parent):
        if self.busy:
            return
        self.busy = True
        try:
            config = self.panel._tag_config
            self.task_runner(parent, "恢复标签修改", lambda **kw: recover_tag_edits(
                config, progress=kw["progress"]), cancellable=False)
            self.panel._command_history.clear()
        finally:
            self.check_recovery()
            self.refresh({}, preserve_history=True)
            self.busy = False
            self.panel.command_history_changed.emit()

    def refresh(self, current, *, filters=None, preserve_history=False):
        panel = self.panel
        self._refresh_token += 1
        token = self._refresh_token
        panel._load_tag_config_if_changed(force=True, preserve_history=preserve_history)
        if filters is not None:
            panel._active_tag_filters = set(filters).intersection(panel._available_tags)
            panel._rebuild_tag_filter_bar()
        # Re-read unchanged photos too: a newly configured label may already be
        # present in their XMP but absent from the previous vocabulary cache.
        panel._stop_photo_tag_cache_loader()
        panel._photo_tag_cache = {}
        panel._photo_tag_cache_complete = False
        self.dirty_paths = set(panel._all_files) | set(current)
        stale = iter(list(panel._meta_cache))
        pending = iter(current.items())
        self.refreshing = True

        def step():
            if token != self._refresh_token:
                return
            if panel._tag_shutdown_requested:
                self.refreshing = False
                return
            start = perf_counter()
            paths = []
            allowed = set(panel._available_tags)
            finished = False
            for _ in range(128):
                try:
                    old_path = next(stale)
                except StopIteration:
                    break
                metadata = panel._meta_cache.get(old_path)
                if isinstance(metadata, dict):
                    metadata.pop("tags", None)
                if perf_counter() - start > .008:
                    QTimer.singleShot(0, step)
                    return
            else:
                QTimer.singleShot(0, step)
                return
            for _ in range(128):
                try:
                    path, tags = next(pending)
                except StopIteration:
                    finished = True
                    break
                path = os.path.normpath(path)
                panel._photo_tag_cache[path] = set(tags).intersection(allowed)
                self.dirty_paths.discard(path)
                paths.append(path)
                if perf_counter() - start > .008:
                    break
            panel._bump_photo_tag_generations(paths)
            panel._sync_photo_tags_to_meta_cache(paths)
            panel._refresh_metadata_state_for_paths(paths)
            if paths:
                panel.photo_tags_cache_updated.emit(paths)
            if not finished:
                QTimer.singleShot(0, step)
                return
            self.refreshing = False
            panel._apply_filter()
            panel.tag_library_changed.emit()
            panel.command_history_changed.emit()
            QTimer.singleShot(0, lambda: panel._start_photo_tag_cache_loader_if_needed(
                panel._all_files, reason="tag_library_edit", allow_without_filters=True))
        step()
