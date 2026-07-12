from birdstamp.gui.editor_photo_list import PhotoListWidget


def test_editor_photo_list_keeps_native_key_navigation() -> None:
    assert PhotoListWidget.enable_key_navigation_playback is False
    assert PhotoListWidget.enable_in_memory_fast_preview is False
    assert PhotoListWidget.skip_uncached_fast_preview is False
