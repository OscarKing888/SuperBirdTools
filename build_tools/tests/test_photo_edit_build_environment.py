"""原生构建预检：缺失依赖有修复命令，固定工具链不受系统旧 Rust 干扰。"""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from build_tools import photo_edit_native as build


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "照片 checkout"
    native = root / "app_common/native/photo_edit"
    (native / "bridge").mkdir(parents=True)
    (native / "bridge/pyproject.toml").write_text('[build-system]\nrequires = ["maturin==1.9.6"]\n', encoding="utf-8")
    (native / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.95.0"\n', encoding="utf-8")
    monkeypatch.setattr(build.importlib.metadata, "version", lambda name: "1.9.6")
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "cargo"))
    return root


@pytest.mark.parametrize("installed", [None, "1.8.0"])
def test_maturin_missing_or_wrong_version_explains_exact_interpreter(repo, monkeypatch, installed):
    def version(name):
        if installed is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed
    monkeypatch.setattr(build.importlib.metadata, "version", version)
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: pytest.fail("must fail before compiling"))
    with pytest.raises(RuntimeError) as error:
        build.native_build_environment(repo)
    assert "maturin==1.9.6" in str(error.value)
    assert sys.executable in str(error.value)
    assert str(repo / "build_tools/requirements-photo-edit.txt") in str(error.value)


def test_missing_rustup_has_install_instructions(repo, monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="rustup toolchain install 1.95.0"):
        build.native_build_environment(repo)


def test_missing_toolchain_does_not_download_implicitly(repo, monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda name: "rustup")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="toolchain missing")
    monkeypatch.setattr(build.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="1.95.0"):
        build.native_build_environment(repo)
    assert calls == [["rustup", "which", "--toolchain", "1.95.0", "cargo"]]


@pytest.mark.parametrize("in_path", [True, False])
def test_pinned_toolchain_wins_without_changing_process_environment(repo, monkeypatch, in_path):
    rustup = Path(os.environ["CARGO_HOME"]) / "bin" / ("rustup.exe" if os.name == "nt" else "rustup")
    rustup.parent.mkdir(parents=True)
    rustup.touch()
    monkeypatch.setattr(build.shutil, "which", lambda name: str(rustup) if in_path else None)
    monkeypatch.setenv("PATH", "old-rust")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "stable")
    cargo = repo / "toolchain/bin" / ("cargo.exe" if os.name == "nt" else "cargo")
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=str(cargo) + "\n"))
    env = build.native_build_environment(repo)
    assert env["PATH"] == str(cargo.parent) + os.pathsep + "old-rust"
    assert env["RUSTUP_TOOLCHAIN"] == "1.95.0"
    assert os.environ["PATH"] == "old-rust"
    assert os.environ["RUSTUP_TOOLCHAIN"] == "stable"


@pytest.mark.parametrize("cached", [False, True])
def test_packaging_checks_dependencies_only_when_native_build_is_needed(repo, monkeypatch, cached):
    native = repo / "app_common/native/photo_edit"
    (native / "tools").mkdir()
    (native / "tools/build_native.py").write_text(
        'def build_fingerprint(): return "fingerprint"\ndef prepare(): pass\n', encoding="utf-8")
    binary = repo / "app_common/photo_edit" / ("_native" + build.sysconfig.get_config_var("EXT_SUFFIX"))
    binary.parent.mkdir()
    binary.write_bytes(b"native binary")
    notices = binary.with_name("_native.notices.txt")
    notices.write_text("license", encoding="utf-8")
    if cached:
        binary.with_name("_native.build.json").write_text(json.dumps({
            "release": True, "fingerprint": "fingerprint",
            "binary": build.hashlib.sha256(binary.read_bytes()).hexdigest(),
            "notices": build.hashlib.sha256(notices.read_bytes()).hexdigest(),
        }), encoding="utf-8")
    checks, calls = [], []
    env = {"PATH": "fixed-toolchain"}
    monkeypatch.setattr(build, "native_build_environment", lambda root: checks.append(root) or env)
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    datas, binaries, imports = build.collect_photo_edit(repo)
    assert checks == ([] if cached else [repo])
    if not cached:
        assert calls == [(([sys.executable, str(native / "tools/build_native.py")],), {"check": True, "env": env})]
    else:
        assert not calls
    assert binaries == [(str(binary), "app_common/photo_edit")]
    assert str(notices) in [source for source, _ in datas]
    assert imports == ["app_common.photo_edit._native"]


def test_init_dry_run_includes_native_requirements_and_both_apps():
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "init_dev.py"), "--dry-run", "--skip-assets"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "requirements-photo-edit.txt" in result.stdout
    assert "SuperViewer" in result.stdout
    assert "SuperBirdStamp" in result.stdout
    assert "Successfully installed" not in result.stdout


def test_repair_command_is_pasteable_in_powershell(monkeypatch):
    monkeypatch.setattr(build.sys, "platform", "win32")
    assert build._command_text(["C:/鸟类照片/O'Brien/python.exe", "-m", "pip"]) == "& 'C:/鸟类照片/O''Brien/python.exe' '-m' 'pip'"
