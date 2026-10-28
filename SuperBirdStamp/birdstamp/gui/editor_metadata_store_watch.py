"""Refresh BirdStamp's complete snapshots after edits in either application."""
from __future__ import annotations

from pathlib import Path
import os

from PyQt6.QtCore import QObject, QTimer

from app_common.file_browser._work_action import CallableAction
from app_common.file_browser._work_policy import WorkKind


class EditorMetadataStoreWatch(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self._sequence = 0
        self._future = None
        self._closed = False
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
                sequence, records = future.result()
            except Exception:
                return
            self._sequence = sequence
            if records:
                self.editor._queue_photo_list_metadata_batch(records)
        # Capture Qt-owned path identity only. SQL, stat and source fallback stay
        # in the existing owned pool; an unchanged change-log does no image I/O.
        paths = tuple(self.editor._photo_item_map)
        sequence = self._sequence

        def poll():
            from app_common.exif_io.metadata_store import get_store, resource_key
            from app_common.exif_io.reader import extract_many_with_xmp_priority
            store = get_store()
            change = store.changes_since(sequence)
            resources = {item["resource"] for item in change["changes"]
                         if item["kind"] in {"edit", "synced", "refresh", "import", "external"}}
            scopes = [item["path"] for item in change["changes"] if item["kind"] == "refresh"]
            affected = [path for path in paths if change["reset"] or resource_key(path) in resources
                        or any(os.path.normcase(str(path)).startswith(os.path.normcase(scope).rstrip("/\\") + os.sep)
                               for scope in scopes)]
            if not affected:
                return change["seq"], {}
            records = extract_many_with_xmp_priority([Path(path) for path in affected], cancelled=lambda: self._closed)
            return change["seq"], {str(path): record for path, record in records.items()}

        try:
            self._future = self.editor._preview_action_pool.submit_action(
                CallableAction(poll, cancelled=lambda: self._closed), kind=WorkKind.METADATA,
            )
        except Exception:
            self._future = None
