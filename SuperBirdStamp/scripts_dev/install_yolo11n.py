from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


MODEL_NAME = "yolo11n.pt"
ULTRALYTICS_SPEC = "ultralytics>=8.3,<9.0"


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


def _ensure_ultralytics() -> None:
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        _run([sys.executable, "-m", "pip", "install", "--upgrade", ULTRALYTICS_SPEC])


def _default_target_path(project_root: Path) -> Path:
    return project_root / "models" / MODEL_NAME


def download_yolo11n(*, target_path: Path, force: bool = False, release: str = "latest") -> Path:
    _ensure_ultralytics()

    from ultralytics.utils.downloads import attempt_download_asset

    target_path = target_path.resolve(strict=False)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        if not force:
            print(f"目标已存在，跳过下载: {target_path}")
            return target_path
        target_path.unlink()

    downloaded_path = Path(
        attempt_download_asset(
            str(target_path),
            repo="ultralytics/assets",
            release=release,
        )
    ).resolve(strict=False)

    if downloaded_path != target_path:
        raise RuntimeError(f"模型下载到了意外路径: {downloaded_path} != {target_path}")
    if not target_path.is_file():
        raise FileNotFoundError(f"下载完成后未找到模型文件: {target_path}")

    print(f"已下载模型: {target_path}")
    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 yolo11n.pt 到项目 models 目录。")
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖已存在的 yolo11n.pt。",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="自定义下载目标路径，默认是项目内 models/yolo11n.pt。",
    )
    parser.add_argument(
        "--release",
        default="latest",
        help="Ultralytics assets release，默认 latest。",
    )
    args = parser.parse_args()

    project_root = _project_root()
    os.chdir(project_root)
    _ensure_project_venv(project_root)

    target_path = args.dest or _default_target_path(project_root)
    download_yolo11n(
        target_path=Path(target_path),
        force=bool(args.force),
        release=str(args.release or "latest"),
    )


if __name__ == "__main__":
    main()
