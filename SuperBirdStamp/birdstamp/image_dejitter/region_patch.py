from __future__ import annotations

import numpy as np
from PIL import Image


def extract_region_patch(
    image: Image.Image,
    box: tuple[float, float, float, float],
    patch_size: int,
) -> "np.ndarray | None":
    """从源图按归一化 box 裁取灰度 patch，缩放为 patch_size 方阵用于特征对齐。"""
    width = int(image.width)
    height = int(image.height)
    if width < 2 or height < 2:
        return None
    left = max(0, min(width - 1, int(round(float(box[0]) * width))))
    top = max(0, min(height - 1, int(round(float(box[1]) * height))))
    right = max(left + 1, min(width, int(round(float(box[2]) * width))))
    bottom = max(top + 1, min(height, int(round(float(box[3]) * height))))
    try:
        patch = image.crop((left, top, right, bottom)).convert("L")
        if patch.width < 2 or patch.height < 2:
            return None
        size = max(2, int(patch_size))
        patch = patch.resize((size, size))
        return np.asarray(patch, dtype=np.float64)
    except Exception:
        return None

