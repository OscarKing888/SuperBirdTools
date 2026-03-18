from __future__ import annotations

from PIL import Image, ImageDraw

from birdstamp.gui.editor_template import default_template_payload, render_template_overlay


def test_render_template_overlay_survives_unicode_measurement_failure(monkeypatch) -> None:
    original_textbbox = ImageDraw.ImageDraw.textbbox

    def _textbbox_with_forced_unicode_error(self, xy, text, *args, **kwargs):
        if "ў" in str(text):
            raise UnicodeEncodeError("latin-1", str(text), 0, 1, "ordinal not in range(256)")
        return original_textbbox(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "textbbox", _textbbox_with_forced_unicode_error)

    image = Image.new("RGB", (1080, 1920), color="#FFFFFF")
    payload = default_template_payload(name="default")
    metadata_context = {
        "bird": "ўbird",
        "bird_species_cn": "ўbird",
        "bird_species_en": "fallback",
        "capture_text": "2026-03-18 12:00",
        "location": "",
        "gps_text": "",
        "camera": "SONY ILCE-1M2",
        "lens": "",
        "settings_text": "f/5.6 1/640s ISO4000 600mm",
        "stem": "sample",
        "filename": "sample.jpg",
    }

    rendered = render_template_overlay(
        image,
        raw_metadata={},
        metadata_context=metadata_context,
        template_payload=payload,
        draw_text=True,
    )

    assert rendered.size == (1080, 1920)
