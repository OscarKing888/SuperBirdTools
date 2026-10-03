"""所有 Viewer spec 共用的降噪资源门禁；构建分析阶段保持离线。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from image_denoise.models import development_model_path, verify_model


def collect_viewer_denoise(repo_root: Path):
    for module in ("torch", "tifffile", "imagecodecs", "psutil"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"SuperViewer 降噪依赖缺失：{module}；请先运行 init_dev.py。")
    model = verify_model(development_model_path(Path(repo_root)))
    notices = [Path(repo_root) / "image_denoise" / name for name in ("THIRD_PARTY_LICENSE.txt", "NOTICE.txt")]
    for path in notices:
        if not path.is_file():
            raise RuntimeError(f"缺少 NAFNet 上游许可证或归属说明：{path}")
    # imagecodecs 按需加载编解码扩展，必须收集其原生扩展；Torch 用官方 hook。
    from PyInstaller.utils.hooks import collect_all
    codec_datas, codec_binaries, codec_imports = collect_all("imagecodecs")
    datas = [(str(model), "models/denoise"), *((str(path), "licenses/NAFNet") for path in notices)]
    imports = ["torch", "tifffile", "psutil", "image_denoise.engine", "image_denoise.architecture"]
    return datas + codec_datas, codec_binaries, imports + codec_imports
