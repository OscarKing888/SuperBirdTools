from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from birdstamp.constants import HEIF_EXTENSIONS, PIL_EXTENSIONS, RAW_EXTENSIONS
from birdstamp.subprocess_utils import decode_subprocess_output

_HEIF_REGISTERED = False
_DEFAULT_PREVIEW_MAX_LONG_EDGE = 2048


def _resize_fit_image(image: Image.Image, max_long_edge: int) -> Image.Image:
    if max_long_edge <= 0:
        return image
    width, height = image.size
    long_edge = max(width, height)
    if long_edge <= max_long_edge:
        return image
    scale = max_long_edge / float(long_edge)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _draft_target_size(width: int, height: int, max_long_edge: int) -> tuple[int, int]:
    long_edge = max(width, height)
    if long_edge <= max_long_edge:
        return width, height
    scale = max_long_edge / float(long_edge)
    return max(1, int(width * scale)), max(1, int(height * scale))


def _decode_standard_for_preview(path: Path, max_long_edge: int) -> Image.Image:
    with Image.open(path) as image:
        width, height = image.size
        draft_w, draft_h = _draft_target_size(width, height, max_long_edge)
        if (draft_w, draft_h) != (width, height):
            try:
                image.draft("RGB", (draft_w, draft_h))
            except Exception:
                pass
        image = ImageOps.exif_transpose(image)
        rgb = image.convert("RGB")
        return _resize_fit_image(rgb, max_long_edge).copy()


def _register_heif_opener() -> bool:
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return True
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return False
    register_heif_opener()
    _HEIF_REGISTERED = True
    return True


def _decode_standard(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB").copy()


def _decode_raw_rawpy(path: Path) -> Image.Image:
    try:
        import rawpy
    except ImportError as exc:
        raise RuntimeError("rawpy is not installed") from exc

    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=False,
            output_bps=8,
        )
    return Image.fromarray(rgb).convert("RGB")


def _decode_raw_darktable(path: Path) -> Image.Image:
    temp_fd, temp_name = tempfile.mkstemp(suffix=".tif")
    os.close(temp_fd)
    temp_output = Path(temp_name)
    try:
        try:
            result = subprocess.run(
                ["darktable-cli", str(path), str(temp_output)],
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("darktable-cli is not installed or not available in PATH") from exc
        if result.returncode != 0:
            stderr_text = decode_subprocess_output(result.stderr).strip()
            stdout_text = decode_subprocess_output(result.stdout).strip()
            raise RuntimeError(stderr_text or stdout_text or "darktable-cli failed")
        with Image.open(temp_output) as image:
            return ImageOps.exif_transpose(image).convert("RGB").copy()
    finally:
        if temp_output.exists():
            temp_output.unlink(missing_ok=True)


def _decode_raw(path: Path, decoder: str) -> Image.Image:
    decoder = decoder.lower()
    if decoder == "rawpy":
        return _decode_raw_rawpy(path)
    if decoder == "darktable":
        return _decode_raw_darktable(path)
    if decoder == "auto":
        errors: list[str] = []
        try:
            return _decode_raw_rawpy(path)
        except Exception as exc:
            errors.append(f"rawpy: {exc}")
        try:
            return _decode_raw_darktable(path)
        except Exception as exc:
            errors.append(f"darktable-cli: {exc}")
        details = "; ".join(errors)
        raise RuntimeError(
            "No RAW decoder is available. Install rawpy (`pip install rawpy`) or install darktable and ensure "
            "`darktable-cli` is in PATH. "
            f"Details: {details}"
        )
    raise ValueError(f"unknown RAW decoder: {decoder}")


def read_decoded_image_size(path: Path) -> tuple[int, int]:
    """Return display-oriented source dimensions (EXIF orientation applied) without decoding pixels."""
    ext = path.suffix.lower()
    if ext in PIL_EXTENSIONS or ext in HEIF_EXTENSIONS:
        if ext in HEIF_EXTENSIONS and not _register_heif_opener():
            raise RuntimeError("pillow-heif is required to decode HEIF/HEIC/HIF")
        with Image.open(path) as image:
            return ImageOps.exif_transpose(image).size
    if ext in RAW_EXTENSIONS:
        with Image.open(path) as image:
            return image.size
    raise RuntimeError(f"unsupported image format: {path.suffix}")


def decode_image(path: Path, decoder: str = "auto") -> Image.Image:
    ext = path.suffix.lower()
    if ext in PIL_EXTENSIONS:
        return _decode_standard(path)
    if ext in HEIF_EXTENSIONS:
        if not _register_heif_opener():
            raise RuntimeError("pillow-heif is required to decode HEIF/HEIC/HIF")
        return _decode_standard(path)
    if ext in RAW_EXTENSIONS:
        return _decode_raw(path, decoder=decoder)
    raise RuntimeError(f"unsupported image format: {path.suffix}")


def decode_image_for_preview(
    path: Path,
    *,
    max_long_edge: int = _DEFAULT_PREVIEW_MAX_LONG_EDGE,
    decoder: str = "auto",
) -> Image.Image:
    """Preview-only decode: draft/downscale large raster images; export still uses decode_image()."""
    limit = max(1, int(max_long_edge))
    ext = path.suffix.lower()
    if ext in PIL_EXTENSIONS:
        return _decode_standard_for_preview(path, limit)
    if ext in HEIF_EXTENSIONS:
        if not _register_heif_opener():
            raise RuntimeError("pillow-heif is required to decode HEIF/HEIC/HIF")
        return _decode_standard_for_preview(path, limit)
    if ext in RAW_EXTENSIONS:
        return _resize_fit_image(_decode_raw(path, decoder=decoder), limit)
    raise RuntimeError(f"unsupported image format: {path.suffix}")
