from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import threading

import pytest

from SuperBirdUpdater.common import INSTALLED_MANIFEST, Cancelled, UpdateError, hash_file
from SuperBirdUpdater.download import prepare, staged_path
from SuperBirdUpdater.install import install, journal_path, recover
from SuperBirdUpdater.manifest import load, matches
from SuperBirdUpdater.sources import LocalSource


def ready(versions, tmp_path):
    root, _, assets, current, candidate = versions
    cache = tmp_path / "cache"
    source = LocalSource(assets, "linux", "x86_64")
    changes = prepare(root, candidate, source, cache)
    return root, cache, current, candidate, changes


def test_only_changed_files_and_success_preserves_user_data(versions, tmp_path):
    root, _, _, current, candidate = versions
    (root / "SuperViewer/super_viewer.cfg").write_text("user preferences")
    (root / "SuperBirdStamp/my-photo.jpg").write_bytes(b"user data")
    root, cache, current, candidate, changes = ready(versions, tmp_path)
    names = {e["path"] for e in changes if e["kind"] == "file"}
    assert names == {"SuperViewer/program.bin", "SuperViewer/中文资源.txt"}
    install(root, current, candidate, cache)
    assert load(root / INSTALLED_MANIFEST) == candidate
    assert not (root / "SuperViewer/removed.bin").exists()
    assert (root / "SuperViewer/super_viewer.cfg").read_text() == "user preferences"
    assert (root / "SuperBirdStamp/my-photo.jpg").read_bytes() == b"user data"
    assert not journal_path(root).exists()


def test_cached_files_are_reused_and_revalidated(versions, tmp_path, monkeypatch):
    root, cache, _, candidate, changes = ready(versions, tmp_path)
    source = LocalSource(versions[2], "linux", "x86_64")
    def no_network(*args, **kwargs):
        raise AssertionError("should reuse verified completed files")
    monkeypatch.setattr(source, "chunks", no_network)
    prepare(root, candidate, source, cache)
    file = next(e for e in changes if e["kind"] == "file")
    staged_path(cache, file).write_bytes(b"tampered")
    with pytest.raises(AssertionError):
        prepare(root, candidate, source, cache)


@pytest.mark.parametrize("failure_at", [0, 1, 2, 3])
def test_failure_rolls_back_every_file_and_manifest(versions, tmp_path, failure_at):
    root, cache, current, candidate, _ = ready(versions, tmp_path)
    def fault(index, operation):
        if index == failure_at:
            raise OSError("injected write failure")
    with pytest.raises(OSError):
        install(root, current, candidate, cache, fault=fault)
    assert load(root / INSTALLED_MANIFEST) == current
    assert all(matches(root, e) for e in current["files"])
    assert not (root / "SuperViewer/中文资源.txt").exists()


class PowerLoss(BaseException):
    pass


def test_interrupted_transaction_recovers_on_next_start(versions, tmp_path):
    root, cache, current, candidate, _ = ready(versions, tmp_path)
    def crash(index, _):
        if index == 1:
            raise PowerLoss()
    with pytest.raises(PowerLoss):
        install(root, current, candidate, cache, fault=crash)
    assert journal_path(root).exists()
    assert recover(root)
    assert not recover(root)
    assert all(matches(root, e) for e in current["files"])


def test_failed_recovery_keeps_backup_for_retry(versions, tmp_path, monkeypatch):
    from SuperBirdUpdater import install as module
    root, cache, current, candidate, _ = ready(versions, tmp_path)
    def crash(*_):
        raise PowerLoss()
    with pytest.raises(PowerLoss):
        install(root, current, candidate, cache, fault=crash)
    original = module._replace_copy
    monkeypatch.setattr(module, "_replace_copy", lambda *_: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(UpdateError, match="保留恢复文件"):
        recover(root)
    assert journal_path(root).exists()
    monkeypatch.setattr(module, "_replace_copy", original)
    recover(root)
    assert all(matches(root, e) for e in current["files"])


def test_tampering_disk_full_and_unknown_collision_abort_before_mutation(versions, tmp_path, monkeypatch):
    root, cache, current, candidate, changes = ready(versions, tmp_path)
    entry = next(e for e in changes if e["kind"] == "file")
    path = staged_path(cache, entry)
    saved = path.read_bytes()
    path.write_bytes(b"bad")
    with pytest.raises(UpdateError, match="校验失败"):
        install(root, current, candidate, cache)
    assert not journal_path(root).exists()
    path.write_bytes(saved)
    (root / "SuperViewer/中文资源.txt").write_text("user file")
    with pytest.raises(UpdateError, match="用户文件重名"):
        install(root, current, candidate, cache)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(1, 1, 0))
    with pytest.raises(UpdateError, match="空间不足"):
        install(root, current, candidate, cache)


def test_cancellation_does_not_modify_installation(versions, tmp_path):
    root, _, assets, current, candidate = versions
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        prepare(root, candidate, LocalSource(assets, "linux", "x86_64"), tmp_path / "cache", cancel=cancel)
    assert all(matches(root, e) for e in current["files"])


@pytest.mark.skipif(os.name == "nt", reason="symlink and hardlink test")
def test_existing_hardlinks_are_not_modified_in_place(versions, tmp_path):
    root, cache, current, candidate, _ = ready(versions, tmp_path)
    original = root / "SuperViewer/program.bin"
    alias = root / "SuperViewer/user-hardlink"
    alias.hardlink_to(original)
    old = alias.read_bytes()
    install(root, current, candidate, cache)
    assert alias.read_bytes() == old
    assert original.read_bytes() != old
