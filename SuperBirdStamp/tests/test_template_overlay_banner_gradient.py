from PIL import Image

from birdstamp.gui.editor_template import (
    _resolve_template_field_text,
    default_template_payload,
    normalize_template_payload,
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

    gradient_height = max(
        1,
        int(round(image.height * float(payload["banner_gradient_height_pct"]) / 100.0)),
    )
    gradient_top = image.height - gradient_height
    top_sample = rendered.getpixel((image.width // 2, max(0, gradient_top - 10)))
    mid_sample = rendered.getpixel((image.width // 2, gradient_top + gradient_height // 2))
    bottom_sample = rendered.getpixel((image.width // 2, image.height - 20))

    assert top_sample == (255, 255, 255)
    assert 255 > mid_sample[0] > bottom_sample[0]
    assert 255 > mid_sample[1] > bottom_sample[1]
    assert 255 > mid_sample[2] > bottom_sample[2]
    assert bottom_sample[0] < 140
    assert bottom_sample[1] < 140
    assert bottom_sample[2] < 140


def test_render_template_overlay_honors_draw_text_false() -> None:
    image = Image.new("RGB", (640, 360), color="#FFFFFF")
    payload = default_template_payload(name="default")
    payload["draw_banner_background"] = False
    for field in payload.get("fields", []):
        field["color"] = "#000000"

    rendered = render_template_overlay(
        image,
        raw_metadata={"SourceFile": "sample.jpg", "XMP-dc:Title": "不应绘制"},
        metadata_context={},
        template_payload=payload,
        draw_banner=False,
        draw_text=False,
    )

    assert rendered.getextrema() == ((255, 255), (255, 255), (255, 255))


def test_template_field_text_falls_back_to_provider_caption_when_empty() -> None:
    photo = PhotoInfo.from_path("/tmp/sample.jpg", raw_metadata={"SourceFile": "/tmp/sample.jpg"})
    provider = build_template_context_provider("exif", "EXIF:Model", display_label="机身型号")

    assert _resolve_template_field_text(provider, photo) == "机身型号"


def test_template_payload_allows_explicit_empty_fields() -> None:
    payload = default_template_payload(name="empty-fields")
    payload["fields"] = []

    normalized = normalize_template_payload(payload, fallback_name="empty-fields")

    assert normalized["fields"] == []


def _overlay_payload(style: str, banner_color: str, text_style: str = "bold_italic") -> dict:
    payload = normalize_template_payload(default_template_payload(name="equivalence"), fallback_name="equivalence")
    payload["banner_background_style"] = style
    payload["draw_banner_background"] = True
    payload["banner_color"] = banner_color
    for field in payload.get("fields") or []:
        field["style"] = text_style
        field["color"] = "#ffcc0099"
    return payload


def _render_both_paths(monkeypatch, image, payload, **kwargs):
    from birdstamp.gui import editor_template as template

    monkeypatch.setattr(template, "_resolve_template_field_text", lambda provider, info: "北红尾鸲 Daurian Redstart 1/2000s")
    fast = template.render_template_overlay(image, raw_metadata={}, metadata_context={}, template_payload=payload, **kwargs)
    monkeypatch.setattr(template, "_banner_fill_is_opaque", lambda _payload: False)
    reference = template.render_template_overlay(image, raw_metadata={}, metadata_context={}, template_payload=payload, **kwargs)
    return fast, reference


def test_rgb_canvas_fast_path_matches_rgba_round_trip(monkeypatch) -> None:
    from birdstamp.gui import editor_template as template

    gradient = Image.linear_gradient("L").resize((1600, 1000))
    image = Image.merge("RGB", (gradient, gradient.transpose(Image.Transpose.FLIP_LEFT_RIGHT), gradient.rotate(90)))
    for style in (template.BANNER_BACKGROUND_STYLE_SOLID, template.BANNER_BACKGROUND_STYLE_GRADIENT_BOTTOM):
        for layout in (None, (4800, 3000)):
            payload = _overlay_payload(style, "#203040")
            fast, reference = _render_both_paths(monkeypatch, image, payload, layout_size=layout)
            assert fast.mode == reference.mode == "RGB"
            assert fast.tobytes() == reference.tobytes(), (style, layout)
            monkeypatch.undo()


def test_translucent_banner_keeps_rgba_canvas_semantics(monkeypatch) -> None:
    from birdstamp.gui import editor_template as template

    assert template._banner_fill_is_opaque({"banner_color": "#ffffff"})
    assert template._banner_fill_is_opaque({"banner_color": "none"})
    assert not template._banner_fill_is_opaque({"banner_color": "#ffffff80"})


def test_rgb_layer_composite_matches_alpha_composite_with_clipping() -> None:
    from birdstamp.gui.editor_template import _composite_rgba_layer

    base = Image.linear_gradient("L").resize((64, 48)).convert("RGB")
    layer = Image.radial_gradient("L").resize((40, 30))
    rgba_layer = Image.merge("RGBA", (layer, layer.rotate(90), layer.transpose(Image.Transpose.FLIP_TOP_BOTTOM), layer))
    for dest in ((5, 7), (-10, -6), (40, 30), (0, 0)):
        rgb = base.copy()
        _composite_rgba_layer(rgb, rgba_layer, dest)
        rgba = base.convert("RGBA")
        rgba.alpha_composite(rgba_layer, dest)
        assert rgb.tobytes() == rgba.convert("RGB").tobytes(), dest
