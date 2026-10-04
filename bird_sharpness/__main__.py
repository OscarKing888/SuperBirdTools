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
    parser.add_argument("paths", nargs="+", help="图片文件或目录")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归子目录")
    parser.add_argument("--write-xmp", action="store_true", help="把结果写入同名 XMP sidecar")
    parser.add_argument("--json", action="store_true", help="逐行输出 JSON")
    parser.add_argument("--source", choices=("raw", "jpeg", "denoised"), default="raw",
                        help="测量哪种图像：raw = RAW 解码（默认，阈值按它标定）；jpeg = 相机内嵌 JPEG；"
                             "denoised = 降噪成片（image_denoise 生成的）。后两者仅供对比，不能与 --write-xmp 同用")
    parser.add_argument("--denoised-dir", default="",
                        help="--source denoised 时降噪成片所在的固定目录；默认找照片旁的 denoised 子目录")
    parser.add_argument("--trace", metavar="DIR",
                        help="导出每张照片的计算过程（各步骤 PNG + trace.json）到 DIR/<文件名>/")
    parser.add_argument(
        "-j", "--workers", type=int, default=min(6, max(1, (os.cpu_count() or 2) // 2)),
        help="并行检测的照片数（模型推理串行，解码与计算并行；默认 CPU 核数的一半，最多 6）",
    )
    args = parser.parse_args(argv)
    if args.write_xmp and args.source != "raw":
        parser.error("--write-xmp 只能与 --source raw 一起使用（XMP 里的清晰度按 RAW 解码标定）")

    from .models import check_runtime

    reason = check_runtime()
    if reason:
        print(reason, file=sys.stderr)
        return 2
    from .analyzer import BirdSharpnessAnalyzer, analyze_paths
    from .scoring import verdict_label

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

    from .image_source import source_loader

    lookup = None
    if args.source == "denoised":
        from image_denoise.preview import find_denoised_preview
        from image_denoise.types import DenoiseOptions

        options = DenoiseOptions(output_directory=args.denoised_dir)
        lookup = lambda path: find_denoised_preview(path, options)  # noqa: E731
    loader = source_loader(args.source, denoised_lookup=lookup)
    analyzer = BirdSharpnessAnalyzer()
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
    return 0 if all(r.ok for r in results) else 3


if __name__ == "__main__":
    raise SystemExit(main())
