"""无系统 ExifTool 的打包诊断：真实导出、严格路径与启动边界。"""
from __future__ import annotations

import builtins
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import exiftool_diagnostics as diagnostics


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mac_bundle(tmp_path, monkeypatch):
    from app_common.exif_io import exiftool_path, exiftool_runner

    bundle = tmp_path / "测试应用.app"
    executable = bundle / "Contents" / "MacOS" / "SuperViewer"
    executable.parent.mkdir(parents=True)
    executable.touch()
    module = bundle / "Contents" / "Resources" / "app_common" / "exif_io"
    tool = module / "exiftools_mac" / "exiftool"
    library = tool.parent / "lib" / "Image" / "ExifTool.pm"
    library.parent.mkdir(parents=True)
    library.write_text("# test library", encoding="utf-8")
    tool.touch()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(exiftool_path, "__file__", str(module / "exiftool_path.py"))
    monkeypatch.setattr(exiftool_path, "_is_usable_exiftool", lambda path: Path(path).is_file())
    monkeypatch.setattr(exiftool_runner, "run_exiftool_once", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="13.50\n", stderr=""))
    monkeypatch.delenv("EXIFTOOL_EXE", raising=False)
    monkeypatch.delenv("EXIFTOOL_PATH", raising=False)
    return bundle, tool, library


def test_mac_bundle_accepts_own_real_resolver_result(mac_bundle):
    bundle, tool, _ = mac_bundle
    report = diagnostics.check_exiftool()
    assert report["exiftool"] == str(tool)
    assert report["bundle"] == str(bundle)
    assert report["require_bundled"] is True


@pytest.mark.parametrize("variable", ["EXIFTOOL_EXE", "EXIFTOOL_PATH"])
def test_mac_bundle_rejects_external_environment_override(mac_bundle, tmp_path, monkeypatch, variable):
    external = tmp_path / "external-exiftool"
    external.touch()
    monkeypatch.setenv(variable, str(external))
    with pytest.raises(RuntimeError, match="outside bundled module"):
        diagnostics.check_exiftool()
    assert os.environ[variable] == str(external)


def test_mac_bundle_rejects_system_fallback(mac_bundle, tmp_path, monkeypatch):
    from app_common.exif_io import exiftool_path

    _, tool, _ = mac_bundle
    tool.unlink()
    external = tmp_path / "homebrew" / "bin" / "exiftool"
    external.parent.mkdir(parents=True)
    external.touch()
    monkeypatch.setattr(exiftool_path, "_absolute_exiftool_candidates", lambda: [str(external)])
    with pytest.raises(RuntimeError, match="outside bundled module"):
        diagnostics.check_exiftool()


def test_mac_bundle_rejects_missing_tool(mac_bundle, monkeypatch):
    from app_common.exif_io import exiftool_path

    _, tool, _ = mac_bundle
    tool.unlink()
    monkeypatch.setattr(exiftool_path, "_absolute_exiftool_candidates", lambda: [])
    monkeypatch.setattr(exiftool_path.shutil, "which", lambda *_: None)
    with pytest.raises(RuntimeError, match="ExifTool unavailable"):
        diagnostics.check_exiftool()


def test_mac_bundle_rejects_missing_library_even_if_perl_environment_masks_it(mac_bundle, monkeypatch):
    _, _, library = mac_bundle
    library.unlink()
    monkeypatch.setenv("PERL5LIB", "/external/perl")
    with pytest.raises(RuntimeError, match="library missing"):
        diagnostics.check_exiftool()
    assert os.environ["PERL5LIB"] == "/external/perl"


def test_mac_bundle_rejects_tool_symlink_escaping_app(mac_bundle, tmp_path):
    _, tool, _ = mac_bundle
    external = tmp_path / "external-exiftool"
    external.touch()
    tool.unlink()
    tool.symlink_to(external)
    with pytest.raises(RuntimeError, match="not contained in the running app"):
        diagnostics.check_exiftool()


@pytest.mark.parametrize("relative", ["lib", "lib/Image", "lib/Image/ExifTool.pm"])
def test_mac_bundle_rejects_library_symlinks_outside_runtime(mac_bundle, tmp_path, relative):
    _, tool, _ = mac_bundle
    linked = tool.parent / relative
    external = tmp_path / "external-library"
    linked.rename(external)
    linked.symlink_to(external, target_is_directory=external.is_dir())
    with pytest.raises(RuntimeError, match="library escapes its runtime"):
        diagnostics.check_exiftool()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Perl environment isolation")
def test_real_check_rejects_incomplete_library_masked_by_perl_environment(tmp_path, monkeypatch):
    from app_common.exif_io import exiftool_path

    source = ROOT / "app_common/exif_io/exiftools_mac"
    bundle = tmp_path / "Incomplete.app"
    module = bundle / "Contents/Resources/app_common/exif_io"
    runtime = module / "exiftools_mac"
    (runtime / "lib/Image").mkdir(parents=True)
    shutil.copy2(source / "exiftool", runtime / "exiftool")
    shutil.copy2(source / "lib/Image/ExifTool.pm", runtime / "lib/Image/ExifTool.pm")
    monkeypatch.setattr(exiftool_path, "__file__", str(module / "exiftool_path.py"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "Contents/MacOS/SuperViewer"))
    monkeypatch.setenv("PERL5LIB", str(source / "lib"))
    monkeypatch.setenv("PERL5OPT", "-MImage::ExifTool")
    monkeypatch.delenv("EXIFTOOL_EXE", raising=False)
    monkeypatch.delenv("EXIFTOOL_PATH", raising=False)
    # 模拟被环境污染的已缓存 resolver 命中；诊断仍须独立失败。
    assert exiftool_path.get_exiftool_executable_path() == str(runtime / "exiftool")
    with pytest.raises(RuntimeError, match="ExifTool could not run"):
        diagnostics.check_exiftool()
    assert os.environ["PERL5OPT"] == "-MImage::ExifTool"


def test_diagnostic_isolates_perl_path_home_and_restores_after_failure(tmp_path, monkeypatch):
    from app_common import exif_io

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PERL5LIB", "external-perl")
    monkeypatch.setenv("PERL5OPT", "-MImage::ExifTool")
    monkeypatch.setenv("PATH", "original-path")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EXIFTOOL_EXE", "external-exiftool")
    monkeypatch.setattr(exif_io, "close_exiftool_process", lambda: None)

    def verify_environment():
        assert not any(name.startswith("PERL") for name in os.environ)
        assert os.environ["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        assert os.environ["HOME"] != str(tmp_path)
        assert os.environ["EXIFTOOL_EXE"] == "external-exiftool"

    def check(**_kwargs):
        verify_environment()
        return {"exiftool": "test-tool"}

    def export(_directory):
        verify_environment()
        raise RuntimeError("test failed export")

    monkeypatch.setattr(diagnostics, "check_exiftool", check)
    assert diagnostics.main("TestApp", [], check_export=export) == 1
    assert os.environ["PERL5LIB"] == "external-perl"
    assert os.environ["PERL5OPT"] == "-MImage::ExifTool"
    assert os.environ["PATH"] == "original-path"
    assert os.environ["HOME"] == str(tmp_path)


@pytest.mark.parametrize("failure", ["resolver", "export"])
def test_failure_returns_json_and_closes_persistent_process(tmp_path, monkeypatch, failure):
    from app_common import exif_io

    closed = []
    monkeypatch.setattr(exif_io, "close_exiftool_process", lambda: closed.append(True))

    def check(**_kwargs):
        if failure == "resolver":
            raise RuntimeError("test unavailable")
        return {"exiftool": "test-tool"}

    temporary = []

    def export(directory):
        temporary.append(directory)
        raise RuntimeError("test failed export")

    monkeypatch.setattr(diagnostics, "check_exiftool", check)
    output = tmp_path / "report.json"
    assert diagnostics.main("TestApp", ["--output", str(output)], check_export=export) == 1
    assert closed == [True]
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "error" and "RuntimeError" in report["error"]
    assert all(not path.exists() for path in temporary)


@pytest.mark.parametrize("app_id", ["SuperViewer", "SuperBirdStamp"])
def test_entry_dispatch_exits_before_gui_or_workspace_imports(app_id, monkeypatch):
    monkeypatch.setattr(sys, "path", sys.path.copy())
    # 直接加载打包脚本，避免 SuperViewer 的兼容 __init__ 导入主窗口。
    spec = importlib.util.spec_from_file_location(f"_diagnostic_{app_id}_entry", ROOT / app_id / "entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    if app_id == "SuperBirdStamp":
        monkeypatch.setattr(entry, "_reexec_into_repo_venv_if_needed", lambda *_: None)
    monkeypatch.setattr(sys, "argv", ["app", "--check-exiftool", "--output", "test.json"])
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in {"main", "SuperViewer.main", "SuperBirdStamp.main", "SuperBirdUpdater.runtime"} or name.startswith("birdstamp.gui"):
            pytest.fail(f"Diagnostic imported GUI/startup module {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    calls = []

    def fake_main(actual_app, argv, *, check_export):
        calls.append((actual_app, argv, callable(check_export)))
        return 23

    monkeypatch.setattr(diagnostics, "main", fake_main)
    with pytest.raises(SystemExit) as exited:
        entry.main()
    assert exited.value.code == 23
    assert calls == [(app_id, ["--output", "test.json"], True)]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS bundled Perl ExifTool portability check")
@pytest.mark.parametrize("app_id", ["SuperViewer", "SuperBirdStamp"])
def test_real_chinese_roundtrip_through_entry_without_system_exiftool(app_id, tmp_path):
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("PERL") and name not in {"EXIFTOOL_EXE", "EXIFTOOL_PATH"}}
    environment.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    output = tmp_path / "中文诊断.json"
    result = subprocess.run([sys.executable, str(ROOT / app_id / "entry.py"), "--check-exiftool", "--output", str(output)],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["require_bundled"] is True
    assert Path(report["exiftool"]) == (ROOT / "app_common/exif_io/exiftools_mac/exiftool").resolve()
    if app_id == "SuperViewer":
        assert report["chinese_xmp"] == report["png_export"] == "ok"
        assert report["source_unchanged"] is True
    else:
        assert report["chinese_exif"] == report["sidecar_xmp"] == report["jpeg_png_export"] == "ok"
