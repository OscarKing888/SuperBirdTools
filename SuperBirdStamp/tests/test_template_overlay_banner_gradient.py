from PIL import Image

from birdstamp.gui.editor_template import (
    _resolve_template_field_text,
    default_template_payload,
    render_template_overlay,
)
from birdstamp.gui.template_context import PhotoInfo, build_template_context_provider


def test_default_template_draws_bottom_gradient_banner() -> None:
    image = Image.new("RGB", (1080, 1920), color="#FFFFFF")
    payload = default_template_payload(name="default")
    metadata_context = {
        "bird": "红胁蓝尾鸲",
        "capture_text": "",
        "location": "",
        "gps_text": "",
        "camera": "SONY · ILCE-1M2",
        "lens": "",
        "settings_text": "f/5.6  1/640s  ISO4000  600mm",
        "stem": "sample",
        "filename": "sample.jpg",
    }

    rendered = render_template_overlay(
        image,
        raw_metadata={},
        metadata_context=metadata_context,
        template_payload=payload,
        draw_text=False,
    )

    top_sample = rendered.getpixel((540, 1300))
    mid_sample = rendered.getpixel((540, 1600))
    bottom_sample = rendered.getpixel((540, 1900))

    assert top_sample == (255, 255, 255)
    assert 255 > mid_sample[0] > bottom_sample[0]
    assert 255 > mid_sample[1] > bottom_sample[1]
    assert 255 > mid_sample[2] > bottom_sample[2]
    assert bottom_sample[0] < 140
    assert bottom_sample[1] < 140
    assert bottom_sample[2] < 140


def test_template_field_text_falls_back_to_provider_caption_when_empty() -> None:
    photo = PhotoInfo.from_path("/tmp/sample.jpg", raw_metadata={"SourceFile": "/tmp/sample.jpg"})
    provider = build_template_context_provider("exif", "EXIF:Model", display_label="机身型号")

    assert _resolve_template_field_text(provider, photo) == "机身型号"
