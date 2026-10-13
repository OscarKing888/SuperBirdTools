"""原尺寸 PNG 导出核心；RAW 仅使用内嵌 JPEG，不依赖 Qt 或当前预览。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import io
import logging
import os
from pathlib import Path
import tempfile
import unicodedata

from PIL import Image, ImageOps

from app_common.image_formats import (
    HEIF_IMAGE_EXTENSIONS, PHOTOSHOP_IMAGE_EXTENSIONS,
    RAW_IMAGE_EXTENSIONS, SUPPORTED_IMAGE_EXTENSIONS,
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PngExportResult:
    source: str
    output_path: str = ""
    width: int = 0
    height: int = 0
    cancelled: bool = False
    error: str = ""


class _Cancelled(Exception):
    pass


def _check_cancelled(cancelled):
    if cancelled():
        raise _Cancelled()


def is_exportable_image(path):
    return Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def _load_raw_jpeg(path, cancelled):
    from app_common import thumb_stream
    from app_common.exif_io.exiftool_runner import exiftool_read_request

    # 预览接口达到 1600 px 就提前返回；导出需要继续检查所有相机 JPEG，取最大可解码者。
    loaders = [lambda: thumb_stream._extract_raw_embedded_jpeg_inprocess(path)]
    loaders += [lambda tag=tag: thumb_stream._run_exiftool_binary_tag(path, tag)
                for tag in thumb_stream.RAW_PREVIEW_JPEG_TAGS]
    loaders.append(lambda: thumb_stream._get_piexif_thumbnail_bytes(path))
    best, best_area = None, 0
    try:
        with exiftool_read_request(cancelled):
            for load in loaders:
                _check_cancelled(cancelled)
                data = load()
                _check_cancelled(cancelled)
                if not data:
                    continue
                try:
                    with Image.open(io.BytesIO(data)) as image:
                        area = image.width * image.height
                        if image.format != "JPEG" or area <= best_area:
                            continue
                        image.load()
                        candidate = ImageOps.exif_transpose(image)
                except (OSError, ValueError):
                    continue
                if best is not None:
                    best.close()
                best, best_area = candidate, area
        if best is None:
            raise ValueError("RAW 中没有可解码的内嵌 JPG，无法导出 PNG")
        return best
    except BaseException:
        if best is not None:
            best.close()
        raise


def _load_image(path, cancelled):
    extension = Path(path).suffix.lower()
    if extension in RAW_IMAGE_EXTENSIONS:
        return _load_raw_jpeg(path, cancelled)
    if extension in HEIF_IMAGE_EXTENSIONS:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    try:
        with Image.open(path) as image:
            image.load()
            return ImageOps.exif_transpose(image)
    except (OSError, ValueError):
        if extension not in PHOTOSHOP_IMAGE_EXTENSIONS:
            raise
        from app_common.psd_composite import load_psd_composite_rgb
        result = load_psd_composite_rgb(path, None)
        if result is None:
            raise ValueError("无法读取 PSD 合成图")
        pixels, width, height = result
        return Image.frombytes("RGB", (width, height), pixels)


def _name_key(name):
    return unicodedata.normalize("NFC", name).casefold()


def _reserve_output(directory, stem):
    # 大小写及 Unicode 等价名称也算重名，使 macOS / Windows / CLI 行为一致。
    names = {_name_key(entry.name) for entry in directory.iterdir()}
    index = 1
    while True:
        name = f"{stem}.png" if index == 1 else f"{stem} ({index}).png"
        index += 1
        if _name_key(name) in names:
            continue
        target = directory / name
        try:
            with target.open("xb"):
                pass
            return target
        except FileExistsError:
            names.add(_name_key(name))


def export_image_png(source, output_directory=None, *, cancelled=lambda: False):
    """导出一张完整图片；重名编号，原子落盘，失败/取消不留下半张 PNG。"""
    source = os.path.abspath(os.fspath(source))
    target = temporary = None
    image = None
    committed = False
    cleanup_errors = []
    try:
        _check_cancelled(cancelled)
        if not is_exportable_image(source):
            raise ValueError("不支持导出此文件类型")
        if not Path(source).is_file():
            raise FileNotFoundError(f"找不到图片：{source}")
        image = _load_image(source, cancelled)
        _check_cancelled(cancelled)
        if image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16"):
            if image.mode == "I" or image.mode.startswith("I;16"):
                # 大端 TIFF 直接转换 I;16 会经过 8 位路径，先转整数模式保留采样值。
                with image.convert("I") as integer:
                    converted = integer.convert("I;16")
            else:
                converted = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.close()
            image = converted
        directory = Path(output_directory or Path(source).parent).expanduser().absolute()
        directory.mkdir(parents=True, exist_ok=True)
        target = _reserve_output(directory, Path(source).stem)
        fd, name = tempfile.mkstemp(prefix=".superviewer-png-", suffix=".tmp", dir=directory)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            # 保留色彩配置和透明通道；方向已应用到像素，不带回旧 EXIF/XMP。
            image.save(stream, format="PNG", exif=b"", icc_profile=image.info.get("icc_profile"),
                       **({"dpi": image.info["dpi"]} if image.info.get("dpi") else {}))
        _check_cancelled(cancelled)
        os.replace(temporary, target)
        committed = True
        result = PngExportResult(source, str(target), image.width, image.height)
    except _Cancelled:
        result = PngExportResult(source, cancelled=True)
    except Exception as exc:
        _log.warning("[PNG export] %s: %s", source, exc)
        result = PngExportResult(source, error=str(exc))
    finally:
        if image is not None:
            image.close()
        for path in (temporary, target if not committed else None):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    message = f"无法清理未完成文件 {path}：{exc}"
                    _log.warning("[PNG export] %s", message)
                    cleanup_errors.append(message)
    if cleanup_errors:
        result = replace(result, error="；".join(filter(None, [result.error, *cleanup_errors])))
    return result


def main(argv=None):
    import argparse
    import json
    import signal
    import threading

    parser = argparse.ArgumentParser(description="原尺寸导出 PNG；RAW 使用最大可用内嵌 JPG")
    parser.add_argument("images", nargs="+")
    parser.add_argument("--output", help="输出目录；默认各图片所在目录，同名自动编号")
    args = parser.parse_args(argv)
    cancelled = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    failed = False
    try:
        for path in args.images:
            result = export_image_png(path, args.output, cancelled=cancelled.is_set)
            print(json.dumps(asdict(result), ensure_ascii=False))
            failed |= bool(result.error)
            if cancelled.is_set():
                return 130
    finally:
        signal.signal(signal.SIGINT, previous)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
