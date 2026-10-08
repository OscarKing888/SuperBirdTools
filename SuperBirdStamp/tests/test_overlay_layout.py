"""自动组合的动态尺寸、持久化、导出及真实 Qt 拖拽回归。"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat
from PyQt6.QtCore import Qt, QPointF, QEvent, QCoreApplication
from test_template_text_scale import _APP
from test_overlay_editor import mouse
from birdstamp.overlays.model import new_item, document, with_document
from birdstamp.overlays.render import build_scene, Scene
from birdstamp.overlays.layout import bounds, ancestors, members, Snap, join, snap_candidate
from birdstamp.gui.overlay_panel import OverlayPanel
from birdstamp.gui.overlay_edit import OverlaySession
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas
from birdstamp.gui.editor_utils import pil_to_qpixmap
from birdstamp.gui import editor_template as template
from birdstamp.export_stage import VideoFrameJob, render_video_frame, source_frame_signature_for_job
from birdstamp.workspace import read_workspace_json, write_workspace_json


def text(key, value, **kwargs):
    return dict(new_item('text'), id=key, text=value, font_size=60, **kwargs)


def group(key, children, direction='row', **kwargs):
    return dict(id=key, children=children, direction=direction, x=.1, y=.9,
                anchor_x=0, anchor_y=1, gap=.01, align='start', **kwargs)


def payload():
    return dict(overlays=[text('bird', '白鹭'), text('badge', '稀有'), text('camera', '相机')],
                overlay_layouts=[group('row', ['bird', 'badge']), group('column', ['row', 'camera'], 'up')])


def boxes(scene):
    return {v.item['id']: bounds([v]) for v in scene.layers}


def test_nested_dynamic_text_keeps_row_and_bottom_anchor():
    doc = payload()
    for value in ('白鹭', '非常长的中文鸟种名称', '两行文字\n第二行更长'):
        doc['overlays'][0]['text'] = value
        scene = build_scene(doc, (1600, 900))
        try:
            b = boxes(scene)
            assert b['badge'][0]-b['bird'][2] == pytest.approx(9)
            assert b['badge'][1] == pytest.approx(b['bird'][1])
            assert min(b['bird'][1], b['badge'][1])-b['camera'][3] == pytest.approx(9)
            assert max(b['bird'][3], b['badge'][3]) == pytest.approx(810)
            assert b['bird'][0] == pytest.approx(160)
        finally:
            scene.close()


@pytest.mark.parametrize('hidden', [True, False])
def test_empty_and_hidden_items_collapse_without_extra_gaps(hidden):
    doc = payload()
    if hidden:
        doc['overlays'][0]['visible'] = False
    else:
        doc['overlays'][0]['text'] = ''
    scene = build_scene(doc, (1000, 600))
    try:
        b = boxes(scene)
        assert 'bird' not in b
        assert b['badge'][0] == pytest.approx(100)
        assert b['badge'][1]-b['camera'][3] == pytest.approx(6)
    finally:
        scene.close()


def test_rotated_effects_and_badge_use_full_extents():
    first = text('first', '中文', rotation=33, stroke_enabled=True, shadow_enabled=True, shadow_blur=8)
    second = dict(new_item('badge'), id='second', text_mode='literal', text='长徽章')
    doc = dict(overlays=[first, second], overlay_layouts=[group('row', ['first', 'second'])])
    scene = build_scene(doc, (1200, 800))
    try:
        b = boxes(scene)
        assert b['second'][0]-b['first'][2] == pytest.approx(8)
        assert b['first'][1] == pytest.approx(b['second'][1])
    finally:
        scene.close()


def test_model_rejects_cycles_duplicates_and_cleans_deletion():
    doc = payload()
    assert document(document(doc)) == document(doc)
    broken = deepcopy(doc)
    broken['overlay_layouts'][0]['children'].append('column')
    with pytest.raises(ValueError, match='循环'):
        document(broken)
    broken = deepcopy(doc)
    broken['overlay_layouts'][1]['children'].append('bird')
    with pytest.raises(ValueError, match='多个'):
        document(broken)
    doc['overlays'] = [doc['overlays'][2]]
    cleaned = document(doc)
    assert [n['id'] for n in cleaned['overlay_layouts']] == ['column']
    assert cleaned['overlay_layouts'][0]['children'] == ['camera']
    assert 'overlay_layouts' not in with_document(payload(), {'overlays': []})


def test_group_template_workspace_export_and_cache(tmp_path):
    doc = payload()
    path = tmp_path/'中文布局.json'
    template.save_template_payload(path, dict(doc, ratio='no_crop'))
    saved = template.load_template_payload(path)
    assert saved['overlay_layouts'] == document(doc)['overlay_layouts']
    workspace = tmp_path/'工作区.json'
    write_workspace_json(workspace, dict(photos=[{'render_settings': {'overlay_override': saved}}]))
    restored = read_workspace_json(workspace)['photos'][0]['render_settings']['overlay_override']
    assert restored == saved
    job = VideoFrameJob(Path('bird.jpg'), dict(template_payload=saved, ratio='no_crop'), {}, {})
    sig = source_frame_signature_for_job(job)
    saved['overlay_layouts'][0]['gap'] = .02
    assert source_frame_signature_for_job(job) != sig
    with Image.new('RGB', (1600, 900), '#657080') as image:
        job.source_image = image
        rendered = render_video_frame(job)
        small = image.resize((800, 450))
        preview = template.render_template_overlay(small, raw_metadata={}, metadata_context={},
                                                  template_payload=saved, layout_size=image.size)
        expected = rendered.resize(small.size, Image.Resampling.LANCZOS)
        try:
            assert max(ImageStat.Stat(ImageChops.difference(expected, preview)).mean) < 1.5
            assert preview.tobytes() != small.tobytes()
        finally:
            for im in (rendered, small, preview, expected): im.close()


def test_snap_joins_rows_into_column_and_rejects_remote_or_locked():
    doc = dict(overlays=[text('a', 'A'), text('b', 'B'), text('c', 'C'), text('d', 'D')],
               overlay_layouts=[group('one', ['a', 'b']), group('two', ['c', 'd'])])
    scene = build_scene(doc, (1000, 600))
    try:
        joined = join(doc, 'two', Snap('one', 'down', False, bounds(scene.layers[:2])), scene)
        normalized = document(joined)
        assert len(ancestors(normalized, 'a')) == 2
        assert set(members(normalized, ancestors(normalized, 'a')[-1]['id'])) == {'a', 'b', 'c', 'd'}
        assert snap_candidate(doc, scene.layers[:1], scene.layers[0], (10, 10), 6) is None
    finally:
        scene.close()


@pytest.mark.parametrize('before', [True, False])
def test_snap_extends_existing_row_in_column_and_respects_lock(before):
    doc = payload()
    doc['overlays'].append(text('moving', '新元素', x=.8, y=.2))
    scene = build_scene(doc, (1000, 600))
    try:
        l, t, r, b = bounds(scene.layers[:2])
        moving = scene.layers[-1]
        moving = replace(moving, center=((l-moving.size[0]/2-6 if before else r+moving.size[0]/2+6),
                                         (t+b)/2))
        layers = scene.layers[:-1]+[moving]
        snap = snap_candidate(doc, layers, moving, (12, 12), 6)
        assert snap is not None and snap.target == 'row'
        joined = document(join(doc, 'moving', snap, Scene(scene.size, layers)))
        row, column = ancestors(joined, 'moving')
        assert row['children'] == (['moving', 'bird', 'badge'] if before else ['bird', 'badge', 'moving'])
        assert column['children'] == ['row', 'camera']
        # 锁定目标行中的成员后，不允许吸附导致它重新排版。
        doc['overlays'][0]['locked'] = True
        layers[0].item['locked'] = True
        assert snap_candidate(doc, layers, moving, (12, 12), 6) is None
    finally:
        scene.close()


@pytest.fixture
def edit_session(tmp_path, monkeypatch):
    from birdstamp import config
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path/'user')
    doc = dict(overlays=[text('a', '白鹭', x=.2, y=.5), text('b', 'B', x=.7, y=.5)])
    panel = OverlayPanel(); panel.set_document(doc, 'photo:layout')
    canvas = EditorPreviewCanvas(); canvas.resize(1000, 600)
    session = OverlaySession(canvas, panel)
    image = Image.new('RGB', (1000, 600), '#657080')
    canvas.set_source_pixmap(pil_to_qpixmap(image))
    def refresh():
        session.capture(image, build_scene(panel.doc, image.size))
    panel.changed.connect(lambda _: refresh())
    refresh(); canvas.set_edit_mode('overlay')
    try:
        yield panel, session, canvas
    finally:
        session.clear(); image.close(); canvas.close(); panel.close()
        canvas.deleteLater(); panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_drag_preview_commit_undo_detach_and_group_controls(edit_session):
    panel, session, canvas = edit_session
    panel.select('b')
    first, second = session.scene.layers
    start = session.to_widget(second.center)
    gap = .006*min(session.scene.size)
    end = session.to_widget((first.center[0]+first.size[0]/2+second.size[0]/2+gap, first.center[1]))
    original = deepcopy(panel.doc)
    assert session.press(mouse(QEvent.Type.MouseButtonPress, start))
    assert session.move(mouse(QEvent.Type.MouseMove, end))
    assert session.drag['snap'] is not None
    assert panel.doc == original
    assert session.drag['layers'][1].center != second.center
    session.release(mouse(QEvent.Type.MouseButtonRelease, end))
    assert len(panel.doc['overlay_layouts']) == 1
    assert not panel.layout_box.isHidden()
    assert panel.widgets['x'].isHidden()
    grouped = deepcopy(panel.doc)
    panel.undo(); assert panel.doc == original
    panel.redo(); assert panel.doc == grouped
    panel._edit_layout('direction', 'up')
    b = boxes(session.scene)
    assert b['b'][3] < b['a'][1]
    assert panel.doc['overlay_layouts'][0]['anchor_y'] == 1
    panel._edit_layout('gap', 0)
    b = boxes(session.scene)
    assert b['b'][3] == pytest.approx(b['a'][1])
    panel._detach_layout()
    assert not ancestors(panel.doc, 'b')
    assert boxes(session.scene)['b'] == pytest.approx(b['b'])


@pytest.mark.parametrize('direction', ['down', 'up'])
@pytest.mark.parametrize('before', [True, False])
@pytest.mark.parametrize('target', ['top', 'middle', 'bottom'])
def test_drag_beside_column_member_creates_nested_row(edit_session, direction, before, target):
    panel, session, canvas = edit_session
    children = ['top', 'middle', 'bottom']
    doc = dict(overlays=[text(key, '白鹭') for key in children]
               + [text('moving', '徽章', x=.8, y=.3)],
               overlay_layouts=[group('column', children, direction)])
    doc['overlay_layouts'][0].update(x=.4, y=.8)
    panel.commit(doc)
    panel.select('moving')
    original = deepcopy(panel.doc)
    target_layer = next(v for v in session.scene.layers if v.item['id'] == target)
    moving = session.selected_layer()
    gap = .006*min(session.scene.size)
    offset = (target_layer.size[0]+moving.size[0])/2+gap
    start = session.to_widget(moving.center)
    end = session.to_widget((target_layer.center[0]+(-offset if before else offset),
                             target_layer.center[1]))
    session.press(mouse(QEvent.Type.MouseButtonPress, start))
    session.move(mouse(QEvent.Type.MouseMove, end))
    assert session.drag['snap'] is not None
    assert session.drag['snap'].target == target
    assert session.drag['snap'].direction == 'row'
    assert session.drag['snap'].before == before
    assert panel.doc == original
    session.release(mouse(QEvent.Type.MouseButtonRelease, end))
    row, column = ancestors(panel.doc, 'moving')
    assert row['direction'] == 'row'
    assert row['children'] == (['moving', target] if before else [target, 'moving'])
    assert column == dict(original['overlay_layouts'][0],
                          children=[row['id'] if key == target else key for key in children])
    b = boxes(session.scene)
    left, right = ('moving', target) if before else (target, 'moving')
    assert b[right][0]-b[left][2] == pytest.approx(gap)
    assert (b['moving'][1]+b['moving'][3])/2 == pytest.approx((b[target][1]+b[target][3])/2)
    committed = deepcopy(panel.doc)
    panel.undo(); assert panel.doc == original
    panel.redo(); assert panel.doc == committed


def test_drag_cancel_and_ctrl_group_move(edit_session):
    panel, session, canvas = edit_session
    panel.commit(dict(panel.doc, overlay_layouts=[group('row', ['a', 'b'])]))
    panel.select('b')
    initial = deepcopy(panel.doc)
    old = [v.center for v in session.scene.layers]
    start = session.to_widget(old[1])
    end = start + QPointF(60, -40)
    mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    session.press(mouse(QEvent.Type.MouseButtonPress, start))
    session.move(mouse(QEvent.Type.MouseMove, end, modifiers=mods))
    delta = [(v.center[0]-c[0], v.center[1]-c[1]) for v, c in zip(session.drag['layers'], old)]
    assert delta[0] == pytest.approx(delta[1])
    session.cancel(); assert panel.doc == initial
    session.press(mouse(QEvent.Type.MouseButtonPress, start))
    session.move(mouse(QEvent.Type.MouseMove, end, modifiers=mods))
    session.release(mouse(QEvent.Type.MouseButtonRelease, end, modifiers=mods))
    assert panel.doc['overlay_layouts'][0]['x'] != initial['overlay_layouts'][0]['x']
    assert panel.doc['overlays'] == initial['overlays']
    panel.undo(); assert panel.doc == initial


def test_dissolve_outer_preserves_nested_row_positions(edit_session):
    panel, session, canvas = edit_session
    doc = payload()
    panel.commit(doc); panel.select('bird')
    before = boxes(session.scene)
    panel.layout_group.setCurrentIndex(1)
    panel._dissolve_layout()
    after = boxes(session.scene)
    for key in before:
        assert after[key] == pytest.approx(before[key])
    assert len(ancestors(panel.doc, 'bird')) == 1
    panel.undo()
    assert len(ancestors(panel.doc, 'bird')) == 2


def test_alt_drag_detaches_and_resize_preserves_group(edit_session):
    panel, session, canvas = edit_session
    panel.commit(dict(panel.doc, overlay_layouts=[group('row', ['a', 'b'])]))
    panel.select('b')
    layer = session.selected_layer()
    corner = session.handles(layer)['corner2']
    end = corner + QPointF(35, 20)
    session.press(mouse(QEvent.Type.MouseButtonPress, corner))
    session.move(mouse(QEvent.Type.MouseMove, end))
    session.release(mouse(QEvent.Type.MouseButtonRelease, end))
    assert ancestors(panel.doc, 'b')
    b = boxes(session.scene)
    assert b['b'][0]-b['a'][2] == pytest.approx(6)
    start = session.to_widget(session.selected_layer().center)
    end = start + QPointF(80, -100)
    session.press(mouse(QEvent.Type.MouseButtonPress, start))
    session.move(mouse(QEvent.Type.MouseMove, end, modifiers=Qt.KeyboardModifier.AltModifier))
    assert session.drag['snap'] is None
    session.release(mouse(QEvent.Type.MouseButtonRelease, end))
    assert not ancestors(panel.doc, 'b')


@pytest.mark.parametrize('size', [(1200, 800), (800, 1200)])
def test_down_column_right_anchor_and_scale(size):
    doc = payload()
    root = doc['overlay_layouts'][1]
    root.update(direction='down', x=.9, y=.1, anchor_x=1, anchor_y=0, align='end')
    for value in (.5, 1, 2):
        scene = build_scene(doc, size, text_scale=value)
        try:
            b = boxes(scene)
            assert b['bird'][1] == pytest.approx(size[1]*.1)
            assert b['badge'][2] == pytest.approx(size[0]*.9)
            assert b['camera'][2] == pytest.approx(size[0]*.9)
            assert b['camera'][1]-max(b['bird'][3], b['badge'][3]) == pytest.approx(min(size)*.01)
        finally:
            scene.close()


def test_layout_cli_uses_same_saved_geometry(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from birdstamp import cli, config
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path/'user')
    source = tmp_path/'source.png'
    with Image.new('RGB', (1200, 800), '#657080') as image: image.save(source)
    path = tmp_path/'layout.json'
    template.save_template_payload(path, dict(payload(), ratio='no_crop'))
    monkeypatch.setattr(cli, 'extract_many_with_xmp_priority', lambda *a, **k: {source: {'SourceFile': str(source)}})
    result = CliRunner().invoke(cli.app, ['render', str(source), '--out', str(tmp_path/'out'),
                               '--template', str(path), '--format', 'png'])
    assert result.exit_code == 0, result.output+str(result.exception)
    with Image.open(next((tmp_path/'out').glob('*.png'))) as actual, Image.open(source) as image:
        expected = template.render_template_overlay(image, raw_metadata={}, metadata_context={},
                                                    template_payload=template.load_template_payload(path))
        try:
            assert actual.size == expected.size
            assert actual.convert('RGB').tobytes() == expected.convert('RGB').tobytes()
        finally:
            expected.close()


def test_template_dialog_persists_layout_and_undo(tmp_path, monkeypatch):
    from birdstamp import config
    from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path/'user')
    monkeypatch.setattr(TemplateManagerDialog, '_load_preview_source', lambda self: None)
    folder = tmp_path/'templates'; folder.mkdir()
    path = folder/'test.json'
    template.save_template_payload(path, template.default_template_payload())
    image = Image.new('RGB', (1000, 600))
    dialog = TemplateManagerDialog(folder, image)
    try:
        dialog._reload_template_list('test')
        dialog.overlay_panel.commit(payload())
        assert template.load_template_payload(path)['overlay_layouts'] == document(payload())['overlay_layouts']
        dialog.overlay_panel.select('bird')
        dialog.overlay_panel._edit_layout('gap', .02)
        assert template.load_template_payload(path)['overlay_layouts'][0]['gap'] == .02
        dialog.overlay_panel.undo()
        assert template.load_template_payload(path)['overlay_layouts'][0]['gap'] == .01
    finally:
        dialog.close(); dialog.deleteLater(); image.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _APP.processEvents()


def test_ctrl_drag_combines_two_rows_as_nested_column(edit_session):
    panel, session, canvas = edit_session
    doc = dict(overlays=[text(key, value) for key, value in [('a','白鹭'),('b','稀有'),('c','相机'),('d','光圈')]],
               overlay_layouts=[group('one',['a','b']), group('two',['c','d'])])
    doc['overlay_layouts'][0].update(x=.15, y=.3)
    doc['overlay_layouts'][1].update(x=.6, y=.7)
    panel.commit(doc); panel.select('d')
    old = deepcopy(panel.doc)
    first = bounds(session.scene.layers[:2]); second = bounds(session.scene.layers[2:])
    start = session.to_widget(session.selected_layer().center)
    dx = first[0]-second[0]
    dy = first[3]+.006*min(session.scene.size)-second[1]
    point = session.selected_layer().center
    end = session.to_widget((point[0]+dx, point[1]+dy))
    session.press(mouse(QEvent.Type.MouseButtonPress, start))
    # 按下到第一次移动之间也会重绘，此时尚未生成临时布局。
    assert not canvas.grab().isNull()
    session.move(mouse(QEvent.Type.MouseMove, end, modifiers=Qt.KeyboardModifier.ControlModifier))
    assert session.drag['snap'] is not None
    assert session.drag['snap'].direction == 'down'
    assert not canvas.grab().isNull()
    session.release(mouse(QEvent.Type.MouseButtonRelease, end))
    for key in ('a','b','c','d'):
        assert len(ancestors(panel.doc, key)) == 2
    b = boxes(session.scene)
    assert min(b['c'][1],b['d'][1])-max(b['a'][3],b['b'][3]) == pytest.approx(3.6)
    panel.undo(); assert panel.doc == old


def test_gap_slider_single_undo_and_locked_group(edit_session):
    panel, session, canvas = edit_session
    panel.commit(dict(panel.doc, overlay_layouts=[group('row',['a','b'])]))
    panel.select('a')
    before = deepcopy(panel.doc)
    panel.layout_gap.slider.setSliderDown(True)
    panel.layout_gap.slider.setSliderPosition(200)
    panel.layout_gap.slider.setSliderPosition(300)
    panel.layout_gap.slider.setSliderDown(False)
    assert panel.doc['overlay_layouts'][0]['gap'] == .03
    panel.undo(); assert panel.doc == before
    panel.select('b'); panel.edit('locked', True); panel.select('a')
    assert not panel.layout_gap.isEnabled()
    start = session.to_widget(session.selected_layer().center)
    session.press(mouse(QEvent.Type.MouseButtonPress, start))
    session.move(mouse(QEvent.Type.MouseMove, start+QPointF(40,40), modifiers=Qt.KeyboardModifier.ControlModifier))
    assert not session.drag['changed']
    session.cancel()
