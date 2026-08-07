# -*- coding: utf-8 -*-
"""Undo/redo commands for SuperViewer photo tag writes."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tagged_file_list import SuperViewerTaggedFileListPanel


class SetPhotoTagCommand:
    """Set or unset one configured tag on one or more photo paths."""

    def __init__(
        self,
        panel: SuperViewerTaggedFileListPanel,
        paths: list[str],
        tag: str,
        enabled: bool,
    ) -> None:
        self._panel = panel
        self._paths = list(paths or [])
        self._tag = str(tag or "").strip()
        self._enabled = bool(enabled)

    def would_change(self) -> bool:
        if not self._tag or not self._paths:
            return False
        snapshot = self._panel.configured_tags_snapshot(self._paths)
        for path in self._paths:
            has_tag = self._tag in snapshot.get(path, set())
            if has_tag != self._enabled:
                return True
        return False

    def execute(self) -> SetPhotoTagCommand:
        self._panel._apply_set_tag_for_paths(self._paths, self._tag, self._enabled)
        return SetPhotoTagCommand(self._panel, self._paths, self._tag, not self._enabled)


class ClearPhotoTagsCommand:
    """Clear configured tags for paths; inverse restores the pre-clear snapshot."""

    def __init__(
        self,
        panel: SuperViewerTaggedFileListPanel,
        paths: list[str],
        before_tags_by_path: dict[str, set[str]] | None = None,
        *,
        clearing: bool = True,
    ) -> None:
        self._panel = panel
        self._paths = list(paths or [])
        self._before = {
            path: set(tags)
            for path, tags in (before_tags_by_path or {}).items()
        }
        self._clearing = bool(clearing)

    def would_change(self) -> bool:
        if not self._paths:
            return False
        if not self._clearing:
            return any(self._before.values())
        snapshot = self._panel.configured_tags_snapshot(self._paths)
        return any(snapshot.get(path) for path in self._paths)

    def execute(self) -> ClearPhotoTagsCommand:
        if self._clearing:
            before = self._panel.configured_tags_snapshot(self._paths)
            self._panel._apply_clear_tags_for_paths(self._paths)
            return ClearPhotoTagsCommand(
                self._panel,
                self._paths,
                before,
                clearing=False,
            )
        self._panel._apply_restore_tags_for_paths(self._before)
        return ClearPhotoTagsCommand(
            self._panel,
            self._paths,
            self._before,
            clearing=True,
        )
