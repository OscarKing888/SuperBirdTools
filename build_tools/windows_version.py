"""从统一身份配置生成 Windows EXE 的版本资源（仅构建时导入 PyInstaller）。"""
from __future__ import annotations

from app_identity import load_app_identity


def version_resource(app_id: str, metadata_path):
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    info = load_app_identity(app_id, metadata_path)
    numeric = tuple(int(part) for part in info.bundle_version.split('.')) + (0,)
    if any(part > 65535 for part in numeric):
        raise ValueError('Windows version components must not exceed 65535')
    values = {
        'FileDescription': info.app_name,
        'FileVersion': info.version,
        'ProductName': info.product_name,
        'ProductVersion': info.version,
        'InternalName': app_id,
        'OriginalFilename': f'{app_id}.exe',
    }
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric, prodvers=numeric, mask=0x3f,
                          flags=0x2 if '-' in info.version else 0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable('040904B0', [StringStruct(k, v) for k, v in values.items()])]),
              VarFileInfo([VarStruct('Translation', [1033, 1200])])],
    )
