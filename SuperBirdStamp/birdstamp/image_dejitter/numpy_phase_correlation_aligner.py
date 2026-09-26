from __future__ import annotations

import numpy as np

from .dejitter_utils import hann_window_2d, to_float_gray
from .feature_aligner import FeatureAligner


class NumpyPhaseCorrelationAligner(FeatureAligner):
    """FFT 粗定位 + 局部 DFT 亚像素细化；只接受有重叠纹理证据的平移。"""

    aligner_id = "numpy_phase_correlation"

    def estimate_translation(self, ref: np.ndarray, target: np.ndarray) -> tuple[float, float, float]:
        a = to_float_gray(ref)
        b = to_float_gray(target)
        if a is None or b is None or a.shape != b.shape or min(a.shape) < 8:
            return (0.0, 0.0, 0.0)
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            return (0.0, 0.0, 0.0)
        a = a - a.mean()
        b = b - b.mean()
        if min(float(a.std()), float(b.std())) < 1e-6:
            return (0.0, 0.0, 0.0)
        height, width = a.shape
        window = hann_window_2d(height, width)
        cross = np.fft.fft2(a * window) * np.conj(np.fft.fft2(b * window))
        magnitude = np.abs(cross)
        normalized = cross / np.maximum(magnitude, float(magnitude.max()) * 1e-12)
        response = np.fft.ifft2(normalized).real
        py, px = np.unravel_index(int(np.argmax(response)), response.shape)
        # 排除主峰邻域后评估旁瓣；无关噪声、周期纹理不能仅凭“最大峰”算成功。
        yy = np.minimum((np.arange(height) - py) % height, (py - np.arange(height)) % height)
        xx = np.minimum((np.arange(width) - px) % width, (px - np.arange(width)) % width)
        side = response[~((yy[:, None] <= 3) & (xx[None, :] <= 3))]
        if not side.size:
            return (0.0, 0.0, 0.0)
        peak = float(response[py, px])
        confidence = (peak - float(side.mean())) / max(float(side.std()), 1e-12)
        if confidence < 8.0 or peak < float(side.max()) * 1.4:
            return (0.0, 0.0, 0.0)
        sy = float(py if py <= height / 2 else py - height)
        sx = float(px if px <= width / 2 else px - width)
        # 在峰附近直接计算离散傅里叶变换，避免把整个图像放大 20 倍。
        offsets = np.arange(-15, 16, dtype=np.float64) / 20.0
        ys, xs = sy + offsets, sx + offsets
        local = (np.exp(2j * np.pi * np.outer(ys, np.fft.fftfreq(height)))
                 @ normalized @ np.exp(2j * np.pi * np.outer(np.fft.fftfreq(width), xs))).real
        iy, ix = np.unravel_index(int(np.argmax(local)), local.shape)
        dx, dy = -float(xs[ix]), -float(ys[iy])
        # 半个区域附近存在周期绕回歧义；宁可报告失配，不制造错误补偿。
        if abs(dx) >= width * 0.4 or abs(dy) >= height * 0.4:
            return (0.0, 0.0, 0.0)
        if self._overlap_correlation(a, b, dx, dy) < 0.45:
            return (0.0, 0.0, 0.0)
        return (dx, dy, confidence)

    @staticmethod
    def _overlap_correlation(a: np.ndarray, b: np.ndarray, dx: float, dy: float) -> float:
        """在真实重叠部分双线性采样，核验位移，不使用 FFT 的循环边界。"""
        h, w = a.shape
        xs = np.arange(max(0, int(np.ceil(-dx))), min(w, int(np.floor(w - 1 - dx))))
        ys = np.arange(max(0, int(np.ceil(-dy))), min(h, int(np.floor(h - 1 - dy))))
        if min(len(xs), len(ys)) < 4:
            return 0.0
        tx, ty = xs + dx, ys + dy
        x0, y0 = np.floor(tx).astype(int), np.floor(ty).astype(int)
        wx, wy = tx - x0, (ty - y0)[:, None]
        sampled = ((1 - wy) * ((1 - wx) * b[y0[:, None], x0] + wx * b[y0[:, None], x0 + 1])
                   + wy * ((1 - wx) * b[y0[:, None] + 1, x0] + wx * b[y0[:, None] + 1, x0 + 1]))
        original = a[np.ix_(ys, xs)]
        original = original - original.mean()
        sampled -= sampled.mean()
        denom = float(np.linalg.norm(original) * np.linalg.norm(sampled))
        return float(np.sum(original * sampled)) / denom if denom > 1e-12 else 0.0
