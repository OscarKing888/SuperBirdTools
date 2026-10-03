"""相机有效画幅之外的填充不能参与降噪或写进新成片。"""
from types import SimpleNamespace

import numpy as np
import pytest
import rawpy

from app_common.raw_preview_geometry import rawpy_camera_crop_box
from image_denoise.image_io import camera_frame_pixels, decode_image, linear_to_srgb, orient_pixels


@pytest.mark.parametrize('flip,orientation', [(0, 1), (3, 3), (5, 8), (6, 6)])
def test_raw_camera_frame_is_cropped_after_rotation_before_inference(tmp_path, monkeypatch, flip, orientation):
    path = tmp_path / '鹰鹃.ARW'
    path.touch()
    sensor = np.zeros((20, 30, 3), np.uint16)
    content = np.arange(12 * 20 * 3, dtype=np.uint16).reshape(12, 20, 3) + 1000
    sensor[2:14, 3:23] = content
    sizes = SimpleNamespace(width=30, height=20, left_margin=4, top_margin=6,
                            crop_left_margin=7, crop_top_margin=8, crop_width=20, crop_height=12, flip=flip)
    class Raw:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def postprocess(self, **kwargs):
            assert kwargs['output_bps'] == 16 and not kwargs['half_size']
            return orient_pixels(sensor, orientation)
    raw = Raw()
    raw.sizes = sizes
    monkeypatch.setattr(rawpy, 'imread', lambda stream: raw)
    decoded = decode_image(path)
    expected = orient_pixels(content, orientation)
    np.testing.assert_array_equal(decoded.rgb, linear_to_srgb(expected.astype(np.float32) / 65535))
    assert decoded.camera_crop is None  # 已是相机画幅，显示时不能再映射一次。


def test_reported_sony_geometry_retains_exact_16bit_pixels_and_real_dark_content():
    sizes = SimpleNamespace(width=6144, height=4096, left_margin=0, top_margin=0,
                            crop_left_margin=12, crop_top_margin=12, crop_width=5616, crop_height=3744, flip=0)
    array = np.zeros((4096, 6144, 3), np.uint16)
    array[12:3756, 12:5628] = 12345
    array[12:30, 12:40] = 0  # 照片真实黑色不会被内容猜测裁掉。
    actual = camera_frame_pixels(array, rawpy_camera_crop_box(sizes))
    assert actual.shape == (3744, 5616, 3) and actual.dtype == np.uint16
    np.testing.assert_array_equal(actual, array[12:3756, 12:5628])


@pytest.mark.parametrize('crop', [None, (0, 0, 1, 1), (-.1, 0, 1, 1), (0, 0, 0, 1), (0, 0, float('nan'), 1)])
def test_unknown_or_invalid_geometry_preserves_all_pixels(crop):
    array = np.arange(60, dtype=np.uint16).reshape(4, 5, 3)
    np.testing.assert_array_equal(camera_frame_pixels(array, crop), array)
