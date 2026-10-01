"""规范分析尺度：同一场景不同分辨率给出一致判定，窗口不越界，采样带抗混叠。"""
import numpy as np
import pytest
from PIL import Image, ImageFilter

from birdstamp.image_dejitter.analysis_window import region_scale, region_window, motion_margin, CANONICAL_GEOM_PX
from birdstamp.image_dejitter.subject_local_tracker import SubjectLocalTracker

REGIONS = ((.2,.25,.4,.6),(.55,.3,.8,.7))


def master(shift=(0,0), size=(1536,1152)):
    rng = np.random.default_rng(42)
    base = Image.fromarray(rng.integers(0,255,(size[1]//3,size[0]//3,3),dtype=np.uint8)).resize(size, Image.Resampling.BICUBIC)
    base = base.filter(ImageFilter.GaussianBlur(1.2))
    moved = Image.new('RGB', size)
    moved.paste(base, shift)
    return moved


def test_same_scene_at_two_resolutions_has_same_verdict_and_scaled_displacement():
    reference, moving = master(), master((12,-9))
    small = [image.resize((768,576), Image.Resampling.LANCZOS) for image in (reference, moving)]
    high = SubjectLocalTracker(reference, REGIONS)
    low = SubjectLocalTracker(small[0], REGIONS)
    assert np.array(high.geometry.scales)/np.array(low.geometry.scales) == pytest.approx((2,2), rel=.05)
    a, b = high.track(moving), low.track(small[1])
    assert a.observation.status == b.observation.status == 'tracked', (a.error, b.error)
    assert np.array(a.observation.displacement) == pytest.approx(np.array(b.observation.displacement)*2, abs=.6)
    # 规范尺度下角点数量相近（同样的相对纹理密度）。
    for x, y in zip(high.templates, low.templates):
        assert abs(len(x.corners)-len(y.corners)) <= .35*max(len(x.corners), len(y.corners))


def test_window_stays_inside_and_scale_targets_canonical_size():
    size = (11232, 7488)
    box = (0., 0., .03, .05)
    scale = region_scale(size, box)
    window = region_window(size, box, scale, motion_margin(size))
    assert window.origin[0] >= 0 and window.origin[1] >= 0
    assert window.origin[0]+window.extent[0] <= size[0]+1e-6 and window.origin[1]+window.extent[1] <= size[1]+1e-6
    w, h = box[2]*size[0], box[3]*size[1]
    assert np.sqrt(w*h)/scale == pytest.approx(CANONICAL_GEOM_PX, rel=.01)
    placed = window.placed(size, (-10_000., 10_000.))
    assert placed.origin[0] == 0 and placed.origin[1]+placed.extent[1] <= size[1]+1e-6


def test_window_sampling_is_antialiased_resize():
    image = master()
    window = region_window(image.size, (.3,.3,.6,.6), 3., 20.)
    x, y = window.origin
    expected = image.resize(window.size, Image.Resampling.LANCZOS,
                            box=(x, y, x+window.extent[0], y+window.extent[1])).convert('L')
    np.testing.assert_array_equal(window.gray(image), np.array(expected))
    # 1 像素棋盘在下采样后应近乎均匀灰，而不是混叠出粗纹理。
    checker = Image.fromarray(((np.indices((300,300)).sum(axis=0) % 2)*255).astype(np.uint8)).convert('RGB')
    gray = region_window(checker.size, (0,0,1,1), 5., 0.).gray(checker)
    assert gray.std() < 3
