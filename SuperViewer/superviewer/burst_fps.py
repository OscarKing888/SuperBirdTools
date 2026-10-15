# -*- coding: utf-8 -*-
"""连拍计算的 FPS 输入边界、换算与相机档位（不依赖 Qt）。

档位汇总各厂商不同机型的静态照片连拍速度，不表示每台相机都支持全部档位。
包括机械/电子快门、裁切和短时高速连拍；不收录视频帧率。
资料来源与维护范围见 ``SuperViewer/docs/burst_fps_presets.md``。
"""
from __future__ import annotations

import math

from app_common.burst_info import DEFAULT_MAX_GAP_MS

DEFAULT_BURST_FPS = 1000.0 / DEFAULT_MAX_GAP_MS
MIN_BURST_FPS = 0.2
MAX_BURST_FPS = 1000.0

# 各组保持数值递增；低速整数档也保留，避免只列旗舰机的最高速度。
CAMERA_FPS_PRESETS: tuple[tuple[str, tuple[float, ...]], ...] = (
    ("佳能 Canon", (1, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 9, 10, 12, 14, 15, 16, 20, 30, 40, 50, 60, 100, 195)),
    ("尼康 Nikon", (1, 2, 3, 4, 4.5, 5, 5.5, 6, 6.5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 30, 60, 120)),
    ("索尼 Sony", (2.5, 3, 5, 6, 8, 10, 11, 15, 20, 24, 30, 60, 90, 120)),
    ("富士 Fujifilm", (2, 3, 4, 5, 5.7, 6, 6.7, 7, 8, 8.9, 10, 11, 13, 14, 15, 20, 30, 40)),
    ("OM SYSTEM / 奥林巴斯", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 12.5, 15, 16.7, 18, 20, 25, 30, 50, 60, 100, 120)),
    ("松下 Panasonic LUMIX", (2, 3, 5, 6, 7, 9, 10, 12, 14, 20, 30, 40, 60, 75)),
    ("宾得 Pentax / 理光", (0.7, 1, 2.5, 3, 4.4, 4.5, 5, 6, 6.5, 7, 8.3, 11, 12)),
    ("徕卡 Leica", (2, 3, 4, 5, 6, 7, 9, 10, 11, 15, 20, 25, 30)),
    ("哈苏 Hasselblad", (1.7, 2.3, 2.7, 3, 3.3)),
    ("适马 Sigma", (2, 3, 3.8, 4.3, 5, 8, 10, 18)),
    ("三星 Samsung NX", (3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 30)),
    ("卡西欧 Casio EXILIM", (1, 3, 5, 7, 10, 15, 30, 40, 60)),
)

# 全部档位由厂商表派生，菜单再按速度范围分组，避免冗长菜单超出屏幕。
ALL_CAMERA_FPS = tuple(sorted({fps for _, speeds in CAMERA_FPS_PRESETS for fps in speeds}))


def fps_to_max_gap_ms(fps: float) -> float:
    """FPS 是分组速度下限；保留浮点间隔供现有分组算法比较。"""
    value = float(fps)
    if not math.isfinite(value) or not MIN_BURST_FPS <= value <= MAX_BURST_FPS:
        raise ValueError(f"连拍速度必须在 {MIN_BURST_FPS:g}–{MAX_BURST_FPS:g} FPS 之间")
    return 1000.0 / value
