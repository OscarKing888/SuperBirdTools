"""Actual MainWindow integration, per-viewport before/after and navigation."""
import time

import pytest
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

from SuperViewer.tests.test_directory_selection_responsiveness import window,_APP,_wait_until


def select(window,path):
    window.show()
    window._on_file_selected_from_list(str(path))
    window.image_info_tabs.setCurrentWidget(window.camera_raw_panel)
    _wait_until(lambda:window._photo_edit.session(str(path)) is not None)


def rendered(preview):
    return preview.canvas._source_pixmap.toImage().pixelColor(5,5).red()


def test_a_b_share_recipe_but_before_and_view_modes_are_independent(window,tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    path = tmp_path/"同一底图.png"
    Image.new("RGB",(96,72),(70,100,130)).save(path)
    select(window,path)
    window.ab_preview.enabled.setChecked(True)
    for preview in (window.preview_a,window.preview_panel):
        preview.set_preview_source_mode("edited")
    _wait_until(lambda:all(p._full_preview_loaded for p in (window.preview_a,window.preview_panel)))
    original = rendered(window.preview_panel)
    recipe = window._photo_edit.session().state.current.recipe.validated()
    recipe.camera["exposure"] = 1
    window._photo_edit.edit(recipe)
    _wait_until(lambda:all(p._full_preview_loaded and rendered(p)>original for p in (window.preview_a,window.preview_panel)))
    adjusted = rendered(window.preview_a)
    window.ab_preview.b_panel.before_button.setChecked(True)
    _wait_until(lambda:window.preview_panel._full_preview_loaded and rendered(window.preview_panel)==original)
    assert rendered(window.preview_a)==adjusted
    assert window.preview_panel.edit_before() and not window.preview_a.edit_before()
    window.ab_preview.activate("a")
    assert window.camera_raw_panel._target_name=="A"
    assert window._photo_edit.session().state.current.recipe.camera["exposure"]==1


def test_edited_proxy_upgrades_to_full_and_old_result_is_rejected(window,tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    path = tmp_path/"大尺寸.png"
    Image.new("RGB",(2400,120),(70,100,130)).save(path)
    select(window,path)
    preview = window.preview_panel
    sizes = []
    preview.canvas.viewport_content_changed.connect(lambda:sizes.append(preview.get_preview_image_size()))
    preview.set_preview_source_mode("edited")
    _wait_until(lambda:preview.get_preview_image_size()==(900,45))
    assert not preview._full_preview_loaded and preview.source_pixmap_for_path(str(path)) is None
    old_token = preview._preview_request_token
    before_state = preview.canvas.viewport_state()
    _wait_until(lambda:preview._full_preview_loaded)
    assert preview.get_preview_image_size()==(2400,120)
    assert preview.source_pixmap_for_path(str(path)) is not None
    assert (900,45) in sizes and (2400,120) in sizes
    assert preview.canvas.viewport_state()[0]==pytest.approx(before_state[0],rel=.02)
    preview.set_edit_before(True)
    image = preview.canvas._source_pixmap.toImage()
    assert not preview.apply_edited_preview(old_token,str(path),image,full=True)


def test_held_navigation_blocks_edit_render_until_final_committed_selection(window,tmp_path,monkeypatch):
    pytest.importorskip("app_common.photo_edit._native")
    photos = [tmp_path/f"鸟{i}.png" for i in range(3)]
    for photo in photos:
        Image.new("RGB",(96,72),(60,100,140)).save(photo)
    select(window,photos[0])
    preview = window.preview_panel
    preview.set_preview_source_mode("edited")
    _wait_until(lambda:preview._full_preview_loaded)
    _wait_until(lambda:not window._photo_edit._workers)
    calls = []
    renderer = window._photo_edit.renderer
    def tracked(state,**kwargs):
        calls.append(state.source)
        return renderer(state,**kwargs)
    monkeypatch.setattr(window._photo_edit,"renderer",tracked)
    preview.set_navigation_playback_active(True)
    pixmap = QPixmap(128,96)
    pixmap.fill(Qt.GlobalColor.green)
    window._on_file_fast_preview_pixmap_requested(str(photos[1]),pixmap,128)
    window._on_file_fast_preview_pixmap_requested(str(photos[2]),pixmap,128)
    preview.set_edit_before(True)
    deadline = time.monotonic()+.15
    while time.monotonic()<deadline:
        _APP.processEvents()
        time.sleep(.005)
    assert not calls and not preview._full_preview_timer.isActive()
    assert preview.get_preview_image_size()==(128,96)
    window._on_file_selected_from_list(str(photos[2]))
    _wait_until(lambda:preview._full_preview_loaded)
    assert calls and all(source==str(photos[2]) for source in calls)
    assert window._photo_edit.session().state.source==str(photos[2])
