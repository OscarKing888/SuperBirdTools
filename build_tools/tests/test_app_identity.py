from __future__ import annotations

import json
from pathlib import Path

import pytest

import app_identity
from app_identity import load_app_identity


def test_frozen_identity_uses_bundle_not_working_directory(monkeypatch, tmp_path):
    source = app_identity.metadata_path()
    raw = json.loads(source.read_text(encoding='utf-8'))
    raw['version'] = '2.3.4-rc.1'
    raw['apps']['SuperViewer']['product_name'] = '中文测试应用'
    raw['apps']['SuperViewer']['window_title'] = '{product_name} / {version}'
    (tmp_path / 'app_metadata.json').write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(app_identity.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app_identity.sys, '_MEIPASS', str(tmp_path), raising=False)
    info = load_app_identity('SuperViewer')
    assert info.window_title() == '中文测试应用 / 2.3.4-rc.1'
    assert info.bundle_version == '2.3.4'
    assert info.about_info({'version': 'old', 'app_name': 'old'}) == {
        'version': info.version, 'app_name': info.app_name,
    }


def test_bad_identity_reports_config_location(tmp_path):
    path = tmp_path / 'app_metadata.json'
    path.write_text('{"version":', encoding='utf-8')
    with pytest.raises(ValueError, match='app_metadata.json.*line 1 column'):
        load_app_identity('SuperViewer', path)


def test_windows_version_resource_round_trips_chinese_names(monkeypatch, tmp_path):
    import sys
    import types
    import importlib.util
    import PyInstaller.compat
    from build_tools.set_build_version import apply_build_version
    from build_tools.windows_version import version_resource

    # macOS 只验证真实资源序列化器；PE 文件读取/写入的 Windows API 不在此执行。
    if importlib.util.find_spec('pefile') is None:
        monkeypatch.setitem(sys.modules, 'pefile', types.ModuleType('pefile'))
    if not hasattr(PyInstaller.compat, 'win32api'):
        monkeypatch.setattr(PyInstaller.compat, 'win32api', None, raising=False)
    from PyInstaller.utils.win32 import versioninfo
    from PyInstaller.utils.win32.versioninfo import VSVersionInfo
    if sys.platform != 'win32':
        import struct
        # Windows 的 unsigned long 为 32 位；macOS LP64 必须显式采用 Windows ABI。
        monkeypatch.setattr(versioninfo, 'struct', types.SimpleNamespace(
            pack=lambda fmt, *values: struct.pack('<' + fmt, *values),
            unpack=lambda fmt, data: struct.unpack('<' + fmt, data),
        ))
    source = app_identity.metadata_path()
    path = tmp_path / 'app_metadata.json'
    path.write_bytes(source.read_bytes())
    apply_build_version(tmp_path, '2.4.6-rc.1+ci.7', build_number='7')
    resource = version_resource('SuperViewer', path)
    parsed = VSVersionInfo()
    parsed.fromRaw(resource.toRaw())
    content = str(parsed)
    assert '极速鸟瞰' in content
    assert '2.4.6-rc.1+ci.7' in content
    assert 'filevers=(2, 4, 6, 0)' in content
    assert 'SuperViewer.exe' in content
