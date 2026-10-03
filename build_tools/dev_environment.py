"""主 checkout 与临时 worktree 共用开发环境。"""
from pathlib import Path
import subprocess


def shared_venv_dir(repo_root: Path) -> Path:
    preferred = repo_root / ".venv"
    if preferred.is_dir() or not (repo_root / ".git").is_file():
        return preferred
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo_root, capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            shared = Path(result.stdout.strip()).parent / ".venv"
            if shared.is_dir():
                return shared
    except (OSError, subprocess.TimeoutExpired):
        pass
    return preferred
