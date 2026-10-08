"""从同名 RAW 恢复 PNG/JPEG 拍摄时间；匹配与时间读取不依赖 Qt。"""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import unicodedata

from app_common.image_formats import JPEG_IMAGE_EXTENSIONS, RAW_IMAGE_EXTENSIONS
from app_common.exif_io.exiftool_path import get_exiftool_executable_path
from app_common.exif_io.exiftool_runner import run_exiftool
from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from .bird_identification import _fingerprint

TARGET_EXTENSIONS = JPEG_IMAGE_EXTENSIONS | {'.png'}
TIME_TAGS = ('DateTimeOriginal', 'SubSecTimeOriginal', 'OffsetTimeOriginal',
             'CreateDate', 'SubSecTimeDigitized', 'OffsetTimeDigitized')
_DATE = re.compile(r'^(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})'
                   r'(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$')


class CaptureTimeCancelled(RuntimeError):
    pass


class MissingRawError(ValueError):
    """扫描成功，但没有同名 RAW；GUI 可保留选择以便换目录重试。"""


@dataclass
class CaptureTimeResult:
    source: str
    message: str = ''
    updates: dict = field(default_factory=dict)
    saved_fingerprint: tuple | None = None
    raw_source: str = ''
    missing_raw: bool = False


def _check_cancelled(cancelled):
    if cancelled():
        raise CaptureTimeCancelled('已停止')


def name_key(path):
    """只忽略扩展名及大小写，不猜测导出编号/后缀。"""
    return unicodedata.normalize('NFC', Path(path).stem).casefold()


def index_raw_files(directory, *, cancelled=lambda: False):
    """递归扫描一次；跳过目录符号链接，扫描错误不能伪装成未找到。"""
    root = Path(directory).absolute()
    if not root.is_dir():
        raise ValueError(f'RAW 目录不存在：{root}')
    found = {}
    def on_error(error):
        raise OSError(f'无法扫描 RAW 目录：{error}') from error
    for parent, dirs, names in os.walk(root, onerror=on_error, followlinks=False):
        _check_cancelled(cancelled)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not Path(parent, d).is_symlink())
        for name in sorted(names):
            _check_cancelled(cancelled)
            path = Path(parent, name)
            if path.suffix.lower() in RAW_IMAGE_EXTENSIONS and not path.is_symlink():
                found.setdefault(name_key(path), []).append(str(path))
    return found


def matching_raw(path, index):
    matches = index.get(name_key(path), [])
    if not matches:
        raise MissingRawError('未找到同名 RAW')
    if len(matches) != 1:
        raise ValueError('找到多个同名 RAW，无法确定：' + '；'.join(matches))
    return matches[0]


def capture_timestamp(metadata):
    """优先原始拍摄时间，再回退 EXIF 数字化时间；不使用文件系统时间。"""
    def value(tag):
        for prefix in ('ExifIFD:', 'EXIF:', ''):
            item = metadata.get(prefix + tag)
            if item is not None and str(item).strip():
                return str(item).strip()
        return ''
    for base, subsec, offset in (('DateTimeOriginal', 'SubSecTimeOriginal', 'OffsetTimeOriginal'),
                                 ('CreateDate', 'SubSecTimeDigitized', 'OffsetTimeDigitized')):
        text = value(base)
        if not text:
            continue
        match = _DATE.fullmatch(text)
        if not match:
            raise ValueError(f'RAW 的 {base} 格式无效：{text}')
        year, month, day, hour, minute, second, fraction, zone = match.groups()
        fraction = fraction or value(subsec)
        zone = zone or value(offset)
        if fraction and (not fraction.isascii() or not fraction.isdigit()):
            raise ValueError('RAW 的亚秒时间无效')
        if zone and not re.fullmatch(r'Z|[+-]\d{2}:\d{2}', zone):
            raise ValueError('RAW 的拍摄时区无效')
        if zone and zone != 'Z' and (int(zone[1:3]) > 23 or int(zone[4:6]) > 59):
            raise ValueError('RAW 的拍摄时区无效')
        stamp = f'{year}-{month}-{day}T{hour}:{minute}:{second}'
        stamp += ('.' + fraction) if fraction else ''
        stamp += zone
        try:
            datetime.fromisoformat(stamp)
        except ValueError as exc:
            raise ValueError(f'RAW 的拍摄时间无效：{stamp}') from exc
        return stamp
    raise ValueError('RAW 缺少有效拍摄时间（DateTimeOriginal / CreateDate）')


def read_raw_capture_time(path, *, cancelled=lambda: False):
    """直接读取 RAW 内嵌 EXIF，忽略同名侧车；使用所属 worker 的 ExifTool 会话。"""
    _check_cancelled(cancelled)
    executable = get_exiftool_executable_path()
    if not executable:
        raise RuntimeError('未找到 ExifTool，无法读取 RAW 拍摄时间')
    # 不使用 -n，保留 SubSecTimeOriginal 的前导零。
    args = ['-j', '-G1', '-s', '-charset', 'filename=UTF8',
            *('-EXIF:' + tag for tag in TIME_TAGS), os.path.abspath(path)]
    result = run_exiftool(executable, args, timeout=20)
    _check_cancelled(cancelled)
    if result.returncode:
        raise RuntimeError(f'读取 RAW 失败：{(result.stderr or result.stdout or "ExifTool 错误").strip()}')
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError('ExifTool 返回无效的 RAW 元数据')
    if rows[0].get('Error'):
        raise ValueError(f'读取 RAW 失败：{rows[0]["Error"]}')
    return capture_timestamp(rows[0])


def time_updates(stamp):
    """标准 XMP 与浏览器缓存统一使用同一拍摄时刻。"""
    return {'XMP-exif:DateTimeOriginal': stamp, 'XMP-xmp:CreateDate': stamp,
            'XMP-superpicky:date_time_original': stamp, 'date_time_original': stamp,
            'DateTimeOriginal': stamp, 'CreateDate': stamp,
            'ExifIFD:DateTimeOriginal': stamp, 'EXIF:DateTimeOriginal': stamp,
            'Composite:SubSecDateTimeOriginal': stamp, 'SubSecDateTimeOriginal': stamp}


def update_capture_times(paths, directory, *, cancelled=lambda: False):
    """逐文件产出结果；共享侧车只写一次，慢扫描/读取期间的编辑不会被覆盖。"""
    sources = list(dict.fromkeys(os.path.normpath(os.path.abspath(p)) for p in paths))
    groups = {}
    store = PhotoMetaDataXMP()
    for source in sources:
        if cancelled():
            return
        if Path(source).suffix.lower() not in TARGET_EXTENSIONS:
            yield CaptureTimeResult(source, '仅支持 PNG/JPG/JPEG 文件')
            continue
        try:
            with xmp_sidecar_write_lock(source):
                before = (_fingerprint(source), _fingerprint(store.sidecar_path_for(source)))
        except OSError as exc:
            yield CaptureTimeResult(source, f'无法读取照片/XMP 状态：{exc}')
            continue
        if before[0] is None:
            yield CaptureTimeResult(source, '所选照片已不存在')
            continue
        key = os.path.normcase(str(store.sidecar_path_for(source)))
        groups.setdefault(key, []).append((source, before))
    if not groups:
        return
    try:
        index = index_raw_files(directory, cancelled=cancelled)
    except CaptureTimeCancelled:
        return
    except Exception as exc:
        for group in groups.values():
            for source, _ in group:
                yield CaptureTimeResult(source, str(exc))
        return
    for group in groups.values():
        raw = ''
        try:
            _check_cancelled(cancelled)
            source, before = group[0]
            raw = matching_raw(source, index)
            raw_before = _fingerprint(raw)
            if raw_before is None:
                raise ValueError('匹配的 RAW 已不存在')
            stamp = read_raw_capture_time(raw, cancelled=cancelled)
            with xmp_sidecar_write_lock(source):
                _check_cancelled(cancelled)
                if _fingerprint(raw) != raw_before:
                    raise ValueError('读取期间 RAW 已变化，请重试')
                for photo, previous in group:
                    if previous != (_fingerprint(photo), _fingerprint(store.sidecar_path_for(photo))):
                        raise ValueError('扫描或读取期间照片/XMP 已变化，请重试')
                if store._load_or_create_xmp_tree(store.sidecar_path_for(source)) is None:
                    raise ValueError('已有 XMP 损坏，未写入')
                updates = time_updates(stamp)
                fields = {key: value for key, value in updates.items() if key.startswith('XMP-')}
                if not store.write(source, fields):
                    raise RuntimeError('拍摄时间写入失败，原 XMP 已保留')
                saved = _fingerprint(store.sidecar_path_for(source))
            for photo, _ in group:
                yield CaptureTimeResult(photo, updates=updates, saved_fingerprint=saved, raw_source=raw)
        except CaptureTimeCancelled:
            return
        except Exception as exc:
            for photo, _ in group:
                yield CaptureTimeResult(photo, str(exc), raw_source=raw,
                                        missing_raw=isinstance(exc, MissingRawError))


def main(argv=None):
    parser = argparse.ArgumentParser(description='从指定目录的同名 RAW 更新 PNG/JPEG 拍摄时间（XMP）')
    parser.add_argument('--raw-directory', required=True)
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args(argv)
    from app_common.exif_io.exiftool_runner import exiftool_worker_session, exiftool_read_request
    failed = False
    with exiftool_worker_session(), exiftool_read_request(lambda: False):
        for result in update_capture_times(args.paths, args.raw_directory):
            failed |= not bool(result.updates)
            print(json.dumps({'source': result.source, 'success': bool(result.updates),
                              'raw_source': result.raw_source, 'error': result.message}, ensure_ascii=False))
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
