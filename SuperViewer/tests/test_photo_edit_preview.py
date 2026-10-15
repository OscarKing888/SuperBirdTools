"""Actual MainWindow integration, per-viewport before/after and navigation."""
import time

import pytest
import numpy as np
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


@pytest.mark.parametrize("repeat",[False,True])
def test_auto_adjust_shows_actual_result_when_before_was_selected(window,tmp_path,repeat):
    pytest.importorskip("app_common.photo_edit._native")
    from SuperViewer.superviewer.photo_edit_preview import render_for_viewer
    path = tmp_path/"自动调整结果.png"
    ramp = np.linspace(40,140,600).astype(np.uint8)
    Image.fromarray(np.broadcast_to(ramp[None,:,None],(400,600,3))).save(path)
    select(window,path)
    panel,preview = window.color_adjust_panel,window.preview_panel
    window.image_info_tabs.setCurrentWidget(panel)
    if repeat:
        panel.auto_button.click()
        _wait_until(lambda:not window._photo_edit._operations and preview._full_preview_loaded)
    preview.set_preview_source_mode("edited")
    preview.set_edit_before(True)
    _wait_until(lambda:preview._full_preview_loaded)
    original = preview.canvas._source_pixmap.toImage()
    panel.auto_button.click()
    _wait_until(lambda:not window._photo_edit._operations)
    assert not preview.edit_before()
    assert not window.ab_preview.b_panel.before_button.isChecked()
    _wait_until(lambda:preview._full_preview_loaded)
    snapshot = window._photo_edit.session().snapshot()
    expected = render_for_viewer(snapshot,proxy=False).display_image
    actual = preview.canvas._source_pixmap.toImage().convertToFormat(expected.format())
    assert actual==expected and actual!=original.convertToFormat(expected.format())


def test_a_b_share_recipe_but_before_and_view_modes_are_independent(window,tmp_path):
    pytest.importorskip("app_common.photo_edit._native")
    path = tmp_path/"同一底图.png"
    Image.new("RGB",(1440,960),(70,100,130)).save(path)
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
    assert all(p.get_preview_image_size()==(1440,960) for p in (window.preview_a,window.preview_panel))
    assert window.preview_panel.edit_before() and not window.preview_a.edit_before()
    window.ab_preview.activate("a")
    assert window.camera_raw_panel._target_name=="A"
    assert window._photo_edit.session().state.current.recipe.camera["exposure"]==1


@pytest.mark.parametrize("keep_view_on_switch",[False,True])
def test_edit_preview_keeps_original_dimensions_and_view_through_edits_and_before(
        window,tmp_path,monkeypatch,keep_view_on_switch):
    pytest.importorskip("app_common.photo_edit._native")
    path = tmp_path/"大尺寸.png"
    dimensions = (1800,1200)
    Image.new("RGB",dimensions,(70,100,130)).save(path)
    select(window,path)
    preview = window.preview_panel
    preview._keep_view_on_switch = keep_view_on_switch
    preview.canvas.set_auto_focus_center(False)
    preview.set_display_scale_percent(100)
    preview.canvas.apply_viewport_state((preview.canvas.viewport_state()[0],(.6,.4)))
    before_state = preview.canvas.viewport_state()
    original = rendered(preview)
    sizes = []
    preview.canvas.viewport_content_changed.connect(lambda:sizes.append(preview.get_preview_image_size()))
    requests = []
    request_render = window._photo_edit.request_render
    def tracked(source,consumer,**kwargs):
        if consumer is preview:
            requests.append(kwargs)
        return request_render(source,consumer,**kwargs)
    monkeypatch.setattr(window._photo_edit,"request_render",tracked)

    def assert_view():
        assert preview.get_preview_image_size()==dimensions
        assert preview.canvas.current_display_scale_percent()==pytest.approx(100)
        state = preview.canvas.viewport_state()
        assert state[0]==pytest.approx(before_state[0])
        assert state[1]==pytest.approx(before_state[1])

    preview.set_preview_source_mode("edited")
    # Entering edit mode must retain the original frame, even with switch-view reset enabled.
    assert_view()
    assert not preview._full_preview_loaded and preview.source_pixmap_for_path(str(path)) is None
    _wait_until(lambda:preview._full_preview_loaded)
    assert_view()
    assert preview.source_pixmap_for_path(str(path)) is not None

    # Each slider update uses native-size pixels; the previous frame stays visible while pending.
    for exposure in (.3,.6):
        previous = rendered(preview)
        recipe = window._photo_edit.session().state.current.recipe.validated()
        recipe.camera["exposure"] = exposure
        window._photo_edit.edit(recipe,commit=False)
        assert_view()
        assert rendered(preview)==previous
        _wait_until(lambda:preview._full_preview_loaded and rendered(preview)>previous)
        assert_view()
    window._photo_edit.finish_gesture()
    old_token = preview._preview_request_token
    preview.set_edit_before(True)
    image = preview.canvas._source_pixmap.toImage()
    assert not preview.apply_edited_preview(old_token,str(path),image,full=True)
    _wait_until(lambda:preview._full_preview_loaded and rendered(preview)==original)
    assert_view()
    assert requests and all(request["full"] for request in requests)
    assert all(not request.get("before",False) for request in requests)
    assert sizes and set(sizes)=={dimensions}


def test_before_uses_original_loader_without_edit_renderer_or_denoised_input(window,tmp_path,monkeypatch):
    from image_denoise.export import _prepare_sidecar
    from SuperViewer.superviewer import preview_panel as module
    path,denoised = tmp_path/"原图.png",tmp_path/"降噪.png"
    Image.new("RGB",(1000,700),(40,80,120)).save(path)
    Image.new("RGB",(1000,700),(180,150,100)).save(denoised)
    _prepare_sidecar(path,denoised.with_suffix(".xmp"),1000,700)
    select(window,path)
    preview = window.preview_panel
    expected = preview.canvas._source_pixmap.toImage()
    window.image_info_tabs.setCurrentWidget(window.image_info_panel)
    _wait_until(lambda:not window._photo_edit._workers)
    # Even a selected denoised input with unavailable editing/LUT support must show the original.
    window._photo_edit.session().add_denoised(denoised)
    reads,renders = [],[]
    load = module._load_full_preview_qimage
    def tracked(source):
        reads.append(source)
        return load(source)
    def forbidden(*args,**kwargs):
        renders.append(True)
        raise ValueError("编辑组件不可用")
    monkeypatch.setattr(module,"_load_full_preview_qimage",tracked)
    monkeypatch.setattr(window._photo_edit,"renderer",forbidden)
    preview.set_edit_before(True)
    preview.set_preview_source_mode("edited")
    _wait_until(lambda:preview._full_preview_loaded)
    assert reads==[str(path)] and not renders
    assert preview.canvas._source_pixmap.toImage()==expected
    assert "原图" in preview._preview_note
    token = preview._preview_request_token
    recipe = window._photo_edit.session().state.current.recipe.validated()
    recipe.camera["exposure"] = 2
    window._photo_edit.edit(recipe)
    assert preview._preview_request_token==token and preview._full_preview_loaded
    assert not preview._full_preview_timer.isActive()


def test_before_raw_uses_owned_original_worker_and_rejects_late_results(tmp_path,monkeypatch):
    import threading
    from SuperViewer.superviewer import preview_panel as module
    path = tmp_path/"原始 RAW.ARW"
    path.touch()
    started,release = threading.Event(),threading.Event()
    reads = []
    def original(source):
        reads.append((source,threading.get_ident()))
        started.set()
        assert release.wait(4)
        return module._qimage_from_pil_image(Image.new("RGB",(1600,1000),"red"))
    monkeypatch.setattr(module,"_load_full_preview_qimage",original)
    panel = module.PreviewPanel()
    requests = []
    panel.edit_preview_requested.connect(lambda *args:requests.append(args))
    panel.set_edit_before(True)
    panel.set_preview_source_mode("edited")
    try:
        panel.set_image(str(path))
        _wait_until(started.is_set)
        assert not requests
        assert reads==[(str(path),reads[0][1])] and reads[0][1]!=threading.get_ident()
        token = panel._preview_request_token
        panel.set_edit_before(False)
        _wait_until(lambda:bool(requests))
        edited = module._qimage_from_pil_image(Image.new("RGB",(1600,1000),"blue"))
        assert panel.apply_edited_preview(requests[-1][0],str(path),edited,full=True)
        release.set()
        _wait_until(lambda:panel._full_preview_loader is None)
        panel._on_full_preview_loaded(token,str(path),module._qimage_from_pil_image(
            Image.new("RGB",(1600,1000),"red")),0)
        assert panel.canvas._source_pixmap.toImage().pixelColor(0,0).blue()==255
        # Missing original must show an error, not remain stuck on the loading message.
        path.unlink()
        panel.set_edit_before(True)
        _wait_until(lambda:"原图预览失败" in panel._preview_note)
    finally:
        release.set()
        panel.shutdown()
        panel.close()


def test_full_edit_render_failure_retains_previous_frame_without_proxy_fallback(window,tmp_path,monkeypatch):
    path = tmp_path/"保留完整画面.png"
    Image.new("RGB",(1800,1200),(70,100,130)).save(path)
    select(window,path)
    preview = window.preview_panel
    preview.set_display_scale_percent(100)
    before_state = preview.canvas.viewport_state()
    original = preview.canvas._source_pixmap.toImage()
    calls = []
    def fail(_snapshot,**kwargs):
        calls.append(kwargs["proxy"])
        raise ValueError("完整渲染内存不足")
    # Hide the histogram consumer so these are only the viewport's requests.
    window.image_info_tabs.setCurrentWidget(window.image_info_panel)
    _wait_until(lambda:not window._photo_edit._workers)
    monkeypatch.setattr(window._photo_edit,"renderer",fail)
    preview.set_preview_source_mode("edited")
    _wait_until(lambda:"编辑预览失败" in preview._preview_note)
    assert calls==[False]
    assert preview.canvas._source_pixmap.toImage()==original
    assert preview.canvas.viewport_state()==before_state
    assert not preview._full_preview_loaded


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
