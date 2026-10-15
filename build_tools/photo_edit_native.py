"""Build/check the checkout-local extension and collect it with its notices."""
from __future__ import annotations
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tomllib


def _command_text(command: list[str]) -> str:
    if sys.platform == "win32":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)
    return shlex.join(command)


def native_build_environment(repo_root: Path) -> dict[str, str]:
    """检查固定的构建依赖，只为编译子进程选择 Rust，不改全局 PATH 或安装软件。"""
    native = repo_root / "app_common" / "native" / "photo_edit"
    project = tomllib.loads((native / "bridge" / "pyproject.toml").read_text("utf-8"))
    requirement = next(item for item in project["build-system"]["requires"] if item.startswith("maturin=="))
    expected_maturin = requirement.split("==", 1)[1]
    try:
        installed_maturin = importlib.metadata.version("maturin")
    except importlib.metadata.PackageNotFoundError:
        installed_maturin = "未安装"
    if installed_maturin != expected_maturin:
        requirements = repo_root / "build_tools" / "requirements-photo-edit.txt"
        command = _command_text([sys.executable, "-m", "pip", "install", "-r", str(requirements)])
        raise RuntimeError(
            f"照片编辑原生构建需要 {requirement}，当前为 {installed_maturin}。\n"
            f"请使用构建解释器安装依赖：\n{command}"
        )

    toolchain = tomllib.loads((native / "rust-toolchain.toml").read_text("utf-8"))["toolchain"]["channel"]
    env = os.environ.copy()
    rustup = shutil.which("rustup")
    if rustup is None:
        cargo_home = Path(env.get("CARGO_HOME") or Path.home() / ".cargo")
        candidate = cargo_home / "bin" / ("rustup.exe" if os.name == "nt" else "rustup")
        if candidate.is_file():
            rustup = str(candidate)
    if rustup is None:
        raise RuntimeError(
            f"照片编辑原生构建需要 Rust {toolchain}。请先从 https://rustup.rs 安装 rustup，"
            f"再运行：\nrustup toolchain install {toolchain} --profile minimal"
        )
    # rustup which 不自动下载；直接使用指定工具链的 bin，避开 PATH 中旧版 Homebrew Rust。
    result = subprocess.run(
        [rustup, "which", "--toolchain", toolchain, "cargo"],
        capture_output=True, text=True, encoding="utf-8", env=env, check=False,
    )
    if result.returncode or not result.stdout.strip():
        command = _command_text([rustup, "toolchain", "install", toolchain, "--profile", "minimal"])
        raise RuntimeError(
            f"照片编辑原生构建缺少 Rust {toolchain} 工具链，请运行：\n"
            f"{command}\n{result.stderr.strip()}"
        )
    toolchain_bin = str(Path(result.stdout.strip()).parent)
    env["PATH"] = toolchain_bin + os.pathsep + env.get("PATH", "")
    env["RUSTUP_TOOLCHAIN"] = toolchain
    return env


def collect_photo_edit(repo_root):
    root = Path(repo_root)/"app_common"
    native = root/"native/photo_edit"
    spec = importlib.util.spec_from_file_location("photo_edit_build",native/"tools/build_native.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    binary = root/"photo_edit"/("_native"+sysconfig.get_config_var("EXT_SUFFIX"))
    try:
        stamp = json.loads(binary.with_name("_native.build.json").read_text("utf-8"))
        ready = (stamp["release"] and stamp["fingerprint"]==builder.build_fingerprint() and
                 stamp["notices"]==hashlib.sha256(binary.with_name("_native.notices.txt").read_bytes()).hexdigest() and
                 stamp["binary"]==hashlib.sha256(binary.read_bytes()).hexdigest())
    except (OSError,ValueError,KeyError):
        ready = False
    # Verify the immutable snapshot even on an incremental build.
    builder.prepare()
    if not ready:
        env = native_build_environment(Path(repo_root))
        subprocess.run([sys.executable,str(native/"tools/build_native.py")],check=True,env=env)
    notices = [native/"upstream"/name for name in
               ("LICENSE-MIT","LICENSE-APACHE","NOTICE","ATTRIBUTION.md")]
    notices.append(native/"upstream.json")
    notices.append(binary.with_name("_native.notices.txt"))
    return ([(str(p),"licenses/PhotoCraft") for p in notices],
            [(str(binary),"app_common/photo_edit")],["app_common.photo_edit._native"])
