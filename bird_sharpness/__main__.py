"""Command line entry: ``python -m bird_sharpness [--write-xmp] [--recursive] PATH...``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, List


def _bootstrap_repo_root() -> None:
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def collect_image_paths(inputs: Iterable[str], *, recursive: bool) -> List[str]:
    from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS

    found: List[str] = []
    for item in inputs:
        path = Path(item)
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                found.append(str(path))
            continue
        if not path.is_dir():
            continue
        if recursive:
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    if not name.startswith(".") and Path(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                        found.append(os.path.join(dirpath, name))
        else:
            for child in sorted(path.iterdir()):
                if child.is_file() and not child.name.startswith(".") and child.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    found.append(str(child))
    return found


def main(argv: List[str] | None = None) -> int:
    _bootstrap_repo_root()
    parser = argparse.ArgumentParser(prog="bird_sharpness", description="检测照片中鸟的清晰度（头部模糊半径）")
    parser.add_argument("paths", nargs="*", help="图片文件或目录")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归子目录")
    parser.add_argument("--write-xmp", action="store_true", help="把结果写入同名 XMP sidecar")
    parser.add_argument("--json", action="store_true", help="逐行输出 JSON")
    parser.add_argument("--source", choices=("raw", "jpeg", "denoised"), default="raw",
                        help="测量哪种图像：raw = RAW 解码（默认，阈值按它标定）；jpeg = 相机内嵌 JPEG；"
                             "denoised = 降噪成片（image_denoise 生成的）。后两者的算法版本带 -jpeg / -denoised 后缀")
    parser.add_argument("--denoised-dir", default="",
                        help="--source denoised 时降噪成片所在的固定目录；默认找照片旁的 denoised 子目录")
    parser.add_argument("--trace", metavar="DIR",
                        help="导出每张照片的计算过程（各步骤 PNG + trace.json）到 DIR/<文件名>/")
    parser.add_argument("--max-birds", type=int, default=0, metavar="N",
                        help="每张最多测量的鸟数（0 = 不限制；限制时压在相机焦点框上的鸟优先）")
    parser.add_argument("--edge-estimator", choices=("standard", "dense"), default="standard",
                        help="边缘统计方式：standard = 最强 30 条边缘的中位数（默认）；"
                             "dense = 至少 60 条边缘的第 40 百分位（小鸟更稳）")
    parser.add_argument("--detector", default="auto",
                        help="鸟体识别模型文件名（如 yolo11x-seg.pt、yolo26l.pt；默认 auto = 内置选择）；见 --list-models")
    parser.add_argument("--sam", default="", metavar="MODEL",
                        help="用 SAM 模型精修鸟体像素（如 sam2.1_t.pt；默认不精修）")
    parser.add_argument("--sam-scope", choices=("all", "rechecked"), default="all",
                        help="SAM 精修范围：all = YOLO 检测到的每只鸟都经 SAM 抠一次（默认），rechecked = 只有复检/增强找到的鸟（更快）")
    parser.add_argument("--timing", action="store_true",
                        help="每张照片显示分阶段耗时，结束时在 stderr 汇总总计 / 平均 / 各阶段累计（--json 的每行始终含 stage_s）")
    parser.add_argument("--min-bird-side", type=int, default=0, metavar="PX",
                        help="忽略鸟框长边小于 PX（全分辨率像素）的鸟，如 64；默认 0 = 不忽略")
    parser.add_argument("--pixels", choices=("outline", "box"), default="outline",
                        help="测哪些像素：outline = 分割/SAM 轮廓内（默认，无轮廓时为鸟框内核）；box = 整个鸟框内核，忽略轮廓，不做 SAM 精修")
    parser.add_argument("--grey-fill", action="store_true",
                        help="先把每只鸟裁切里鸟以外的像素涂成灰色 114（与模型链抠图一致）再定位鸟眼、测边缘")
    parser.add_argument("--enhanced", choices=("off", "manual", "nobird"), default="off",
                        help="增强找鸟（没找到鸟时放大窗口再找）：off（默认）/ manual 仅手动对焦 / nobird 所有无鸟照片")
    parser.add_argument("--enh-region-percent", type=int, default=70, help="增强找鸟：中心区域每边占画幅百分比（默认 70）")
    parser.add_argument("--enh-grid", type=int, default=2, help="增强找鸟：N×N 个重叠窗口（默认 2）")
    parser.add_argument("--enh-imgsz", type=int, default=1024, help="增强找鸟：网络输入尺寸（默认 1024）")
    parser.add_argument("--enh-min-conf", type=float, default=0.5, help="增强找鸟：采纳门槛（默认 0.5）")
    parser.add_argument("--no-enh-lift", action="store_true", help="增强找鸟：画面暗时不提亮")
    parser.add_argument("--list-models", action="store_true", help="列出可选模型及是否已安装，然后退出")
    parser.add_argument("--download-model", action="append", default=[], metavar="MODEL",
                        help="下载模型到用户模型目录后退出（可重复）")
    parser.add_argument("--full-tile", type=int, default=1024,
                        help="无鸟、无焦点时全图的分块边长（px，默认 1024）")
    parser.add_argument("--no-mf-center", action="store_true",
                        help="手动对焦、无鸟、无焦点时也用全图分块（默认只测画面中心的最清晰分块）")
    parser.add_argument("--mf-center-percent", type=int, default=50,
                        help="手动对焦：中心区域每边占画幅的百分比（默认 50）")
    parser.add_argument("--mf-tile", type=int, default=256, help="手动对焦：中心分块边长（px，默认 256）")
    parser.add_argument("--mf-sharpest-percent", type=int, default=10,
                        help="手动对焦：取最清晰的分块占有效分块的百分比（默认 10，至少 3 块）")
    parser.add_argument(
        "-j", "--workers", type=int, default=min(6, max(1, (os.cpu_count() or 2) // 2)),
        help="并行检测的照片数（模型推理串行，解码与计算并行；默认 CPU 核数的一半，最多 6）",
    )
    args = parser.parse_args(argv)
    if args.list_models or args.download_model:
        from . import model_catalog

        if args.list_models:
            for model in (*model_catalog.DETECTORS, *model_catalog.SAM_MODELS):
                where = model_catalog.locate(model.name)
                print(f"{model.name:18s} {model.label:28s} {where or '未安装'}")
            print(f"下载目录：{model_catalog.user_model_dir()}")
        for name in args.download_model:
            print(f"下载 {name} …", flush=True)
            print(model_catalog.download(name))
        return 0
    if not args.paths:
        parser.error("请指定图片或目录")

    from .models import check_runtime

    reason = check_runtime()
    if reason:
        print(reason, file=sys.stderr)
        return 2
    import time

    from .analyzer import BirdSharpnessAnalyzer, analyze_paths
    from .scoring import verdict_label
    from .timing import stage_summary, stats_of_results

    paths = collect_image_paths(args.paths, recursive=args.recursive)
    if not paths:
        print("没有找到支持的图片", file=sys.stderr)
        return 1
    writer = None
    if args.write_xmp:
        from .xmp_store import write_result

        writer = write_result

    def report(index: int, total: int, result) -> None:
        written = ""
        if writer is not None and result.ok:
            written = " xmp=ok" if writer(result.path, result) else " xmp=FAILED"
        if args.json:
            row = result.to_dict()
            row["xmp_written"] = written.strip()
            print(json.dumps(row, ensure_ascii=False), flush=True)
            return
        sigma = result.sigma
        sigma_text = "-" if sigma is None else f"{sigma:.2f}"
        score_text = "-" if result.score is None else str(result.score)
        label = verdict_label(result.verdict) or result.verdict
        extra = f" {result.error}" if result.error else ""
        print(
            f"[{index}/{total}] {os.path.basename(result.path)}  {label}  score={score_text}  "
            f"sigma={sigma_text}  region={result.region or '-'}  birds={result.bird_count}  eye={result.eye_visibility}  {result.elapsed_s:.1f}s{written}{extra}",
            flush=True,
        )
        if args.timing and result.stage_s:
            print(f"    {stage_summary(result.stage_s, result.elapsed_s)}", flush=True)

    from .image_source import source_loader

    lookup = None
    if args.source == "denoised":
        from image_denoise.preview import find_denoised_preview
        from image_denoise.types import DenoiseOptions

        options = DenoiseOptions(output_directory=args.denoised_dir)
        lookup = lambda path: find_denoised_preview(path, options)  # noqa: E731
    loader = source_loader(args.source, denoised_lookup=lookup)
    from .metrics import TileOptions

    tiles = TileOptions(args.full_tile, not args.no_mf_center, args.mf_center_percent, args.mf_tile,
                        args.mf_sharpest_percent).normalized()
    from .params import AnalysisParams, EnhancedSearch

    params = AnalysisParams(max(0, args.max_birds), args.edge_estimator, args.detector, args.sam, args.sam_scope,
                            EnhancedSearch(args.enhanced, args.enh_region_percent, args.enh_grid, args.enh_imgsz,
                                           int(round(args.enh_min_conf * 100)), not args.no_enh_lift),
                            tiles, args.pixels, bool(args.grey_fill), max(0, args.min_bird_side),
                            args.source).normalized()
    from .models import BirdSharpnessModelError, find_model

    for name in (params.detector if params.detector != "auto" else "", params.sam_model):
        if name and find_model((name,)) is None:
            print(f"找不到模型 {name}：先用 --download-model {name} 下载", file=sys.stderr)
            return 2
    analyzer = BirdSharpnessAnalyzer(params=params)
    wall_start = time.perf_counter()
    try:
        if args.trace:
            from .trace import AnalysisTracer

            results = []
            for index, path in enumerate(paths, start=1):
                tracer = AnalysisTracer()
                result = analyzer.analyze(path, tracer=tracer, image_loader=loader)
                results.append(result)
                if tracer.trace is not None:
                    out_dir = os.path.join(args.trace, os.path.splitext(os.path.basename(path))[0])
                    tracer.trace.export(out_dir)
                report(index, len(paths), result)
        else:
            results = analyze_paths(paths, analyzer=analyzer, on_result=report, workers=max(1, args.workers),
                                    image_loader=loader)
    finally:
        analyzer.release()
    if args.timing and results:  # stderr: stdout stays one JSON object per line with --json
        for line in stats_of_results(results).lines(time.perf_counter() - wall_start):
            print(f"[计时] {line}", file=sys.stderr, flush=True)
    return 0 if all(r.ok for r in results) else 3


if __name__ == "__main__":
    raise SystemExit(main())
