"""成片与同名 XMP 的配对提交、失败恢复和缓存复制。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unicodedata

from app_common.exif_io import find_same_stem_xmp_sidecar


def sidecar_for(path: Path) -> Path:
    found = find_same_stem_xmp_sidecar(str(path))
    return Path(found) if found else path.with_suffix('.xmp')


def check_export_destination(source: Path, target: Path) -> None:
    def key(path):
        return unicodedata.normalize('NFC', str(path.resolve())).casefold()

    if (key(source) == key(target)
            or key(source.with_suffix('.xmp')) == key(target.with_suffix('.xmp'))
            or (target.exists() and os.path.samefile(source, target))
            or (sidecar_for(source).exists() and sidecar_for(target).exists()
                and os.path.samefile(sidecar_for(source), sidecar_for(target)))):
        raise ValueError('导出目标不能覆盖原图或原图 XMP，请选择其他文件名。')


def publish_image_pair(staged: Path, staged_sidecar: Path | None, target: Path) -> None:
    """两文件无法一次原子替换：提交前保留旧副本，任一步失败恢复整对。"""
    sidecar = sidecar_for(target)
    folder = Path(tempfile.mkdtemp(prefix='.birdstamp-backup-', dir=target.parent))
    backups, committed = {}, []
    retain = False
    try:
        for destination in (sidecar, target):
            if destination.exists():
                backup = folder / destination.name
                shutil.copy2(destination, backup)
                backups[destination] = backup
        for source, destination in ((staged_sidecar, sidecar), (staged, target)):
            if source is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(source, destination)
            committed.append(destination)
    except BaseException as exc:
        failures = []
        for destination in reversed(committed):
            try:
                if destination in backups:
                    os.replace(backups[destination], destination)
                else:
                    destination.unlink(missing_ok=True)
            except OSError:
                failures.append(str(destination))
        if failures:
            retain = True
            raise RuntimeError(f'{exc}；成片配对恢复失败：{", ".join(failures)}；恢复副本：{folder}') from exc
        raise
    finally:
        if not retain:
            shutil.rmtree(folder)


def copy_export_image(source: Path, target: Path) -> None:
    """复制已完成的成片和侧车，不重新编码像素。"""
    check_export_destination(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.birdstamp-frame-', dir=target.parent) as directory:
        staged = Path(directory) / target.name
        shutil.copyfile(source, staged)
        sidecar = sidecar_for(source)
        staged_sidecar = staged.with_suffix('.xmp') if sidecar.is_file() else None
        if staged_sidecar is not None:
            shutil.copyfile(sidecar, staged_sidecar)
        publish_image_pair(staged, staged_sidecar, target)


def sidecar_signature(path: Path) -> str | None:
    import json
    from app_common.exif_io.metadata_sqlite import sidecar_stamp

    sidecar = sidecar_for(path)
    stamp = sidecar_stamp(sidecar)
    return json.dumps(stamp) if stamp is not None else None
