"""系统移动的会话日志：记录已完成结果，绝不猜测重命名或覆盖前的数据。"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import stat

from app_common.exif_io.xmp_sidecar import find_same_stem_xmp_sidecar


def path_key(path):
    return os.path.normcase(os.path.abspath(path))


def is_sidecar(path):
    return Path(path).suffix.lower() == ".xmp"


def _digest(path, cancelled=None):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if cancelled is not None and cancelled():
                raise InterruptedError("已取消移动准备")
            digest.update(chunk)
    return digest.digest()


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    digest: bytes | None = None
    birth_ns: int | None = None

    def same_object(self, other):
        if other is None:
            return False
        if self.inode and other.inode:
            return (self.device, self.inode, self.birth_ns) == (other.device, other.inode, other.birth_ns)
        return self.digest is not None and self.digest == other.digest

    @classmethod
    def read(cls, path, *, for_directory=None, cancelled=None):
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"不能记录非普通文件：{path}")
        cross_volume = for_directory is not None and info.st_dev != os.stat(for_directory).st_dev
        # 同卷移动保留文件身份；跨卷复制后删除会改变身份，需验证内容。
        digest = _digest(path, cancelled) if cross_volume or not info.st_ino else None
        return cls(info.st_dev, info.st_ino, digest, getattr(info, "st_birthtime_ns", None))

    def matches(self, path):
        try:
            info = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                return False
            if self.inode and (info.st_dev, info.st_ino) == (self.device, self.inode):
                return self.birth_ns is None or self.birth_ns == getattr(info, "st_birthtime_ns", None)
            return self.digest is not None and _digest(path) == self.digest
        except OSError:
            return False


@dataclass(frozen=True)
class MoveEntry:
    source: str
    target: str
    identity: FileIdentity | None
    target_identity: FileIdentity | None = None


@dataclass(frozen=True)
class MoveSnapshot:
    entry: MoveEntry
    target_was_free: bool


def snapshot_move(paths, destination, cancelled=None):
    """只读快照；哈希仅在跨卷或文件系统没有稳定 ID 时于 worker 计算。"""
    targets = Counter(path_key(Path(destination) / Path(path).name).casefold() for path in paths)
    snapshots = []
    for path in paths:
        if cancelled is not None and cancelled():
            raise InterruptedError("已取消移动准备")
        try:
            identity = FileIdentity.read(path, for_directory=destination, cancelled=cancelled)
        except InterruptedError:
            raise
        except (OSError, ValueError):
            identity = None  # 无法记录身份不阻止系统处理文件，但不能承诺撤销。
        target = str(Path(destination) / Path(path).name)
        snapshots.append(MoveSnapshot(MoveEntry(path, target, identity),
                                      targets[path_key(target).casefold()] == 1 and not os.path.lexists(target)))
    return tuple(snapshots)


def completed_moves(snapshots):
    inverse, untracked = [], []
    for snapshot in snapshots:
        entry = snapshot.entry
        if os.path.lexists(entry.source):
            continue
        try:
            if snapshot.target_was_free and entry.identity is not None and entry.identity.matches(entry.target):
                inverse.append(MoveEntry(entry.target, entry.source, FileIdentity.read(entry.target), entry.identity))
                continue
        except (OSError, ValueError):
            pass  # 系统窗口结束后外部程序仍可能立即移动/删除该文件。
        untracked.append(entry.source)
    # 同源同 stem 的照片/RAW/XMP 不能只承诺一半的还原。
    blocked_stems = {path_key(Path(path).with_suffix("")) for path in untracked}
    kept = []
    for entry in inverse:
        if path_key(Path(entry.target).with_suffix("")) in blocked_stems:
            untracked.append(entry.target)
        else:
            kept.append(entry)
    return tuple(kept), tuple(untracked)


def prepare_replay(entries):
    """先验证整批照片，再配当前 XMP；不以旧副本覆盖后续元数据修改。"""
    photos = [entry for entry in entries if not is_sidecar(entry.source)]
    pairs = {(path_key(Path(e.source).with_suffix("")), path_key(Path(e.target).with_suffix("")))
             for e in photos}
    result = []
    seen = set()
    for entry in entries:
        paired_xmp = is_sidecar(entry.source) and (
            path_key(Path(entry.source).with_suffix("")), path_key(Path(entry.target).with_suffix(""))) in pairs
        if paired_xmp:
            if not os.path.lexists(entry.source):
                continue  # 用户删除的侧车不凭空恢复。
            entry = replace(entry, identity=FileIdentity.read(entry.source))
        elif entry.identity is None or not entry.identity.matches(entry.source):
            raise ValueError(f"文件已被删除、替换或移到其它位置，未执行移动：{entry.source}")
        result.append(entry)
        seen.add(path_key(entry.source))
    for photo in photos:
        sidecar = find_same_stem_xmp_sidecar(photo.source)
        if sidecar and path_key(sidecar) not in seen:
            result.append(MoveEntry(sidecar, str(Path(photo.target).parent / Path(sidecar).name),
                                    FileIdentity.read(sidecar)))
            seen.add(path_key(sidecar))
    for entry in result:
        if Path(entry.source).name != Path(entry.target).name:
            raise ValueError(f"移动历史中的文件名不一致：{entry.source}")
        if not Path(entry.target).parent.is_dir():
            raise ValueError(f"原目录已不存在，请恢复目录后重试：{Path(entry.target).parent}")
        if os.path.lexists(entry.target):
            raise ValueError(f"目标位置已有文件，请处理后重试：{entry.target}")
    return tuple(result)


class MoveHistory:
    """异步操作完成后更新两个有界栈；部分成功与剩余工作分别保留。"""
    def __init__(self, limit=100):
        self.undo = deque(maxlen=limit)
        self.redo = deque(maxlen=limit)

    def record(self, inverse, *, changed=False):
        if inverse:
            self.undo.append(tuple(inverse))
        if inverse or changed:
            self.redo.clear()

    def finish(self, direction, inverse, remaining):
        source, target = (self.undo, self.redo) if direction == "undo" else (self.redo, self.undo)
        original = {path_key(e.source): e for e in source.pop()}
        if remaining:
            source.append(tuple(remaining))
        if inverse:
            target.append(tuple(inverse))
        restored = {}
        for entry in inverse:
            previous = original.get(path_key(entry.target))
            if (previous is not None and previous.target_identity is not None
                    and not previous.target_identity.same_object(entry.identity)):
                restored[path_key(entry.source)] = (previous.target_identity, entry.identity)
        if not restored:
            return
        # 跨卷来回移动会产生新的文件 ID；只按这次已验证的来源关系更新旧命令。
        def rebound(entry):
            identity, target_identity = entry.identity, entry.target_identity
            before_after = restored.get(path_key(entry.source))
            if before_after and before_after[0].same_object(identity):
                identity = before_after[1]
            before_after = restored.get(path_key(entry.target))
            if before_after and before_after[0].same_object(target_identity):
                target_identity = before_after[1]
            return replace(entry, identity=identity, target_identity=target_identity)
        for stack in (self.undo, self.redo):
            for index, batch in enumerate(stack):
                stack[index] = tuple(rebound(entry) for entry in batch)
