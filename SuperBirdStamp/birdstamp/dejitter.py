# -*- coding: utf-8 -*-
"""dejitter.py – 去抖动（裁切中心稳定化）策略接口与实现。

本模块把 SuperBirdStamp 的"去抖动"逻辑抽象为可插拔的策略（Strategy）接口，
并把策略运行所需的上下文封装为 :class:`DeJitterContext`，以便支持多种场景：

* 不选参考标记区域：``MedianCenterStabilizationStrategy`` —— 把每帧裁切中心向
  组内中位中心线性混合（保留原有"防抖滑块"行为）。
* 选 1 个或多个参考标记区域：``ReferenceRegionStabilizationStrategy`` —— 以初始
  帧框选的特征参考区为锚点，后续帧用特征对齐估计平移，使被跟踪特征在输出中保持稳定。

对齐后端通过 :class:`FeatureAligner` 抽象，默认实现
``NumpyPhaseCorrelationAligner`` 仅依赖 numpy（相位相关估计平移），跨平台/打包安全；
未来可新增基于 OpenCV 的实现而无需改动调用方。

设计要点：
* 本模块只处理 numpy 灰度 patch 与归一化/像素坐标，不依赖 PIL/Qt，图像生命周期由
  调用方（``export_stage``）负责。
* 策略 ``stabilize(context)`` 就地把结果写回每个 :class:`DeJitterFrame` 的
  ``stable_center``（源图像素坐标），与现有 ``candidate["stable_center"]`` 约定一致。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

NormalizedBox = tuple[float, float, float, float]

STRATEGY_MEDIAN = "median"
STRATEGY_REFERENCE_REGION = "reference_region"
DEFAULT_STRATEGY = STRATEGY_MEDIAN

DEFAULT_PATCH_SIZE = 192
# 匹配置信度低于该阈值的区域视为无效匹配（不参与位移估计）。
DEFAULT_MIN_CONFIDENCE = 2.0


def _median_float(values: Sequence[float]) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) * 0.5


def _clamp_percent(value: Any, default: int = 0) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = int(default)
    return max(0, min(100, parsed))


# ---------------------------------------------------------------------------
# 上下文封装
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DeJitterFrame:
    """单帧去抖动上下文。

    ``region_patches`` 为该帧在各参考区（归一化坐标）位置抽取并缩放到统一尺寸的
    灰度 patch，顺序与 :attr:`DeJitterContext.reference_patches` 对齐；当不使用参考区
    策略时可为空。
    """

    source_width: int
    source_height: int
    center: tuple[float, float]  # 原始裁切中心（源图像素）
    center_norm: tuple[float, float]  # 原始裁切中心（归一化 0..1）
    strength: int = 0  # 该帧稳定化强度 0..100
    source_path: Path | None = None
    region_patches: tuple[np.ndarray, ...] = ()
    is_reference: bool = False
    stable_center: tuple[float, float] | None = None  # 输出：稳定后中心（源图像素）


@dataclass(slots=True)
class DeJitterContext:
    """去抖动策略运行所需的封装上下文。

    支持 0 / 1 / N 个参考区：``reference_regions`` 为空时只有中位混合等无参考策略
    可用；为多个时由支持多区域的策略聚合处理。
    """

    frames: list[DeJitterFrame]
    strength: int = 0
    reference_regions: tuple[NormalizedBox, ...] = ()
    reference_patches: tuple[np.ndarray, ...] = ()
    reference_raw_center: tuple[float, float] | None = None
    reference_source: Path | None = None
    aligner: "FeatureAligner | None" = None
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 对齐后端（可插拔）
# ---------------------------------------------------------------------------


class FeatureAligner(ABC):
    """特征对齐后端抽象：估计 ``target`` 相对 ``ref`` 的平移。"""

    aligner_id = "aligner"

    @abstractmethod
    def estimate_translation(
        self,
        ref: np.ndarray,
        target: np.ndarray,
    ) -> tuple[float, float, float]:
        """返回 ``(dx, dy, confidence)``。

        约定：当 ``target`` 的内容相对 ``ref`` 向右/下平移了 ``(dx, dy)`` 像素时，
        返回的 ``dx``/``dy`` 为正。``confidence`` 越大表示匹配越可靠。
        """
        raise NotImplementedError


def _to_float_gray(patch: np.ndarray) -> np.ndarray | None:
    if patch is None:
        return None
    arr = np.asarray(patch, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr.mean(axis=2)
    if arr.ndim != 2 or arr.size == 0:
        return None
    return arr


def _hann_window_2d(height: int, width: int) -> np.ndarray:
    if height <= 1:
        wy = np.ones(max(1, height), dtype=np.float64)
    else:
        wy = np.hanning(height)
    if width <= 1:
        wx = np.ones(max(1, width), dtype=np.float64)
    else:
        wx = np.hanning(width)
    return np.outer(wy, wx)


class NumpyPhaseCorrelationAligner(FeatureAligner):
    """基于 numpy FFT 相位相关的平移估计（仅依赖 numpy）。"""

    aligner_id = "numpy_phase_correlation"

    def estimate_translation(
        self,
        ref: np.ndarray,
        target: np.ndarray,
    ) -> tuple[float, float, float]:
        a = _to_float_gray(ref)
        b = _to_float_gray(target)
        if a is None or b is None or a.shape != b.shape:
            return (0.0, 0.0, 0.0)
        height, width = a.shape
        if height < 2 or width < 2:
            return (0.0, 0.0, 0.0)

        window = _hann_window_2d(height, width)
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
        # 相位相关峰位移表示把 target 对齐回 ref 所需的平移；
        # target 内容相对 ref 的位移与之反号。
        return (-dx, -dy, confidence)


# ---------------------------------------------------------------------------
# 策略接口与实现
# ---------------------------------------------------------------------------


class DeJitterStrategy(ABC):
    strategy_id = "strategy"
    label = "去抖动策略"
    requires_reference_regions = False
    supports_multiple_regions = False

    @abstractmethod
    def stabilize(self, context: DeJitterContext) -> None:
        """就地把稳定后中心写入每个 frame 的 ``stable_center``。"""
        raise NotImplementedError


class MedianCenterStabilizationStrategy(DeJitterStrategy):
    """把每帧裁切中心向中位中心线性混合（保留原有防抖行为）。"""

    strategy_id = STRATEGY_MEDIAN
    label = "中位中心混合"
    requires_reference_regions = False
    supports_multiple_regions = False

    def stabilize(self, context: DeJitterContext) -> None:
        frames = context.frames
        if len(frames) <= 1:
            return
        blend = _clamp_percent(context.strength, 0) / 100.0
        if blend <= 0:
            return
        center_x = _median_float([float(frame.center_norm[0]) for frame in frames])
        center_y = _median_float([float(frame.center_norm[1]) for frame in frames])
        if center_x is None or center_y is None:
            return
        for frame in frames:
            raw_x, raw_y = frame.center_norm
            stable_x = float(raw_x) * (1.0 - blend) + center_x * blend
            stable_y = float(raw_y) * (1.0 - blend) + center_y * blend
            frame.stable_center = (
                stable_x * float(frame.source_width),
                stable_y * float(frame.source_height),
            )


class ReferenceRegionStabilizationStrategy(DeJitterStrategy):
    """以初始帧框选的特征参考区为锚点，按帧间平移补偿裁切中心。

    对每帧、每个参考区用对齐后端估计相对参考帧的平移，聚合（中位）为该帧位移；
    目标中心 = 参考帧原始裁切中心 + 帧位移，再按强度与原始中心混合。
    """

    strategy_id = STRATEGY_REFERENCE_REGION
    label = "参考区特征对齐"
    requires_reference_regions = True
    supports_multiple_regions = True

    def stabilize(self, context: DeJitterContext) -> None:
        frames = context.frames
        if not frames:
            return
        aligner = context.aligner or NumpyPhaseCorrelationAligner()
        reference_patches = context.reference_patches
        regions = context.reference_regions
        if not reference_patches or not regions:
            return
        blend = _clamp_percent(context.strength, 0) / 100.0
        if blend <= 0:
            blend = 1.0  # 参考区模式默认完全跟随特征

        for frame in frames:
            displacement = self._estimate_frame_displacement(
                frame=frame,
                regions=regions,
                reference_patches=reference_patches,
                aligner=aligner,
                min_confidence=context.min_confidence,
            )
            raw_center = frame.center
            if displacement is None:
                frame.stable_center = raw_center
                continue
            base_center = context.reference_raw_center or raw_center
            target_x = float(base_center[0]) + displacement[0]
            target_y = float(base_center[1]) + displacement[1]
            stable_x = float(raw_center[0]) * (1.0 - blend) + target_x * blend
            stable_y = float(raw_center[1]) * (1.0 - blend) + target_y * blend
            frame.stable_center = (stable_x, stable_y)

    @staticmethod
    def _estimate_frame_displacement(
        *,
        frame: DeJitterFrame,
        regions: tuple[NormalizedBox, ...],
        reference_patches: tuple[np.ndarray, ...],
        aligner: FeatureAligner,
        min_confidence: float,
    ) -> tuple[float, float] | None:
        if not frame.region_patches:
            return None
        count = min(len(regions), len(reference_patches), len(frame.region_patches))
        if count <= 0:
            return None
        dxs: list[float] = []
        dys: list[float] = []
        for index in range(count):
            ref_patch = reference_patches[index]
            target_patch = frame.region_patches[index]
            if ref_patch is None or target_patch is None:
                continue
            patch_h, patch_w = np.asarray(ref_patch).shape[:2]
            if patch_h < 2 or patch_w < 2:
                continue
            dx_patch, dy_patch, confidence = aligner.estimate_translation(ref_patch, target_patch)
            if confidence < float(min_confidence):
                continue
            box = regions[index]
            region_px_w = max(1.0, (float(box[2]) - float(box[0])) * float(frame.source_width))
            region_px_h = max(1.0, (float(box[3]) - float(box[1])) * float(frame.source_height))
            scale_x = region_px_w / float(patch_w)
            scale_y = region_px_h / float(patch_h)
            disp_x = dx_patch * scale_x
            disp_y = dy_patch * scale_y
            # 限幅：单帧位移不超过参考区尺寸的一半，避免误匹配导致跳变。
            disp_x = max(-region_px_w * 0.5, min(region_px_w * 0.5, disp_x))
            disp_y = max(-region_px_h * 0.5, min(region_px_h * 0.5, disp_y))
            dxs.append(disp_x)
            dys.append(disp_y)
        if not dxs:
            return None
        median_x = _median_float(dxs)
        median_y = _median_float(dys)
        if median_x is None or median_y is None:
            return None
        return (median_x, median_y)


_STRATEGY_REGISTRY: dict[str, type[DeJitterStrategy]] = {
    STRATEGY_MEDIAN: MedianCenterStabilizationStrategy,
    STRATEGY_REFERENCE_REGION: ReferenceRegionStabilizationStrategy,
}


def normalize_strategy_id(value: Any) -> str:
    strategy_id = str(value or "").strip().lower()
    if strategy_id in _STRATEGY_REGISTRY:
        return strategy_id
    return DEFAULT_STRATEGY


def resolve_dejitter_strategy(strategy_id: Any) -> DeJitterStrategy:
    return _STRATEGY_REGISTRY[normalize_strategy_id(strategy_id)]()


__all__ = [
    "DEFAULT_PATCH_SIZE",
    "DEFAULT_STRATEGY",
    "STRATEGY_MEDIAN",
    "STRATEGY_REFERENCE_REGION",
    "DeJitterContext",
    "DeJitterFrame",
    "DeJitterStrategy",
    "FeatureAligner",
    "MedianCenterStabilizationStrategy",
    "NumpyPhaseCorrelationAligner",
    "ReferenceRegionStabilizationStrategy",
    "normalize_strategy_id",
    "resolve_dejitter_strategy",
]
