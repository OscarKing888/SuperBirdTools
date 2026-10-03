"""开发初始化复用 BirdStamp 的官方 YOLO 下载入口；运行时保持离线。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build_tools.viewer_bird_body import MODEL_NAME, development_model_path


def prepare_model(repo_root: Path = REPO_ROOT) -> Path:
    try:
        return development_model_path(repo_root)
    except FileNotFoundError:
        from SuperBirdStamp.scripts_dev.install_yolo11n import download_yolo11n
        target = Path(repo_root) / "SuperViewer" / "models" / MODEL_NAME
        # 已存在但不完整的权重不能由下载器的 exists 快路跳过。
        return download_yolo11n(target_path=target, force=target.exists())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="准备 SuperViewer 鸟体识别模型")
    parser.add_argument("--dry-run", action="store_true", help="只显示资源计划，不下载或写文件")
    args = parser.parse_args(argv)
    if args.dry_run:
        try:
            print(f"复用鸟体模型：{development_model_path(REPO_ROOT)}")
        except FileNotFoundError:
            print(f"将下载鸟体模型：{REPO_ROOT / 'SuperViewer' / 'models' / MODEL_NAME}")
        return 0
    print(f"鸟体模型已准备：{prepare_model()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
