from __future__ import annotations

import io
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from app_common.thumb_stream import get_raw_preview_jpeg
from birdstamp.constants import HEIF_EXTENSIONS, PIL_EXTENSIONS, RAW_EXTENSIONS
from birdstamp.subprocess_utils import decode_subprocess_output

_HEIF_REGISTERED = False


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


def _read_orientation_from_file(path: Path) -> int:
    try:
        import piexif
        data = piexif.load(str(path))
        for ifd in ("0th", "Exif"):
            if data.get(ifd) and 274 in data[ifd]:
                value = data[ifd][274]
                if isinstance(value, int) and 1 <= value <= 8:
                    return value
    except Exception:
        pass
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            value = int(exif.get(274, 1))
            if 1 <= value <= 8:
                return value
    except Exception:
        pass
    return 1


def _apply_orientation_to_image(image: Image.Image, orientation: int) -> Image.Image:
    try:
        orientation = int(orientation or 1)
    except Exception:
        orientation = 1
    method = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }.get(orientation)
    return image.transpose(method) if method is not None else image


def _decode_raw_embedded_preview(path: Path) -> Image.Image | None:
    preview_data = get_raw_preview_jpeg(str(path))
    if not preview_data:
        return None
    try:
        with Image.open(io.BytesIO(preview_data)) as image:
            image = _apply_orientation_to_image(image, _read_orientation_from_file(path))
            return image.convert("RGB").copy()
    except Exception:
        return None


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


def decode_preview_image_with_source(path: Path, decoder: str = "auto") -> tuple[Image.Image, str]:
    """
    Decode an image for interactive preview.

    RAW preview follows SuperViewer's source order: camera embedded JPEG first,
    then full RAW decode only when the embedded preview is unavailable.  The
    returned source label lets GUI/export code avoid using preview-only RAW
    images as final export input.
    """
    ext = path.suffix.lower()
    if ext in RAW_EXTENSIONS:
        embedded = _decode_raw_embedded_preview(path)
        if embedded is not None:
            return (embedded, "raw_embedded_preview")
        return (_decode_raw(path, decoder=decoder), "full")
    return (decode_image(path, decoder=decoder), "full")


def decode_preview_image(path: Path, decoder: str = "auto") -> Image.Image:
    image, _source = decode_preview_image_with_source(path, decoder=decoder)
    return image
