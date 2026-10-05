"""Selectable models: YOLO bird detectors (boxes or masks) and SAM2 mask refiners.

Weights are never committed. Besides the existing lookup directories
(:func:`bird_sharpness.models.find_model`) every model can be downloaded on
request into a per-user directory (:func:`user_model_dir`) from the Ultralytics
assets release that the installed ``ultralytics`` package itself uses.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

AUTO_DETECTOR = "auto"  # the built-in choice: yolo11l-seg ... yolo11n-seg, else a box detector
RELEASE_URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0"
SIZE_LABELS = {"n": "nano", "s": "small", "m": "medium", "l": "large", "x": "xlarge",
               "t": "tiny", "b": "base+"}

# File sizes of the v8.4.0 release assets in MB (shown before downloading).
_MB = {
    "yolo11n-seg": 6.2, "yolo11s-seg": 20.7, "yolo11m-seg": 45.4, "yolo11l-seg": 56.1, "yolo11x-seg": 125.1,
    "yolo11n": 5.6, "yolo11s": 19.3, "yolo11m": 40.7, "yolo11l": 51.4, "yolo11x": 114.6,
    "yolo26n-seg": 6.7, "yolo26s-seg": 23.5, "yolo26m-seg": 54.8, "yolo26l-seg": 63.7, "yolo26x-seg": 142.1,
    "yolo26n": 5.5, "yolo26s": 20.4, "yolo26m": 44.3, "yolo26l": 53.2, "yolo26x": 118.7,
    "yolo12n": 5.6, "yolo12s": 19.0, "yolo12m": 40.9, "yolo12l": 53.7, "yolo12x": 119.3,
    "yolov8n-seg": 7.1, "yolov8s-seg": 23.9, "yolov8m-seg": 54.9, "yolov8l-seg": 92.4, "yolov8x-seg": 144.1,
    "yolov8n": 6.5, "yolov8s": 22.6, "yolov8m": 52.1, "yolov8l": 87.8, "yolov8x": 136.9,
    "sam2.1_t": 78.1, "sam2.1_s": 92.3, "sam2.1_b": 161.9, "sam2.1_l": 449.2,
    "sam2_t": 78.1, "sam2_s": 92.3, "sam2_b": 161.9, "sam2_l": 449.2,
}


@dataclass(frozen=True)
class CatalogModel:
    name: str        # file name, e.g. "yolo11x-seg.pt"
    kind: str        # "detector" | "sam"
    family: str      # "YOLO11", "YOLO26", "YOLO12", "YOLOv8", "SAM2.1", "SAM2"
    size: str        # n/s/m/l/x (YOLO), t/s/b/l (SAM)
    masks: bool      # detector with segmentation masks (SAM always gives masks)
    megabytes: float

    @property
    def label(self) -> str:
        what = "" if self.kind == "sam" else (" 分割" if self.masks else " 检测框")
        return f"{self.family} {SIZE_LABELS.get(self.size, self.size)}{what}（{self.megabytes:g} MB）"


def _detectors() -> Tuple[CatalogModel, ...]:
    out = []
    for family, stem, seg in (("YOLO11", "yolo11", True), ("YOLO26", "yolo26", True),
                              ("YOLO12", "yolo12", False), ("YOLOv8", "yolov8", True)):
        for masks in ((True, False) if seg else (False,)):
            for size in "nsmlx":
                key = f"{stem}{size}{'-seg' if masks else ''}"
                out.append(CatalogModel(f"{key}.pt", "detector", family, size, masks, _MB[key]))
    return tuple(out)


DETECTORS: Tuple[CatalogModel, ...] = _detectors()
SAM_MODELS: Tuple[CatalogModel, ...] = tuple(
    CatalogModel(f"{stem}_{size}.pt", "sam", family, size, True, _MB[f"{stem}_{size}"])
    for family, stem in (("SAM2.1", "sam2.1"), ("SAM2", "sam2")) for size in "tsbl")
_BY_NAME = {m.name: m for m in (*DETECTORS, *SAM_MODELS)}


def catalog_model(name: str) -> Optional[CatalogModel]:
    return _BY_NAME.get(str(name or ""))


def user_model_dir() -> Path:
    """Per-user directory for downloaded models (writable also when the app bundle is not)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "SuperBirdTools" / "models"


def locate(name: str) -> Optional[Path]:
    """Where ``name`` is installed (any lookup directory), else ``None``."""
    from .models import find_model

    return find_model((name,)) if name else None


def download_url(name: str) -> str:
    return f"{RELEASE_URL}/{name}"


class DownloadCancelled(Exception):
    pass


def download(name: str, *, progress: Optional[Callable[[int, int], None]] = None,
             cancelled: Callable[[], bool] = lambda: False, timeout: float = 30.0,
             directory: Optional[Path] = None) -> Path:
    """Download a catalog model into :func:`user_model_dir` and return its path.

    Streams into ``<name>.part`` and renames only when complete (and the size
    matches the server's), so an interrupted or cancelled download never leaves
    a truncated model behind. ``progress(done_bytes, total_bytes_or_0)``.
    """
    model = catalog_model(name)
    if model is None:
        raise ValueError(f"不支持的模型：{name}")
    import ssl
    import urllib.request

    target_dir = Path(directory) if directory is not None else user_model_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / model.name
    part = target_dir / f"{model.name}.part"
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    request = urllib.request.Request(download_url(model.name), headers={"User-Agent": "SuperBirdTools"})
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response, open(part, "wb") as fh:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                if cancelled():
                    raise DownloadCancelled(model.name)
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if total and done != total:
            raise IOError(f"下载不完整：{done} / {total} 字节")
        os.replace(part, final)
        return final
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
