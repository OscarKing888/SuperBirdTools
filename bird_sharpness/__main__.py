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
    args = parser.parse_args(argv)

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
        sigma = result.head_sigma if result.head_sigma is not None else result.body_sigma
        sigma_text = "-" if sigma is None else f"{sigma:.2f}"
        score_text = "-" if result.score is None else str(result.score)
        label = verdict_label(result.verdict) or result.verdict
        extra = f" {result.error}" if result.error else ""
        print(
            f"[{index}/{total}] {os.path.basename(result.path)}  {label}  score={score_text}  "
            f"sigma={sigma_text}  eye={result.eye_visibility}  {result.elapsed_s:.1f}s{written}{extra}",
            flush=True,
        )

    analyzer = BirdSharpnessAnalyzer()
    try:
        results = analyze_paths(paths, analyzer=analyzer, on_result=report)
    finally:
        analyzer.release()
    return 0 if all(r.ok for r in results) else 3


if __name__ == "__main__":
    raise SystemExit(main())
