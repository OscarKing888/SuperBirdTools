"""预览来源状态与后台降噪成片读取；不改变编辑、元数据和导出的源路径。"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from app_common.image_formats import RAW_EXTENSIONS
from app_common.raw_preview_geometry import RAW_FOCUS_CROP_KEY, rawpy_camera_crop_box
from app_common.superviewer_user_options import get_runtime_user_options
from image_denoise.preview import denoise_preview_history_path, find_denoised_preview
from image_denoise.types import DenoiseCancelled, DenoiseOptions, check_cancelled

from .image_decoder import decode_image_for_preview

PREVIEW_SOURCE_MODE_KEY = "birdstamp_preview_source_mode"
PREVIEW_SOURCE_PATH_KEY = "birdstamp_preview_source_path"
PREVIEW_SOURCE_MESSAGE_KEY = "birdstamp_preview_source_message"
_log = logging.getLogger(__name__)


def normalize_preview_source_mode(value=None, *, show_raw: bool = False) -> str:
    """兼容旧 show_raw 参数；显式来源模式优先。"""
    if value is None:
        return "raw" if show_raw else "default"
    return value if value in ("default", "raw", "denoised") else "default"


def set_preview_source_info(image: Image.Image, path: Path, mode: str, message: str = "") -> None:
    """来源与提示跟随当前像素，避免 A/B 或迟到结果读取另一视口状态。"""
    image.info[PREVIEW_SOURCE_MODE_KEY] = mode
    image.info[PREVIEW_SOURCE_PATH_KEY] = str(path)
    image.info[PREVIEW_SOURCE_MESSAGE_KEY] = message


def load_denoised_preview(path: Path, *, max_long_edge: int = 0, cancelled=None):
    """仅由 WorkerAction 调用；返回已核验图像或回退提示，不运行降噪推理。"""
    image = None
    note = "未找到降噪成片，当前显示原图；请先在 SuperViewer 中执行降噪"
    try:
        check_cancelled(cancelled)
        settings = get_runtime_user_options()
        options = DenoiseOptions(**{
            key: settings[f"denoise_{key}"] for key in
            ("output_mode", "subdir", "output_directory", "format", "strength", "device", "workers")
        })
        result = find_denoised_preview(
            path, options, cancelled=cancelled, history_path=denoise_preview_history_path(),
        )
        check_cancelled(cancelled)
        if result is None:
            return None, note
        note = "降噪成片读取失败，当前显示原图"
        # 保留成片本身的原生尺寸，不再以原图/相机内嵌 JPEG 的尺寸覆盖它。
        image = decode_image_for_preview(
            Path(result.path), max_long_edge=max_long_edge or 2**31 - 1, decoder="auto",
        )
        check_cancelled(cancelled)
        crop = result.camera_crop
        if result.legacy and path.suffix.lower() in RAW_EXTENSIONS:
            # 老成片缺少几何记录时只读 LibRaw 头，不显影第二张整图；文件对象兼容中文 Windows 路径。
            import rawpy
            with path.open("rb") as stream, rawpy.imread(stream) as raw:
                crop = rawpy_camera_crop_box(raw.sizes)
        if crop is not None:
            image.info[RAW_FOCUS_CROP_KEY] = crop
        else:
            image.info.pop(RAW_FOCUS_CROP_KEY, None)
        check_cancelled(cancelled)
        set_preview_source_info(image, Path(result.path), "denoised")
        accepted, image = image, None
        return accepted, ""
    except DenoiseCancelled:
        raise
    except Exception:
        _log.exception("[preview.denoised] source=%s lookup/decode failed", path)
        return None, note
    finally:
        if image is not None:
            image.close()
