from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from build_tools import dev_environment

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_worktree_reuses_main_environment(tmp_path, monkeypatch):
    main = tmp_path / "main checkout"
    worktree = tmp_path / "feature checkout"
    (main / ".venv").mkdir(parents=True)
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: unused", encoding="utf-8")
    monkeypatch.setattr(dev_environment.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=str(main / ".git") + "\n"))
    assert dev_environment.shared_venv_dir(worktree) == main / ".venv"
    assert not (worktree / ".venv").exists()


@pytest.mark.parametrize("script", ["init_dev.py", "SuperViewer/init_dev.py", "SuperBirdStamp/init_dev.py"])
def test_dry_run_environment_probe_never_creates_files(tmp_path, script):
    spec = importlib.util.spec_from_file_location("isolated_init", REPO_ROOT / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if script == "init_dev.py":
        module._ensure_repo_venv(tmp_path, dry_run=True)
    else:
        module._ensure_venv(tmp_path, dry_run=True)
    assert list(tmp_path.iterdir()) == []
