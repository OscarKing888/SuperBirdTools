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


def test_uniform_auto_crop_precomputes_same_source_frame_size() -> None:
    jobs = [
        VideoFrameJob(
            path=Path("wide.jpg"),
            settings=_settings(ratio=2.0),
            raw_metadata={},
            metadata_context={},
            source_image=Image.new("RGB", (120, 80), "#ffffff"),
        ),
        VideoFrameJob(
            path=Path("tall.jpg"),
            settings=_settings(ratio=2.0),
            raw_metadata={},
            metadata_context={},
            source_image=Image.new("RGB", (80, 120), "#ffffff"),
        ),
    ]

    prepared = prepare_uniform_auto_crop_plans(jobs)
    rendered_sizes = [render_video_frame(job).size for job in jobs]

    assert prepared == 2
    assert rendered_sizes == [(120, 60), (120, 60)]


def test_precomputed_uniform_crop_plan_is_used_without_second_bird_detection() -> None:
    calls = 0
    original_detect = export_stage._detect_primary_bird_box

    def _fake_detect(_image):
        nonlocal calls
        calls += 1
        return (0.25, 0.25, 0.75, 0.75)

    jobs = [
        VideoFrameJob(
            path=Path("bird-a.jpg"),
            settings=_settings(center_mode="bird"),
            raw_metadata={},
            metadata_context={},
            source_image=Image.new("RGB", (100, 80), "#ffffff"),
        ),
        VideoFrameJob(
            path=Path("bird-b.jpg"),
            settings=_settings(center_mode="bird"),
            raw_metadata={},
            metadata_context={},
            source_image=Image.new("RGB", (80, 100), "#ffffff"),
        ),
    ]

    try:
        export_stage._detect_primary_bird_box = _fake_detect
        signature_before = source_frame_signature_for_job(jobs[0])
        prepare_uniform_auto_crop_plans(jobs)
        signature_after = source_frame_signature_for_job(jobs[0])
        assert calls == 2
        assert signature_before != signature_after

        def _fail_detect(_image):
            raise AssertionError("bird detection should not run during render")

        export_stage._detect_primary_bird_box = _fail_detect
        for job in jobs:
            rendered = render_video_frame(job)
            assert rendered.size[0] == rendered.size[1]
    finally:
        export_stage._detect_primary_bird_box = original_detect


def test_auto_crop_stabilization_blends_centers_to_group_median() -> None:
    original_detect = export_stage._detect_primary_bird_box
    boxes = iter(
        [
            (0.15, 0.15, 0.35, 0.35),
            (0.65, 0.65, 0.85, 0.85),
        ]
    )

    def _fake_detect(_image):
        return next(boxes)

    jobs = [
        VideoFrameJob(
            path=Path("bird-left.jpg"),
            settings=_settings(center_mode="bird", stabilization=100),
            raw_metadata={},
            metadata_context={},
            source_image=Image.new("RGB", (100, 100), "#ffffff"),
        ),
        VideoFrameJob(
            path=Path("bird-right.jpg"),
            settings=_settings(center_mode="bird", stabilization=100),
            raw_metadata={},
            metadata_context={},
            source_image=Image.new("RGB", (100, 100), "#ffffff"),
        ),
    ]

    try:
        export_stage._detect_primary_bird_box = _fake_detect
        prepare_uniform_auto_crop_plans(jobs)
        centers = [
            export_stage._crop_plan_center_in_source_pixels(
                source_width=100,
                source_height=100,
                crop_plan=job.crop_plan,
            )
            for job in jobs
        ]
        assert centers == [(50.0, 50.0), (50.0, 50.0)]
    finally:
        export_stage._detect_primary_bird_box = original_detect
