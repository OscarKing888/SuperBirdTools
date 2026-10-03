"""GUI 和 CLI 共用的降噪参数与结果；导入时不加载模型或 Qt。"""
from __future__ import annotations

from dataclasses import dataclass


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


class DenoiseCancelled(Exception):
    """任务已取消，调用者须清理当前照片的临时结果。"""


class UnsupportedImage(ValueError):
    """不能按静态 SDR 照片安全处理的输入。"""


def check_cancelled(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise DenoiseCancelled("已取消")
