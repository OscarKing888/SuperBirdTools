from __future__ import annotations

import io
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from birdstamp.constants import HEIF_EXTENSIONS, PIL_EXTENSIONS, RAW_EXTENSIONS
from birdstamp.subprocess_utils import decode_subprocess_output

_HEIF_REGISTERED = False
_DEFAULT_PREVIEW_MAX_LONG_EDGE = 2048


def _pillow_oriented_size(image: Image.Image) -> tuple[int, int]:
    width, height = image.size
    try:
        orientation = int(image.getexif().get(274, 1))
    except Exception:
        orientation = 1
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    return max(1, int(width)), max(1, int(height))


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


def _decode_raw_rawpy_for_preview(path: Path, max_long_edge: int) -> Image.Image:
    try:
        import rawpy
    except ImportError as exc:
        raise RuntimeError("rawpy is not installed") from exc

    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=False,
            output_bps=8,
            half_size=True,
        )
    image = Image.fromarray(rgb).convert("RGB")
    resized = _resize_fit_image(image, max_long_edge)
    if resized is not image:
        image.close()
    return resized


def _decode_embedded_raw_preview(path: Path, max_long_edge: int) -> Image.Image | None:
    try:
        from app_common.thumb_stream import get_raw_preview_jpeg
    except Exception:
        return None
    try:
        preview_bytes = get_raw_preview_jpeg(str(path))
    except Exception:
        return None
    if not preview_bytes:
        return None
    try:
        with Image.open(io.BytesIO(preview_bytes)) as source:
            transposed = ImageOps.exif_transpose(source)
            rgb = transposed.convert("RGB")
        resized = _resize_fit_image(rgb, max_long_edge)
        if resized is not rgb:
            rgb.close()
        return resized
    except Exception:
        return None


def _positive_metadata_int(metadata: dict, *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        try:
            parsed = int(float(str(value).strip()))
        except Exception:
            continue
        if parsed > 0:
            return parsed
    return 0


def _read_raw_exif_size(path: Path) -> tuple[int, int] | None:
    """Read RAW dimensions through ExifTool metadata without decoding pixels."""
    try:
        from app_common.exif_io import read_batch_metadata

        normalized_path = os.path.normpath(str(path))
        rows = read_batch_metadata(
            [normalized_path],
            tags=[
                "-ExifImageWidth",
                "-ExifImageHeight",
                "-RawImageWidth",
                "-RawImageHeight",
                "-ImageWidth",
                "-ImageHeight",
                "-Orientation",
            ],
            use_cache=False,
        )
        metadata = rows.get(normalized_path)
    except Exception:
        return None
    if not isinstance(metadata, dict):
        return None
    width = _positive_metadata_int(
        metadata,
        "ExifImageWidth",
        "EXIF:ExifImageWidth",
        "RawImageWidth",
        "ImageWidth",
        "File:ImageWidth",
    )
    height = _positive_metadata_int(
        metadata,
        "ExifImageHeight",
        "EXIF:ExifImageHeight",
        "RawImageHeight",
        "ImageHeight",
        "File:ImageHeight",
    )
    if width <= 0 or height <= 0:
        return None
    orientation = _positive_metadata_int(
        metadata,
        "Orientation",
        "IFD0:Orientation",
        "EXIF:Orientation",
    )
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    return width, height


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
            return _pillow_oriented_size(image)
    if ext in RAW_EXTENSIONS:
        exif_size = _read_raw_exif_size(path)
        if exif_size is not None:
            return exif_size
        try:
            import rawpy
        except ImportError:
            rawpy = None
        if rawpy is not None:
            try:
                with rawpy.imread(str(path)) as raw:
                    sizes = raw.sizes
                    width = max(1, int(getattr(sizes, "width", 0) or getattr(sizes, "iwidth", 0)))
                    height = max(1, int(getattr(sizes, "height", 0) or getattr(sizes, "iheight", 0)))
                    if int(getattr(sizes, "flip", 0) or 0) in {5, 6}:
                        width, height = height, width
                    return width, height
            except Exception:
                pass
        raise RuntimeError(
            "unable to determine RAW dimensions from EXIF or rawpy headers "
            f"without a full pixel decode: {path}"
        )
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
        embedded = _decode_embedded_raw_preview(path, limit)
        if embedded is not None:
            return embedded
        try:
            return _decode_raw_rawpy_for_preview(path, limit)
        except Exception as exc:
            raise RuntimeError(
                "RAW preview decode failed: no usable embedded preview and "
                "half-size rawpy decode is unavailable; full RAW demosaic is "
                f"reserved for export ({path.name}): {exc}"
            ) from exc
    raise RuntimeError(f"unsupported image format: {path.suffix}")
