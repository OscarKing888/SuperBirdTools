"""Regression coverage for an incomplete bundle masked by a local ExifTool."""
from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys

import pytest

from build_tools import macos_exiftool as packaging


ROOT = Path(__file__).resolve().parents[2]


def _runtime(path: Path, *, version: str = "13.50", library_version: str | None = None) -> Path:
    (path / "lib/Image").mkdir(parents=True)
    entry = path / "exiftool"
    entry.write_text(f"#!/usr/bin/env perl\nmy $version = '{version}';\n", encoding="utf-8")
    entry.chmod(0o755)
    (path / "lib/Image/ExifTool.pm").write_text(
        f"$VERSION = '{library_version or version}';\n", encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("missing", ["exiftool", "lib/Image/ExifTool.pm"])
def test_missing_runtime_fails_before_trying_system_tool(tmp_path, monkeypatch, missing):
    runtime = _runtime(tmp_path / "exiftool")
    (runtime / missing).unlink()
    monkeypatch.setenv("EXIFTOOL_EXE", "/opt/homebrew/bin/exiftool")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must fail before execution"))
    with pytest.raises(RuntimeError, match="Incomplete macOS ExifTool"):
        packaging.check_exiftool(runtime)


def test_mismatched_versions_fail_before_execution(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path / "exiftool", library_version="13.49")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must fail before execution"))
    with pytest.raises(RuntimeError, match="version mismatch"):
        packaging.check_exiftool(runtime)


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute permission")
def test_non_executable_entry_is_rejected(tmp_path):
    runtime = _runtime(tmp_path / "exiftool")
    (runtime / "exiftool").chmod(0o644)
    with pytest.raises(RuntimeError, match="not executable"):
        packaging.check_exiftool(runtime)


@pytest.mark.skipif(os.name == "nt", reason="macOS runtime symlink portability")
@pytest.mark.parametrize("linked", ["lib", "lib/Image/ExifTool.pm", "exiftool", "lib/Image/Extra.pm"])
def test_runtime_rejects_external_library_or_entry_symlinks(tmp_path, monkeypatch, linked):
    runtime = _runtime(tmp_path / "bundled")
    external = _runtime(tmp_path / "developer-machine")
    target = runtime / linked
    if target.is_dir():
        target.rename(runtime / "original-lib")
    elif target.exists():
        target.unlink()
    source = external / linked
    if not source.exists():
        source.write_text("external module", encoding="utf-8")
    target.symlink_to(source, target_is_directory=source.is_dir())
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must fail before execution"))
    with pytest.raises(RuntimeError, match="links outside"):
        packaging.check_exiftool(runtime)


def test_runtime_runs_only_system_perl_with_clean_environment(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path / "空 格" / "exiftool")
    for name in ("PERL5LIB", "PERLLIB", "PERL5OPT", "EXIFTOOL_EXE", "EXIFTOOL_PATH", "PYTHONPATH"):
        monkeypatch.setenv(name, "/opt/homebrew/developer-library")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command == ["/usr/bin/perl", str(runtime / "exiftool"), "-config", "", "-ver"]
        assert kwargs["cwd"] == runtime
        assert kwargs["env"]["PATH"] == packaging.SYSTEM_PATH
        assert not any(key.startswith("PERL") for key in kwargs["env"])
        assert "EXIFTOOL_EXE" not in kwargs["env"]
        assert "EXIFTOOL_PATH" not in kwargs["env"]
        assert "PYTHONPATH" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "13.50\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert packaging.check_exiftool(runtime) == "13.50"
    assert len(calls) == 1


@pytest.mark.parametrize("returncode,output", [(1, ""), (0, "13.49")])
def test_broken_or_wrong_runtime_is_fatal(tmp_path, monkeypatch, returncode, output):
    runtime = _runtime(tmp_path / "exiftool")
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, returncode, output, "Can't locate Image/ExifTool/Exif.pm"),
    )
    with pytest.raises(RuntimeError, match="validation failed|Unexpected ExifTool version"):
        packaging.check_exiftool(runtime)


def test_collection_keeps_every_runtime_file_and_relative_directory(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path / packaging.RELATIVE_TOOL)
    (runtime / "lib/Image/ExifTool").mkdir()
    (runtime / "lib/Image/ExifTool/XMP.pm").write_text("module", encoding="utf-8")
    (runtime / "LICENSE").write_text("license", encoding="utf-8")
    checked = []
    monkeypatch.setattr(packaging, "check_exiftool", lambda path: checked.append(path) or "13.50")
    collected = packaging.collect_macos_exiftool(tmp_path)
    assert checked == [runtime]
    assert len(collected) == 4
    for source, destination in collected:
        assert destination == (packaging.RELATIVE_TOOL / Path(source).parent.relative_to(runtime)).as_posix()
    assert str(runtime / "lib/Image/ExifTool/XMP.pm") in dict(collected)


def test_app_check_uses_emitted_bundle_and_isolates_user_state(tmp_path, monkeypatch):
    app = tmp_path / "产物 目录/SuperBirdStamp.app"
    runtime = _runtime(app / "Contents/Resources" / packaging.RELATIVE_TOOL)
    executable = app / "Contents/MacOS/SuperBirdStamp"
    executable.parent.mkdir(parents=True)
    executable.touch(mode=0o755)
    checked = []
    monkeypatch.setattr(packaging, "check_exiftool", lambda path: checked.append(path) or "13.50")
    working_dirs = []

    def run(command, **kwargs):
        report_path = kwargs["cwd"] / "exiftool-report.json"
        assert command == [str(executable), "--check-exiftool", "--output", str(report_path)]
        assert kwargs["env"]["PATH"] == packaging.SYSTEM_PATH
        assert kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen"
        assert kwargs["env"]["HOME"] == str(kwargs["cwd"])
        assert kwargs["cwd"].is_dir()
        working_dirs.append(kwargs["cwd"])
        report_path.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert packaging.check_app(app) == "13.50"
    assert checked == [runtime]
    assert working_dirs and not working_dirs[0].exists()


@pytest.mark.skipif(os.name == "nt", reason="macOS runtime symlink portability")
def test_app_rejects_external_runtime_directory(tmp_path):
    app = tmp_path / "SuperViewer.app"
    runtime = app / "Contents/Resources" / packaging.RELATIVE_TOOL
    runtime.parent.mkdir(parents=True)
    external = _runtime(tmp_path / "developer-machine")
    runtime.symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="outside the app bundle"):
        packaging.check_app(app)


@pytest.mark.parametrize("report", [None, '{"status":"error"}', 'not JSON'])
def test_app_requires_successful_json_report(tmp_path, monkeypatch, report):
    app = tmp_path / "SuperViewer.app"
    executable = app / "Contents/MacOS/SuperViewer"
    executable.parent.mkdir(parents=True)
    executable.touch(mode=0o755)
    monkeypatch.setattr(packaging, "check_exiftool", lambda path: "13.50")

    def run(command, **kwargs):
        if report is not None:
            Path(command[-1]).write_text(report, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="diagnostic"):
        packaging.check_app(app)


def test_cli_fails_build_when_app_diagnostic_fails(tmp_path, monkeypatch):
    def fail(app):
        raise RuntimeError("metadata/export read-back failed")

    monkeypatch.setattr(packaging, "check_app", fail)
    with pytest.raises(SystemExit) as error:
        packaging.main(["--app", str(tmp_path / "SuperViewer.app")])
    assert error.value.code == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="uses the macOS system Perl")
def test_vendored_runtime_runs_without_developer_perl_environment(monkeypatch):
    monkeypatch.setenv("PERL5LIB", "/nonexistent/developer-lib")
    monkeypatch.setenv("PERL5OPT", "-MNonexistentDeveloperModule")
    monkeypatch.setenv("EXIFTOOL_EXE", "/opt/homebrew/bin/exiftool")
    version = packaging.check_exiftool(ROOT / packaging.RELATIVE_TOOL)
    assert version == packaging._version(ROOT / packaging.RELATIVE_TOOL / "exiftool", "version")
