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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from app_common.image_formats import HEIF_IMAGE_EXTENSIONS, RAW_IMAGE_EXTENSIONS


# Which pixels are measured. RAW decode is the calibrated default; the camera's
# embedded JPEG and a denoised rendering are offered for comparison (trace viewer,
# CLI ``--source``). Their noise, sharpening and tone differ, so thresholds
# calibrated on RAW decodes are not re-tuned for them.
SOURCE_RAW = "raw"
SOURCE_JPEG = "jpeg"
SOURCE_DENOISED = "denoised"
SOURCE_LABELS = {SOURCE_RAW: "RAW 解码", SOURCE_JPEG: "相机 JPEG", SOURCE_DENOISED: "降噪成片"}


@dataclass
class AnalysisImage:
    rgb8: np.ndarray   # HxWx3 uint8, display-referred, for detection models
    gray: np.ndarray   # HxW float32 0..1, gamma-encoded luminance for blur measurement
    is_raw: bool
    # Normalised (left, top, right, bottom) of the camera JPEG frame inside these
    # pixels; RAW output keeps sensor margins that camera/focus coordinates exclude.
    camera_crop: Optional[Tuple[float, float, float, float]] = None
    source: str = SOURCE_RAW
    source_path: str = ""  # file actually decoded (embedded JPEG: the RAW itself)

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


def _from_rgb8(rgb8: np.ndarray, *, source: str, source_path: str, camera_crop=None) -> AnalysisImage:
    rgb8 = np.ascontiguousarray(rgb8[..., :3])
    return AnalysisImage(rgb8=rgb8, gray=rgb8[..., 1].astype(np.float32) / 255.0, is_raw=False,
                         camera_crop=camera_crop, source=source, source_path=source_path)


def load_embedded_jpeg(path: str) -> AnalysisImage:
    """The camera's embedded full-size JPEG of a RAW (camera frame, no sensor margins).

    Non-RAW files are their own JPEG. The camera has sharpened, noise-reduced and
    compressed these pixels.
    """
    if Path(path).suffix.lower() not in RAW_IMAGE_EXTENSIONS:
        return replace(_load_pillow(path), source=SOURCE_JPEG, source_path=path)
    import io

    from PIL import Image, ImageOps

    from app_common import thumb_stream

    data = thumb_stream.get_raw_preview_jpeg(path)
    if not data:
        raise ValueError("RAW 文件里没有内嵌 JPEG")
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)  # same orientation rule as the Viewer preview
        rgb8 = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return _from_rgb8(rgb8, source=SOURCE_JPEG, source_path=path)


class DenoisedImageMissing(LookupError):
    """No denoised rendering exists for this photo yet."""


def source_loader(source: str, *, denoised_lookup=None):
    """``image_loader`` for :meth:`BirdSharpnessAnalyzer.analyze`; ``None`` = RAW decode.

    ``denoised_lookup(path)`` returns an object with ``path`` and ``camera_crop``
    (``image_denoise.preview.DenoisedPreview``) or ``None``.
    """
    if source == SOURCE_RAW:
        return None
    if source == SOURCE_JPEG:
        return load_embedded_jpeg
    if source == SOURCE_DENOISED:
        if denoised_lookup is None:
            raise ValueError("降噪成片需要 denoised_lookup")

        def load(path: str) -> AnalysisImage:
            found = denoised_lookup(path)
            if found is None:
                raise DenoisedImageMissing(f"没有降噪成片：{path}")
            return load_image_file(found.path, source=SOURCE_DENOISED, camera_crop=found.camera_crop)

        return load
    raise ValueError(f"未知图像来源：{source}")


def load_image_file(file_path: str, *, source: str, camera_crop=None) -> AnalysisImage:
    """A rendered image (e.g. a denoised 16-bit TIFF) measured in place of the original."""
    if Path(file_path).suffix.lower() in (".tif", ".tiff"):
        import tifffile

        pixels = np.asarray(tifffile.imread(file_path))
        if pixels.ndim == 2:
            pixels = np.repeat(pixels[..., None], 3, axis=2)
        pixels = pixels[..., :3]
        if pixels.dtype == np.uint16:
            return AnalysisImage(rgb8=np.ascontiguousarray((pixels >> 8).astype(np.uint8)),
                                 gray=pixels[..., 1].astype(np.float32) / 65535.0, is_raw=False,
                                 camera_crop=camera_crop, source=source, source_path=file_path)
        return _from_rgb8(pixels.astype(np.uint8), source=source, source_path=file_path, camera_crop=camera_crop)
    return replace(_load_pillow(file_path), camera_crop=camera_crop, source=source, source_path=file_path)
