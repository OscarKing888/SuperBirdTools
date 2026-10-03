"""命令行入口：python -m image_denoise [--recursive] PATH...。"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import signal
import sys
import threading

from .batch import collect_image_paths, run_batch, validate_options
from .types import DenoiseCancelled, DenoiseOptions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="image_denoise", description="NAFNet RGB 批量降噪；保留原图，默认输出 16 位 TIFF")
    parser.add_argument("paths", nargs="+", help="照片或目录")
    parser.add_argument("-r", "--recursive", action="store_true", help="处理子目录（排除降噪输出目录）")
    parser.add_argument("-o", "--output-directory", "--output-dir", default="", help="固定输出目录；默认每张源照片目录下的子目录")
    parser.add_argument("--subdir", default="denoised", help="源照片目录中的输出子目录名")
    parser.add_argument("--format", choices=("tiff", "jpeg"), default="tiff")
    parser.add_argument("--strength", type=int, default=100, help="降噪强度 0–100")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("-j", "--workers", type=int, default=2, help="并行照片数 1–4，仍受内存预算限制")
    parser.add_argument("--json", action="store_true", help="每张照片输出一行 JSON")
    args = parser.parse_args(argv)
    options = DenoiseOptions(output_mode="fixed" if args.output_directory else "source_subdir",
                             subdir=args.subdir, output_directory=args.output_directory,
                             format=args.format, strength=args.strength, device=args.device,
                             workers=args.workers)
    stop = threading.Event()
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGINT, lambda *_args: stop.set())

    def report(result):
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
        else:
            print(f"{result.status}: {result.source} → {result.destination}" +
                  (f"：{result.error}" if result.error else ""), flush=True)

    try:
        validate_options(options)
        paths = collect_image_paths(args.paths, recursive=args.recursive, options=options,
                                    cancelled=stop.is_set)
        if not paths:
            print("没有找到可降噪的照片。", file=sys.stderr)
            return 1
        results = run_batch(paths, options, cancelled=stop.is_set, on_result=report)
        if stop.is_set():
            return 130
        return 1 if any(r.status == "failed" for r in results) else 0
    except DenoiseCancelled:
        return 130
    except Exception as exc:
        print(f"降噪失败：{exc}", file=sys.stderr)
        return 2
    finally:
        if previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
