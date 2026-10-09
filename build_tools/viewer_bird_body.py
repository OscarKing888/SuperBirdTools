"""离线收集 Viewer 鸟体识别模型与 Ultralytics 资源，三份 spec 共用。"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

MODEL_NAME = "yolo11n.pt"
MIN_MODEL_BYTES = 100_000
# "all" 或逗号分隔的模型名：从 workspace 额外打包所选 YOLO / SAM 模型。
# build_all.sh / build_all.bat 设置，发布构建默认不设置。
BUNDLE_MODELS_ENV = "SUPERBIRDTOOLS_BUNDLE_MODELS"


def development_model_path(repo_root: Path) -> Path:
    """Reuse the existing BirdStamp asset when Viewer has no private copy."""
    root = Path(repo_root)
    for app in ("SuperViewer", "SuperBirdStamp"):
        path = root / app / "models" / MODEL_NAME
        if path.is_file() and path.stat().st_size >= MIN_MODEL_BYTES:
            return path
    raise FileNotFoundError("SuperViewer 鸟体模型 yolo11n.pt 缺失或不完整；请先运行 init_dev.py。")


def bundled_catalog_models(repo_root: Path, mode: str | None = None) -> list:
    """离线收集选定模型；构建入口先校验 SHA-256，此处再检查文件大小。"""
    mode = os.environ.get(BUNDLE_MODELS_ENV, "") if mode is None else mode
    mode = mode.strip()
    if not mode:
        return []
    from build_tools.download_models import resolve_names

    models = resolve_names([] if mode.lower() == "all" else [mode])
    directory = Path(repo_root) / "SuperViewer" / "models"
    datas, missing = [], []
    for model in models:
        path = directory / model.name
        if path.is_file() and path.stat().st_size == model.size_bytes:
            datas.append((str(path), "models"))
        else:
            missing.append(model.name)
    if missing:
        raise FileNotFoundError(f"打包所选模型时缺少或不完整：{'、'.join(missing)}；请先运行 ./download_models.sh")
    return datas


def collect_viewer_bird_body(repo_root: Path, *, ultralytics_assets=None):
    for module in ("torch", "torchvision", "ultralytics", "cv2", "lap"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"SuperViewer 鸟体识别依赖缺失：{module}；请先运行 init_dev.py。")
    model = development_model_path(repo_root)
    if ultralytics_assets is None:
        from PyInstaller.utils.hooks import collect_all
        ultralytics_assets = collect_all("ultralytics")
    extra = [item for item in bundled_catalog_models(repo_root) if Path(item[0]).name != model.name]
    datas, binaries, imports = ultralytics_assets
    return ([(str(model), "models"), *extra, *datas], list(binaries),
            ["torch", "torchvision", "cv2", "lap", *imports])
