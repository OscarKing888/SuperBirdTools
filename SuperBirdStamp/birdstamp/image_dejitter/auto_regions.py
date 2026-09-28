"""Suggest dispersed, textured reference regions in source-image coordinates."""

from __future__ import annotations

import numpy as np
from PIL import Image


def _overlaps_with_margin(box, other, margin=0.025):
    return (box[0] < other[2] + margin and box[2] > other[0] - margin
            and box[1] < other[3] + margin and box[3] > other[1] - margin)


def suggest_reference_regions(
    image: Image.Image,
    existing=(),
    *,
    max_new: int = 4,
) -> tuple[tuple[float, float, float, float], ...]:
    """Find up to ``max_new`` non-overlapping patches with texture in both axes.

    The bounded thumbnail keeps this suitable for a button click even with large
    originals. Suggestions are only evidence candidates; the user can adjust
    them before analysing the sequence.
    """
    if image.width < 64 or image.height < 64 or max_new <= 0:
        return ()
    with image.copy() as small:
        small.thumbnail((768, 768), Image.Resampling.BILINEAR)
        with small.convert('L') as gray:
            pixels = np.asarray(gray, dtype=np.float32)
    height, width = pixels.shape
    gradient_x = np.abs(np.diff(pixels, axis=1))
    gradient_y = np.abs(np.diff(pixels, axis=0))
    box_width, box_height = .16, .16
    candidates = []
    for top in np.linspace(.03, .97 - box_height, 11):
        for left in np.linspace(.03, .97 - box_width, 11):
            box = (float(left), float(top), float(left + box_width), float(top + box_height))
            if any(_overlaps_with_margin(box, occupied) for occupied in existing):
                continue
            x0, x1 = round(box[0] * width), round(box[2] * width)
            y0, y1 = round(box[1] * height), round(box[3] * height)
            patch = pixels[y0:y1, x0:x1]
            if min(patch.shape) < 12:
                continue
            horizontal = float(gradient_x[y0:y1, x0:x1-1].mean())
            vertical = float(gradient_y[y0:y1-1, x0:x1].mean())
            if min(horizontal, vertical) < 3.5 or float(patch.std()) < 10:
                continue
            if float(np.mean((patch < 5) | (patch > 250))) > .75:
                continue
            # Two-axis structure is easier to locate than a one-direction stripe.
            score = min(horizontal, vertical) + .1 * float(patch.std())
            candidates.append((score, box))
    chosen = []
    while candidates and len(chosen) < max_new:
        available = [(score, box) for score, box in candidates
                     if not any(_overlaps_with_margin(box, occupied) for occupied in chosen)]
        if not available:
            break
        def distributed_score(candidate):
            score, box = candidate
            if not chosen:
                return score
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            distance = min(np.hypot(cx - (other[0] + other[2]) / 2,
                                    cy - (other[1] + other[3]) / 2) for other in chosen)
            return score * (.5 + .5 * min(1.0, distance / .45))
        _, box = max(available, key=distributed_score)
        chosen.append(box)
        candidates = [candidate for candidate in available if candidate[1] != box]
    return tuple(chosen)
