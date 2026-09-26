# -*- coding: utf-8 -*-
"""SuperViewer / SuperBirdStamp 图像热路径离线基准（M0-1）。

直接调用现有生产函数，逐样本记录分段耗时、ExifTool 进程启动次数与峰值 RSS。
不修改任何生产代码；样本一律先复制到临时目录，只在副本上运行。

用法（仓库根目录，使用 repo 根 .venv）：
    macOS:   QT_QPA_PLATFORM=offscreen .venv/bin/python3 benchmark/bench_imaging.py
    Windows: $env:QT_QPA_PLATFORM='offscreen'; .\\.venv\\Scripts\\python.exe benchmark\\bench_imaging.py

详见 benchmark/README.md。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gc
import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
for _extra in (REPO_ROOT, REPO_ROOT / "SuperBirdStamp"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from benchmark.env_report import collect_environment, file_sha256  # noqa: E402

_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
_JPEG_EXTS = {".jpg", ".jpeg"}
_HEIF_EXTS = {".heic", ".heif", ".hif"}


# ── 采样与计数 ────────────────────────────────────────────────────────────


class _RssSampler:
    """后台线程按固定间隔采样整个进程 RSS（覆盖原生分配），记录峰值。"""

    def __init__(self, interval_s: float = 0.002) -> None:
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak = 0
        try:
            import psutil

            self._proc = psutil.Process()
        except Exception:
            self._proc = None

    @property
    def available(self) -> bool:
        return self._proc is not None

    def current(self) -> int:
        return int(self._proc.memory_info().rss) if self._proc is not None else 0

    def __enter__(self) -> "_RssSampler":
        if self._proc is None:
            return self
        self.peak = self.current()

        def _run() -> None:
            while not self._stop.is_set():
                rss = self.current()
                if rss > self.peak:
                    self.peak = rss
                self._stop.wait(self._interval)

        self._thread = threading.Thread(target=_run, name="bench-rss", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            rss = self.current()
            if rss > self.peak:
                self.peak = rss


class _ExiftoolCounter:
    """包装 run_exiftool_once，统计一次性 ExifTool 进程启动次数与耗时。"""

    def __init__(self) -> None:
        self.calls = 0
        self.elapsed_ms = 0.0
        self._original: Callable[..., Any] | None = None

    def install(self) -> None:
        from app_common.exif_io import exiftool_runner

        original = exiftool_runner.run_exiftool_once
        self._original = original
        counter = self

        def _counted(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                counter.calls += 1
                counter.elapsed_ms += (time.perf_counter() - start) * 1000.0

        exiftool_runner.run_exiftool_once = _counted

    def reset(self) -> None:
        self.calls = 0
        self.elapsed_ms = 0.0


# ── 用例定义 ──────────────────────────────────────────────────────────────


@dataclass
class Case:
    name: str
    description: str
    applies: Callable[[Path], bool]
    run: Callable[[Path], Any]
    heavy: bool = False  # 例如 RAW 全尺寸解马赛克：重复次数减半


@dataclass
class Sample:
    sample_id: str
    path: Path
    category: str
    sha256: str
    status: str = "ok"


@dataclass
class CaseResult:
    case: str
    sample_id: str
    timings_ms: list[float] = field(default_factory=list)
    exiftool_calls: list[int] = field(default_factory=list)
    exiftool_ms: list[float] = field(default_factory=list)
    peak_rss_delta_bytes: list[int] = field(default_factory=list)
    output: str = ""
    error: str = ""


def _is_raw(path: Path) -> bool:
    from app_common.image_formats import RAW_EXTENSIONS

    return path.suffix.lower() in RAW_EXTENSIONS


def _is_supported(path: Path) -> bool:
    from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS

    return path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def _describe(result: Any) -> str:
    if result is None:
        return "None"
    if isinstance(result, (bytes, bytearray)):
        return f"bytes[{len(result)}]"
    if isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], (bytes, bytearray)):
        return f"rgb {result[1]}x{result[2]}"
    width = getattr(result, "width", None)
    height = getattr(result, "height", None)
    if callable(width) and callable(height):  # QImage / QPixmap
        null = getattr(result, "isNull", lambda: False)()
        return f"{type(result).__name__} {width()}x{height()}{' null' if null else ''}"
    if width is not None and height is not None:  # PIL
        return f"{type(result).__name__} {width}x{height} {getattr(result, 'mode', '')}".strip()
    return type(result).__name__


def build_cases() -> list[Case]:
    from app_common import thumb_stream
    from birdstamp.decoders import image_decoder
    from birdstamp.gui.editor_utils import pil_to_qpixmap
    from SuperViewer.superviewer import focus_preview_loader, preview_panel
    from SuperViewer.superviewer.qt_compat import QPixmap

    def _viewer_full_then_pixmap(path: Path) -> Any:
        # 等价于 PreviewPanel.set_image 同步完整路径：解码 + GUI 线程 QPixmap.fromImage
        qimg = preview_panel._load_full_preview_qimage(str(path))
        return QPixmap.fromImage(qimg) if qimg is not None else None

    def _viewer_raw_set_image_sync(path: Path) -> Any:
        # 当前 set_image 对 RAW 在 GUI 线程执行的同步段
        qimg = preview_panel._load_raw_embedded_preview_qimage(str(path))
        return QPixmap.fromImage(qimg) if qimg is not None else None

    def _birdstamp_preview_to_pixmap(path: Path) -> Any:
        image = image_decoder.decode_image_for_preview(path, max_long_edge=2048)
        return pil_to_qpixmap(image, max_pixels=1920 * 1080 * 2)

    cases = [
        Case(
            "viewer.raw.get_raw_preview_jpeg",
            "thumb_stream.get_raw_preview_jpeg：提取 RAW 内嵌 JPEG 字节（ExifTool 逐 tag 新进程）",
            _is_raw,
            lambda p: thumb_stream.get_raw_preview_jpeg(str(p)),
        ),
        Case(
            "viewer.raw.orientation_read",
            "focus_preview_loader._get_orientation_from_file：RAW 内嵌预览方向读取",
            _is_raw,
            lambda p: focus_preview_loader._get_orientation_from_file(str(p)),
        ),
        Case(
            "viewer.raw.set_image_sync_segment",
            "当前 RAW 点击在 GUI 线程的同步段：内嵌提取 + 方向 + 解码 + QImage + QPixmap",
            _is_raw,
            _viewer_raw_set_image_sync,
        ),
        Case(
            "viewer.full_preview_qimage",
            "preview_panel._load_full_preview_qimage：完整预览解码（非 RAW ≤40MP 时在 GUI 线程执行）",
            lambda p: not _is_raw(p),
            lambda p: preview_panel._load_full_preview_qimage(str(p)),
        ),
        Case(
            "viewer.full_preview_to_pixmap",
            "完整预览解码 + QPixmap.fromImage（同步完整路径总成本）",
            lambda p: not _is_raw(p),
            _viewer_full_then_pixmap,
        ),
        Case(
            "viewer.quick_preview_pixmap_512",
            "preview_panel._load_quick_preview_pixmap(512)：缓存未命中时的快速预览兜底",
            lambda p: p.suffix.lower() not in _HEIF_EXTS,
            lambda p: preview_panel._load_quick_preview_pixmap(str(p), 512),
        ),
    ]
    for size in (128, 512, 2048):
        cases.append(
            Case(
                f"thumb.load_thumbnail_rgb_{size}",
                f"thumb_stream.load_thumbnail_rgb({size})：缩略图解码（池线程）",
                _is_supported,
                lambda p, s=size: thumb_stream.load_thumbnail_rgb(str(p), s),
            )
        )
    cases.extend(
        [
            Case(
                "birdstamp.decode_image_for_preview_2048",
                "image_decoder.decode_image_for_preview(2048)：BirdStamp 预览解码（QThread）",
                _is_supported,
                lambda p: image_decoder.decode_image_for_preview(p, max_long_edge=2048),
            ),
            Case(
                "birdstamp.preview_to_qpixmap",
                "预览解码 + pil_to_qpixmap（像素预算 1920x1080x2）",
                _is_supported,
                _birdstamp_preview_to_pixmap,
            ),
            Case(
                "birdstamp.decode_image_full",
                "image_decoder.decode_image：导出用全尺寸解码（RAW 为 rawpy 全尺寸解马赛克）",
                _is_supported,
                lambda p: image_decoder.decode_image(p),
                heavy=True,
            ),
        ]
    )
    return cases


# ── 样本 ──────────────────────────────────────────────────────────────────


def _category_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _JPEG_EXTS:
        return "jpeg"
    if ext in _HEIF_EXTS:
        return "heif"
    if _is_raw(path):
        return "raw"
    return ext.lstrip(".") or "unknown"


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(_LFS_POINTER_PREFIX)) == _LFS_POINTER_PREFIX
    except OSError:
        return False


def discover_samples(sample_dir: Path) -> list[Sample]:
    samples: list[Sample] = []
    for path in sorted(sample_dir.iterdir()):
        if not path.is_file() or not _is_supported(path):
            continue
        sample = Sample(
            sample_id=path.name,
            path=path,
            category=_category_for(path),
            sha256=file_sha256(path),
        )
        if _is_lfs_pointer(path):
            sample.status = "lfs-pointer（未拉取 LFS 对象，未覆盖）"
        samples.append(sample)
    return samples


# ── 执行 ──────────────────────────────────────────────────────────────────


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def run_case(
    case: Case,
    sample: Sample,
    *,
    repeat: int,
    warmup: int,
    cold_copy: bool,
    work_dir: Path,
    counter: _ExiftoolCounter,
    sampler_factory: Callable[[], _RssSampler],
) -> CaseResult:
    result = CaseResult(case=case.name, sample_id=sample.sample_id)
    iterations = max(1, repeat // 2) if case.heavy else repeat
    shared_copy = work_dir / "warm" / sample.path.name
    if not shared_copy.exists():
        shared_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sample.path, shared_copy)

    for index in range(warmup + iterations):
        if cold_copy:
            # 每轮复制到新目录：新路径使按路径键控的应用缓存全部失效（OS 页缓存不受控，见 README）
            target_dir = Path(tempfile.mkdtemp(prefix="cold-", dir=work_dir))
            target = target_dir / sample.path.name
            shutil.copy2(sample.path, target)
        else:
            target_dir = None
            target = shared_copy
        gc.collect()
        counter.reset()
        sampler = sampler_factory()
        baseline_rss = sampler.current()
        output: Any = None
        error = ""
        with sampler:
            start = time.perf_counter()
            try:
                output = case.run(target)
            except Exception as exc:  # 记录而非中断：损坏样本等是被测行为的一部分
                error = f"{type(exc).__name__}: {exc}"
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        if index >= warmup:
            result.timings_ms.append(elapsed_ms)
            result.exiftool_calls.append(counter.calls)
            result.exiftool_ms.append(counter.elapsed_ms)
            if sampler.available:
                result.peak_rss_delta_bytes.append(max(0, sampler.peak - baseline_rss))
            result.output = _describe(output)
            result.error = error
        del output
        if target_dir is not None:
            shutil.rmtree(target_dir, ignore_errors=True)
    return result


def summarize(result: CaseResult) -> dict[str, Any]:
    timings = result.timings_ms
    return {
        "case": result.case,
        "sample_id": result.sample_id,
        "n": len(timings),
        "p50_ms": round(_percentile(timings, 50), 2),
        "p95_ms": round(_percentile(timings, 95), 2),
        "min_ms": round(min(timings), 2) if timings else None,
        "max_ms": round(max(timings), 2) if timings else None,
        "stdev_ms": round(statistics.pstdev(timings), 2) if len(timings) > 1 else 0.0,
        "exiftool_calls_per_run": max(result.exiftool_calls) if result.exiftool_calls else 0,
        "exiftool_ms_p50": round(_percentile(result.exiftool_ms, 50), 2) if result.exiftool_ms else 0.0,
        "peak_rss_delta_mb_max": (
            round(max(result.peak_rss_delta_bytes) / (1024 * 1024), 1) if result.peak_rss_delta_bytes else None
        ),
        "output": result.output,
        "error": result.error,
        "timings_ms": [round(value, 2) for value in timings],
    }


def render_markdown(report: dict[str, Any]) -> str:
    env = report["environment"]
    lines = [
        f"# 图像热路径基准 {report['started_at']}",
        "",
        f"- 平台：{env['platform']['system']} {env['platform']['release']} {env['platform']['machine']}，"
        f"CPU：{env['platform']['cpu_model']}（逻辑 {env['platform']['logical_cpus']}）",
        f"- Python：{env['python']['version'].split()[0]}；Qt：{env['native_libraries'].get('qt')}；"
        f"Pillow：{env['packages'].get('Pillow')}；rawpy/LibRaw：{env['packages'].get('rawpy')}/"
        f"{env['native_libraries'].get('libraw')}；pillow-heif/libheif：{env['packages'].get('pillow-heif')}/"
        f"{env['native_libraries'].get('libheif')}；ExifTool：{env['exiftool'].get('version')}",
        f"- 模式：{'cold-copy（每轮新路径）' if report['config']['cold_copy'] else 'warm（同一副本）'}，"
        f"预热 {report['config']['warmup']}，重复 {report['config']['repeat']}（heavy 用例减半）",
        f"- 合成样本：{'是（不作为基线）' if report['config']['synthetic'] else '否'}",
        "",
        "## 样本",
        "",
        "| 样本 | 类别 | 状态 | SHA-256 |",
        "| --- | --- | --- | --- |",
    ]
    for sample in report["samples"]:
        lines.append(f"| {sample['sample_id']} | {sample['category']} | {sample['status']} | `{sample['sha256'][:16]}…` |")
    lines += [
        "",
        "## 结果",
        "",
        "| 用例 | 样本 | n | P50 ms | P95 ms | max ms | ExifTool 进程/次 | ExifTool ms P50 | 峰值 RSS 增量 MB | 输出 | 错误 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["results"]:
        lines.append(
            f"| {row['case']} | {row['sample_id']} | {row['n']} | {row['p50_ms']} | {row['p95_ms']} | "
            f"{row['max_ms']} | {row['exiftool_calls_per_run']} | {row['exiftool_ms_p50']} | "
            f"{row['peak_rss_delta_mb_max']} | {row['output']} | {row['error']} |"
        )
    if report.get("uncovered"):
        lines += ["", "## 未覆盖", ""] + [f"- {item}" for item in report["uncovered"]]
    lines += ["", "## 样本完整性", "", f"- 运行前后样本哈希一致：{report['integrity_ok']}", ""]
    return "\n".join(lines)


def _make_synthetic_samples(target: Path) -> None:
    """生成合成样本，仅用于验证脚本可运行（不作为基线）。"""
    from PIL import Image

    target.mkdir(parents=True, exist_ok=True)
    gradient = Image.linear_gradient("L").resize((6000, 4000))
    rgb = Image.merge("RGB", (gradient, gradient.rotate(90, expand=False), gradient.transpose(Image.Transpose.FLIP_LEFT_RIGHT)))
    for orientation in (1, 6):
        exif = Image.Exif()
        exif[0x0112] = orientation
        rgb.save(target / f"synthetic_24mp_o{orientation}.jpg", quality=92, exif=exif)
    rgb.convert("RGBA").resize((3000, 2000)).save(target / "synthetic_rgba_6mp.png")
    data = (target / "synthetic_24mp_o1.jpg").read_bytes()
    (target / "synthetic_truncated_50pct.jpg").write_bytes(data[: len(data) // 2])


# ── 入口 ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=Path, default=Path(__file__).resolve().parent, help="样本目录（默认 benchmark/）")
    parser.add_argument("--synthetic", action="store_true", help="改用临时生成的合成样本（仅验证脚本，不作为基线）")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--cold-copy", action="store_true", help="每轮把样本复制到新目录，使按路径的应用缓存失效")
    parser.add_argument("--cases", default="", help="逗号分隔的用例名前缀过滤，例如 viewer.raw,thumb")
    parser.add_argument("--out", type=Path, default=None, help="结果输出目录（默认系统临时目录）")
    args = parser.parse_args(argv)

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # QPixmap 需要 GUI 应用；保持强引用

    work_dir = Path(tempfile.mkdtemp(prefix="sbt-bench-"))
    out_dir = args.out or Path(tempfile.gettempdir()) / "sbt-bench-results"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sample_dir = args.samples
        if args.synthetic:
            sample_dir = work_dir / "synthetic"
            _make_synthetic_samples(sample_dir)
        samples = discover_samples(sample_dir)
        hashes_before = {s.sample_id: s.sha256 for s in samples}

        counter = _ExiftoolCounter()
        counter.install()
        prefixes = [item.strip() for item in args.cases.split(",") if item.strip()]
        cases = [c for c in build_cases() if not prefixes or any(c.name.startswith(p) for p in prefixes)]

        results: list[dict[str, Any]] = []
        started = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        for sample in samples:
            if sample.status != "ok":
                continue
            for case in cases:
                if not case.applies(sample.path):
                    continue
                print(f"[bench] {case.name} :: {sample.sample_id}", flush=True)
                case_result = run_case(
                    case,
                    sample,
                    repeat=args.repeat,
                    warmup=args.warmup,
                    cold_copy=args.cold_copy,
                    work_dir=work_dir,
                    counter=counter,
                    sampler_factory=_RssSampler,
                )
                results.append(summarize(case_result))

        hashes_after = {s.sample_id: file_sha256(s.path) for s in samples}
        categories = {s.category for s in samples if s.status == "ok"}
        uncovered = [f"{s.sample_id}: {s.status}" for s in samples if s.status != "ok"]
        for wanted in ("jpeg", "raw", "heif", "png", "tif", "psd"):
            if wanted not in categories:
                uncovered.append(f"类别 {wanted}: 无样本")
        report = {
            "started_at": started,
            "config": {
                "samples": str(sample_dir),
                "synthetic": bool(args.synthetic),
                "repeat": args.repeat,
                "warmup": args.warmup,
                "cold_copy": bool(args.cold_copy),
                "cases": [c.name for c in cases],
            },
            "environment": collect_environment(),
            "samples": [
                {"sample_id": s.sample_id, "category": s.category, "sha256": s.sha256, "status": s.status}
                for s in samples
            ],
            "results": results,
            "uncovered": uncovered,
            "integrity_ok": hashes_before == hashes_after,
        }
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        json_path = out_dir / f"bench-{stamp}.json"
        md_path = out_dir / f"bench-{stamp}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(f"[bench] JSON: {json_path}\n[bench] Markdown: {md_path}", flush=True)
        return 0 if report["integrity_ok"] else 2
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        del app


if __name__ == "__main__":
    sys.exit(main())
