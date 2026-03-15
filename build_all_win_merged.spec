# -*- mode: python ; coding: utf-8 -*-
# Windows merged build: use PyInstaller MERGE so SuperViewer and SuperBirdStamp
# can share common runtime files in a single dist root.

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.building.api import MERGE
from PyInstaller.utils.hooks import collect_all, collect_submodules


REPO_ROOT = Path(SPECPATH).resolve()
SUPERVIEWER_ROOT = REPO_ROOT / "SuperViewer"
SUPERBIRDSTAMP_ROOT = REPO_ROOT / "SuperBirdStamp"
APP_COMMON_ROOT = REPO_ROOT / "app_common"

for candidate in (REPO_ROOT, SUPERVIEWER_ROOT, SUPERBIRDSTAMP_ROOT):
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


# --------------------------------------------------------------------------- #
# SuperViewer
# --------------------------------------------------------------------------- #
superviewer_datas = [
    *collect_tree(SUPERVIEWER_ROOT / "super_viewer.cfg", "."),
    *collect_tree(SUPERVIEWER_ROOT / "icons", "icons"),
    *collect_tree(APP_COMMON_ROOT / "about_dialog" / "about.cfg", "app_common/about_dialog"),
    *collect_tree(APP_COMMON_ROOT / "about_dialog" / "images", "app_common/about_dialog/images"),
    *collect_tree(APP_COMMON_ROOT / "exif_io" / "exiftools_win", "app_common/exif_io/exiftools_win"),
]

superviewer_a = Analysis(
    [str(SUPERVIEWER_ROOT / "entry.py")],
    pathex=[str(SUPERVIEWER_ROOT), str(REPO_ROOT)],
    binaries=[],
    datas=superviewer_datas,
    hiddenimports=collect_submodules("app_common"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
superviewer_pyz = PYZ(superviewer_a.pure)

# --------------------------------------------------------------------------- #
# SuperBirdStamp
# --------------------------------------------------------------------------- #
ultralytics_datas, ultralytics_binaries, ultralytics_hiddenimports = collect_all("ultralytics")
superbirdstamp_datas = [
    *collect_tree(SUPERBIRDSTAMP_ROOT / "models", "models"),
    *collect_tree(SUPERBIRDSTAMP_ROOT / "icons", "icons"),
    *collect_tree(SUPERBIRDSTAMP_ROOT / "images", "images"),
    *collect_tree(SUPERBIRDSTAMP_ROOT / "config", "config"),
    *collect_tree(SUPERBIRDSTAMP_ROOT / "tools" / "ffmpeg", "tools/ffmpeg"),
    *collect_tree(SUPERBIRDSTAMP_ROOT / "scripts_dev" / "install_ffmpeg_tool.py", "scripts_dev"),
    *collect_tree(APP_COMMON_ROOT / "about_dialog" / "about.cfg", "app_common/about_dialog"),
    *collect_tree(APP_COMMON_ROOT / "about_dialog" / "images", "app_common/about_dialog/images"),
    *collect_tree(APP_COMMON_ROOT / "exif_io" / "exiftools_win", "app_common/exif_io/exiftools_win"),
]

superbirdstamp_a = Analysis(
    [str(SUPERBIRDSTAMP_ROOT / "entry.py")],
    pathex=[str(SUPERBIRDSTAMP_ROOT), str(REPO_ROOT)],
    binaries=ultralytics_binaries,
    datas=superbirdstamp_datas + ultralytics_datas,
    hiddenimports=[
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtPrintSupport",
        "PyQt6.QtSvg",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "PIL.ImageFilter",
        "PIL.ExifTags",
        "PIL.TiffImagePlugin",
        "PIL.JpegImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.WebPImagePlugin",
        "rawpy",
        "pillow_heif",
        "yaml",
        "typer",
        "click",
        "win32timezone",
    ]
    + collect_submodules("birdstamp")
    + collect_submodules("app_common")
    + ultralytics_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SUPERBIRDSTAMP_ROOT / "scripts_dev" / "pyi_rthook_cwd.py")],
    excludes=[
        "IPython",
        "notebook",
        "nbformat",
        "matplotlib",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)
superbirdstamp_pyz = PYZ(superbirdstamp_a.pure, superbirdstamp_a.zipped_data)

# Let SuperBirdStamp reference common files from SuperViewer so that both
# directories can live side-by-side under dist/ with fewer duplicated runtime files.
MERGE(
    (superviewer_a, "superviewer", "SuperViewer/SuperViewer"),
    (superbirdstamp_a, "superbirdstamp", "SuperBirdStamp/SuperBirdStamp"),
)

superviewer_exe = EXE(
    superviewer_pyz,
    superviewer_a.scripts,
    superviewer_a.dependencies,
    [],
    exclude_binaries=True,
    name="SuperViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SUPERVIEWER_ROOT / "icons" / "app_icon.ico"),
)

superviewer_coll = COLLECT(
    superviewer_exe,
    superviewer_a.binaries,
    superviewer_a.zipfiles,
    superviewer_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SuperViewer",
)

superbirdstamp_exe = EXE(
    superbirdstamp_pyz,
    superbirdstamp_a.scripts,
    superbirdstamp_a.dependencies,
    [],
    exclude_binaries=True,
    name="SuperBirdStamp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SUPERBIRDSTAMP_ROOT / "icons" / "app_icon.ico"),
)

superbirdstamp_coll = COLLECT(
    superbirdstamp_exe,
    superbirdstamp_a.binaries,
    superbirdstamp_a.zipfiles,
    superbirdstamp_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SuperBirdStamp",
)
