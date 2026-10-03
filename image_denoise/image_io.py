"""完整照片解码和 sRGB 工作空间；不接入任何预览/缩略图缓存。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import zlib
import numpy as np
from PIL import Image

from app_common.image_formats import (
    RAW_IMAGE_EXTENSIONS, HEIF_IMAGE_EXTENSIONS, PHOTOSHOP_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
)
from app_common.raw_preview_geometry import rawpy_camera_crop_box
from .types import UnsupportedImage, check_cancelled


@dataclass
class DecodedImage:
    rgb: np.ndarray
    alpha: np.ndarray | None = None
    camera_crop: tuple | None = None


def srgb_profile() -> bytes:
    import imagecodecs
    return imagecodecs.cms_profile("srgb")


def linear_to_srgb(value: np.ndarray) -> np.ndarray:
    result = np.array(value, dtype=np.float32, copy=True)
    np.clip(result, 0, 1, out=result)
    low = result <= .0031308
    high = ~low
    # 大 RAW 不同时构造两个全尺寸分支，避免 np.where 带来的内存峰值。
    np.power(result, 1 / 2.4, out=result, where=high)
    np.multiply(result, 1.055, out=result, where=high)
    np.subtract(result, .055, out=result, where=high)
    np.multiply(result, 12.92, out=result, where=low)
    return result


def orient_pixels(array: np.ndarray, orientation: int) -> np.ndarray:
    if orientation == 2:
        return np.flip(array, 1)
    if orientation == 3:
        return np.flip(array, (0, 1))
    if orientation == 4:
        return np.flip(array, 0)
    if orientation == 5:
        return np.swapaxes(array, 0, 1)
    if orientation == 6:
        return np.rot90(array, -1)
    if orientation == 7:
        return np.flip(np.swapaxes(array, 0, 1), (0, 1))
    if orientation == 8:
        return np.rot90(array, 1)
    return array


def _check_static(image) -> None:
    if getattr(image, "n_frames", 1) != 1:
        raise UnsupportedImage("首版不处理动画或多页图片")


def _png_color_info(path: Path, *, cancelled=None) -> dict:
    """读取 IDAT 前的 cICP；Pillow 目前不暴露该 HDR/色彩信号。

    PNG 第三版以 cICP 的 16/18 表示 PQ/HLG。只读取四字节色彩字段，
    其余块直接跳过，不为尺寸探测解码图像或分配整个文件。
    """
    result = {}
    with path.open("rb") as stream:
        stream.seek(0, 2)
        file_size = stream.tell()
        stream.seek(0)
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("PNG 文件头无效")
        while True:
            check_cancelled(cancelled)
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("PNG 元数据块不完整")
            length, kind = struct.unpack(">I4s", header)
            if length > 0x7FFFFFFF or stream.tell() + length + 4 > file_size:
                raise ValueError("PNG 元数据块长度无效")
            if kind in (b"IDAT", b"IEND"):
                return result
            if kind != b"cICP":
                stream.seek(length + 4, 1)
                continue
            if length != 4 or result:
                raise ValueError("PNG cICP 色彩字段无效或重复")
            values = stream.read(4)
            crc = struct.unpack(">I", stream.read(4))[0]
            if zlib.crc32(kind + values) != crc:
                raise ValueError("PNG cICP 校验失败")
            primaries, transfer, matrix, full_range = values
            if transfer in (16, 18):
                raise UnsupportedImage("首版不处理 PQ/HLG HDR PNG")
            if (matrix != 0 or full_range != 1 or primaries not in (1, 2, 9, 12)
                    or transfer not in (1, 2, 6, 8, 13, 14, 15)):
                raise UnsupportedImage("不支持此 PNG cICP 色彩编码")
            result = {"color_primaries": primaries, "transfer_characteristics": transfer}


def probe_image(path: str | Path) -> tuple[int, int]:
    """后台读取尺寸供提交前预算使用，不进行 RAW 显影或网络推理。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise UnsupportedImage(f"首版不处理此格式：{suffix or '无扩展名'}")
    if suffix in PHOTOSHOP_IMAGE_EXTENSIONS:
        raise UnsupportedImage("首版不处理 PSD")
    if suffix == ".png":
        _png_color_info(path)
    if suffix in RAW_IMAGE_EXTENSIONS:
        import rawpy
        # LibRaw 的文件流接口兼容 Windows 非 ASCII 路径。
        with path.open("rb") as stream, rawpy.imread(stream) as raw:
            return int(raw.sizes.width), int(raw.sizes.height)
    if suffix in HEIF_IMAGE_EXTENSIONS:
        import pillow_heif
        image = pillow_heif.open_heif(path, convert_hdr_to_8bit=False)
        if len(image) != 1:
            raise UnsupportedImage("首版不处理多图 HEIF")
        _check_heif_sdr(image.info)
        return image.size
    if suffix in (".tif", ".tiff"):
        import tifffile
        with tifffile.TiffFile(path) as image:
            _check_tiff(image)
            return image.pages[0].imagewidth, image.pages[0].imagelength
    with Image.open(path) as image:
        _check_static(image)
        return image.size


def _check_tiff(image) -> None:
    if len(image.pages) != 1 or image.pages[0].subifds:
        raise UnsupportedImage("首版不处理多页或金字塔 TIFF")
    page = image.pages[0]
    if (page.dtype.kind != "u" or page.dtype.itemsize not in (1, 2)
            or page.bitspersample not in (8, 16)):
        raise UnsupportedImage("仅支持 8/16 位整数 SDR TIFF")
    if int(page.photometric) not in (0, 1, 2):
        raise UnsupportedImage("仅支持灰度或 RGB TIFF")


def _check_heif_sdr(info: dict) -> None:
    nclx = info.get("nclx_profile") or {}
    if nclx.get("transfer_characteristics") in (16, 18):
        raise UnsupportedImage("首版不处理 PQ/HLG HDR HEIF")


def _array_to_srgb(array: np.ndarray, icc: bytes | None, *, associated_alpha=False,
                   invert_gray=False) -> DecodedImage:
    import imagecodecs
    if array.dtype.kind != "u" or array.dtype.itemsize not in (1, 2):
        raise UnsupportedImage("仅支持 8/16 位整数 SDR 图像")
    if array.ndim == 2:
        array = array[..., None]
    if array.ndim != 3 or array.shape[-1] not in (1, 2, 3, 4):
        raise UnsupportedImage("不支持的照片通道布局")
    maximum = np.iinfo(array.dtype).max
    value = array.astype(np.float32) / maximum
    alpha = None
    if value.shape[-1] in (2, 4):
        alpha = value[..., -1].copy()
        value = value[..., :-1]
    if associated_alpha and alpha is not None:
        value = np.divide(value, alpha[..., None], out=np.zeros_like(value),
                          where=alpha[..., None] > 0)
        np.clip(value, 0, 1, out=value)
    if invert_gray:
        # MINISWHITE 在解预乘之后反相亮度，alpha 本身不受光度解释影响。
        np.subtract(1, value, out=value)
    if icc:
        value = imagecodecs.cms_transform(
            np.ascontiguousarray(value), icc, srgb_profile(),
            colorspace="GRAY" if value.shape[-1] == 1 else "RGB",
            outcolorspace="RGB", outdtype=np.float32,
        )
    elif value.shape[-1] == 1:
        value = np.repeat(value, 3, axis=2)
    np.clip(value, 0, 1, out=value)
    return DecodedImage(np.ascontiguousarray(value, dtype=np.float32), alpha)


def _nclx_to_srgb(rgb: np.ndarray, nclx: dict) -> np.ndarray:
    """无 ICC 的 SDR HEIF/PNG 按 NCLX/cICP 的传递函数和色域转为 sRGB。"""
    transfer = nclx.get("transfer_characteristics", 2)
    primaries = nclx.get("color_primaries", 2)
    if transfer in (2, 13) and primaries in (1, 2):
        return rgb
    if transfer in (1, 6, 14, 15):
        linear = np.where(rgb < .081, rgb / 4.5, ((rgb + .099) / 1.099) ** (1 / .45))
    elif transfer == 8:
        linear = rgb
    elif transfer in (2, 13):
        linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    else:
        raise UnsupportedImage(f"不支持的 SDR 传递函数：{transfer}")
    matrices = {
        9: ((1.660491, -.587641, -.072850), (-.124550, 1.132900, -.008349), (-.018151, -.100579, 1.118730)),
        12: ((1.224940, -.224940, 0), (-.042057, 1.042057, 0), (-.019638, -.078636, 1.098274)),
    }
    if primaries in matrices:
        linear = linear @ np.asarray(matrices[primaries], dtype=np.float32).T
    elif primaries not in (1, 2):
        raise UnsupportedImage(f"不支持的色域：{primaries}")
    return linear_to_srgb(linear)


def decode_image(path: str | Path, *, cancelled=None) -> DecodedImage:
    path = Path(path)
    check_cancelled(cancelled)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise UnsupportedImage(f"首版不处理此格式：{suffix or '无扩展名'}")
    if suffix in PHOTOSHOP_IMAGE_EXTENSIONS:
        raise UnsupportedImage("首版不处理 PSD")
    png_color = _png_color_info(path, cancelled=cancelled) if suffix == ".png" else {}
    if suffix in RAW_IMAGE_EXTENSIONS:
        import rawpy
        with path.open("rb") as stream, rawpy.imread(stream) as raw:
            check_cancelled(cancelled)
            array = raw.postprocess(use_camera_wb=True, use_auto_wb=False,
                                    output_color=rawpy.ColorSpace.sRGB, output_bps=16,
                                    gamma=(1, 1), no_auto_bright=True, half_size=False)
            camera_crop = rawpy_camera_crop_box(getattr(raw, "sizes", None))
        check_cancelled(cancelled)
        return DecodedImage(linear_to_srgb(array.astype(np.float32) / 65535), camera_crop=camera_crop)
    if suffix in HEIF_IMAGE_EXTENSIONS:
        import pillow_heif
        image = pillow_heif.open_heif(path, convert_hdr_to_8bit=False, hdr_to_16bit=True)
        if len(image) != 1:
            raise UnsupportedImage("首版不处理多图 HEIF")
        _check_heif_sdr(image.info)
        # libheif 已应用容器旋转；不能再套用原始 EXIF 方向。
        array = np.asarray(image)[:, :image.size[0]]
        result = _array_to_srgb(array, image.info.get("icc_profile"),
                                associated_alpha=image.premultiplied_alpha)
        if not image.info.get("icc_profile"):
            result.rgb = _nclx_to_srgb(result.rgb, image.info.get("nclx_profile") or {})
        check_cancelled(cancelled)
        return result
    if suffix in (".tiff", ".tif"):
        import tifffile
        with tifffile.TiffFile(path) as image:
            _check_tiff(image)
            page = image.pages[0]
            array = page.asarray(maxworkers=1)
            if int(page.planarconfig) == 2:
                array = np.moveaxis(array, 0, -1)
            orientation = page.tags.get(274)
            array = orient_pixels(array, int(orientation.value) if orientation else 1)
            icc = page.tags.get(34675)
            extras = tuple(int(x) for x in page.extrasamples)
            if extras and extras != (1,) and extras != (2,):
                raise UnsupportedImage("TIFF 的附加通道不是明确的透明通道")
            result = _array_to_srgb(array, icc.value if icc else None,
                                    associated_alpha=extras == (1,),
                                    invert_gray=int(page.photometric) == 0)
        check_cancelled(cancelled)
        return result
    with Image.open(path) as image:
        _check_static(image)
        icc = image.info.get("icc_profile")
        orientation = int(image.getexif().get(274, 1))
        if image.mode == "CMYK":
            if not icc:
                raise UnsupportedImage("CMYK 照片需要有效 ICC 色彩描述文件")
            from PIL import ImageCms
            from io import BytesIO
            image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(BytesIO(icc)),
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
            icc = None
        if suffix == ".png" and image.mode != "P":
            import imagecodecs
            # Pillow 对 16 位彩色 PNG 会返回 8 位 RGB，因此使用原生数组解码。
            array = imagecodecs.png_decode(path.read_bytes())
            if "transparency" in image.info and array.ndim == 3 and array.shape[-1] == 3:
                transparent = np.asarray(image.info["transparency"])
                alpha = np.where(np.all(array == transparent, axis=-1), 0, np.iinfo(array.dtype).max)
                array = np.concatenate((array, alpha[..., None].astype(array.dtype)), axis=-1)
            elif "transparency" in image.info and array.ndim == 2:
                alpha = np.where(array == image.info["transparency"], 0, np.iinfo(array.dtype).max)
                array = np.stack((array, alpha.astype(array.dtype)), axis=-1)
        else:
            if image.mode == "P":
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            elif image.mode not in ("RGB", "RGBA", "L", "LA", "I;16", "I;16B"):
                image = image.convert("RGB")
            array = np.asarray(image)
        result = _array_to_srgb(orient_pixels(array, orientation), icc)
        if png_color and not icc:
            result.rgb = _nclx_to_srgb(result.rgb, png_color)
    check_cancelled(cancelled)
    return result
