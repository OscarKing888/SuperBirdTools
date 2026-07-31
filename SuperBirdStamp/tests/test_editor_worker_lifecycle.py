from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from birdstamp.gui import bird_detect_worker
from birdstamp.gui.editor import BirdStampEditorWindow


def test_bird_detect_worker_closes_owned_source_image(monkeypatch) -> None:
    class _OwnedImage:
        closed = False

        def close(self) -> None:
            self.closed = True

    source_image = _OwnedImage()
    monkeypatch.setattr(
        bird_detect_worker,
        "detect_primary_bird_box",
        lambda image: (0.1, 0.2, 0.8, 0.9),
    )
    worker = bird_detect_worker.BirdDetectWorker("signature", source_image)  # type: ignore[arg-type]

    worker.run()

    assert source_image.closed is True


def test_video_result_keeps_worker_reference_until_finished() -> None:
    class _Panel:
        busy_updates: list[tuple[bool, str]] = []

        def set_busy(self, busy: bool, *, status_text: str = "") -> None:
            self.busy_updates.append((busy, status_text))

    class _Editor:
        def __init__(self) -> None:
            self._video_export_worker = object()
            self._pending_video_export_dirty_keys: set[str] = set()
            self._photo_export_dirty_keys: set[str] = set()
            self.video_export_panel = _Panel()
            self.status = ""

        def _consume_video_export_elapsed_time(self):
            return None

        def _set_status(self, message: str) -> None:
            self.status = message

    editor = _Editor()
    worker = editor._video_export_worker

    BirdStampEditorWindow._on_video_export_succeeded(editor, "output.mp4")  # type: ignore[arg-type]

    assert editor._video_export_worker is worker
    assert "output.mp4" in editor.status

    BirdStampEditorWindow._cleanup_video_export_worker(editor, worker)  # type: ignore[arg-type]
    assert editor._video_export_worker is None


def test_finished_old_video_worker_does_not_clear_new_worker() -> None:
    class _Editor:
        pass

    editor = _Editor()
    old_worker = object()
    new_worker = object()
    editor._video_export_worker = new_worker

    BirdStampEditorWindow._cleanup_video_export_worker(editor, old_worker)  # type: ignore[arg-type]

    assert editor._video_export_worker is new_worker
