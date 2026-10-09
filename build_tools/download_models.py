"""Prefetch every model into the workspace so builds, packaging and local runs need no download.

Downloads the bird sharpness model catalog (YOLO detectors and SAM refiners,
``bird_sharpness/model_catalog.py``) and the NAFNet denoise model into this
checkout's ``SuperViewer/models`` (the denoise model into ``SuperViewer/models/denoise``),
where the build (``build_tools/viewer_bird_body.py``, ``viewer_denoise.py``) and the
analyzer already look. Every file is verified by size and SHA-256; verified files
are skipped, bad ones are downloaded again. Packaging is unchanged: only the
models the specs already bundle (yolo11n.pt, NAFNet) go into the installers.

    python build_tools/download_models.py                 # everything (~3.9 GB)
    python build_tools/download_models.py yolo11x-seg.pt sam2.1_b.pt
    python build_tools/download_models.py --dry-run       # plan only
    python build_tools/download_models.py --check-only    # verify offline

Wrappers: ``download_models.sh`` (macOS/Linux), ``download_models.bat`` (Windows).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bird_sharpness import model_catalog  # noqa: E402

RETRIES = 3


def workspace_model_dir(repo_root: Path = REPO_ROOT) -> Path:
    return Path(repo_root) / "SuperViewer" / "models"


def resolve_names(names: List[str]) -> List[model_catalog.CatalogModel]:
    """Catalog models for ``names`` (all of them when empty); unknown names raise ValueError."""
    if not names:
        return [*model_catalog.DETECTORS, *model_catalog.SAM_MODELS]
    out, unknown = [], []
    for name in (part.strip() for value in names for part in value.split(",")):
        name = name if name.endswith(".pt") else f"{name}.pt"
        model = model_catalog.catalog_model(name)
        (out if model is not None else unknown).append(model or name)
    if unknown:
        raise ValueError("未知模型：" + "、".join(unknown) + "（--list 查看全部）")
    return out


class _Progress:
    """One updating line on a terminal, a line per 20 % otherwise (build logs)."""

    def __init__(self, label: str, stream=sys.stdout):
        self.label, self.stream, self.tty = label, stream, stream.isatty()
        self.last = -1

    def __call__(self, done: int, total: int) -> None:
        percent = int(100 * done / total) if total else 0
        if self.tty:
            self.stream.write(f"\r  {self.label} {percent:3d}%  {done / 1e6:7.1f} / {total / 1e6:.1f} MB")
            self.stream.flush()
        elif percent // 20 != self.last // 20:
            print(f"  {self.label} {percent}%", file=self.stream, flush=True)
        self.last = percent

    def end(self) -> None:
        if self.tty:
            self.stream.write("\n")
            self.stream.flush()


def fetch(model: model_catalog.CatalogModel, dest: Path, *, force: bool = False,
          download: Optional[Callable] = None, sleep: Callable = time.sleep) -> str:
    """Make ``dest/model.name`` a verified copy. Returns "ok" (already verified) or "downloaded"."""
    download = download or model_catalog.download  # looked up per call (tests replace it)
    target = dest / model.name
    if not force and model_catalog.verify(target, model.name):
        return "ok"
    last: Optional[Exception] = None
    for attempt in range(1, RETRIES + 1):
        progress = _Progress(f"{model.name}（第 {attempt}/{RETRIES} 次）")
        try:
            download(model.name, directory=dest, progress=progress)
            progress.end()
            return "downloaded"
        except Exception as exc:  # network / disk / checksum: the old file is left untouched
            progress.end()
            last = exc
            print(f"  {model.name} 下载失败：{exc}", file=sys.stderr, flush=True)
            if attempt < RETRIES:
                sleep(attempt)
    raise RuntimeError(f"{model.name}：{last}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把鸟清晰度检测模型（YOLO / SAM）和降噪模型下载到 workspace，build 与打包时不再下载。")
    parser.add_argument("models", nargs="*", help="只下载这些模型（空格或逗号分隔，如 yolo11x-seg.pt sam2.1_b）；默认全部")
    parser.add_argument("--dest", type=Path, default=workspace_model_dir(), help="目标目录（默认 SuperViewer/models）")
    parser.add_argument("--no-denoise", action="store_true", help="不处理 NAFNet 降噪模型")
    parser.add_argument("--force", action="store_true", help="已校验通过的也重新下载")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划（已就绪 / 需下载 / 大小），不联网不写文件")
    parser.add_argument("--check-only", action="store_true", help="只离线校验，缺失或损坏时返回非零")
    parser.add_argument("--list", action="store_true", help="列出全部可下载的模型")
    args = parser.parse_args(argv)

    if args.list:
        for m in (*model_catalog.DETECTORS, *model_catalog.SAM_MODELS):
            print(f"{m.name:18s} {m.label}")
        return 0
    try:
        models = resolve_names(args.models)
    except ValueError as exc:
        parser.error(str(exc))
    with_denoise = not args.no_denoise and not args.models
    dest = Path(args.dest).resolve()
    print(f"[models] 目标目录：{dest}")

    ready = [m for m in models if not args.force and model_catalog.verify(dest / m.name, m.name)]
    missing = [m for m in models if m not in ready]
    denoise_ok = None
    if with_denoise:
        from image_denoise.models import MODEL_SIZE, DenoiseModelError, development_model_path, verify_model

        denoise_target = dest / "denoise" / development_model_path(REPO_ROOT).name
        try:
            verify_model(denoise_target)
            denoise_ok = not args.force
        except (OSError, DenoiseModelError):
            denoise_ok = False
    todo_bytes = sum(m.size_bytes for m in missing) + (0 if denoise_ok in (None, True) else MODEL_SIZE)
    print(f"[models] 已就绪 {len(ready)} 个，需下载 {len(missing)} 个"
          + ("" if denoise_ok is None else f"；降噪模型{'已就绪' if denoise_ok else '需下载'}")
          + f"（约 {todo_bytes / 1e9:.2f} GB）")
    if args.dry_run or args.check_only:
        for m in missing:
            print(f"  需下载 {m.name}  {m.megabytes:g} MB")
        if args.check_only:
            return 0 if not missing and denoise_ok in (None, True) else 1
        return 0

    failures = []
    for i, model in enumerate(missing, 1):
        print(f"[models] ({i}/{len(missing)}) {model.name}  {model.megabytes:g} MB", flush=True)
        try:
            fetch(model, dest, force=args.force)
        except RuntimeError as exc:
            failures.append(str(exc))
    if with_denoise and not denoise_ok:
        from build_tools.download_denoise_model import download_model

        print("[models] NAFNet 降噪模型", flush=True)
        try:
            download_model(denoise_target, force=args.force)
        except Exception as exc:
            failures.append(f"NAFNet：{exc}")
    if failures:
        print("[models] 以下模型未能下载，已有文件未被修改：", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"[models] 完成：{len(models)} 个模型均已校验" + ("，降噪模型已校验" if with_denoise else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
