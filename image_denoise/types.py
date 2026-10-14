"""GUI 和 CLI 共用的降噪参数与结果；导入时不加载模型或 Qt。"""
from __future__ import annotations

from dataclasses import dataclass
from app_common.image_processing_types import (
    ImageProcessingCancelled as DenoiseCancelled, UnsupportedImage, check_cancelled,
)


@dataclass(frozen=True)
class DenoiseOptions:
    output_mode: str = "source_subdir"
    subdir: str = "denoised"
    output_directory: str = ""
    format: str = "tiff"
    strength: int = 100
    device: str = "auto"
    workers: int = 2


@dataclass(frozen=True)
class DenoiseResult:
    source: str
    destination: str = ""
    status: str = "success"  # success / failed / skipped / cancelled
    error: str = ""
    device: str = ""
    tile_size: int = 0
