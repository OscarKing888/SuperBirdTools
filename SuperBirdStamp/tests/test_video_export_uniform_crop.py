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


def _write_photo(path: Path, size: tuple[int, int], color: str) -> Path:
    Image.new("RGB", size, color).save(path, quality=95)
    return path


def test_persisted_crop_plans_skip_second_precompute_until_inputs_change(tmp_path, monkeypatch) -> None:
    import threading

    from birdstamp.export_stage import core
    from birdstamp.export_stage.video_export_options import VideoExportOptions

    photos = [_write_photo(tmp_path / f"p{i}.jpg", (120 + 10 * i, 80), "#88aacc") for i in range(3)]
    calls: list[int] = []
    original_prepare = core.prepare_uniform_auto_crop_plans

    def _counting_prepare(jobs, **kwargs):
        calls.append(len(jobs))
        return original_prepare(jobs, **kwargs)

    monkeypatch.setattr(core, "prepare_uniform_auto_crop_plans", _counting_prepare)

    def _jobs(ratio: float = 2.0):
        return [VideoFrameJob(path=p, settings=_settings(ratio=ratio), raw_metadata={}, metadata_context={}) for p in photos]

    def _run(jobs, *, preserve=True, dirty=frozenset()):
        options = VideoExportOptions(output_path=tmp_path / "out.mp4", preserve_temp_files=preserve)
        core._ensure_source_frame_cache(
            jobs,
            output_path=tmp_path / "out.mp4",
            options=options,
            template_paths={},
            progress_callback=None,
            cancel_event=None,
            bird_box_cache={},
            bird_box_lock=threading.Lock(),
            dirty_path_keys=set(dirty),
        )
        return [job.crop_plan for job in jobs]

    first = _run(_jobs())
    assert calls == [3]
    second = _run(_jobs())
    assert calls == [3]  # unchanged inputs: no decode/precompute
    assert second == first
    _run(_jobs(ratio=1.5))
    assert calls == [3, 3]  # changed settings invalidate the saved plans
    _run(_jobs(ratio=1.5), dirty={str(photos[0])})
    assert calls == [3, 3, 3]  # dirty photos always recompute
    photos[1].write_bytes(photos[1].read_bytes())  # touch: new signature
    _write_photo(photos[1], (150, 80), "#88aacc")
    _run(_jobs(ratio=1.5))
    assert calls == [3, 3, 3, 3]


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
