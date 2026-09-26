from pathlib import Path

import numpy as np
from PIL import Image

from birdstamp import export_stage
from birdstamp.export_stage import (
    VideoFrameJob,
    crop_plan_precompute_required,
    dejitter_reference_active,
    prepare_uniform_auto_crop_plans,
)


def _reference_settings(source: str) -> dict:
    return {
        "draw_banner": False,
        "draw_text": False,
        "draw_focus": False,
        "uniform_auto_crop": False,
        "auto_crop_stabilization": 0,
        "ratio": 1.0,
        "center_mode": "image",
        "max_long_edge": 0,
        "crop_padding_top": 0,
        "crop_padding_bottom": 0,
        "crop_padding_left": 0,
        "crop_padding_right": 0,
        "crop_padding_fill": "#000000",
        "dejitter_strategy": "reference_region",
        "dejitter_reference_enabled": True,
        "dejitter_reference_regions": [[0.3, 0.3, 0.7, 0.7]],
        "dejitter_reference_source": source,
    }


def test_reference_active_and_precompute_required() -> None:
    settings = _reference_settings("frame-1.png")
    assert dejitter_reference_active(settings) is True
    assert crop_plan_precompute_required(settings) is True
    # 没有参考区时回落到原有行为（不触发参考路径）。
    plain = dict(settings)
    plain["dejitter_reference_enabled"] = False
    plain["dejitter_reference_regions"] = []
    assert dejitter_reference_active(plain) is False
    assert crop_plan_precompute_required(plain) is False


def test_reference_region_dejitter_shifts_crop_center_to_follow_feature() -> None:
    rng = np.random.default_rng(3)
    base = (rng.random((100, 100)) * 255).astype("uint8")
    shifted = np.roll(np.roll(base, 4, axis=1), 4, axis=0)  # 内容整体右移+下移 4 像素
    img1 = Image.fromarray(base, "L").convert("RGB")
    img2 = Image.fromarray(shifted, "L").convert("RGB")

    source = "frame-1.png"
    jobs = [
        VideoFrameJob(
            path=Path("frame-1.png"),
            settings=_reference_settings(source),
            raw_metadata={},
            metadata_context={},
            source_image=img1,
        ),
        VideoFrameJob(
            path=Path("frame-2.png"),
            settings=_reference_settings(source),
            raw_metadata={},
            metadata_context={},
            source_image=img2,
        ),
    ]

    prepared = prepare_uniform_auto_crop_plans(jobs)
    assert prepared == 2

    centers = [
        export_stage._crop_plan_center_in_source_pixels(
            source_width=100,
            source_height=100,
            crop_plan=job.crop_plan,
        )
        for job in jobs
    ]
    # 参考帧保持原中心；后续帧裁切中心跟随特征位移（约 +4 像素）。
    assert abs(centers[0][0] - 50.0) <= 1.0
    assert abs(centers[0][1] - 50.0) <= 1.0
    assert abs(centers[1][0] - 54.0) <= 1.5
    assert abs(centers[1][1] - 54.0) <= 1.5


def _sequence(tmp_path, **overrides):
    rng = np.random.default_rng(11)
    pixels = (rng.random((120, 160)) * 255).astype('uint8')
    paths = [tmp_path / '参考照片.png', tmp_path / '第二帧.png']
    Image.fromarray(pixels).save(paths[0])
    Image.fromarray(np.roll(np.roll(pixels, 6, axis=1), -3, axis=0)).save(paths[1])
    settings = {**_reference_settings(str(paths[0])), 'center_mode': 'custom',
                'crop_box': [.2, .2, .8, .8], **overrides}
    return [VideoFrameJob(path=p, settings=dict(settings), raw_metadata={}, metadata_context={}) for p in paths]


def _center(job):
    return export_stage._crop_plan_center_in_source_pixels(source_width=160, source_height=120, crop_plan=job.crop_plan)


def test_manual_crop_follows_reference_and_rendered_pixels_align(tmp_path):
    jobs = _sequence(tmp_path)
    prepare_uniform_auto_crop_plans(jobs)
    np.testing.assert_allclose(np.subtract(_center(jobs[1]), _center(jobs[0])), (6, -3), atol=1)
    rendered = [export_stage.render_video_frame(job) for job in jobs]
    try:
        assert rendered[0].size == rendered[1].size == (96, 72)
        np.testing.assert_array_equal(np.asarray(rendered[0]), np.asarray(rendered[1]))
    finally:
        for image in rendered:
            image.close()


def test_export_subset_uses_reference_outside_batch(tmp_path):
    jobs = _sequence(tmp_path)
    prepare_uniform_auto_crop_plans(jobs[1:])
    np.testing.assert_allclose(_center(jobs[1]), (86, 57), atol=1)


def test_missing_reference_is_reported_not_silently_ignored(tmp_path):
    import pytest
    jobs = _sequence(tmp_path)
    jobs[0].path.unlink()
    with pytest.raises(ValueError, match='无法读取去抖动参考照片'):
        prepare_uniform_auto_crop_plans(jobs[1:])


def test_original_free_and_no_crop_and_zero_strength(tmp_path):
    for ratio in (None, 'free'):
        jobs = _sequence(tmp_path, ratio=ratio)
        prepare_uniform_auto_crop_plans(jobs)
        np.testing.assert_allclose(np.subtract(_center(jobs[1]), _center(jobs[0])), (6, -3), atol=1)
    for override in ({'ratio': 'no_crop'}, {'dejitter_reference_strength': 0}):
        jobs = _sequence(tmp_path, **override)
        assert prepare_uniform_auto_crop_plans(jobs) == 0
        assert jobs[1].crop_plan is None


def test_reference_file_and_strength_invalidate_frame_signature(tmp_path):
    from birdstamp.export_stage.core import source_frame_signature_for_job
    from birdstamp.export_frame_cache import build_source_frame_bucket_key
    jobs = _sequence(tmp_path)
    sig = source_frame_signature_for_job(jobs[1])
    Image.new('RGB', (160, 120), 'red').save(jobs[0].path)
    assert source_frame_signature_for_job(jobs[1]) != sig
    settings = jobs[1].settings
    assert build_source_frame_bucket_key(global_export_settings=settings) != build_source_frame_bucket_key(
        global_export_settings={**settings, 'dejitter_reference_strength': 25})


def test_partial_strength_and_border_padding_keep_crop_size(tmp_path):
    from birdstamp.export_stage import core
    jobs = _sequence(tmp_path, dejitter_reference_strength=50, crop_box=[.5, .2, 1.0, .8])
    prepare_uniform_auto_crop_plans(jobs)
    np.testing.assert_allclose(np.subtract(_center(jobs[1]), _center(jobs[0])), (3, -1.5), atol=.6)
    assert jobs[1].crop_plan[1][3] == 3
    assert core._compute_crop_output_size(160, 120, *jobs[1].crop_plan) == (80, 72)


def test_cancelled_precompute_does_not_open_reference(tmp_path, monkeypatch):
    import threading
    import pytest
    from birdstamp.export_stage import core
    jobs = _sequence(tmp_path)
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(core, '_open_job_image_for_crop_plan', lambda *args: pytest.fail('cancelled read'))
    with pytest.raises(export_stage.VideoExportCancelledError):
        prepare_uniform_auto_crop_plans(jobs, cancel_event=stop)


def test_external_reference_keeps_focus_metadata(tmp_path, monkeypatch):
    from birdstamp.export_stage import core
    import app_common.exif_io
    jobs = _sequence(tmp_path)
    jobs[1].settings.update(center_mode='focus', crop_box=None,
                            dejitter_reference_crop_settings={'center_mode': 'focus', 'ratio': 1})
    monkeypatch.setattr(app_common.exif_io, 'extract_many_with_xmp_priority',
                        lambda *args, **kwargs: {jobs[0].path.resolve(): {'focus-test': 1}})
    original = core._compute_crop_plan_for_image
    seen = []
    def capture(**kwargs):
        seen.append((kwargs['path'], kwargs['raw_metadata']))
        return original(**kwargs)
    monkeypatch.setattr(core, '_compute_crop_plan_for_image', capture)
    prepare_uniform_auto_crop_plans(jobs[1:])
    assert seen[0] == (jobs[0].path, {'focus-test': 1})
