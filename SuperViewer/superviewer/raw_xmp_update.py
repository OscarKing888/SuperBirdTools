"""按同名 RAW 原样复制整份 XMP；核心和 CLI 不依赖 Qt。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.exif_io.metadata_store import get_store, notify_sidecar_write
from app_common.exif_io.writer import invalidate_same_stem_metadata_cache
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_identification import _fingerprint
from .capture_time_update import (
    CaptureTimeCancelled, CaptureTimeResult, MissingRawError, _check_cancelled,
    index_raw_files, matching_raw, name_key,
)


def _sidecar_path(photo):
    path = PhotoMetaDataXMP().sidecar_path_for(str(photo))
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f'XMP 不是普通文件：{path}')
    return path


def _copy_sidecar(group, raw, *, cancelled):
    """同侧车只提交一次；持有源/目标事务锁，固定锁顺序避免交叉复制死锁。"""
    photo, destination, _ = group[0]
    locks = {xmp_sidecar_write_lock(str(raw)), xmp_sidecar_write_lock(photo)}
    staging = None
    with ExitStack() as stack:
        # Flush the source before capturing its bytes; target scopes are already
        # protected by update_all_information before their fingerprints are read.
        stack.enter_context(get_store().file_operation([str(raw)]))
        for lock in sorted(locks, key=lambda item: item.identity):
            stack.enter_context(lock)
        _check_cancelled(cancelled)
        source = _sidecar_path(raw)
        before = (_fingerprint(raw), _fingerprint(source))
        if before[0] is None:
            raise ValueError('匹配的 RAW 已不存在')
        if before[1] is None:
            raise ValueError(f'对应 RAW 缺少 XMP：{source}')
        try:
            fd, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
            os.close(fd)  # Windows 下复制/替换前必须关闭临时文件句柄。
            staging = Path(temporary)
            shutil.copyfile(source, staging)
            # 只验证 XML/RDF，不重新序列化；未知命名空间、编辑参数、中文及格式原样保留。
            root = ET.parse(staging).getroot()
            rdf = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF'
            if root.tag != rdf and root.find(f'.//{rdf}') is None:
                raise ValueError(f'RAW 的 XMP 缺少 RDF 数据：{source}')
            _check_cancelled(cancelled)
            if before != (_fingerprint(raw), _fingerprint(source)) or _sidecar_path(raw) != source:
                raise ValueError('复制期间 RAW/XMP 已变化，请重试')
            for target, sidecar, previous in group:
                if (_sidecar_path(target) != sidecar or
                        previous != (_fingerprint(target), _fingerprint(sidecar))):
                    raise ValueError('扫描或复制期间照片/XMP 已变化，请重试')
            # 已共用源侧车时保持文件原样，也避免 SameFileError。
            if not (destination.exists() and os.path.samefile(source, destination)):
                os.replace(staging, destination)
                notify_sidecar_write(photo).result()
            saved = _fingerprint(destination)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
    invalidate_same_stem_metadata_cache([target for target, _, _ in group])
    return saved


def update_all_information(paths, directory, *, cancelled=lambda: False):
    """递归匹配 RAW，覆盖选中照片旁的同名 XMP；失败不影响其余照片。"""
    paths = list(dict.fromkeys(os.path.normpath(os.path.abspath(p)) for p in paths))
    try:
        # A failed pending edit must stop replacement, not disappear with the
        # destination XML. Capture fingerprints only after this barrier succeeds.
        with get_store().file_operation(paths):
            yield from _update_all_information(paths, directory, cancelled=cancelled)
    except Exception as exc:
        for source in paths:
            yield CaptureTimeResult(source, str(exc))


def _update_all_information(paths, directory, *, cancelled):
    groups = {}
    for source in dict.fromkeys(os.path.normpath(os.path.abspath(p)) for p in paths):
        if cancelled():
            return
        try:
            if Path(source).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                raise ValueError('仅支持图像文件')
            with xmp_sidecar_write_lock(source):
                sidecar = _sidecar_path(source)
                before = (_fingerprint(source), _fingerprint(sidecar))
            if before[0] is None:
                raise ValueError('所选照片已不存在')
            key = (os.path.normcase(str(sidecar.parent.resolve())), name_key(sidecar))
            groups.setdefault(key, []).append((source, sidecar, before))
        except Exception as exc:
            yield CaptureTimeResult(source, str(exc))
    if not groups:
        return
    try:
        index = index_raw_files(directory, cancelled=cancelled)
    except CaptureTimeCancelled:
        return
    except Exception as exc:
        for group in groups.values():
            for photo, _, _ in group:
                yield CaptureTimeResult(photo, str(exc))
        return
    for group in groups.values():
        raw = ''
        try:
            _check_cancelled(cancelled)
            raw = matching_raw(group[0][0], index)
            saved = _copy_sidecar(group, raw, cancelled=cancelled)
            for photo, _, _ in group:
                # 空 RDF 也是有效的整份替换；SourceFile 使成功与失败保持明确区分。
                yield CaptureTimeResult(photo, updates={'SourceFile': photo},
                                        saved_fingerprint=saved, raw_source=raw)
        except CaptureTimeCancelled:
            return
        except Exception as exc:
            for photo, _, _ in group:
                yield CaptureTimeResult(photo, str(exc), raw_source=raw,
                                        missing_raw=isinstance(exc, MissingRawError))


def main(argv=None):
    parser = argparse.ArgumentParser(description='从同名 RAW 复制整份 XMP，覆盖选中图像的所有侧车信息')
    parser.add_argument('--raw-directory', required=True)
    parser.add_argument('paths', nargs='+')
    args = parser.parse_args(argv)
    failed = False
    for result in update_all_information(args.paths, args.raw_directory):
        failed |= not bool(result.updates)
        print(json.dumps({'source': result.source, 'success': bool(result.updates),
                          'raw_source': result.raw_source, 'error': result.message}, ensure_ascii=False))
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
