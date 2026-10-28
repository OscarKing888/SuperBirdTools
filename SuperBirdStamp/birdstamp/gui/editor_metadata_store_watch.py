"""Refresh BirdStamp's complete snapshots after edits in either application."""
from __future__ import annotations

from pathlib import Path
import os
import time

from PyQt6.QtCore import QObject, QTimer

from app_common.file_browser._work_action import CallableAction
from app_common.file_browser._work_policy import WorkKind
from birdstamp.gui.editor_metadata_state import complete_metadata


def path_key(path):
    # Editor list keys are absolute/casefolded; its paths were already resolved
    # by the loading worker. The timer must not perform another filesystem read.
    return os.path.abspath(os.fspath(path)).casefold()


class EditorMetadataStoreWatch(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self._sequence = 0
        self._future = None
        self._closed = False
        self._retry_paths = set()
        self._retry_at = 0.0
        self._retry_delay = 0.5
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def request_shutdown(self):
        self._closed = True
        self._timer.stop()
        if self._future is not None:
            self.editor._preview_action_pool.cancel(self._future)

    def _tick(self):
        if self._closed:
            return
        if self._future is not None:
            if not self._future.done():
                return
            future, self._future = self._future, None
            try:
                sequence, records, attempted, failed = future.result()
            except Exception:
                return
            self._sequence = sequence
            self._retry_paths.difference_update(attempted)
            self._retry_paths.update(failed)
            if failed:
                self._retry_at = time.monotonic() + self._retry_delay
                self._retry_delay = min(30.0, self._retry_delay * 2)
            elif attempted and not self._retry_paths:
                self._retry_at, self._retry_delay = 0.0, 0.5
            if records:
                self.editor._queue_photo_list_metadata_batch(records)
        # Capture Qt-owned path identity only. SQL, stat and source fallback stay
        # in the existing owned pool; an unchanged change-log does no image I/O.
        list_paths = getattr(self.editor, "_list_photo_paths", None)
        # Map keys are casefolded identities, not source paths on case-sensitive
        # volumes. Preserve the path spelling from the Qt item roles.
        paths = tuple(str(path) for path in list_paths()) if callable(list_paths) else tuple(self.editor._photo_item_map)
        sequence = self._sequence
        known_paths = {path_key(path) for path in paths}
        self._retry_paths.intersection_update(known_paths)
        # Once the initial loader has drained, failed rows need retries even if
        # no edit/import event was created for that unsuccessful source read.
        if (getattr(self.editor, "_photo_list_metadata_loader", None) is None
                and not getattr(self.editor, "_photo_list_metadata_loading", False)):
            pending = set(getattr(self.editor, "_photo_list_metadata_pending_keys", ())) & known_paths
            queued = getattr(self.editor, "_photo_list_metadata_updates", {})
            pending = {key for key in pending
                       if key not in queued or not complete_metadata(queued[key][1])}
            new = pending - self._retry_paths
            if new and not self._retry_paths:
                self._retry_at = time.monotonic() + 0.5
            self._retry_paths.update(new)
        retry = set(self._retry_paths) if time.monotonic() >= self._retry_at else set()

        def poll():
            from app_common.exif_io.metadata_store import get_store, resource_key
            from app_common.exif_io.reader import extract_many_with_xmp_priority
            store = get_store()
            change = store.changes_since(sequence)
            resources = {item["resource"] for item in change["changes"]
                         if item["kind"] in {"edit", "synced", "refresh", "import", "external"}}
            scopes = [item["path"] for item in change["changes"] if item["kind"] == "refresh"]
            affected = [path for path in paths if path_key(path) in retry or change["reset"] or resource_key(path) in resources
                        or any(os.path.normcase(str(path)).startswith(os.path.normcase(scope).rstrip("/\\") + os.sep)
                               for scope in scopes)]
            if not affected:
                return change["seq"], {}, set(), set()
            attempted = {path_key(path) for path in affected}
            try:
                records = extract_many_with_xmp_priority([Path(path) for path in affected], cancelled=lambda: self._closed)
            except Exception:
                return change["seq"], {}, attempted, attempted
            successful = {path_key(path) for path, record in records.items()
                          if complete_metadata(record) and not record.get("_metadata_refresh_failed")}
            return (change["seq"], {str(path): record for path, record in records.items()},
                    attempted, attempted - successful)

        try:
            self._future = self.editor._preview_action_pool.submit_action(
                CallableAction(poll, cancelled=lambda: self._closed), kind=WorkKind.METADATA,
            )
        except Exception:
            self._future = None
