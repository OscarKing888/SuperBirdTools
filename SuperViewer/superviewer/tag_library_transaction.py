"""Recoverable, all-or-nothing tag-library edits, independent of Qt."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS

from .photo_tags import PhotoTagConfig, atomic_write_bytes


class TagEditCancelled(Exception):
    pass


class TagRecoveryRequired(Exception):
    pass


class TagLibraryFailure(Exception):
    """An atomic transaction failed; its message describes recovery status."""


def _bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _checkpoint(cancel):
    if cancel():
        raise TagEditCancelled("已取消，修改已回滚。")


@dataclass
class SubjectPatch:
    source: str
    sidecar: str
    aliases: list[str]
    before: dict[str, bool]
    after: dict[str, bool]

    def inverse(self):
        return SubjectPatch(self.source, self.sidecar, self.aliases, self.after, self.before)


@dataclass
class TagEditPlan:
    config_path: str
    before: bytes | None
    after: bytes | None
    patches: list[SubjectPatch] = field(default_factory=list)
    filter_mapping: dict[str, str | None] = field(default_factory=dict)

    @property
    def affected_count(self):
        return sum(len(p.aliases) for p in self.patches)

    def inverse(self):
        return TagEditPlan(self.config_path, self.after, self.before,
                           [p.inverse() for p in self.patches],
                           {new: old for old, new in self.filter_mapping.items() if new})


def prepare_tag_edit(config: PhotoTagConfig, before, after, directory: str,
                     mapping: dict[str, str | None], *, cancel=lambda: False,
                     progress=lambda *args: None) -> TagEditPlan:
    if config.read_bytes() != before:
        raise ValueError("标签配置已被其他程序修改，请重新加载。")
    assert_no_recovery(config)
    plan = TagEditPlan(str(config.path), before, after, filter_mapping=dict(mapping))
    if not mapping:
        return plan
    if not directory or not Path(directory).is_dir():
        raise ValueError("请先选择需要同步照片的目录。")
    meta = PhotoMetaDataXMP()
    by_sidecar: dict[str, tuple[str, list[str]]] = {}
    def scan_error(error):
        raise error
    for root, dirs, names in os.walk(directory, followlinks=False, onerror=scan_error):
        _checkpoint(cancel)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in {"thumb_cache", "__pycache__"}
                         and not Path(root, d).is_symlink()
                         and not getattr(Path(root, d), "is_junction", lambda: False)())
        for name in sorted(names):
            _checkpoint(cancel)
            source = Path(root, name)
            if source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS or source.is_symlink():
                continue
            sidecar = meta.sidecar_path_for(str(source))
            if sidecar.is_symlink():
                raise ValueError(f"侧车为链接，无法安全同步：{sidecar}")
            _, aliases = by_sidecar.setdefault(_key(sidecar), (str(sidecar), []))
            aliases.append(str(source))
        progress("扫描照片", len(by_sidecar), 0)
    touched = set(mapping) | {name for name in mapping.values() if name is not None}
    for index, (sidecar, aliases) in enumerate(by_sidecar.values()):
        _checkpoint(cancel)
        subjects = meta.read_subjects(aliases[0], strict=True)
        changed = {mapping.get(tag, tag) for tag in subjects} - {None}
        old = {name: name in subjects for name in touched}
        new = {name: name in changed for name in touched}
        if old != new:
            plan.patches.append(SubjectPatch(aliases[0], sidecar, aliases, old, new))
        progress("检查照片标签", index + 1, len(by_sidecar))
    return plan


def recovery_directories(config: PhotoTagConfig) -> list[Path]:
    result = []
    if config.path is None:
        return result
    for directory in config.path.parent.glob(".tag-edit-recovery-*"):
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            continue
        # A damaged journal must never silently permit another edit.
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            result.append(directory)
            continue
        if not isinstance(data, dict) or not isinstance(data.get("config"), str):
            result.append(directory)
            continue
        if _key(data["config"]) == _key(config.path):
            result.append(directory)
    return result


def assert_no_recovery(config):
    pending = recovery_directories(config)
    if pending:
        raise TagRecoveryRequired("存在未完成的标签恢复，请先点击“重试恢复”：\n" + "\n".join(map(str, pending)))


class _Journal:
    def __init__(self, config, paths, *, cancel=lambda: False, progress=lambda *args: None):
        self.directory = Path(tempfile.mkdtemp(prefix=".tag-edit-recovery-", dir=config.path.parent))
        self.data = {"config": str(config.path.absolute()), "committed": False, "entries": []}
        try:
            for index, (path, aliases) in enumerate(paths):
                _checkpoint(cancel)
                raw = _bytes(Path(path))
                backup = f"{index}.bak"
                if raw is not None:
                    atomic_write_bytes(self.directory / backup, raw)
                self.data["entries"].append({"path": str(Path(path).absolute()), "aliases": aliases,
                                             "backup": backup if raw is not None else None,
                                             "before": _digest(raw), "after": None, "after_known": False,
                                             "started": False})
                progress("准备恢复快照", index + 1, len(paths))
            self.save()
        except Exception:
            self.clean()
            raise

    def save(self):
        atomic_write_bytes(self.directory / "manifest.json",
                           json.dumps(self.data, ensure_ascii=False).encode("utf-8"))

    def save_entry(self, index):
        entry = self.data["entries"][index]
        state = {key: entry[key] for key in ("started", "after", "after_known")}
        atomic_write_bytes(self.directory / f"{index}.state", json.dumps(state).encode("utf-8"))

    def start(self, index):
        entry = self.data["entries"][index]
        if _digest(_bytes(Path(entry["path"]))) != entry["before"]:
            raise ValueError(f"文件已被外部修改：{entry['path']}")
        entry["started"] = True
        entry["after"] = entry["before"]
        self.save_entry(index)

    def finish(self, index):
        self.data["entries"][index]["after"] = _digest(_bytes(Path(self.data["entries"][index]["path"])))
        self.data["entries"][index]["after_known"] = True
        self.save_entry(index)

    def clean(self):
        # Delete only files in this verified, directly-owned journal directory.
        parent = Path(self.data["config"]).resolve().parent
        if self.directory.resolve().parent != parent or not self.directory.name.startswith(".tag-edit-recovery-"):
            raise ValueError("无效恢复目录。")
        for child in self.directory.iterdir():
            child.unlink()
        self.directory.rmdir()

    def rollback(self, progress=lambda *args: None):
        failures = []
        entries = self.data["entries"]
        for index, entry in reversed(list(enumerate(entries))):
            if not entry["started"]:
                continue
            try:
                path = Path(entry["path"])
                current = _digest(_bytes(path))
                if current != entry["before"]:
                    if current != entry["after"] or not entry.get("after_known"):
                        raise ValueError("文件在写入后被外部修改，保留恢复副本以免覆盖")
                    if entry["backup"]:
                        raw = (self.directory / entry["backup"]).read_bytes()
                        if _digest(raw) != entry["before"]:
                            raise ValueError("恢复副本校验失败")
                        atomic_write_bytes(path, raw)
                    else:
                        path.unlink(missing_ok=True)
                for alias in entry["aliases"]:
                    PhotoMetaDataXMP._invalidate_metadata_cache(alias)
                entry["started"] = False
                self.save_entry(index)
            except Exception as exc:
                failures.append(f"{entry['path']}: {exc}")
            progress("恢复原标签", len(entries) - index, len(entries))
        if failures:
            raise TagRecoveryRequired("恢复未完成：\n" + "\n".join(failures) + f"\n恢复资料：{self.directory}")
        self.clean()


def recover_tag_edits(config: PhotoTagConfig, *, progress=lambda *args: None):
    for directory in recovery_directories(config):
        journal = object.__new__(_Journal)
        journal.directory = directory
        journal.data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if journal.data.get("committed"):
            journal.clean()
        else:
            for index, entry in enumerate(journal.data["entries"]):
                state = directory / f"{index}.state"
                if state.exists():
                    entry.update(json.loads(state.read_text(encoding="utf-8")))
            journal.rollback(progress)


def apply_tag_edit(plan: TagEditPlan, *, cancel=lambda: False, progress=lambda *args: None,
                   metadata=None) -> dict[str, set[str]]:
    config = PhotoTagConfig(plan.config_path)
    assert_no_recovery(config)
    if config.read_bytes() != plan.before:
        raise ValueError("标签配置已被其他程序修改，请重新加载。")
    meta = metadata or PhotoMetaDataXMP()
    snapshots = []
    for patch in plan.patches:
        _checkpoint(cancel)
        if not Path(patch.source).is_file() or _key(meta.sidecar_path_for(patch.source)) != _key(patch.sidecar):
            raise ValueError(f"照片或侧车位置发生变化：{patch.source}")
        subjects = meta.read_subjects(patch.source, strict=True)
        if any((name in subjects) != value for name, value in patch.before.items()):
            raise ValueError(f"照片标签已被外部修改：{patch.source}")
        snapshots.append(subjects)
    _checkpoint(cancel)
    journal = _Journal(config, [(p.sidecar, p.aliases) for p in plan.patches] + [(plan.config_path, [])],
                       cancel=cancel, progress=progress)
    result = {}
    try:
        for index, (patch, subjects) in enumerate(zip(plan.patches, snapshots)):
            _checkpoint(cancel)
            journal.start(index)
            # Recheck after snapshot staging so external changes cannot be folded into stale subjects.
            if meta.read_subjects(patch.source, strict=True) != subjects:
                raise ValueError(f"照片标签已被外部修改：{patch.source}")
            after = [tag for tag in subjects if patch.after.get(tag, True)]
            after.extend(tag for tag, enabled in patch.after.items() if enabled and tag not in after)
            try:
                ok = meta.write_subjects(patch.source, after)
            finally:
                journal.finish(index)
            if not ok:
                raise OSError(f"无法保存照片标签：{patch.source}")
            if set(meta.read_subjects(patch.source, strict=True)) != set(after):
                raise OSError(f"照片标签读回校验失败：{patch.source}")
            for alias in patch.aliases:
                result[alias] = set(after)
                meta._invalidate_metadata_cache(alias)
            progress("保存照片标签", index + 1, len(plan.patches))
        _checkpoint(cancel)
        config_index = len(plan.patches)
        journal.start(config_index)
        try:
            config.save_bytes(plan.after, expected=plan.before)
        finally:
            journal.finish(config_index)
        journal.data["committed"] = True
        journal.save()
    except Exception:
        journal.rollback(progress)
        raise
    # A committed journal is safe to clean on the next invocation if cleanup fails.
    try:
        journal.clean()
    except OSError:
        pass
    return result
