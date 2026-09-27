"""效果参数、实际像素及预览/导出共用路径回归。"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageStat

from birdstamp.export_stage import VideoFrameJob, render_video_frame
from birdstamp.gui import editor_template as template
from birdstamp.render.text_effects import normalize_text_effects
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
def test_preview_effects_match_export_and_scale_with_text(payload, scale):
    payload['fields'][0].update(stroke_enabled=True, stroke_color='#125AFF', stroke_width=4,
                               shadow_enabled=True, shadow_color='#EE7711', shadow_offset_x=-12,
                               shadow_offset_y=15, shadow_blur=4)
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
    field = template.normalize_template_field(normalized, 0)
    assert normalize_text_effects(field) == normalized
