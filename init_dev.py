from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python3"


def _ensure_repo_venv(repo_root: Path) -> Path:
    venv_dir = repo_root / ".venv"
    python_path = _venv_python(venv_dir)
    if python_path.is_file():
        return python_path

    print(f"[init_dev] creating venv: {venv_dir}")
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


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _app_init_scripts(repo_root: Path) -> list[Path]:
    return [
        repo_root / "SuperViewer" / "init_dev.py",
        repo_root / "SuperBirdStamp" / "init_dev.py",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 SuperBirdTools 开发环境。")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的步骤，不实际执行。")
    args = parser.parse_args()

    repo_root = _repo_root()
    os.chdir(repo_root)

    repo_python = _ensure_repo_venv(repo_root)
    _reexec_if_needed(repo_python)

    scripts = _app_init_scripts(repo_root)
    for script_path in scripts:
        cmd = [sys.executable, str(script_path)]
        if args.dry_run:
            cmd.append("--dry-run")
        _run(cmd, cwd=repo_root)


if __name__ == "__main__":
    main()
