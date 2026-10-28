"""所有静态导出和中间图片共用的原文件元数据保存入口。"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile

from PIL import Image
from app_common.exif_io import get_exiftool_executable_path
from app_common.exif_io.exiftool_runner import run_exiftool
from app_common.exif_io.export_xmp import read_export_xmp, export_xmp_geometry
from birdstamp.export_image_pair import check_export_destination, publish_image_pair

_LOG = logging.getLogger(__name__)


def _run(executable: str, args: list[str], *, allow_empty_source: bool = False) -> None:
    result = run_exiftool(executable, ['-charset', 'filename=UTF8', '-overwrite_original', *args], timeout=60)
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    # The bundled Windows Perl may emit a startup locale warning when LANG is
    # inherited from a Unix-like shell. It is unrelated to ExifTool's result.
    if lines and lines[0] == 'perl: warning: Setting locale failed.':
        for index, line in enumerate(lines):
            if line.startswith('perl: warning: Falling back to the standard locale'):
                lines = lines[index + 1:]
                break
    # 共享 runner 把此 ExifTool 警告视为错误；无元数据原图是合法输入。
    if (allow_empty_source and lines
            and all(line.startswith('Warning: No writable tags set from ') for line in lines)
            and 'due to errors' not in result.stdout):
        return
    if result.returncode or any('Invalid XMP data' in line for line in lines):
        raise RuntimeError(f'导出 EXIF/XMP 保存失败：{result.stderr or result.stdout}')
    if result.stderr.strip():
        _LOG.warning('导出 EXIF/XMP：%s', result.stderr.strip())


def copy_export_metadata(source: Path, target: Path, image: Image.Image) -> None:
    """只复制 EXIF/ICC 等原生元数据；XMP 独立保存为同名侧车。"""
    executable = get_exiftool_executable_path()
    if not executable:
        raise RuntimeError('无法使用 ExifTool（程序缺失或捆绑组件不完整），不能完整保留原图 EXIF，已取消此图片导出。')
    # 直接从原文件复制，不能用模板用的筛选后元数据字典重建 EXIF。
    # EXIF 块保留未知标签；all:all 兼容 TIFF/RAW 标签复制并保留其他可写元数据。
    # all:all 必须排除整个 XMP 组；删除成片可能从 Pillow info 继承的内嵌包。
    _run(executable, ['-TagsFromFile', str(source), '-all:all', '--XMP:all', '-EXIF', '-ICC_Profile', '-XMP=', '-IFD0:Orientation#=1', str(target)], allow_empty_source=True)
    with tempfile.TemporaryDirectory(prefix='birdstamp-exif-') as directory:
        thumbnail_path = Path(directory) / 'thumbnail.jpg'
        with image.convert('RGB') as thumbnail:
            thumbnail.thumbnail((160, 160), Image.Resampling.LANCZOS)
            thumbnail.save(thumbnail_path, format='JPEG', quality=85)
            tw, th = thumbnail.size
        width, height = image.size
        # 块复制与逐标签修改必须分成两次调用，否则 EXIF 块会覆盖新方向/尺寸。
        # 未识别的 MakerNotes 按原始块保留，允许 ExifTool 不解析其内部标签。
        # 成片缩略图要重建整个 IFD1；只替换 ThumbnailImage 仍会解析原图的
        # 损坏目录并报 Truncated IFD1，且可能留下原缩略图的旧布局标签。
        _run(executable, [
            '-m', '-IFD1:all=', '-IFD0:Orientation#=1',
            f'-IFD0:ImageWidth={width}', f'-IFD0:ImageHeight={height}',
            f'-ExifIFD:ExifImageWidth={width}', f'-ExifIFD:ExifImageHeight={height}',
            f'-ThumbnailImage<={thumbnail_path}', '-IFD1:Orientation#=1',
            f'-IFD1:ImageWidth={tw}', f'-IFD1:ImageHeight={th}', str(target),
        ])


def save_export_image(image: Image.Image, target: Path, *, source_path: Path,
                      format: str, xmp_snapshot: bytes | None = None, **save_options) -> None:
    """先准备图片与独立 XMP，再配对提交；失败恢复已有成片和侧车。"""
    target = Path(target)
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f'无法读取原图 EXIF：{source}')
    check_export_destination(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.birdstamp-export-', suffix=target.suffix, dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    temporary_sidecar = temporary.with_suffix('.xmp')
    try:
        payload = read_export_xmp(source) if xmp_snapshot is None else xmp_snapshot
        temporary_sidecar.write_bytes(export_xmp_geometry(payload, source=source, size=image.size))
        image.save(temporary, format=format, **save_options)
        copy_export_metadata(source, temporary, image)
        publish_image_pair(temporary, temporary_sidecar, target)
    finally:
        temporary.unlink(missing_ok=True)
        temporary_sidecar.unlink(missing_ok=True)
