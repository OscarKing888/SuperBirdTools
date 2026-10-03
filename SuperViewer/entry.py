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
    if len(sys.argv) > 1 and sys.argv[1] == "--check-bird-body":
        # 打包诊断可读已有缓存并离线推理，始终禁止写入照片侧车。
        from superviewer.bird_body import main as check_bird_body
        raise SystemExit(check_bird_body([*sys.argv[2:], "--no-write"]))
    if len(sys.argv) > 1 and sys.argv[1] == "--check-denoise":
        from superviewer.denoise_diagnostics import main as check_denoise
        raise SystemExit(check_denoise(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == '--check-video':
        # 无窗口的只读打包诊断：不会扫描目录或写入用户的缩略图缓存。
        from superviewer.video_diagnostics import main as check_video
        raise SystemExit(check_video(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--check-about":
        from about_diagnostics import main as check_about
        raise SystemExit(check_about("SuperViewer", sys.argv[2:]))
    from SuperBirdUpdater.runtime import admit_startup
    admit_startup("SuperViewer")
    try:
        from .main import main as run_main
    except ImportError:
        from main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
