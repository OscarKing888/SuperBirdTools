from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from SuperBirdUpdater.build import generate
from SuperBirdUpdater.common import APPS


def identity(number: int):
    return {"commit": str(number) * 40, "version": str(number) * 8,
            "revision": number, "app_common_commit": "a" * 40}


@pytest.fixture
def versions(tmp_path):
    old_root = tmp_path / "已安装 suite"
    new_root = tmp_path / "新构建"
    for app in APPS:
        directory = old_root / app
        directory.mkdir(parents=True)
        (directory / "program.bin").write_bytes(b"unchanged " * 300)
        (directory / app).write_bytes(b"test executable")
    (old_root / "SuperViewer" / "super_viewer.cfg").write_text("old default", encoding="utf-8")
    (old_root / "SuperViewer" / "removed.bin").write_bytes(b"obsolete")
    old_assets = tmp_path / "old-assets"
    current = generate(old_root, old_assets, identity(1), "linux", "x86_64", workers=2)
    shutil.copytree(old_root, new_root)
    (new_root / "SuperViewer" / "program.bin").write_bytes(b"new program " * 500)
    (new_root / "SuperViewer" / "removed.bin").unlink()
    (new_root / "SuperViewer" / "中文资源.txt").write_text("中文正常", encoding="utf-8")
    (new_root / "SuperViewer" / "super_viewer.cfg").write_text("new default", encoding="utf-8")
    assets = tmp_path / "assets"
    candidate = generate(new_root, assets, identity(2), "linux", "x86_64", workers=2)
    return old_root, new_root, assets, current, candidate
