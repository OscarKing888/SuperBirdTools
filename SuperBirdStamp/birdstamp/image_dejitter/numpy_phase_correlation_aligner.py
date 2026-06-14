from __future__ import annotations

import numpy as np

from .dejitter_utils import hann_window_2d, to_float_gray
from .feature_aligner import FeatureAligner


class NumpyPhaseCorrelationAligner(FeatureAligner):
    """基于 numpy FFT 相位相关的平移估计（仅依赖 numpy）。"""

    aligner_id = "numpy_phase_correlation"

    def estimate_translation(
        self,
        ref: np.ndarray,
        target: np.ndarray,
    ) -> tuple[float, float, float]:
        a = to_float_gray(ref)
        b = to_float_gray(target)
        if a is None or b is None or a.shape != b.shape:
            return (0.0, 0.0, 0.0)
        height, width = a.shape
        if height < 2 or width < 2:
            return (0.0, 0.0, 0.0)

        window = hann_window_2d(height, width)
        a = (a - float(a.mean())) * window
        b = (b - float(b.mean())) * window

        fa = np.fft.fft2(a)
        fb = np.fft.fft2(b)
        cross = fa * np.conj(fb)
        magnitude = np.abs(cross)
        magnitude[magnitude == 0] = 1.0
        normalized = cross / magnitude
        response = np.fft.ifft2(normalized).real

        peak_index = int(np.argmax(response))
        peak_y, peak_x = np.unravel_index(peak_index, response.shape)
        peak_value = float(response[peak_y, peak_x])

        dy = float(peak_y)
        dx = float(peak_x)
        if dy > height / 2.0:
            dy -= height
        if dx > width / 2.0:
            dx -= width

        mean_value = float(np.mean(np.abs(response))) or 1e-9
        confidence = abs(peak_value) / mean_value
        return (-dx, -dy, confidence)
