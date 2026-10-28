"""Photo tag history commands based on per-file membership snapshots."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Iterable

from app_common.command_history import CommandHistory, CommandHistoryError, PartialCommandError

from .photo_tags import TagStates

if TYPE_CHECKING:
    from .tagged_file_list import SuperViewerTaggedFileListPanel


class RestorePhotoTagStatesCommand:
    def __init__(self, panel: SuperViewerTaggedFileListPanel, states: TagStates) -> None:
        self._panel = panel
        self._states = {os.path.normpath(path): dict(values) for path, values in states.items() if path}

    def execute(self) -> RestorePhotoTagStatesCommand | None:
        result = self._panel._apply_photo_tag_states(self._states)
        return self.inverse_for_result(result)

    def inverse_for_result(self, result):
        inverse = (
            RestorePhotoTagStatesCommand(self._panel, result.inverse_states)
            if result.inverse_states else None
        )
        if result.failed_paths:
            remaining = {
                path: self._states[path]
                for path in result.failed_paths
                if path in self._states
            }
            details = "\n".join(
                f"{path}: {reason}" for path, reason in list(result.failed_paths.items())[:5]
            )
            raise PartialCommandError(
                f"{len(result.failed_paths)} 个文件的标签未能保存。\n{details}",
                inverse=inverse,
                remaining=RestorePhotoTagStatesCommand(self._panel, remaining) if remaining else None,
            )
        return inverse


class PhotoTagCommandHistory(CommandHistory):
    """Preserve the shared history order while tag commits complete asynchronously."""
    def __init__(self, panel, max_commands=100):
        super().__init__(max_commands)
        self.panel = panel
        self.busy = False
        self._generation = 0

    def clear(self):
        self._generation += 1
        super().clear()

    def add_command(self, cmd, execute=True):
        if execute and isinstance(cmd, RestorePhotoTagStatesCommand):
            return self._submit(cmd, "add")
        if self.busy:
            raise CommandHistoryError("请等待标签保存完成。")
        return super().add_command(cmd, execute=execute)

    def undo(self):
        if self.busy:
            return
        if self._undo and isinstance(self._undo[-1], RestorePhotoTagStatesCommand):
            return self._submit(self._undo[-1], "undo")
        return super().undo()

    def redo(self):
        if self.busy:
            return
        if self._redo and isinstance(self._redo[-1], RestorePhotoTagStatesCommand):
            return self._submit(self._redo[-1], "redo")
        return super().redo()

    def _submit(self, command, operation):
        if self.busy:
            return
        self.busy = True
        generation = self._generation
        self._notify()

        def complete(result):
            self.busy = False
            if generation != self._generation:
                self._notify()
                return
            error = None
            try:
                inverse = command.inverse_for_result(result)
            except PartialCommandError as exc:
                inverse, error = exc.inverse, exc
            if operation == "add":
                self._record_inverse(inverse)
            else:
                source, target = (self._undo, self._redo) if operation == "undo" else (self._redo, self._undo)
                if error is not None and error.remaining is not None:
                    source[-1] = error.remaining
                else:
                    source.pop()
                if inverse is not None:
                    target.append(inverse)
            self._notify()
            if error is not None:
                self.panel._show_tag_write_error({"add": "保存标签", "undo": "撤销标签", "redo": "重做标签"}[operation], error)

        try:
            return self.panel._apply_photo_tag_states_async(command._states, complete)
        except Exception:
            self.busy = False
            self._notify()
            raise


class SetPhotoTagCommand(RestorePhotoTagStatesCommand):
    def __init__(self, panel: SuperViewerTaggedFileListPanel, paths: Iterable[str], tag: str, enabled: bool) -> None:
        super().__init__(panel, {path: {tag: enabled} for path in paths})


class ClearPhotoTagsCommand(RestorePhotoTagStatesCommand):
    def __init__(self, panel: SuperViewerTaggedFileListPanel, paths: Iterable[str], tags: Iterable[str]) -> None:
        memberships = {tag: False for tag in tags}
        super().__init__(panel, {path: dict(memberships) for path in paths})
