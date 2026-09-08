"""Photo tag history commands based on per-file membership snapshots."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Iterable

from app_common.command_history import PartialCommandError

from .photo_tags import TagStates

if TYPE_CHECKING:
    from .tagged_file_list import SuperViewerTaggedFileListPanel


class RestorePhotoTagStatesCommand:
    def __init__(self, panel: SuperViewerTaggedFileListPanel, states: TagStates) -> None:
        self._panel = panel
        self._states = {os.path.normpath(path): dict(values) for path, values in states.items() if path}

    def execute(self) -> RestorePhotoTagStatesCommand | None:
        result = self._panel._apply_photo_tag_states(self._states)
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


class SetPhotoTagCommand(RestorePhotoTagStatesCommand):
    def __init__(self, panel: SuperViewerTaggedFileListPanel, paths: Iterable[str], tag: str, enabled: bool) -> None:
        super().__init__(panel, {path: {tag: enabled} for path in paths})


class ClearPhotoTagsCommand(RestorePhotoTagStatesCommand):
    def __init__(self, panel: SuperViewerTaggedFileListPanel, paths: Iterable[str], tags: Iterable[str]) -> None:
        memberships = {tag: False for tag in tags}
        super().__init__(panel, {path: dict(memberships) for path in paths})
