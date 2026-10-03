"""离线收集 Viewer 鸟体识别模型与 Ultralytics 资源，三份 spec 共用。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODEL_NAME = "yolo11n.pt"
MIN_MODEL_BYTES = 100_000


def development_model_path(repo_root: Path) -> Path:
    """Reuse the existing BirdStamp asset when Viewer has no private copy."""
    root = Path(repo_root)
    for app in ("SuperViewer", "SuperBirdStamp"):
        path = root / app / "models" / MODEL_NAME
        if path.is_file() and path.stat().st_size >= MIN_MODEL_BYTES:
            return path
    raise FileNotFoundError("SuperViewer 鸟体模型 yolo11n.pt 缺失或不完整；请先运行 init_dev.py。")


def collect_viewer_bird_body(repo_root: Path, *, ultralytics_assets=None):
    for module in ("torch", "torchvision", "ultralytics", "cv2", "lap"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"SuperViewer 鸟体识别依赖缺失：{module}；请先运行 init_dev.py。")
    model = development_model_path(repo_root)
    if ultralytics_assets is None:
        from PyInstaller.utils.hooks import collect_all
        ultralytics_assets = collect_all("ultralytics")
    datas, binaries, imports = ultralytics_assets
    return ([(str(model), "models"), *datas], list(binaries),
            ["torch", "torchvision", "cv2", "lap", *imports])
