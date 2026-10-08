"""Exercise staging, MERGE runtime paths, and real exported metadata."""
from pathlib import Path
import runpy
import sys

import pytest

from build_tools import stage_windows_exiftool as staging


ROOT = Path(__file__).resolve().parents[2]


def test_staging_keeps_complete_tool_in_both_apps(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "exiftool_files/lib/Image").mkdir(parents=True)
    (source / "exiftool.exe").write_bytes(b"launcher")
    (source / "exiftool_files/lib/Image/ExifTool.pm").write_bytes(b"library")
    dist = tmp_path / "dist with spaces"
    for app in staging.APPS:
        (dist / app).mkdir(parents=True)
        (dist / app / f"{app}.exe").touch()
    checked = []
    monkeypatch.setattr(staging, "check_exiftool", lambda p: checked.append(p) or "13.49")
    staging.stage_exiftool(source, dist)
    for app in staging.APPS:
        target = dist / app / "_internal" / staging.RELATIVE_TOOL
        assert (target / "exiftool.exe").read_bytes() == b"launcher"
        assert (target / "exiftool_files/lib/Image/ExifTool.pm").read_bytes() == b"library"
        assert target in checked


@pytest.mark.parametrize("override", [None, "EXIFTOOL_EXE", "EXIFTOOL_PATH"])
def test_runtime_hook_uses_installed_tool_with_temporary_meipass(tmp_path, monkeypatch, override):
    hook = runpy.run_path(str(ROOT / "SuperBirdStamp/scripts_dev/pyi_rthook_cwd.py"))
    executable = tmp_path / "dist/SuperBirdStamp/SuperBirdStamp.exe"
    tool = executable.parent / "_internal" / staging.RELATIVE_TOOL / "exiftool.exe"
    tool.parent.mkdir(parents=True)
    tool.touch()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI1234"), raising=False)
    for name in ("EXIFTOOL_EXE", "EXIFTOOL_PATH"):
        monkeypatch.delenv(name, raising=False)
    if override:
        monkeypatch.setenv(override, "custom.exe")
    hook["_configure_windows_exiftool"]()
    import os
    if override:
        assert os.environ[override] == "custom.exe"
        if override == "EXIFTOOL_PATH":
            assert "EXIFTOOL_EXE" not in os.environ
    else:
        assert os.environ["EXIFTOOL_EXE"] == str(tool)


@pytest.mark.skipif(sys.platform != "win32", reason="bundled Windows executable")
def test_staged_exiftool_exports_chinese_metadata(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    for app in staging.APPS:
        (dist / app).mkdir(parents=True)
        (dist / app / f"{app}.exe").touch()
    staging.stage_exiftool(ROOT / staging.RELATIVE_TOOL, dist)
    executable = dist / "SuperBirdStamp/_internal" / staging.RELATIVE_TOOL / "exiftool.exe"
    monkeypatch.setenv("EXIFTOOL_EXE", str(executable))
    from birdstamp.runtime_diagnostics import check_export
    assert check_export(tmp_path)["jpeg_png_export"] == "ok"
