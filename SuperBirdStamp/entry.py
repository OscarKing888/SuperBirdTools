# -*- coding: utf-8 -*-
from __future__ import annotations

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


def main() -> None:
    _bootstrap_repo_root()
    try:
        from .main import main as run_main
    except ImportError:
        from main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
