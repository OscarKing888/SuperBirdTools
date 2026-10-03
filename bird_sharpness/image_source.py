"""Full-resolution image decoding for sharpness analysis.

Blur radii are measured in pixels of the camera's full output, so RAW files are
demosaiced with LibRaw instead of using the embedded preview: that is a camera
JPEG whose in-camera sharpening, noise reduction and 8-bit compression change
edge widths, and on some files it is also small (Sony's ARW PreviewImage is
1616 px wide, where a 1 px softness at 100% becomes invisible).
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from app_common.image_formats import HEIF_IMAGE_EXTENSIONS, RAW_IMAGE_EXTENSIONS


@dataclass
class AnalysisImage:
    rgb8: np.ndarray   # HxWx3 uint8, display-referred, for detection models
    gray: np.ndarray   # HxW float32 0..1, gamma-encoded luminance for blur measurement
    is_raw: bool
    # Normalised (left, top, right, bottom) of the camera JPEG frame inside these
    # pixels; RAW output keeps sensor margins that camera/focus coordinates exclude.
    camera_crop: Optional[Tuple[float, float, float, float]] = None

    @property
    def long_edge(self) -> int:
        return int(max(self.gray.shape[:2]))


def _rawpy_source(path: str):
    # Windows LibRaw narrow-char paths cannot open non-ASCII names; hand it a stream.
    if os.name == "nt" and not str(path).isascii():
        return open(path, "rb")
    return nullcontext(path)


def _load_raw(path: str) -> AnalysisImage:
    import rawpy

    from app_common.raw_preview_geometry import rawpy_camera_crop_box

    with _rawpy_source(path) as source, rawpy.imread(source) as raw:
        camera_crop = rawpy_camera_crop_box(getattr(raw, "sizes", None))
        rgb16 = raw.postprocess(
            use_camera_wb=True,
            output_bps=16,
            gamma=(2.222, 4.5),
            no_auto_bright=True,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
        )
    # Green carries half the Bayer samples and most luminance; it avoids chroma noise.
    gray = rgb16[..., 1].astype(np.float32) / 65535.0
    rgb8 = (rgb16 >> 8).astype(np.uint8)
    return AnalysisImage(rgb8=rgb8, gray=gray, is_raw=True,
                         camera_crop=tuple(float(v) for v in camera_crop) if camera_crop else None)


def _load_pillow(path: str) -> AnalysisImage:
    from PIL import Image, ImageOps

    if Path(path).suffix.lower() in HEIF_IMAGE_EXTENSIONS:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except Exception:
            pass
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("I;16", "I;16B", "I;16L", "I"):
            arr16 = np.asarray(img, dtype=np.float32)
            gray = arr16 / max(float(arr16.max()), 1.0)
            rgb8 = np.repeat((gray * 255).astype(np.uint8)[..., None], 3, axis=2)
            return AnalysisImage(rgb8=rgb8, gray=gray.astype(np.float32), is_raw=False)
        rgb8 = np.asarray(img.convert("RGB"), dtype=np.uint8)
    gray = rgb8[..., 1].astype(np.float32) / 255.0
    return AnalysisImage(rgb8=np.ascontiguousarray(rgb8), gray=gray, is_raw=False)


def load_analysis_image(path: str) -> AnalysisImage:
    if Path(path).suffix.lower() in RAW_IMAGE_EXTENSIONS:
        return _load_raw(path)
    return _load_pillow(path)
