from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication

from birdstamp.gui import editor as editor_module
from birdstamp.gui.editor import BirdStampEditorWindow

editor_module._load_bird_detector = lambda: None


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_window() -> BirdStampEditorWindow:
    window = BirdStampEditorWindow()
    window._schedule_workspace_autosave = lambda *args, **kwargs: None
    window._autosave_workspace_now = lambda *args, **kwargs: None
    window._schedule_async_bird_detect = lambda *args, **kwargs: None
    if hasattr(window, "show_bird_box_check"):
        window.show_bird_box_check.setChecked(False)
    return window


def _cleanup_window(app: QApplication, window: BirdStampEditorWindow) -> None:
    try:
        window._stop_photo_list_metadata_loader(wait=True, reset_progress=True)
    except Exception:
        pass
    try:
        window._stop_received_photo_import(reset_progress=True)
    except Exception:
        pass
    window.close()
    window.deleteLater()
    app.processEvents()


def _add_photo(window: BirdStampEditorWindow, path: Path) -> None:
    window._append_photo_path_to_list(
        path.resolve(strict=False),
        existing_keys=set(),
        default_settings=window._build_current_render_settings(),
    )


def test_photo_selection_does_not_block_on_metadata_read(monkeypatch, tmp_path: Path) -> None:
    app = _app()
    image_path = tmp_path / "first.jpg"
    Image.new("RGB", (24, 24), (180, 30, 20)).save(image_path, format="JPEG")
    window = _make_window()
    try:
        _add_photo(window, image_path)
        item = window.photo_list.topLevelItem(0)

        def fail_metadata_read(_path: Path) -> dict:
            raise AssertionError("selection should not synchronously read metadata")

        monkeypatch.setattr(window, "_load_raw_metadata", fail_metadata_read)
        monkeypatch.setattr(
            window,
            "_provider_text_candidates",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("selection should not synchronously resolve display providers")
            ),
        )
        monkeypatch.setattr(
            editor_module,
            "_build_metadata_context",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("selection should not synchronously build full metadata context")
            ),
        )
        window._on_photo_selected(item, None)

        assert window.current_path == image_path.resolve(strict=False)
        assert window.current_source_image is not None
        assert window.preview_pixmap is not None
        assert window.current_raw_metadata == {"SourceFile": str(image_path.resolve(strict=False))}
        assert window.current_metadata_context["filename"] == image_path.name
    finally:
        _cleanup_window(app, window)


def test_current_preview_refreshes_when_background_metadata_arrives(tmp_path: Path) -> None:
    app = _app()
    image_path = tmp_path / "second.jpg"
    Image.new("RGB", (24, 24), (20, 80, 180)).save(image_path, format="JPEG")
    window = _make_window()
    render_snapshots: list[dict] = []
    try:
        _add_photo(window, image_path)
        item = window.photo_list.topLevelItem(0)
        window.render_preview = lambda *args, **kwargs: render_snapshots.append(dict(window.current_raw_metadata))

        window._on_photo_selected(item, None)
        metadata = {
            "SourceFile": str(image_path.resolve(strict=False)),
            "XMP-dc:Title": "metadata title",
        }
        window._apply_photo_list_metadata_batch({str(image_path.resolve(strict=False)): metadata})

        assert render_snapshots[0] == {"SourceFile": str(image_path.resolve(strict=False))}
        assert render_snapshots[-1].get("XMP-dc:Title") == "metadata title"
        assert window.current_raw_metadata.get("XMP-dc:Title") == "metadata title"
    finally:
        _cleanup_window(app, window)
