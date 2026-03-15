from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _app_root() -> Path:
    return Path(__file__).resolve().parent


def _repo_root(app_root: Path) -> Path:
    return app_root.parent


def _is_monorepo_root(path: Path) -> bool:
    return (path / "app_common").exists() and (path / "build_all.sh").is_file()


def _selected_venv_dir(app_root: Path) -> Path:
    repo_root = _repo_root(app_root)
    if _is_monorepo_root(repo_root):
        return repo_root / ".venv"
    return app_root / ".venv"


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python3"


def _ensure_venv(app_root: Path) -> Path:
    venv_dir = _selected_venv_dir(app_root)
    python_path = _venv_python(venv_dir)
    if python_path.is_file():
        return python_path

    print(f"[SuperViewer init] creating venv: {venv_dir}")
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    if not python_path.is_file():
        raise FileNotFoundError(f"venv python not found after creation: {python_path}")
    return python_path


def _reexec_if_needed(target_python: Path) -> None:
    current_python = Path(os.path.abspath(sys.executable))
    resolved_target = Path(os.path.abspath(str(target_python)))
    if current_python == resolved_target:
        return
    cmd = [str(resolved_target), str(Path(__file__).resolve()), *sys.argv[1:]]
    raise SystemExit(subprocess.run(cmd, check=False).returncode)


def _run(cmd: list[str], *, cwd: Path, dry_run: bool) -> None:
    print("$", " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 SuperViewer 开发环境。")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的步骤，不实际执行。")
    args = parser.parse_args()

    app_root = _app_root()
    os.chdir(app_root)

    target_python = _ensure_venv(app_root)
    _reexec_if_needed(target_python)

    requirements_path = app_root / "requirements.txt"
    _run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], cwd=app_root, dry_run=args.dry_run)
    _run([sys.executable, "-m", "pip", "install", "-r", str(requirements_path)], cwd=app_root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
