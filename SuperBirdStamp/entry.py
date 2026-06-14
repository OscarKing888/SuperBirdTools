# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _bootstrap_repo_root() -> Path:
    app_root = Path(__file__).resolve().parent
    repo_root = app_root.parent
    for candidate in (repo_root, app_root):
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
    return repo_root


def _repo_venv_python(repo_root: Path) -> Path | None:
    if sys.platform.startswith("win"):
        candidate = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = repo_root / ".venv" / "bin" / "python3"
    return candidate if candidate.is_file() else None


def _reexec_into_repo_venv_if_needed(repo_root: Path) -> None:
    """若当前解释器不是仓库 .venv，则自动切换（避免缺 PIL/PyQt6 等依赖）。"""
    if getattr(sys, "frozen", False):
        return
    target = _repo_venv_python(repo_root)
    if target is None:
        return
    current = Path(os.path.abspath(sys.executable))
    resolved_target = Path(os.path.abspath(str(target)))
    if current == resolved_target:
        return
    cmd = [str(resolved_target), "-m", "SuperBirdStamp.entry", *sys.argv[1:]]
    raise SystemExit(subprocess.run(cmd, check=False).returncode)


def main() -> None:
    repo_root = _bootstrap_repo_root()
    _reexec_into_repo_venv_if_needed(repo_root)
    try:
        from .main import main as run_main
    except ImportError:
        from main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
