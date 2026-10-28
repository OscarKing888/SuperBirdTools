"""Catchlights must not remove ordinary high-contrast feather detail."""

import cv2
import numpy as np
import pytest

from bird_sharpness.metrics import specular_highlights


@pytest.mark.parametrize("radius", [2, 4, 7])
def test_isolated_catchlight_keeps_its_exclusion_margin(radius):
    gray = np.full((160, 160), 0.04, np.float32)
    cv2.circle(gray, (80, 80), radius, 0.9, -1)
    original = gray.copy()

    excluded = specular_highlights(gray, 90, margin=8)

    assert excluded[80, 80]
    assert excluded[80, 80 + radius + 7]
    assert not excluded[80, 80 + radius + 10]
    np.testing.assert_array_equal(gray, original)


@pytest.mark.parametrize("angle", [0, 30, 45, 90])
def test_bright_feather_streak_is_not_a_catchlight(angle):
    gray = np.full((180, 180), 0.04, np.float32)
    radians = np.deg2rad(angle)
    offset = np.rint(np.array([np.cos(radians), np.sin(radians)]) * 20).astype(int)
    center = np.array([90, 90])
    cv2.line(gray, tuple(center - offset), tuple(center + offset), 0.9, 3)

    assert not specular_highlights(gray, 150).any()


def test_compact_texture_is_not_glare_but_nearby_isolated_spot_is():
    gray = np.full((240, 280), 0.04, np.float32)
    # Dense bright feather tips are small and round, but have other bright texture
    # around them. Top-hat's erosion minimum alone wrongly treats them as glints.
    for y in range(40, 169, 8):
        for x in range(40, 169, 8):
            cv2.circle(gray, (x, y), 3, 0.7, -1)
    cv2.circle(gray, (230, 110), 4, 0.9, -1)

    excluded = specular_highlights(gray, 150)

    assert not excluded[70:140, 70:140].any()
    assert excluded[110, 230]


def test_unresolved_bright_specks_do_not_remove_large_patches_of_feathers():
    gray = np.full((150, 150), 0.04, np.float32)
    gray[20:130:10, 20:130:10] = 0.9

    assert not specular_highlights(gray, 100).any()
