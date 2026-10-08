"""平台安全框：整组几何、真实像素、预览/导出和逐图工作区回归。"""
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication
import pytest

from birdstamp import config
from birdstamp.overlays.safe_area import fit_layers, guide_box, normalize_options, safe_rect, layout_rect
from birdstamp.overlays.model import new_item
from birdstamp.overlays.assets import import_image
from birdstamp.overlays.render import build_scene, compose_scene, Layer
from birdstamp.overlays.layout import arrange, bounds
from birdstamp.gui import editor_options, editor_template as template
from birdstamp.gui.editor import BirdStampEditorWindow
from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas, EditorPreviewOverlayState
from birdstamp.gui.editor_utils import path_key
from birdstamp.export_stage import VideoFrameJob, render_video_frame, source_frame_signature_for_job, build_default_image_proc_pipeline
from birdstamp.export_stage import core
from birdstamp.workspace import read_workspace_json, write_workspace_json

from birdstamp.overlays import safe_area_options

PLATFORMS = tuple(editor_options.PLATFORM_SAFE_AREA['labels'])
_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_options(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path/'safe-user')
    safe_area_options.reload_options()
    yield
    safe_area_options._read_options.cache_clear()


@pytest.fixture
def payload(tmp_path):
    asset_path = tmp_path / '标识.png'
    with Image.new('RGBA', (80, 40), (255, 120, 0, 255)) as image:
        image.save(asset_path)
    key, asset = import_image(asset_path)
    logo = new_item('image')
    logo.update(asset_id=key, width=.16, x=.84, y=.86, rotation=25)
    text = new_item('text')
    text.update(text='白鹭 Bird', font_size=72, x=.72, y=.91, stroke_enabled=True, shadow_enabled=True)
    background = new_item('background')
    background.update(x=.74, y=.88, width=.4, height=.16, banner_color='#224466')
    return dict(name='safe-test', ratio='no_crop', overlays=[background, logo, text], overlay_assets={key: asset})


@pytest.mark.parametrize('platform', PLATFORMS[1:])
@pytest.mark.parametrize('size', [(960, 540), (540, 960), (600, 800), (800, 600), (700, 700)])
@pytest.mark.parametrize('oversized', [False, True])
def test_safe_layout_preserves_content_size_with_rotation_and_large_background(payload, platform, size, oversized):
    if oversized:
        payload['overlays'][0].update(width=1.4, height=1.2)
    before = deepcopy(payload)
    original = build_scene(payload, size)
    fitted = build_scene(payload, size, platform_safe_area=platform)
    try:
        rect = safe_rect(size, platform)
        assert len(original.layers) == len(fitted.layers) == 3
        for old, new in zip(original.layers, fitted.layers):
            if old.item['type'] != 'background':
                assert new.size == old.size
                assert new.effective_scale == old.effective_scale
                assert new.pixels.tobytes() == old.pixels.tobytes()
            assert new.rotation == old.rotation
            for x, y in new.corners():
                assert rect[0]-1e-9 <= x <= rect[2]+1e-9
                assert rect[1]-1e-9 <= y <= rect[3]+1e-9
        with Image.new('RGB', size) as base:
            rendered = compose_scene(base, fitted)
        box = rendered.getbbox()
        assert box and box[0] >= int(rect[0]) and box[1] >= int(rect[1])
        assert box[2] <= int(rect[2])+1 and box[3] <= int(rect[3])+1
        rendered.close()
        assert payload == before
    finally:
        original.close()
        fitted.close()


def test_already_safe_and_hidden_layers_do_not_move_visible_content(payload):
    payload['overlays'] = [payload['overlays'][1]]
    payload['overlays'][0].update(x=.5, y=.4, rotation=0, width=.1)
    scene = build_scene(payload, (800, 600))
    try:
        fitted = fit_layers(scene.layers, safe_rect(scene.size, 'douyin'))
        assert fitted[0].center == scene.layers[0].center
        assert fitted[0].size == scene.layers[0].size
    finally:
        scene.close()
    hidden = new_item('text')
    hidden.update(text='hidden', visible=False, scale=50, x=10)
    payload['overlays'].append(hidden)
    scene = build_scene(payload, (800, 600), platform_safe_area='douyin')
    try:
        assert len(scene.layers) == 1 and scene.layers[0].center == (400, 240)
    finally:
        scene.close()


@pytest.mark.parametrize('platform', PLATFORMS[1:])
@pytest.mark.parametrize('crop', [False, True])
def test_preview_matches_image_gif_video_frame_pipeline(payload, platform, crop):
    settings = dict(template_payload=payload, ratio='no_crop', max_long_edge=1000, platform_safe_area=platform)
    box = (.3, 0, .7, 1) if crop else None
    if crop:
        payload['overlay_layouts'] = [dict(id='info', children=[payload['overlays'][1]['id'], payload['overlays'][2]['id']],
            direction='row', gap=.08, align='end', x=.95, y=.95, anchor_x=1, anchor_y=1)]
        settings.update(ratio='free', center_mode='custom', crop_box=box)
    renderer = _BirdStampRendererMixin()
    renderer.template_paths = {}
    renderer.current_photo_info = None
    renderer.current_metadata_context = {}
    with Image.new('RGB', (2000, 1200)) as source:
        renderer.current_source_image = source.resize((500, 300))
        try:
            for order in [('template_crop', 'resize_limit', 'template_overlay'),
                          ('template_crop', 'template_overlay', 'resize_limit')]:
                settings['pipeline_stage_order'] = order
                exported = render_video_frame(VideoFrameJob(Path('bird.jpg'), settings, {}, {}, source_image=source))
                preview = renderer._render_preview_pipeline_image(
                    renderer.current_source_image.copy(), {}, source_image=renderer.current_source_image,
                    settings=settings, crop_box=box, outer_pad=(0, 0, 0, 0),
                    crop_output_size=(800, 1200) if crop else source.size)
                if crop:
                    cropped = preview.crop((150, 0, 350, 300))
                    preview.close()
                    preview = cropped
                with exported.resize(preview.size, Image.Resampling.LANCZOS) as expected:
                    with ImageChops.difference(expected, preview) as diff:
                        assert max(ImageStat.Stat(diff).mean) < 2
                exported.close()
                preview.close()
        finally:
            renderer.current_source_image.close()


def test_legacy_nonfullscreen_unchanged_and_safe_mode_adapts_fields(monkeypatch):
    monkeypatch.setattr(template, '_resolve_template_field_text', lambda *args: 'Bird')
    payload = dict(fields=[dict(font_size=100, align_horizontal='right', align_vertical='bottom')], draw_banner_background=False)
    with Image.new('RGB', (800, 600)) as base:
        legacy = template.render_template_overlay(base, template_payload=payload, raw_metadata={}, metadata_context={})
        disabled = template.render_template_overlay(base, template_payload=payload, raw_metadata={}, metadata_context={}, platform_safe_area='off')
        safe = template.render_template_overlay(base, template_payload=payload, raw_metadata={}, metadata_context={}, platform_safe_area='douyin')
        assert disabled.tobytes() == legacy.tobytes()
        assert safe.getbbox()[3] < legacy.getbbox()[3]
        legacy.close(); disabled.close(); safe.close()


def test_config_normalization_stage_parameter_and_frame_cache(monkeypatch):
    descriptor = next(d for d in build_default_image_proc_pipeline().ui_descriptors() if d.stage_id == 'template_crop')
    option = next(o for o in descriptor.parameter_options if o.key == 'platform_safe_area')
    assert option.default == 'off' and len(option.choices) == 4
    raw = {'platform_safe_area': {'labels': {'douyin': '抖音全屏'}, 'presets': {'douyin': {'portrait': [.1, .1, .2, .2], 'landscape': [.1, .1, .2, .2]}}}}
    monkeypatch.setattr(editor_options, '_load_builtin_editor_options_raw', lambda: raw)
    options = editor_options.load_editor_options()['platform_safe_area']
    assert options['labels']['douyin'] == '抖音全屏'
    assert options['presets']['douyin']['portrait'] == (.1, .1, .2, .2)
    assert normalize_options({'presets': {'douyin': {'portrait': [float('nan')]}}}) == normalize_options(None)
    for legacy in (None, {}, {'platform_safe_area': 'unknown'}):
        assert _BirdStampRendererMixin()._normalize_render_settings(legacy, {'platform_safe_area': 'douyin'})['platform_safe_area'] == 'off'
    job = VideoFrameJob(Path('bird.jpg'), {}, {}, {})
    baseline = source_frame_signature_for_job(job)
    job.settings['platform_safe_area'] = 'off'
    assert source_frame_signature_for_job(job) == baseline
    job.settings['platform_safe_area'] = 'douyin'
    active = source_frame_signature_for_job(job)
    assert active != baseline
    monkeypatch.setattr(editor_options, 'PLATFORM_SAFE_AREA', options)
    safe_area_options.reload_options()
    assert source_frame_signature_for_job(job) != active
    assert core._clone_render_settings(job.settings)['platform_safe_area'] == 'douyin'


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'cache'))
    for name in ('_start_bird_detector_preload', '_run_deferred_startup_tasks',
                 '_restart_photo_list_metadata_loader', '_schedule_async_bird_detect'):
        monkeypatch.setattr(BirdStampEditorWindow, name, lambda *args, **kwargs: None)
    errors = []
    monkeypatch.setattr(BirdStampEditorWindow, '_show_error', lambda self, *args: errors.append(args))
    window = BirdStampEditorWindow()
    yield window
    window.close()
    window.deleteLater()
    _APP.processEvents()
    assert not errors


def test_instance_photo_switch_apply_all_workspace_and_reset(window, payload, tmp_path, monkeypatch):
    template_path = window.template_dir / 'safe-test.json'
    template.save_template_payload(template_path, payload)
    window.template_paths['safe-test'] = template_path
    window.template_combo.addItem('safe-test')
    settings = window._build_current_render_settings()
    settings.update(template_name='safe-test', template_payload=payload, ratio='no_crop')
    paths = [tmp_path / name for name in ('横图.png', '竖图.png')]
    for path, size in zip(paths, ((800, 450), (450, 800))):
        image = Image.new('RGB', size, '#203040')
        image.save(path)
        window._append_photo_path_to_list(path, existing_keys=set(), default_settings=settings)
        window._store_preview_image_cache(window._preview_image_cache_signature(path), image)
    def select(path):
        item = window._find_photo_item_by_path(path)
        window.photo_list.blockSignals(True)
        window.photo_list.setCurrentItem(item)
        window.photo_list.blockSignals(False)
        window._on_photo_selected(item, None)
    combo = window.platform_safe_area_combo
    assert [combo.itemData(i) for i in range(combo.count())] == list(PLATFORMS)
    assert window._pipeline_stage_option_groups['template_crop'].isAncestorOf(combo)
    select(paths[0])
    old = window._original_mode_cache_key()
    combo.setCurrentIndex(combo.findData('douyin'))
    window.render_preview()
    assert window._original_mode_cache_key() != old
    assert window.photo_render_overrides[path_key(paths[0])]['platform_safe_area'] == 'douyin'
    assert window.preview_label.canvas._platform_safe_area_box is not None
    select(paths[1])
    assert combo.currentData() == 'off'
    select(paths[0])
    assert combo.currentData() == 'douyin'
    workspace = tmp_path / '安全框.birdstamp-workspace.json'
    write_workspace_json(workspace, window._collect_workspace_payload(workspace))
    monkeypatch.setattr(window, '_schedule_workspace_photo_selection', lambda *args, **kwargs: None)
    window._restore_workspace_payload(read_workspace_json(workspace), workspace, autosave_after_restore=False)
    for _ in range(20):
        if not window._workspace_restore_in_progress():
            break
        window._process_workspace_restore_photo_batch()
    assert not window._workspace_restore_in_progress()
    select(paths[0])
    assert combo.currentData() == 'douyin'
    window._apply_current_settings_to_all_photos()
    select(paths[1])
    assert combo.currentData() == 'douyin'
    window.render_preview()
    assert window.preview_label.canvas._platform_safe_area_box == pytest.approx(guide_box((450, 800), 'douyin'))
    window.show()
    _APP.processEvents()
    assert window.grab().save(str(tmp_path / 'platform-safe-area-window.png'))
    assert window._pipeline_stage_option_groups['template_crop'].grab().save(str(tmp_path / 'platform-safe-area-controls.png'))
    window._reset_template_overrides()
    assert combo.currentData() == 'off'
    assert window.photo_render_overrides[path_key(paths[0])]['platform_safe_area'] == 'douyin'


def test_guide_ui_only_and_crop_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    canvas = EditorPreviewCanvas()
    canvas.resize(800, 600)
    pixmap = QPixmap(800, 600)
    pixmap.fill(QColor('black'))
    canvas.set_source_pixmap(pixmap, log_performance=False)
    before = canvas.render_source_pixmap_with_overlays().toImage()
    crop = (.2, .1, .8, .9)
    box = guide_box((600, 800), 'xiaohongshu', crop)
    assert crop[0] == box[0] < box[2] == crop[2] and crop[1] < box[1] < box[3] < crop[3]
    canvas.apply_overlay_state(EditorPreviewOverlayState(platform_safe_area_box=box, platform_safe_area='xiaohongshu'))
    canvas.show()
    _APP.processEvents()
    labels = []
    monkeypatch.setattr(canvas, '_draw_crop_resolution_label', lambda p, r, text, c, **kw: labels.append(text))
    canvas.grab()
    assert '小红书 · 叠加安全框' in labels
    assert canvas.render_source_pixmap_with_overlays().toImage() == before
    canvas.apply_overlay_state(EditorPreviewOverlayState())
    assert canvas._platform_safe_area_box is None
    canvas.close()


def test_cli_safe_area(payload, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from birdstamp import cli
    monkeypatch.setattr(config, 'get_user_data_dir', lambda: tmp_path / 'user')
    source = tmp_path / 'Bird.png'
    Image.new('RGB', (800, 600)).save(source)
    path = tmp_path / 'template.json'
    template.save_template_payload(path, payload)
    monkeypatch.setattr(cli, 'extract_many_with_xmp_priority', lambda *a, **k: {source: {'SourceFile': str(source)}})
    args = ['render', str(source), '--out', str(tmp_path/'output'), '--template', str(path),
            '--format', 'png', '--max-long-edge', '0', '--platform-safe-area']
    result = CliRunner().invoke(cli.app, args + ['douyin'])
    assert result.exit_code == 0, result.output + str(result.exception)
    with Image.open(next((tmp_path/'output').glob('*.png'))) as image:
        assert image.getbbox()[3] <= safe_rect(image.size, 'douyin')[3] + 1
    assert CliRunner().invoke(cli.app, args + ['invalid']).exit_code != 0


@pytest.mark.parametrize('direction', ['row', 'up', 'down'])
@pytest.mark.parametrize('align', ['start', 'center', 'end'])
def test_layout_compacts_gaps_without_changing_leaf_size(direction, align):
    doc = dict(overlay_layouts=[dict(id='group', children=['a', 'b'], direction=direction,
        gap=.3, align=align, x=.95, y=.95, anchor_x=1, anchor_y=1)])
    with Image.new('RGBA', (20, 20), 'white') as pixels:
        layers = {key: Layer(dict(id=key), pixels, (900, 900), size)
                  for key, size in [('a', (240, 140)), ('b', (200, 100))]}
        before = {key: layer.size for key, layer in layers.items()}
        rect = (100, 100, 600, 400)
        arrange(doc, layers, (1000, 1000), rect=rect)
        assert {key: layer.size for key, layer in layers.items()} == before
        a, b = (bounds([layers[key]]) for key in ('a', 'b'))
        if direction == 'row':
            assert b[0]-a[2] == pytest.approx(60)
        elif direction == 'down':
            assert b[1]-a[3] == pytest.approx(60)
        else:
            assert a[1]-b[3] == pytest.approx(60)
        box = bounds(list(layers.values()))
        assert box[0] >= rect[0] and box[1] >= rect[1]
        assert box[2:] == pytest.approx(rect[2:])


@pytest.mark.parametrize('platform', PLATFORMS[1:])
def test_nested_row_wraps_and_retains_bottom_right_anchor(platform):
    rect = layout_rect(safe_rect((800, 1000), platform))
    doc = dict(overlay_layouts=[
        dict(id='row', children=['a', 'b', 'c'], direction='row', gap=.02, align='end',
             x=.5, y=.5, anchor_x=1, anchor_y=1),
        dict(id='column', children=['row', 'd'], direction='down', gap=.01, align='end',
             x=.99, y=.99, anchor_x=1, anchor_y=1)])
    with Image.new('RGBA', (20, 20), 'white') as pixels:
        layers = {key: Layer(dict(id=key), pixels, (900, 900), (280, 70)) for key in 'abcd'}
        arrange(doc, layers, (800, 1000), rect=rect)
        a, b, c, d = (bounds([layers[key]]) for key in 'abcd')
        assert a[3] == b[3]  # 第一行
        assert c[1] >= a[3] and c[2] == b[2]  # 第二行沿右边对齐
        assert d[1] > c[3] and d[2] == c[2]  # 外层列仍然生效
        assert bounds(list(layers.values()))[2:] == pytest.approx((min(792, rect[2]), min(990, rect[3])))
        assert all(layer.size == (280, 70) and layer.effective_scale == 1 for layer in layers.values())


def test_impossible_size_remains_visible_at_original_scale():
    with Image.new('RGBA', (20, 20), 'white') as pixels:
        layer = Layer(dict(id='huge'), pixels, (500, 500), (1000, 800), effective_scale=2)
        fitted = fit_layers([layer], (100, 100, 500, 500), anchor=(0, 1))[0]
        assert fitted.size == layer.size and fitted.effective_scale == 2
        box = bounds([fitted])
        assert box[0] == 100 and box[3] == 500
        assert box[2] > 500 and box[1] < 100


def test_xiaohongshu_current_screenshot_has_bottom_controls_not_right_rail():
    # 用户截图 945x2048，头像约 y=1635；留少许余量，将底边定在 79%。
    left, top, right, bottom = safe_rect((945, 2048), 'xiaohongshu')
    assert left == 0
    assert right == 945
    assert 1590 < bottom < 1635
    assert top/2048 == pytest.approx(.12)
    defaults = safe_area_options.default_options()
    assert editor_options.PLATFORM_SAFE_AREA['presets']['xiaohongshu'] == defaults['presets']['xiaohongshu']


def test_number_and_full_width_gradient_do_not_shrink_bottom_layout(payload, tmp_path):
    name = payload['overlays'][2]
    name.update(id='name', text='苍鹭 Grey Heron', font_size=55, rotation=0)
    caption = dict(name, id='caption', text='上海南汇东滩湿地', font_size=45)
    number = dict(name, id='number', text='8', font_size=60, x=.96, y=.02)
    background = payload['overlays'][0]
    background.update(layout_mode='auto', banner_background_style='gradient_bottom')
    payload['overlays'] = [background, name, caption, number]
    payload['overlay_layouts'] = [dict(id='details', direction='down', children=['name', 'caption'],
        gap=.01, align='start', x=.05, y=.97, anchor_x=0, anchor_y=1)]
    original = build_scene(payload, (900, 1600), text_scale=1.3)
    active = build_scene(payload, (900, 1600), text_scale=1.3, platform_safe_area='xiaohongshu')
    try:
        before, after = ({v.item['id']: v for v in scene.layers} for scene in (original, active))
        for key in ('name', 'caption', 'number'):
            assert after[key].size == before[key].size
            assert after[key].effective_scale == before[key].effective_scale
        assert after['name'].center[0] == before['name'].center[0]
        assert after['number'].center[1] < after['name'].center[1]
        rect = safe_rect(active.size, 'xiaohongshu')
        assert bounds(active.layers)[3] <= rect[3]
        assert after[background['id']].size[0] > 840  # 不再把横幅缩成中间窄块。
        with Image.new('RGB', active.size, '#567080') as image:
            rendered = compose_scene(image, active)
            rendered.save(tmp_path/'layout-safe-preview.png')
            rendered.close()
    finally:
        original.close(); active.close()
