from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from build_tools.viewer_ffmpeg import collect_viewer_ffmpeg


def test_missing_dependency_stops_packaging(monkeypatch):
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', None)
    with pytest.raises(RuntimeError, match='requirements.txt') as error:
        collect_viewer_ffmpeg()
    assert sys.executable in str(error.value)


@pytest.mark.parametrize('filename', ['ffmpeg-macos-aarch64-v7', 'ffmpeg-win64-v7.exe'])
def test_packaging_includes_executable_and_resource_package(tmp_path, monkeypatch, filename):
    (tmp_path / 'binaries').mkdir()
    binary = tmp_path / 'binaries' / filename
    binary.write_bytes(b'platform binary')
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(__file__=str(tmp_path / '__init__.py')))
    datas, imports = collect_viewer_ffmpeg()
    assert datas == [(str(binary), 'imageio_ffmpeg/binaries')]
    assert 'imageio_ffmpeg.binaries' in imports


def test_missing_wheel_binary_stops_packaging_even_if_system_ffmpeg_exists(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'imageio_ffmpeg', SimpleNamespace(__file__=str(tmp_path / '__init__.py')))
    with pytest.raises(RuntimeError, match='可执行文件'):
        collect_viewer_ffmpeg()
