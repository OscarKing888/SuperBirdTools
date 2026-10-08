"""模板参数热路径：真实 Qt 信号合并，以及不改变来源规则的单帧解析缓存。"""
from contextlib import nullcontext
from time import monotonic

import pytest
from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtTest import QTest

from test_template_text_scale import _APP
from birdstamp import config
from birdstamp.gui import editor_template as templates, template_context as context
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
from birdstamp.overlays.model import new_item


def wait_preview(dialog):
    deadline = monotonic() + 3
    while dialog._preview_refresh_timer.isActive() and monotonic() < deadline:
        QTest.qWait(5)
    assert not dialog._preview_refresh_timer.isActive()


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    monkeypatch.setattr(TemplateManagerDialog, '_load_preview_source', lambda self: None)
    monkeypatch.setattr(TemplateManagerDialog, '_preview_source_bird_box', lambda self: None)
    folder = tmp_path / 'templates'
    folder.mkdir()
    for name in ('a', 'b'):
        templates.save_template_payload(folder / f'{name}.json', dict(
            name=name, ratio='no_crop', fields=[], overlays=[new_item('text')]))
    with Image.new('RGB', (800, 450), '#405060') as source:
        widget = TemplateManagerDialog(folder, source)
    widget.overlay_panel.select(widget.overlay_panel.doc['overlays'][0]['id'])
    widget.overlay_edit_check.setChecked(True)
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _APP.processEvents()


def test_slider_burst_saves_latest_and_renders_once_with_undo(dialog, monkeypatch):
    calls = []
    original = dialog._refresh_preview
    monkeypatch.setattr(dialog, '_refresh_preview', lambda: (calls.append(True), original())[-1])
    editor = dialog.overlay_panel.widgets['opacity']
    editor.slider.setSliderDown(True)
    for value in range(8000, 2900, -100):
        editor.slider.setSliderPosition(value)
    editor.slider.setSliderDown(False)
    assert calls == []
    assert dialog.overlay_session.scene is None  # 旧参数的几何不能参与命中。
    path = dialog.template_paths[dialog.current_template_name]
    assert templates.load_template_payload(path)['overlays'][0]['opacity'] == 30
    wait_preview(dialog)
    assert len(calls) == 1
    assert dialog.overlay_session.scene.layers[0].item['opacity'] == 30
    dialog.overlay_panel.undo()
    wait_preview(dialog)
    assert dialog.overlay_session.scene.layers[0].item['opacity'] == 100
    dialog.overlay_panel.redo()
    wait_preview(dialog)
    assert dialog.overlay_session.scene.layers[0].item['opacity'] == 30


def test_pending_edit_does_not_replace_next_template(dialog):
    dialog.overlay_panel.edit('text', '刚刚编辑的中文')
    old_path = dialog.template_paths[dialog.current_template_name]
    dialog.template_list.setCurrentRow(1)
    assert not dialog._preview_refresh_timer.isActive()
    assert dialog.current_template_name == 'b'
    assert templates.load_template_payload(old_path)['overlays'][0]['text'] == '刚刚编辑的中文'
    assert dialog.overlay_panel.doc['overlays'][0]['text'] != '刚刚编辑的中文'


@pytest.mark.parametrize('finish', ['close', 'reject', 'accept'])
def test_dialog_exit_flushes_text_and_cancels_pending_render(dialog, finish):
    dialog.overlay_panel.text.setPlainText('退出前最后输入')
    assert dialog.overlay_panel._text_timer.isActive()
    path = dialog.template_paths[dialog.current_template_name]
    getattr(dialog, finish)()
    assert templates.load_template_payload(path)['overlays'][0]['text'] == '退出前最后输入'
    assert not dialog._preview_refresh_timer.isActive()
    assert dialog.overlay_session.scene is None


def test_cached_render_matches_uncached_and_builds_provider_once(tmp_path, monkeypatch):
    metadata = {f'MakerNotes:Unused{i}': str(i) for i in range(300)}
    metadata.update({'EXIF:Model': '相机甲', 'EXIF:ISO': 640, 'EXIF:LensModel': '镜头乙'})
    photo = context.EditorPhotoInfo.from_path(tmp_path / 'photo.jpg', raw_metadata=metadata)
    payload = dict(overlays=[dict(new_item('text', metadata=True), text_source=dict(type='exif', key=key))
                             for key in ('camera_model', 'iso', 'lens_model')])
    calls = []
    original = context.normalize_metadata
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(context, 'normalize_metadata', counted)
    results = []
    counts = []
    for scope in (nullcontext, context.template_render_context, context.template_render_context):
        calls.clear()
        with scope(), Image.new('RGB', (800, 450)) as source:
            rendered = templates.render_template_overlay(source, raw_metadata=metadata,
                metadata_context={}, photo_info=photo, template_payload=payload)
            results.append(rendered.tobytes())
            rendered.close()
        counts.append(len(calls))
    assert results[0] == results[1] == results[2]
    assert counts == [3, 1, 1]  # 下一帧重新读取，而非跨帧永久缓存。


def test_render_cache_separates_sidecar_and_full_metadata_and_expires(tmp_path):
    photo = context.PhotoInfo.from_path(tmp_path / 'photo.jpg')
    build = context.ExifTemplateContextProvider._build_context_entries_from_metadata
    full = {'EXIF:Model': '源相机', 'XMP-dc:Title': '原始鸟名'}
    sidecar = {'XMP-dc:Title': '侧车鸟名'}
    with context.template_render_context():
        assert build(photo, full)['camera_model'] == '源相机'
        assert build(photo, sidecar)['bird_species_cn'] == '侧车鸟名'
        assert build(photo, sidecar).get('camera_model', '') == ''
        assert build(photo, full)['bird_species_cn'] == '原始鸟名'
        assert context._normalize_lookup(full)['model'] == '源相机'
        full['EXIF:Model'] = '已修改相机'
        assert context._normalize_lookup(full)['model'] == '已修改相机'
        assert build(photo, full)['camera_model'] == '已修改相机'
    full['XMP-dc:Title'] = '下一帧鸟名'
    with context.template_render_context():
        assert build(photo, full)['bird_species_cn'] == '下一帧鸟名'
    assert context._RENDER_CONTEXT_CACHE.get() is None


def test_render_cache_is_bounded_nested_and_cleans_up_on_error():
    with context.template_render_context():
        outer = context._RENDER_CONTEXT_CACHE.get()
        for i in range(30):
            context._normalize_lookup({'EXIF:ISO': i})
        assert len(outer['lookups']) == 8
        with pytest.raises(RuntimeError), context.template_render_context():
            assert context._RENDER_CONTEXT_CACHE.get() is not outer
            raise RuntimeError('中断渲染')
        assert context._RENDER_CONTEXT_CACHE.get() is outer
    assert context._RENDER_CONTEXT_CACHE.get() is None


def test_lookup_cache_preserves_namespace_insertion_order():
    first = {'Camera:Model': '相机', 'Lens:Model': '镜头'}
    second = dict(reversed(list(first.items())))
    with context.template_render_context():
        assert context.lookup_exif_text('Model', first, {}) == '相机'
        assert context.lookup_exif_text('Model', second, {}) == '镜头'
