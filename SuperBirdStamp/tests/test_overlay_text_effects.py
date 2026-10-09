"""效果参数、实际像素及预览/导出共用路径回归。"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageStat, ImageFont, ImageFilter

from birdstamp.export_stage import VideoFrameJob, render_video_frame, source_frame_signature_for_job
from birdstamp.gui import editor_template as template
from birdstamp.render.text_effects import normalize_text_effects, styled_text_layer
from birdstamp.overlays.model import document, new_item
from birdstamp.workspace import read_workspace_json, write_workspace_json
from test_template_text_scale import payload


@pytest.mark.parametrize('style', ['normal', 'bold', 'italic', 'bold_italic'])
def test_colored_outline_and_shadow_render_real_pixels(payload, style, monkeypatch):
    monkeypatch.setattr(template, "_resolve_template_field_text", lambda *args: "白鹭 Bird")
    field = payload['fields'][0]
    field.update(style=style, stroke_enabled=True, stroke_color='#FF0000', stroke_width=3,
                 shadow_enabled=True, shadow_color='#00FF00', shadow_opacity=100,
                 shadow_offset_x=-16, shadow_offset_y=16, shadow_blur=0)
    with Image.new('RGB', (800, 450)) as source:
        result = template.render_template_overlay(source, raw_metadata={}, metadata_context={},
                                                   template_payload=payload, auto_scale_font=False)
    pixels = np.asarray(result)
    assert np.count_nonzero((pixels[:,:,0] > 150) & (pixels[:,:,1] < 40)) > 30
    assert np.count_nonzero((pixels[:,:,1] > 150) & (pixels[:,:,0] < 40)) > 30
    assert np.count_nonzero((pixels[:,:,0] > 200) & (pixels[:,:,1] > 200)) > 30
    result.close()


def test_disabled_effects_preserve_legacy_output(payload):
    with Image.new('RGB', (800, 450)) as source:
        legacy = template.render_template_overlay(source, raw_metadata={}, metadata_context={}, template_payload=payload)
        payload['fields'][0].update(stroke_enabled=False, stroke_width=20, shadow_enabled=False, shadow_blur=50)
        disabled = template.render_template_overlay(source, raw_metadata={}, metadata_context={}, template_payload=payload)
    assert legacy.tobytes() == disabled.tobytes()


@pytest.mark.parametrize('scale', [.5, 1, 2])
@pytest.mark.parametrize('kind', ['legacy', 'text', 'badge'])
def test_preview_effects_match_export_and_scale_with_text(payload, scale, kind):
    payload['fields'][0].update(stroke_enabled=True, stroke_color='#125AFF', stroke_width=4,
                               shadow_enabled=True, shadow_color='#EE7711', shadow_offset_x=-12,
                               shadow_offset_y=15, shadow_blur=4, stroke_opacity=37.5)
    if kind != 'legacy':
        item = new_item(kind)
        item.update(payload['fields'][0], text_mode='literal', text='Bird')
        payload = dict(overlays=[item])
    with Image.new('RGB', (1600, 900)) as source:
        exported = render_video_frame(VideoFrameJob(Path('bird.jpg'),
            dict(template_payload=payload, ratio='no_crop', draw_banner=False, text_scale=scale),
            {}, {}, source_image=source))
        preview = template.render_template_overlay(source.resize((400,225)), raw_metadata={}, metadata_context={},
            template_payload=payload, layout_size=source.size, text_scale=scale, draw_banner=False)
    diff = ImageChops.difference(exported.resize(preview.size, Image.Resampling.LANCZOS), preview)
    assert max(ImageStat.Stat(diff).mean) < 1.0


def test_effect_normalization_roundtrip_and_invalid_values():
    values = dict(shadow_enabled='false', shadow_color='bad', shadow_blur=float('nan'),
                  shadow_offset_x=-999, stroke_enabled=True, stroke_width=100, stroke_color='red')
    normalized = normalize_text_effects(values)
    assert normalized['shadow_enabled'] is False
    assert normalized['shadow_color'] == '#000000'
    assert normalized['shadow_blur'] == 3
    assert normalized['shadow_offset_x'] == -100
    assert normalized['stroke_width'] == 20
    assert normalized['stroke_color'] == '#FF0000'
    assert normalized['stroke_opacity'] == 100
    field = template.normalize_template_field(normalized, 0)
    assert normalize_text_effects(field) == normalized


@pytest.mark.parametrize('value,expected', [(-20, 0), (200, 100), (37.5, 37.5),
                                           (None, 100), ('bad', 100), (float('nan'), 100)])
def test_stroke_opacity_normalization(value, expected):
    assert normalize_text_effects({'stroke_opacity': value})['stroke_opacity'] == expected


@pytest.mark.parametrize('style', ['normal', 'bold', 'italic', 'bold_italic'])
def test_stroke_alpha_changes_without_fading_text(style):
    font = ImageFont.load_default(size=80)
    layers = []
    try:
        for opacity in (0, 50, 100):
            effects = normalize_text_effects(dict(stroke_enabled=True, stroke_color='#FF0000',
                                                 stroke_width=6, stroke_opacity=opacity))
            layer, _ = styled_text_layer('Bird', font.getbbox('Bird'), font=font,
                color='#FFFFFF', style=style, effects=effects, scale=1)
            layers.append(layer)
        pixels = [np.asarray(layer) for layer in layers]
        outline = (pixels[2][:, :, 0] == 255) & (pixels[2][:, :, 1] == 0) & (pixels[2][:, :, 3] == 255)
        text = np.all(pixels[2] == 255, axis=2)
        # 斜体会做双三次插值；在实心区域验证 alpha，避开边缘插值振铃。
        outline = np.asarray(Image.fromarray(outline.astype('uint8') * 255).filter(ImageFilter.MinFilter(5))) > 0
        text = np.asarray(Image.fromarray(text.astype('uint8') * 255).filter(ImageFilter.MinFilter(5))) > 0
        assert np.count_nonzero(outline) > 50 and np.count_nonzero(text) > 50
        assert np.all(pixels[0][:, :, 3][outline] == 0)
        assert np.all(pixels[1][:, :, 3][outline] == 128)
        assert all(np.all(p[text] == 255) for p in pixels)
    finally:
        for layer in layers:
            layer.close()


@pytest.mark.parametrize('kind', ['text', 'badge'])
def test_stroke_opacity_saved_in_template_workspace_and_cache_signature(tmp_path, kind):
    item = new_item(kind)
    item.update(stroke_enabled=True, stroke_opacity=42.25)
    doc = document(dict(overlays=[item]))
    template_path = tmp_path / '半透明描边.json'
    template.save_template_payload(template_path, doc)
    loaded = template.load_template_payload(template_path)
    assert loaded['overlays'][0]['stroke_opacity'] == 42.25
    workspace = tmp_path / '工作区.json'
    write_workspace_json(workspace, dict(photos=[dict(render_settings=dict(overlay_override=doc))]))
    restored = read_workspace_json(workspace)['photos'][0]['render_settings']['overlay_override']
    assert restored == doc
    job = VideoFrameJob(Path('bird.jpg'), dict(template_payload=loaded, overlay_override=restored), {}, {})
    original = source_frame_signature_for_job(job)
    restored['overlays'][0]['stroke_opacity'] = 100
    assert source_frame_signature_for_job(job) != original
