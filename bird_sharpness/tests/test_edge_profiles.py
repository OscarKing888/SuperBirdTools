"""Sharpened step edges remain measurable without admitting thin lines as detail."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from bird_sharpness.metrics import EdgeBlurField, SIGMA_FLOOR_PX
from bird_sharpness.scoring import classify


def _unsharp(image: np.ndarray, amount: float) -> np.ndarray:
    return np.clip(image + amount * (image - cv2.GaussianBlur(image, (0, 0), 1.0)), 0.0, 1.0)


def _disc() -> np.ndarray:
    image = np.full((400, 400), 0.2, np.float32)
    cv2.circle(image, (200, 200), 100, 0.8, -1)
    return image


@pytest.mark.parametrize("amount", [0.5, 1.0, 2.0, 3.0])
def test_camera_sharpening_does_not_turn_a_sharp_step_into_a_blank_head(amount):
    """The original floor rejected all 38 real edge samples and assigned soft/100."""
    field = EdgeBlurField(_unsharp(_disc(), amount))
    stats = field.strongest_edge_blur(None)
    verdict, score = classify(stats.sigma, None, None, eye_visible=True, head_blank=stats.sigma is None)
    assert stats.edge_count >= 20
    assert stats.sigma == pytest.approx(SIGMA_FLOOR_PX)
    assert verdict == "sharp" and score >= 500


@pytest.mark.parametrize("angle", [0, 22.5, 45, 67.5, 90, 137.5])
@pytest.mark.parametrize("polarity", [-1, 1])
def test_sharpened_step_profiles_work_in_both_polarities_and_oblique_directions(angle, polarity):
    yy, xx = np.mgrid[:240, :240].astype(np.float32)
    radians = np.deg2rad(angle)
    signed = (xx - 119.5) * np.cos(radians) + (yy - 119.5) * np.sin(radians)
    image = (0.5 + polarity * np.where(signed >= 0, 0.3, -0.3)).astype(np.float32)
    stats = EdgeBlurField(_unsharp(image, 1.0)).strongest_edge_blur(None)
    assert stats.edge_count >= 20
    assert SIGMA_FLOOR_PX <= stats.sigma <= 0.85


@pytest.mark.parametrize("amount", [0.0, 0.5, 2.0])
def test_thin_needles_are_not_restored_as_sharpened_steps(amount):
    image = np.full((400, 400), 0.2, np.float32)
    for y in range(40, 360, 24):
        image[y, 20:-20] = 0.9
    selection = EdgeBlurField(_unsharp(image, amount)).select_strongest_edges(None)
    assert selection.selected.sum() >= 30
    assert selection.line_like.sum() >= 30
    assert selection.sigma.size == 0


def test_sharp_needles_do_not_make_an_out_of_focus_disc_sharp():
    image = cv2.GaussianBlur(_disc(), (0, 0), 2.0)
    for y in range(40, 360, 24):
        image[y, 20:-20] = 0.9
    stats = EdgeBlurField(image).strongest_edge_blur(None)
    assert stats.sigma is None or stats.sigma > 0.85


def test_sharpened_noise_still_has_no_measurable_head():
    noise = np.random.default_rng(47).normal(0.4, 0.01, (400, 400)).astype(np.float32)
    assert EdgeBlurField(_unsharp(noise, 1.0)).strongest_edge_blur(None).sigma is None


def test_profile_restoration_requires_real_neighbours_at_the_crop_border():
    image = np.full((120, 120), 0.2, np.float32)
    image[:, 2:] = 0.8
    stats = EdgeBlurField(_unsharp(image, 1.0)).strongest_edge_blur(None)
    assert stats.sigma is None


def test_unsharpened_gaussian_blur_keeps_its_measured_radius():
    image = cv2.GaussianBlur(_disc(), (0, 0), 2.0)
    stats = EdgeBlurField(image).strongest_edge_blur(None)
    assert stats.sigma == pytest.approx(np.hypot(2.0, 0.73), rel=0.1)
