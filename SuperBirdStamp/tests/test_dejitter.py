import numpy as np

from birdstamp.image_dejitter import (
    DeJitterContext,
    DeJitterFrame,
    MedianCenterStabilizationStrategy,
    NumpyPhaseCorrelationAligner,
    ReferenceRegionStabilizationStrategy,
    normalize_strategy_id,
    resolve_dejitter_strategy,
)


def _shifted_patches(dx: int, dy: int, size: int = 192, margin: int = 24):
    """构造一对"真实平移"的 patch：target 内容相对 ref 向右/下平移 (dx, dy)。"""
    rng = np.random.default_rng(7)
    big = rng.random((size + 2 * margin, size + 2 * margin))
    ref = big[margin : margin + size, margin : margin + size]
    target = big[margin - dy : margin - dy + size, margin - dx : margin - dx + size]
    return np.ascontiguousarray(ref), np.ascontiguousarray(target)


def test_numpy_aligner_estimates_translation_sign_and_magnitude() -> None:
    ref, target = _shifted_patches(dx=6, dy=4)
    aligner = NumpyPhaseCorrelationAligner()
    dx, dy, confidence = aligner.estimate_translation(ref, target)
    assert abs(dx - 6.0) <= 1.0
    assert abs(dy - 4.0) <= 1.0
    assert confidence > 2.0


def test_numpy_aligner_handles_mismatched_shapes() -> None:
    aligner = NumpyPhaseCorrelationAligner()
    dx, dy, confidence = aligner.estimate_translation(
        np.zeros((10, 10)), np.zeros((12, 12))
    )
    assert (dx, dy, confidence) == (0.0, 0.0, 0.0)


def test_strategy_registry_defaults_to_median() -> None:
    assert normalize_strategy_id("bogus") == "median"
    assert isinstance(resolve_dejitter_strategy("median"), MedianCenterStabilizationStrategy)
    assert isinstance(
        resolve_dejitter_strategy("reference_region"),
        ReferenceRegionStabilizationStrategy,
    )


def test_median_strategy_blends_centers_to_median() -> None:
    frames = [
        DeJitterFrame(
            source_width=100,
            source_height=100,
            center=(25.0, 25.0),
            center_norm=(0.25, 0.25),
            strength=100,
        ),
        DeJitterFrame(
            source_width=100,
            source_height=100,
            center=(75.0, 75.0),
            center_norm=(0.75, 0.75),
            strength=100,
        ),
    ]
    context = DeJitterContext(frames=frames, strength=100)
    MedianCenterStabilizationStrategy().stabilize(context)
    assert frames[0].stable_center == (50.0, 50.0)
    assert frames[1].stable_center == (50.0, 50.0)


def test_median_strategy_noop_when_strength_zero() -> None:
    frames = [
        DeJitterFrame(100, 100, (25.0, 25.0), (0.25, 0.25), strength=0),
        DeJitterFrame(100, 100, (75.0, 75.0), (0.75, 0.75), strength=0),
    ]
    MedianCenterStabilizationStrategy().stabilize(DeJitterContext(frames=frames, strength=0))
    assert frames[0].stable_center is None
    assert frames[1].stable_center is None


def test_reference_region_strategy_follows_feature_displacement() -> None:
    ref_patch, target_patch = _shifted_patches(dx=20, dy=10)
    region = (0.4, 0.4, 0.6, 0.6)  # 100px 图上为 20x20 像素区域

    reference_frame = DeJitterFrame(
        source_width=100,
        source_height=100,
        center=(50.0, 50.0),
        center_norm=(0.5, 0.5),
        region_patches=(ref_patch,),
        is_reference=True,
    )
    target_frame = DeJitterFrame(
        source_width=100,
        source_height=100,
        center=(50.0, 50.0),
        center_norm=(0.5, 0.5),
        region_patches=(target_patch,),
    )
    context = DeJitterContext(
        frames=[reference_frame, target_frame],
        reference_regions=(region,),
        reference_patches=(ref_patch,),
        reference_raw_center=(50.0, 50.0),
    )
    ReferenceRegionStabilizationStrategy().stabilize(context)

    # 参考帧位移为 0，稳定中心保持原点。
    assert reference_frame.stable_center is not None
    assert abs(reference_frame.stable_center[0] - 50.0) <= 0.6
    assert abs(reference_frame.stable_center[1] - 50.0) <= 0.6

    # 目标帧特征右移 20、下移 10 patch 像素，按 20/192 缩放回源图像素。
    expected_dx = 20.0 * (20.0 / 192.0)
    expected_dy = 10.0 * (20.0 / 192.0)
    assert target_frame.stable_center is not None
    assert abs(target_frame.stable_center[0] - (50.0 + expected_dx)) <= 0.6
    assert abs(target_frame.stable_center[1] - (50.0 + expected_dy)) <= 0.6


def test_reference_region_strategy_noop_without_regions() -> None:
    frame = DeJitterFrame(100, 100, (10.0, 10.0), (0.1, 0.1))
    context = DeJitterContext(frames=[frame], reference_regions=(), reference_patches=())
    ReferenceRegionStabilizationStrategy().stabilize(context)
    assert frame.stable_center is None
