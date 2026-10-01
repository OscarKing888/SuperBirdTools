"""带持久化撤销记录的文件事务；恢复副本始终保留到恢复成功。"""
from __future__ import annotations

import os
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid

from .common import (INSTALLED_MANIFEST, UpdateError, atomic_json, hash_file,
                     read_json, sync_directory)
from .download import changed_entries, staged_path
from .manifest import checked_path, matches, mutable_path, relative_path, validate

STATE_DIR = ".superbird-update"


def state_dir(root: Path) -> Path:
    path = root / STATE_DIR
    if path.is_symlink():
        raise UpdateError(f"更新状态目录不能是链接: {path}")
    return path


def journal_path(root: Path) -> Path:
    return state_dir(root) / "transaction.json"


def preflight(root: Path, required: int = 0) -> None:
    if root.is_symlink() or not root.is_dir():
        raise UpdateError("安装目录无效")
    state = state_dir(root)
    state.mkdir(exist_ok=True)
    try:
        fd, name = tempfile.mkstemp(prefix="write-test-", dir=root)
        os.close(fd)
        os.unlink(name)
    except OSError as exc:
        raise UpdateError("安装目录不可写，请将整套应用移到当前用户可写目录") from exc
    if shutil.disk_usage(root).free < required + 16 * 1024**2:
        raise UpdateError("安装目录所在磁盘空间不足")


def _replace_copy(source: Path, dest: Path) -> None:
    temporary = dest.parent / f".sbt-{uuid.uuid4().hex}"
    try:
        shutil.copy2(source, temporary, follow_symlinks=False)
        if not temporary.is_symlink():
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
        os.replace(temporary, dest)
        sync_directory(dest.parent)
    finally:
        temporary.unlink(missing_ok=True)


def recover(root: Path) -> bool:
    """调用方必须持有启动/安装锁；恢复失败不删除日志和备份。"""
    journal = journal_path(root)
    if not journal.exists():
        return False
    data = read_json(journal)
    transaction = data.get("id", "")
    if len(transaction) != 32 or any(c not in "0123456789abcdef" for c in transaction):
        raise UpdateError("更新恢复记录损坏，请保留 .superbird-update 目录")
    backup = state_dir(root) / transaction
    if backup.is_symlink():
        raise UpdateError("更新备份目录不能是链接")
    if data["phase"] != "committed":
        try:
            for operation in reversed(data["operations"]):
                target = checked_path(root, relative_path(operation["path"]))
                if operation["directory"]:
                    if not operation["existed"] and target.is_dir() and not target.is_symlink():
                        target.rmdir()  # 不删除安装期间出现的未知文件。
                elif operation["existed"]:
                    original = backup / str(operation["backup"])
                    if not original.exists() and not original.is_symlink():
                        raise UpdateError(f"恢复副本丢失: {original}")
                    _replace_copy(original, target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                    sync_directory(target.parent)
        except Exception as exc:
            raise UpdateError(f"恢复未完成；请保留恢复文件 {backup}: {exc}") from exc
    # 先移除日志再清理冗余备份，崩溃不能留下指向已删备份的待恢复记录。
    journal.unlink()
    sync_directory(journal.parent)
    try:
        shutil.rmtree(backup)
    except OSError:
        logging.getLogger(__name__).warning("事务已完成，但备份清理失败: %s", backup, exc_info=True)
    return True


def install(root: Path, current: dict, candidate: dict, cache: Path, *, fault=None) -> None:
    """两个主进程退出且调用方持有启动/安装锁后才可调用。"""
    validate(current)
    validate(candidate)
    if journal_path(root).exists():
        raise UpdateError("存在未恢复的更新事务")
    changed = changed_entries(root, candidate, threading.Event())
    old = {e["path"]: e for e in current["files"]}
    new = {e["path"]: e for e in candidate["files"]}
    writes = [e for e in changed if e["kind"] != "directory"]
    required = sum(e.get("size", 0) for e in writes) * 2
    preflight(root, required)
    operations = []
    for entry in sorted(changed, key=lambda e: (e["kind"] != "directory", e["path"].count("/"), e["path"])):
        path = checked_path(root, entry["path"])
        existed = path.exists() or path.is_symlink()
        if entry["kind"] == "directory":
            if existed and (not path.is_dir() or path.is_symlink()):
                raise UpdateError(f"目录结构冲突，需手动安装: {path}")
        elif existed and path.is_dir() and not path.is_symlink():
            raise UpdateError(f"不能用文件覆盖已有目录: {path}")
        elif existed and entry["path"] not in old:
            raise UpdateError(f"新程序文件与用户文件重名，未覆盖: {path}")
        if entry["kind"] == "file":
            staged = staged_path(cache, entry)
            if staged.is_symlink() or not staged.is_file():
                raise UpdateError(f"缺少已校验文件: {entry['path']}")
            hashes = hash_file(staged)
            if any(hashes[k] != entry[k] for k in ("size", "md5", "sha256")):
                raise UpdateError(f"安装前校验失败: {entry['path']}")
        operations.append({"path": entry["path"], "directory": entry["kind"] == "directory",
                           "existed": existed, "entry": entry})
    for name, entry in old.items():
        if name not in new and entry["kind"] != "directory" and not mutable_path(name) and matches(root, entry):
            operations.append({"path": name, "directory": False, "existed": True, "entry": None})
    operations.append({"path": INSTALLED_MANIFEST, "directory": False,
                       "existed": (root / INSTALLED_MANIFEST).exists(), "entry": "manifest"})
    transaction = uuid.uuid4().hex
    backup = state_dir(root) / transaction
    backup.mkdir()
    journal = {"id": transaction, "phase": "installing", "operations": operations}
    try:
        for index, operation in enumerate(operations):
            operation["backup"] = index
            if operation["existed"] and not operation["directory"]:
                _replace_copy(checked_path(root, operation["path"]), backup / str(index))
        atomic_json(journal_path(root), journal)
        for index, operation in enumerate(operations):
            dest = checked_path(root, operation["path"])
            entry = operation["entry"]
            if entry == "manifest":
                atomic_json(dest, candidate)
            elif entry is None:
                dest.unlink()
            elif entry["kind"] == "directory":
                dest.mkdir(exist_ok=True)
                if not operation["existed"]:
                    dest.chmod(entry["mode"])
            elif entry["kind"] == "file":
                _replace_copy(staged_path(cache, entry), dest)
                dest.chmod(entry["mode"])
            else:
                temporary = dest.parent / f".sbt-{uuid.uuid4().hex}"
                try:
                    temporary.symlink_to(entry["target"], target_is_directory=(dest.parent / entry["target"]).is_dir())
                    os.replace(temporary, dest)
                finally:
                    temporary.unlink(missing_ok=True)
            sync_directory(dest.parent)
            if fault:
                fault(index, operation)
        if candidate["platform"] == "macos" and sys.platform == "darwin":
            from .common import component_names
            for component in component_names("macos"):
                check = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", str(root / component)],
                                       capture_output=True, text=True, timeout=90)
                if check.returncode:
                    raise UpdateError(f"macOS 应用签名校验失败: {component}\n{check.stderr}")
        journal["phase"] = "committed"
        atomic_json(journal_path(root), journal)
    except Exception:
        if journal_path(root).exists():
            recover(root)
        else:
            shutil.rmtree(backup)
        raise
    recover(root)  # committed 仅清理备份，不执行撤销。
