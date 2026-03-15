# -*- mode: python ; coding: utf-8 -*-
# macOS 打包用：SuperViewer 位于子模块目录，app_common 位于仓库根目录平级共享

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


APP_ROOT = Path(SPECPATH).resolve()
REPO_ROOT = APP_ROOT.parent
ENTRY_SCRIPT = APP_ROOT / "entry.py"
ICON_PATH = APP_ROOT / "icons" / "app_icon.icns"
APP_COMMON_ROOT = REPO_ROOT / "app_common"

for candidate in (REPO_ROOT, APP_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)


def collect_tree(source: Path, dest: str) -> list[tuple[str, str]]:
    if not source.exists():
        return []
    if source.is_file():
        return [(str(source), dest)]

    items: list[tuple[str, str]] = []
    dest_root = Path(dest)
    for child in source.rglob("*"):
        if not child.is_file():
            continue
        relative_parent = child.parent.relative_to(source)
        target_dir = dest_root / relative_parent
        items.append((str(child), str(target_dir).replace("\\", "/")))
    return items


datas: list[tuple[str, str]] = []
datas.extend(collect_tree(APP_ROOT / "super_viewer.cfg", "."))
datas.extend(collect_tree(APP_ROOT / "icons", "icons"))
datas.extend(collect_tree(APP_COMMON_ROOT / "about_dialog" / "about.cfg", "app_common/about_dialog"))
datas.extend(collect_tree(APP_COMMON_ROOT / "about_dialog" / "images", "app_common/about_dialog/images"))
datas.extend(collect_tree(APP_COMMON_ROOT / "exif_io" / "exiftools_mac", "app_common/exif_io/exiftools_mac"))


a = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(APP_ROOT), str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("app_common"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SuperViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ICON_PATH)] if ICON_PATH.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SuperViewer",
)
app = BUNDLE(
    coll,
    name="SuperViewer.app",
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
    bundle_identifier=None,
)
