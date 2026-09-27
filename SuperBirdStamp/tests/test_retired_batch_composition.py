from pathlib import Path

from PIL import Image

from birdstamp import export_stage
from birdstamp.export_stage import (
    VideoFrameJob,
    prepare_uniform_auto_crop_plans,
    render_video_frame,
    source_frame_signature_for_job,
)


def _settings(*, ratio: float = 1.0, center_mode: str = "image", stabilization: int = 0) -> dict:
    return {
        "draw_banner": False,
        "draw_text": False,
        "draw_focus": False,
        "uniform_auto_crop": True,
        "auto_crop_stabilization": stabilization,
        "ratio": ratio,
        "center_mode": center_mode,
        "max_long_edge": 0,
        "crop_padding_top": 0,
        "crop_padding_bottom": 0,
        "crop_padding_left": 0,
        "crop_padding_right": 0,
        "crop_padding_fill": "#000000",
    }


def test_retired_batch_flags_do_not_precompute_or_unify_sizes():
    from birdstamp.export_stage.core import crop_plan_precompute_required
    jobs = [VideoFrameJob(path=Path(str(i)),settings=_settings(ratio=2,stabilization=100),
                         raw_metadata={},metadata_context={}, source_image=Image.new('RGB',size))
            for i,size in enumerate(((120,80),(80,120)))]
    assert not crop_plan_precompute_required(jobs[0].settings)
    assert prepare_uniform_auto_crop_plans(jobs) == 0
    assert all(job.crop_plan is None for job in jobs)
    assert [render_video_frame(job).size for job in jobs] == [(120,60),(80,40)]


def test_old_batch_settings_are_ignored_by_render_and_cache_keys():
    from birdstamp.export_stage import core
    from birdstamp.export_frame_cache import global_export_settings_from_settings
    settings = _settings(stabilization=100)
    normalized = core._clone_render_settings(settings)
    assert 'uniform_auto_crop' not in normalized and 'auto_crop_stabilization' not in normalized
    old = global_export_settings_from_settings(settings)
    settings.pop('uniform_auto_crop'); settings.pop('auto_crop_stabilization')
    assert old == global_export_settings_from_settings(settings)


def test_pad_and_crop_matches_pad_then_crop() -> None:
    from birdstamp.gui import editor_core as core

    base_rgb = Image.linear_gradient("L").resize((90, 60)).convert("RGB")
    images = {
        "RGB": base_rgb,
        "RGBA": base_rgb.convert("RGBA"),
        "L": base_rgb.convert("L"),
    }
    pads = [(0, 0, 0, 0), (10, 5, 20, 0), (0, 12, 0, 7), (8, 8, 8, 8)]
    boxes = [None, (0.0, 0.0, 1.0, 1.0), (0.1, 0.2, 0.7, 0.9), (0.0, 0.0, 0.5, 0.5), (0.3, 0.1, 1.0, 0.6), (-0.2, 0.1, 0.8, 1.2)]
    for mode, image in images.items():
        for pad in pads:
            for box in boxes:
                expected = core.crop_image_by_normalized_box(
                    core.pad_image(image, top=pad[0], bottom=pad[1], left=pad[2], right=pad[3], fill="#336699"), box
                )
                actual = core.pad_and_crop_image(image, pad, box, fill="#336699")
                assert actual.mode == expected.mode and actual.size == expected.size, (mode, pad, box)
                assert actual.tobytes() == expected.tobytes(), (mode, pad, box)


def test_render_video_frame_returns_independent_rgb_image() -> None:
    source = Image.new("RGB", (60, 40), "#224466")
    job = VideoFrameJob(
        path=Path("frame.jpg"),
        settings={"draw_banner": False, "draw_text": False, "draw_focus": False, "ratio": "no_crop", "max_long_edge": 0},
        raw_metadata={},
        metadata_context={},
        source_image=source,
    )
    rendered = render_video_frame(job)
    assert rendered.mode == "RGB"
    assert rendered is not source
    rendered.paste((255, 0, 0), (0, 0, 10, 10))
    assert source.getpixel((0, 0)) == (0x22, 0x44, 0x66)
