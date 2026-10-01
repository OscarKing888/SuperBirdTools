from __future__ import annotations

import hashlib
import http.client
import os
from pathlib import Path
import shutil
import threading
from urllib.error import URLError
import zlib

from .common import BLOCK_SIZE, Cancelled, UpdateError, hash_file
from .manifest import checked_path, matches, mutable_path, validate
from .locking import FileLock
from .sources import RangeUnsupported


def staged_path(cache: Path, entry: dict) -> Path:
    key = hashlib.sha256(entry["path"].encode("utf-8")).hexdigest()
    return cache / f"{key}-{entry['sha256']}"


def _verified(path: Path, entry: dict, cancel) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    hashes = hash_file(path, cancel)
    return all(hashes[k] == entry[k] for k in ("size", "md5", "sha256"))


def changed_entries(root: Path, manifest: dict, cancel) -> list[dict]:
    result = []
    for entry in manifest["files"]:
        if cancel.is_set():
            raise Cancelled("更新已取消")
        path = checked_path(root, entry["path"])
        if mutable_path(entry["path"]) and path.exists():
            continue
        if not matches(root, entry, cancel):
            result.append(entry)
    return result


def _download(source, entry: dict, asset: dict, cache: Path, cancel, allow_full: bool) -> None:
    result = staged_path(cache, entry)
    if _verified(result, entry, cancel):
        return
    part = result.with_suffix(".part")
    try:
        decoder = zlib.decompressobj(-15) if entry["compression"] == 8 else None
        size = 0
        with part.open("wb") as dest:
            for block in source.chunks(entry["asset"], entry["offset"], entry["compressed_size"],
                                       asset, cancel, cache, allow_full):
                while block:
                    if cancel.is_set():
                        raise Cancelled("更新已取消")
                    if decoder is not None:
                        decoded = decoder.decompress(block, min(BLOCK_SIZE, entry["size"] - size + 1))
                        block = decoder.unconsumed_tail
                    else:
                        decoded, block = block, b""
                    size += len(decoded)
                    if size > entry["size"]:
                        raise UpdateError("解压内容超过清单大小")
                    dest.write(decoded)
            if decoder is not None and (not decoder.eof or decoder.unused_data):
                raise UpdateError("压缩文件不完整或包含额外数据")
            dest.flush()
            os.fsync(dest.fileno())
        if not _verified(part, entry, cancel):
            raise UpdateError(f"文件校验失败: {entry['path']}")
        os.replace(part, result)
    finally:
        part.unlink(missing_ok=True)


def prepare(root: Path, manifest: dict, source, cache: Path, *, cancel=None,
            progress=None, allow_full: bool = False) -> list[dict]:
    validate(manifest)
    cache.mkdir(parents=True, exist_ok=True)
    with FileLock(cache / ".download.lock"):
        return _prepare(root, manifest, source, cache, cancel=cancel, progress=progress, allow_full=allow_full)


def _prepare(root: Path, manifest: dict, source, cache: Path, *, cancel=None,
             progress=None, allow_full: bool = False) -> list[dict]:
    cancel = cancel or threading.Event()
    cache.mkdir(parents=True, exist_ok=True)
    changed = changed_entries(root, manifest, cancel)
    files = [entry for entry in changed if entry["kind"] == "file"]
    required = sum(entry["size"] for entry in files)
    if allow_full:
        required += sum(a["size"] for a in manifest["assets"].values())
    if shutil.disk_usage(cache).free < required + 16 * 1024**2:
        raise UpdateError("下载暂存目录空间不足")
    for index, entry in enumerate(files):
        if progress:
            progress(index, len(files), entry["path"])
        for attempt in range(3):
            try:
                _download(source, entry, manifest["assets"][entry["asset"]], cache, cancel, allow_full)
                break
            except (Cancelled, RangeUnsupported):
                raise
            except (OSError, URLError, http.client.HTTPException, zlib.error, UpdateError) as exc:
                if attempt == 2:
                    raise UpdateError(f"下载失败 {entry['path']}: {exc}") from exc
                if cancel.wait(0.2 * (attempt + 1)):
                    raise Cancelled("更新已取消") from exc
    if progress:
        progress(len(files), len(files), "下载及校验完成")
    return changed
