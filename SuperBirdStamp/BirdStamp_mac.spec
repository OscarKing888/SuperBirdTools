# -*- mode: python ; coding: utf-8 -*-
# BirdStamp macOS PyInstaller spec
# Run from project root: pyinstaller BirdStamp_mac.spec
#
# Prerequisites:
#   pip install pyinstaller pyinstaller-hooks-contrib
#
# Output: dist/SuperBirdStamp.app

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


APP_ROOT = Path(SPECPATH).resolve()
REPO_ROOT = APP_ROOT.parent
ENTRY_SCRIPT = APP_ROOT / "entry.py"
RUNTIME_HOOK = APP_ROOT / "scripts_dev" / "pyi_rthook_cwd.py"
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


block_cipher = None

# --------------------------------------------------------------------------- #
# Collect complex packages that rely on dynamic imports / native extensions
# --------------------------------------------------------------------------- #
ultralytics_datas, ultralytics_binaries, ultralytics_hiddenimports = collect_all("ultralytics")

# --------------------------------------------------------------------------- #
# Project-specific data files
# --------------------------------------------------------------------------- #
project_datas = [
    *collect_tree(APP_ROOT / "models", "models"),
    *collect_tree(APP_ROOT / "icons", "icons"),
    *collect_tree(APP_ROOT / "images", "images"),
    *collect_tree(APP_ROOT / "config", "config"),
    *collect_tree(APP_ROOT / "tools" / "ffmpeg", "tools/ffmpeg"),
    *collect_tree(APP_ROOT / "scripts_dev" / "install_ffmpeg_tool.py", "scripts_dev"),
    *collect_tree(APP_COMMON_ROOT / "about_dialog" / "about.cfg", "app_common/about_dialog"),
    *collect_tree(APP_COMMON_ROOT / "about_dialog" / "images", "app_common/about_dialog/images"),
    *collect_tree(APP_COMMON_ROOT / "exif_io" / "exiftools_mac", "app_common/exif_io/exiftools_mac"),
]

# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
a = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(APP_ROOT), str(REPO_ROOT)],
    binaries=ultralytics_binaries,
    datas=project_datas + ultralytics_datas,
    hiddenimports=[
        # PyQt6
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtPrintSupport",
        "PyQt6.QtSvg",
        # Pillow plugins that may not be auto-detected
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "PIL.ImageFilter",
        "PIL.ExifTags",
        "PIL.TiffImagePlugin",
        "PIL.JpegImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.WebPImagePlugin",
        # Optional decoders
        "rawpy",
        "pillow_heif",
        # YAML / CLI
        "yaml",
        "typer",
        "click",
    ]
    + collect_submodules("birdstamp")
    + collect_submodules("app_common")
    + ultralytics_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(RUNTIME_HOOK)],
    excludes=[
        # Keep out IPython / notebook cruft that ultralytics may pull in
        "IPython",
        "notebook",
        "nbformat",
        "matplotlib",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SuperBirdStamp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Keep False: UPX can corrupt Torch/MPS native libs
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # None = native arch; set "universal2" for fat binary
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SuperBirdStamp",
)

app = BUNDLE(
    coll,
    name="SuperBirdStamp.app",
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
    bundle_identifier="com.birdstamp.app",
    info_plist={
        "NSPrincipalClass": "NSApplication",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription": "BirdStamp does not require camera access.",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
        # Allow opening images via Finder drag-and-drop / open-with
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Image",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": [
                    "public.jpeg",
                    "public.png",
                    "public.tiff",
                    "public.heic",
                    "com.adobe.raw-image",
                ],
            }
        ],
    },
)
