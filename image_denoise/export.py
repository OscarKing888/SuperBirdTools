"""降噪成片与 XMP 的安全输出；所有元数据操作只指向临时成片。"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import shutil
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from app_common.exif_io import get_exiftool_executable_path, find_same_stem_xmp_sidecar
from app_common.exif_io.exiftool_runner import run_exiftool_utf8_args
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.export_xmp import read_export_xmp
from app_common.exif_io.xmp_sidecar import _photo_descriptions
from .image_io import srgb_profile
from .types import DenoiseOptions, check_cancelled
from app_common import image_export as _shared
from .preview import NAMESPACE, source_provenance, register_denoised_output

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
TIFF = "http://ns.adobe.com/tiff/1.0/"
EXIF = "http://ns.adobe.com/exif/1.0/"
XMP = "http://ns.adobe.com/xap/1.0/"


_exiftool = _shared._exiftool


def _prepare_sidecar(source: Path, staged: Path, width: int, height: int, *, camera_crop=None,
                     source_stat=None) -> None:
    existing = find_same_stem_xmp_sidecar(str(source))
    tree = ET.ElementTree(ET.fromstring(read_export_xmp(source)))
    root = tree.getroot()
    descriptions = _photo_descriptions(root, str(source), sidecar_path=existing)
    if not descriptions:
        raise ValueError("XMP 中未找到属于源照片的描述，未发布当前照片")
    for desc in descriptions:
        desc.set(f"{{{RDF}}}about", "")
    # 使用共享 XMP 属性替换语义，兼容属性形式和拆分的同照片 RDF 描述。
    for uri, key, value in (
        (TIFF, "Orientation", 1), (TIFF, "ImageWidth", width), (TIFF, "ImageLength", height),
        (EXIF, "PixelXDimension", width), (EXIF, "PixelYDimension", height),
        (EXIF, "ColorSpace", 1), (XMP, "CreatorTool", "SuperViewer RGB denoise / NAFNet-SIDD-width64"),
    ):
        PhotoMetaDataXMP._replace_text_node(descriptions, f"{{{uri}}}{key}", str(value))
    for key in (f"{{{XMP}}}Thumbnails", f"{{{TIFF}}}BitsPerSample", f"{{{TIFF}}}Compression"):
        PhotoMetaDataXMP._remove_property(descriptions, key)
    # 来源记录仅写到成片侧车，原图及原侧车保持不变。
    for key, value in source_provenance(source, camera_crop, source_stat=source_stat).items():
        PhotoMetaDataXMP._replace_text_node(descriptions, f"{{{NAMESPACE}}}{key}", value)
    tree.write(staged, encoding="utf-8", xml_declaration=True)


copy_output_metadata = _shared.copy_output_metadata


_name_key = _shared._name_key


_check_destination = _shared._check_destination


_publish_new = _shared._publish_new


def export_image(source: Path, destination: Path, rgb: np.ndarray,
                 alpha: np.ndarray | None, options: DenoiseOptions, *, cancelled=None,
                 camera_crop=None, source_stat=None) -> None:
    source, destination = Path(source).resolve(), Path(destination).absolute()
    source_stat = source_stat or source.stat()
    def ensure_source_unchanged():
        current = source.stat()
        if ((current.st_size, current.st_mtime_ns, current.st_ino) !=
                (source_stat.st_size, source_stat.st_mtime_ns, source_stat.st_ino)):
            raise RuntimeError("降噪期间原图已变化，未发布过期成片，请重试")
    _shared.export_pixels(source, destination, rgb, alpha, options.format,
        prepare_sidecar=lambda staged, w, h: _prepare_sidecar(
            source, staged, w, h, camera_crop=camera_crop, source_stat=source_stat),
        cancelled=cancelled, validate_source=ensure_source_unchanged,
        metadata_writer=copy_output_metadata, publisher=_publish_new)
    register_denoised_output(source, destination)
