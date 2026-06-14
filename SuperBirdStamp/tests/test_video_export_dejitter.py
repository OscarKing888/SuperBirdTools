from pathlib import Path

import numpy as np
from PIL import Image

from birdstamp import video_export
from birdstamp.video_export import (
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
        video_export._crop_plan_center_in_source_pixels(
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
