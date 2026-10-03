"""无窗口、无用户配置写入的打包诊断：离线加载真实权重并完成一次推理。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def diagnose(device: str = "cpu") -> dict:
    import numpy as np
    import torch
    import imagecodecs
    import tifffile
    import psutil
    from image_denoise.engine import DenoiseEngine
    from image_denoise.models import MODEL_SHA256, resolve_model_path

    torch.set_num_threads(1)
    model_path = resolve_model_path()
    engine = DenoiseEngine(device=device)
    try:
        engine.load()
        source = np.random.default_rng(42).uniform(0.1, 0.9, (32, 33, 3)).astype(np.float32)
        output, actual_device, tile_size = engine.denoise(source)
        if output.shape != source.shape or output.dtype != np.float32 or not np.isfinite(output).all():
            raise RuntimeError("NAFNet 推理输出的形状、类型或有限数值检查失败")
        if np.allclose(output, source, atol=1e-6):
            raise RuntimeError("NAFNet 推理没有产生降噪输出")
        # 同时检查关键原生扩展；仅 import 包无法发现延迟加载失败。
        profile = imagecodecs.cms_profile("srgb")
        transformed = imagecodecs.cms_transform(
            np.zeros((2, 2, 3), dtype=np.uint16), profile, profile,
            colorspace="RGB", outcolorspace="RGB", outdtype=np.uint16,
        )
        if transformed.shape != (2, 2, 3):
            raise RuntimeError("高位深色彩转换检查失败")
        import io
        buffer = io.BytesIO()
        tifffile.imwrite(buffer, transformed, photometric="rgb", compression="deflate", predictor=2)
        buffer.seek(0)
        if tifffile.imread(buffer).dtype != np.uint16:
            raise RuntimeError("16 位 TIFF 编解码检查失败")
        return {
            "ok": True, "model": str(model_path), "model_sha256": MODEL_SHA256,
            "device": actual_device, "tile_size": tile_size, "shape": list(output.shape),
            "max_change": float(np.abs(output - source).max()),
            "memory_total": psutil.virtual_memory().total,
            "versions": {module.__name__: module.__version__ for module in (torch, np, tifffile, imagecodecs, psutil)},
        }
    finally:
        engine.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="离线检查 NAFNet 模型、推理和高位深导出运行库")
    parser.add_argument("--device", choices=("cpu", "auto", "cuda", "mps"), default="cpu")
    parser.add_argument("--output", type=Path, help="UTF-8 JSON 诊断结果，适用于 Windows windowed EXE")
    args = parser.parse_args(argv)
    try:
        result = diagnose(args.device)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if result["ok"] else 1
