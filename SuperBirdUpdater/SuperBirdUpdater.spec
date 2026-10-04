# -*- mode: python ; coding: utf-8 -*-
"""独立 onedir/app；不参与两个主应用的 MERGE 依赖共享。"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))
from app_identity import load_app_identity
from build_tools.set_build_version import prepare_build_metadata

BUILD_METADATA = prepare_build_metadata(ROOT)

identity = load_app_identity("SuperViewer", BUILD_METADATA)
target_arch = os.environ.get("SUPERBIRDTOOLS_TARGET_ARCH") or None
a = Analysis(
    [str(ROOT / "SuperBirdUpdater" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[], datas=[], hiddenimports=[],
    hookspath=[], runtime_hooks=[],
    excludes=["__main__", "torch", "ultralytics", "numpy", "PIL", "cv2", "matplotlib", "PyQt5", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SuperBirdUpdater", console=False,
          debug=False, strip=False, upx=False, target_arch=target_arch, codesign_identity=None)
if sys.platform == "darwin":
    # macOS 无需中间 onedir；Windows 保留独立更新器的 COLLECT 布局。
    app = BUNDLE(exe, a.binaries, a.datas, name="SuperBirdUpdater.app", bundle_identifier="local.superbirdtools.updater",
                 info_plist={"CFBundleShortVersionString": identity.bundle_version,
                             "CFBundleVersion": identity.build_number,
                             "CFBundleDisplayName": "SuperBirdTools 更新器"})
else:
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SuperBirdUpdater")
