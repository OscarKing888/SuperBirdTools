"""在最终打包产物上生成可范围下载的确定性 ZIP64 载荷。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import zipfile

from .common import (APPS, BLOCK_SIZE, CONFIG_NAME, INSTALLED_MANIFEST, UpdateError,
                     atomic_json, component_names, hash_file)
from .manifest import MAX_ASSET_SIZE, excluded_path, validate


def git_identity(repo: Path) -> dict:
    from build_tools.set_build_version import prepare_build_metadata
    from app_identity import load_app_identity

    def git(*args, cwd=repo):
        return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()
    if git("rev-parse", "--is-shallow-repository") == "true":
        raise UpdateError("生成更新版本需要完整 Git 历史；CI checkout 请设置 fetch-depth: 0")
    commit = git("rev-parse", "HEAD")
    release_version = load_app_identity("SuperViewer", prepare_build_metadata(repo)).version
    # 保留 schema 1 的短 hash 字段，已发布的更新器仍能读取新清单。
    return {"commit": commit, "version": commit[:8], "release_version": release_version,
            "revision": int(git("rev-list", "--count", "--first-parent", "HEAD")),
            "app_common_commit": git("rev-parse", "HEAD", cwd=repo / "app_common")}


def _entries(root: Path, target: str) -> list[dict]:
    entries = []
    for component in component_names(target):
        top = root / component
        if not top.is_dir() or top.is_symlink():
            raise UpdateError(f"缺少独立打包组件: {top}")
        paths = [top]
        for directory, dirs, files in os.walk(top, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not excluded_path((Path(directory) / d).relative_to(root).as_posix()))
            paths.extend(Path(directory) / name for name in dirs + sorted(files))
        for path in paths:
            name = path.relative_to(root).as_posix()
            if excluded_path(name):
                continue
            mode = path.lstat().st_mode
            entry = {"path": name, "mode": stat.S_IMODE(mode) & 0o777}
            if path.is_symlink():
                entry.update(kind="symlink", target=os.readlink(path))
            elif path.is_dir():
                entry.update(kind="directory")
            elif stat.S_ISREG(mode):
                entry.update(kind="file")
            else:
                raise UpdateError(f"不支持的特殊文件: {path}")
            entries.append(entry)
    return sorted(entries, key=lambda e: e["path"])


def generate(root: Path, output: Path, identity: dict, target: str, arch: str,
             workers: int | None = None, volume_size: int = 1024**3) -> dict:
    root = root.resolve()
    if not 1024 <= volume_size <= MAX_ASSET_SIZE - 1024**2:
        raise UpdateError("分卷大小必须在 1 KiB 与 GitHub 附件上限之间")
    workers = workers if workers is not None else min(8, os.cpu_count() or 1)
    if not 1 <= workers <= 64:
        raise UpdateError("哈希线程数必须在 1–64 之间")
    entries = _entries(root, target)
    files = [e for e in entries if e["kind"] == "file"]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="update-hash") as pool:
        for entry, hashes in zip(files, pool.map(lambda e: hash_file(root / e["path"]), files)):
            entry.update(hashes)
    output.mkdir(parents=True, exist_ok=True)
    prefix = f"update-{target}-{arch}"
    with tempfile.TemporaryDirectory(prefix="update-build-", dir=output.parent) as temporary:
        stage = Path(temporary)
        archives = []
        archive = None
        uncompressed = 0
        try:
            for entry in files:
                # 给压缩膨胀、文件头和中央目录留出空间；过大单文件不静默截断。
                if entry["size"] + entry["size"] // 1000 + 1024**2 >= MAX_ASSET_SIZE:
                    raise UpdateError(f"单文件超出 GitHub 附件限制: {entry['path']}")
                if archive is None or uncompressed + entry["size"] + 4096 > volume_size:
                    if archive is not None:
                        archive.close()
                    name = f"{prefix}-{len(archives) + 1:03d}.zip"
                    archives.append(name)
                    archive = zipfile.ZipFile(stage / name, "w", compression=zipfile.ZIP_DEFLATED,
                                              compresslevel=6, allowZip64=True)
                    uncompressed = 0
                info = zipfile.ZipInfo(entry["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | entry["mode"]) << 16
                sha = hashlib.sha256()
                with (root / entry["path"]).open("rb") as source, archive.open(info, "w", force_zip64=True) as dest:
                    while block := source.read(BLOCK_SIZE):
                        dest.write(block)
                        sha.update(block)
                if sha.hexdigest() != entry["sha256"]:
                    raise UpdateError(f"构建文件在生成清单期间发生变化: {entry['path']}")
                entry.update(asset=archives[-1], compression=info.compress_type,
                             compressed_size=info.compress_size, offset=info.header_offset)
                uncompressed += entry["size"] + 4096
        finally:
            if archive is not None:
                archive.close()
        assets = {name: {k: v for k, v in hash_file(stage / name).items() if k != "md5"}
                  for name in archives}
        for entry in files:
            with (stage / entry["asset"]).open("rb") as stream:
                stream.seek(entry["offset"] + 26)
                name_length, extra_length = struct.unpack("<HH", stream.read(4))
            entry["offset"] += 30 + name_length + extra_length
        data = validate({"schema": 1, "platform": target, "arch": arch,
                         **identity, "assets": assets, "files": entries})
        for name in archives:
            os.replace(stage / name, output / name)
        # 清单最后发布，避免消费者读到不完整的新版本。
        atomic_json(output / f"{prefix}.json", data)
        for stale in output.glob(f"{prefix}-*.zip"):
            if stale.name not in assets:
                stale.unlink()
    atomic_json(root / INSTALLED_MANIFEST, data)
    return data


def package_suite(root: Path, output: Path, manifest: dict, repo: Path) -> Path:
    """完整安装包与增量清单来自同一份最终产物，保留 macOS 链接和权限。"""
    version = manifest.get("release_version", manifest["version"])
    name = f"SuperBirdTools-{version}-{manifest['platform']}-{manifest['arch']}"
    result = output / f"{name}.zip"
    output.mkdir(parents=True, exist_ok=True)
    # zipfile 不自动保存 symlink，因此显式编码 Unix 类型和链接目标。
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for entry in manifest["files"]:
            info = zipfile.ZipInfo(f"{name}/{entry['path']}", (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            kind = entry["kind"]
            types = {"file": stat.S_IFREG, "directory": stat.S_IFDIR, "symlink": stat.S_IFLNK}
            info.external_attr = (types[kind] | entry["mode"]) << 16
            if kind == "directory":
                info.filename += "/"
                info.external_attr |= 0x10
                archive.writestr(info, b"")
            elif kind == "symlink":
                archive.writestr(info, entry["target"].encode("utf-8"))
            else:
                with archive.open(info, "w", force_zip64=True) as dest, (root / entry["path"]).open("rb") as src:
                    shutil.copyfileobj(src, dest, BLOCK_SIZE)
        for file in (INSTALLED_MANIFEST, CONFIG_NAME):
            archive.write(root / file, f"{name}/{file}")
        for source, dest in (("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
                             ("SuperViewer/LICENSE", "LICENSE-SuperViewer"),
                             ("SuperBirdStamp/LICENSE", "LICENSE-SuperBirdStamp")):
            if (repo / source).is_file():
                archive.write(repo / source, f"{name}/{dest}")
    if result.stat().st_size > MAX_ASSET_SIZE:
        result.unlink()
        raise UpdateError("完整安装包超过 GitHub 单附件限制；请缩减打包依赖后重建")
    return result
