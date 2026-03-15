from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _venv_python(project_root: Path) -> Path | None:
    candidates = [
        project_root / ".venv" / "bin" / "python3",
        project_root / ".venv" / "bin" / "python",
        project_root / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _ensure_project_venv(project_root: Path) -> None:
    venv_python = _venv_python(project_root)
    if venv_python is None:
        return

    current_python = Path(sys.executable).resolve(strict=False)
    target_python = venv_python.resolve(strict=False)
    if current_python == target_python:
        return

    cmd = [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.run(cmd, check=False).returncode)


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _ensure_imageio_ffmpeg() -> object:
    try:
        import imageio_ffmpeg  # type: ignore
    except ImportError:
        _run([sys.executable, "-m", "pip", "install", "--upgrade", "imageio-ffmpeg"])
        import imageio_ffmpeg  # type: ignore
    return imageio_ffmpeg


def _platform_subdir() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def _ffmpeg_executable_name() -> str:
    return "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"


def _target_path(project_root: Path) -> Path:
    return project_root / "tools" / "ffmpeg" / _platform_subdir() / _ffmpeg_executable_name()


def _user_data_root() -> Path:
    home = Path.home()
    if sys.platform.startswith("win"):
        base = (
            os.environ.get("APPDATA")
            or os.environ.get("LOCALAPPDATA")
            or str(home / "AppData" / "Roaming")
        )
        return Path(base) / "BirdStamp"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BirdStamp"
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "BirdStamp"
    return home / ".config" / "BirdStamp"


def _user_data_target_path() -> Path:
    return _user_data_root() / "tools" / "ffmpeg" / _platform_subdir() / _ffmpeg_executable_name()


def _chmod_executable(path: Path) -> None:
    if sys.platform.startswith("win"):
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_ffmpeg(source_path: Path, target_path: Path, *, force: bool) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and not force:
        print(f"目标已存在，跳过覆盖: {target_path}")
        return target_path
    shutil.copy2(source_path, target_path)
    _chmod_executable(target_path)
    print(f"已安装 ffmpeg: {target_path}")
    return target_path


def install_ffmpeg_tool(*, force: bool = False) -> list[Path]:
    project_root = _project_root()
    package = _ensure_imageio_ffmpeg()
    source_path = Path(package.get_ffmpeg_exe()).resolve(strict=False)
    if not source_path.is_file():
        raise FileNotFoundError(f"未找到 imageio-ffmpeg 提供的 ffmpeg: {source_path}")

    installed_paths: list[Path] = []
    candidates = [_target_path(project_root), _user_data_target_path()]
    seen: set[str] = set()
    for target_path in candidates:
        key = str(target_path.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        installed_paths.append(_copy_ffmpeg(source_path, target_path, force=force))
    return installed_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="将 ffmpeg 安装到项目 tools 目录。")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 ffmpeg 文件。")
    args = parser.parse_args()

    project_root = _project_root()
    os.chdir(project_root)
    _ensure_project_venv(project_root)
    install_ffmpeg_tool(force=bool(args.force))


if __name__ == "__main__":
    main()
