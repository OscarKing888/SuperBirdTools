"""离线收集 Viewer 鸟体识别模型与 Ultralytics 资源，三份 spec 共用。"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

MODEL_NAME = "yolo11n.pt"
MIN_MODEL_BYTES = 100_000
# "all": also bundle every bird sharpness catalog model (YOLO / SAM, ~3.45 GB) from the
# workspace SuperViewer/models (download_models.sh). Set by build_all.sh --bundle-all-models,
# which build_all_no_zip.sh always passes; release builds leave it unset.
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
    """``(path, "models")`` for every catalog model when ``mode`` (default: the
    ``BUNDLE_MODELS_ENV`` variable) is ``"all"``; never downloads. Sizes are checked
    here (build_all.sh verifies SHA-256 before PyInstaller runs); a missing or
    incomplete model stops the build before Analysis."""
    mode = os.environ.get(BUNDLE_MODELS_ENV, "") if mode is None else mode
    if mode.strip().lower() != "all":
        return []
    from bird_sharpness.model_catalog import DETECTORS, SAM_MODELS

    directory = Path(repo_root) / "SuperViewer" / "models"
    datas, missing = [], []
    for model in (*DETECTORS, *SAM_MODELS):
        path = directory / model.name
        if path.is_file() and path.stat().st_size == model.size_bytes:
            datas.append((str(path), "models"))
        else:
            missing.append(model.name)
    if missing:
        raise FileNotFoundError(f"打包全部模型时缺少或不完整：{'、'.join(missing)}；请先运行 ./download_models.sh")
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
