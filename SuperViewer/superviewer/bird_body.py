# -*- coding: utf-8 -*-
"""Bird-body detection and versioned XMP cache, independent of Qt.

Boxes are normalized in the display-oriented camera-preview coordinate frame.
Full RAW views map them with their actual pixel crop geometry, just like focus
boxes. All filesystem/model calls belong in a worker, never in a playback slot.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from functools import lru_cache
import io
import json
import math
import os
from pathlib import Path
import threading

from app_common.image_formats import HEIF_EXTENSIONS, PHOTOSHOP_EXTENSIONS, RAW_EXTENSIONS
from app_common.log import get_logger

_log = get_logger("superviewer.bird_body")
# v2: camouflaged-bird recheck when the first pass finds none.
# v3: every bird's box (main bird first), small-bird pass for flocks; older
#     single-box caches are re-detected.
ALGORITHM_VERSION = "bird-body-v3"
_COMPATIBLE_BOX_VERSIONS = ()
MAX_CACHE_CHARS = 65536  # a flock of ~60 boxes is ~3 KB
FIELD_CACHE = "bird_body_cache"
MAX_DETECT_LONG_EDGE = 1280
_MODEL_NAMES = ("yolo11n.pt", "yolo11s.pt", "yolov8n.pt")


@dataclass(frozen=True)
class BirdBodyResult:
    box: tuple[float, float, float, float] | None  # main bird (largest confidence x area)
    source_fingerprint: str
    version: str = ALGORITHM_VERSION
    geometry: str = "camera"
    boxes: tuple = ()  # every bird, main bird first; empty for caches with one box only

    @property
    def overlay(self):
        """What the preview draws: the single box, or every box when there are several."""
        return self.boxes if len(self.boxes) > 1 else self.box

    def to_json(self) -> str:
        data = {"version": self.version, "source": self.source_fingerprint,
                "geometry": self.geometry, "box": self.box}
        if len(self.boxes) > 1:
            data["boxes"] = [[round(v, 5) for v in b] for b in self.boxes]
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def source_fingerprint(path: str) -> str:
    """Worker-only file identity; distinct RAW/JPEG siblings must not share boxes."""
    stat = os.stat(path)
    return f"{Path(path).suffix.lower()}:{stat.st_size}:{stat.st_mtime_ns}"


def cache_field(path: str) -> str:
    # 同名 RAW 与 JPEG 共用侧车但可能裁切不同，分别保存各自检测缓存。
    suffix = Path(path).suffix.lower().lstrip(".")
    suffix = "".join(c for c in suffix if c.isascii() and c.isalnum()) or "image"
    return f"XMP-superpicky:{FIELD_CACHE}_{suffix}"


def normalize_box(box) -> tuple[float, float, float, float] | None:
    if box is None:
        return None
    if not isinstance(box, (tuple, list)) or len(box) != 4:
        raise ValueError("鸟体框必须包含四个坐标")
    values = tuple(float(value) for value in box)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("鸟体框包含无效坐标")
    values = tuple(max(0.0, min(1.0, value)) for value in values)
    if not (values[0] < values[2] and values[1] < values[3]):
        raise ValueError("鸟体框为空或坐标顺序错误")
    return values


def result_from_metadata(path: str, metadata: dict, fingerprint: str | None = None) -> BirdBodyResult | None:
    """Parse already-loaded metadata without I/O; invalid/missing means cache miss."""
    key = cache_field(path)
    name = key.partition(":")[2]
    value = metadata.get(key, metadata.get(name, metadata.get(f"report.{name}")))
    if not isinstance(value, str) or not value or len(value) > MAX_CACHE_CHARS:
        return None
    try:
        data = json.loads(value)
        if not isinstance(data, dict):
            return None
        version = data.get("version")
        if version != ALGORITHM_VERSION and not (version in _COMPATIBLE_BOX_VERSIONS and data.get("box") is not None):
            return None
        if data.get("geometry") != "camera" or "box" not in data:
            return None
        source = data.get("source")
        if not isinstance(source, str) or not source or (fingerprint is not None and source != fingerprint):
            return None
        raw_box = data["box"]
        box = normalize_box(raw_box)
        # Do not quietly repair corrupt/out-of-frame saved coordinates into a hit.
        if raw_box is not None and tuple(raw_box) != box:
            return None
        raw_boxes = data.get("boxes", [])
        if not isinstance(raw_boxes, list):
            return None
        boxes = tuple(normalize_box(b) for b in raw_boxes)
        if any(b is None or tuple(r) != b for r, b in zip(raw_boxes, boxes)):
            return None
        return BirdBodyResult(box, source, version, boxes=boxes)
    except (ValueError, TypeError, OverflowError):
        return None


def _small_rgb(image, long_edge: int = MAX_DETECT_LONG_EDGE):
    from PIL import Image, ImageOps

    # 先缩小再旋转/转 RGB，普通大图不额外复制多份原生尺寸像素。
    image.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    ImageOps.exif_transpose(image, in_place=True)
    return image.convert("RGB")


def load_detection_image(path: str, long_edge: int = MAX_DETECT_LONG_EDGE):
    """Return a small owned PIL RGB image and optional camera crop in RAW pixels."""
    from PIL import Image

    suffix = Path(path).suffix.lower()
    if suffix in RAW_EXTENSIONS:
        from app_common import thumb_stream
        from app_common.raw_preview_geometry import rawpy_camera_crop_box

        try:
            jpeg = thumb_stream.get_raw_preview_jpeg(path)
            if jpeg:
                with Image.open(io.BytesIO(jpeg)) as image:
                    if max(image.size) >= thumb_stream.RAW_INPROCESS_PREVIEW_MIN_LONG_EDGE:
                        return _small_rgb(image, long_edge), None
        except Exception as exc:
            _log.debug("[bird.body] embedded RAW preview unavailable path=%r: %s", path, exc)
        import rawpy

        source = open(path, "rb") if os.name == "nt" and not str(path).isascii() else nullcontext(path)
        with source as raw_source, rawpy.imread(raw_source) as raw:
            crop = rawpy_camera_crop_box(getattr(raw, "sizes", None))
            pixels = raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
        # LibRaw 输出已经旋转；这里没有源文件 EXIF，因此不会二次旋转。
        with Image.fromarray(pixels) as image:
            return _small_rgb(image, long_edge), crop
    if suffix in HEIF_EXTENSIONS:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    try:
        with Image.open(path) as image:
            return _small_rgb(image, long_edge), None
    except Exception:
        if suffix not in PHOTOSHOP_EXTENSIONS:
            raise
        from app_common.psd_composite import load_psd_composite_rgb
        rgb = load_psd_composite_rgb(path, long_edge)
        if not rgb:
            raise ValueError(f"无法解码鸟体识别图像：{path}")
        data, width, height = rgb
        return Image.frombytes("RGB", (width, height), data), None


def camera_box_from_raw(box, camera_crop):
    """Convert a sensor-output detection back to canonical camera coordinates."""
    box = normalize_box(box)
    if box is None or camera_crop is None:
        return box
    left, top, right, bottom = camera_crop
    mapped = ((box[0] - left) / (right - left), (box[1] - top) / (bottom - top),
              (box[2] - left) / (right - left), (box[3] - top) / (bottom - top))
    try:
        return normalize_box(mapped)
    except ValueError:
        # 只存在于相机默认裁切之外的目标不能作为相机预览中的有效鸟体框。
        return None


class BirdBodyDetector:
    """Lazy local YOLO detector; no GUI imports, keypoint model, or downloads."""

    def __init__(self):
        self._lock = threading.RLock()
        self._model = None
        self._classes = ()
        self._device = "cpu"

    def _load(self):
        if self._model is not None:
            return
        from bird_sharpness.models import SEG_MODEL_NAMES, find_model, select_device

        model_path = find_model(_MODEL_NAMES) or find_model(SEG_MODEL_NAMES)
        if model_path is None:
            raise RuntimeError("YOLO 鸟体识别模型缺失：请安装鸟清晰度模型，或在 models 中放入 yolo11n.pt")
        import torch
        from ultralytics import YOLO

        model = YOLO(str(model_path))
        model.to("cpu")
        names = getattr(model, "names", {}) or {}
        names = names.items() if isinstance(names, dict) else enumerate(names)
        classes = tuple(int(index) for index, name in names if str(name).strip().lower() == "bird")
        if not classes:
            raise RuntimeError(f"YOLO 模型没有 bird 类别：{model_path}")
        self._classes = classes
        self._device = select_device(torch)
        self._model = model

    def detect(self, image, *, cancelled=lambda: False):
        """Choose the largest confidence-weighted bird, matching BirdStamp's UX."""
        boxes = self.detect_all(image, cancelled=cancelled)
        return boxes[0] if boxes else None

    def detect_all(self, image, *, cancelled=lambda: False, imgsz=None):
        """Every bird as a normalised box, largest confidence x area first."""
        with self._lock:
            # A/B 两侧可能在此等待同一模型，快切取消后不能再补做旧帧推理。
            if cancelled():
                raise InterruptedError("鸟体识别已取消")
            self._load()
            if cancelled():
                raise InterruptedError("鸟体识别已取消")
            kwargs = dict(source=image, classes=list(self._classes), conf=0.2, verbose=False)
            if imgsz:
                kwargs["imgsz"] = int(imgsz)
            try:
                results = self._model.predict(device=self._device, **kwargs)
            except Exception as exc:
                if cancelled():
                    raise InterruptedError("鸟体识别已取消") from exc
                if self._device == "cpu":
                    raise
                _log.warning("[bird.body] YOLO device=%s failed; retrying CPU: %s", self._device, exc)
                self._device = "cpu"
                results = self._model.predict(device="cpu", **kwargs)
            found = []
            for result in results or ():
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                for item in boxes:
                    if int(item.cls.item()) not in self._classes:
                        continue
                    x0, y0, x1, y1 = (float(value) for value in item.xyxy.cpu().numpy()[0])
                    confidence = float(item.conf.item())
                    if not math.isfinite(confidence) or confidence <= 0:
                        continue
                    score = max(0.0, x1 - x0) * max(0.0, y1 - y0) * confidence
                    try:
                        box = normalize_box((x0 / image.width, y0 / image.height,
                                             x1 / image.width, y1 / image.height))
                    except ValueError:
                        continue
                    found.append((score, box))
            found.sort(key=lambda item: item[0], reverse=True)
            return [box for _score, box in found]


def merge_bird_boxes(first, added, image_aspect: float = 1.0):
    """First-pass boxes unchanged, then the added boxes that are not one of them.

    Same duplicate rule as bird sharpness: one box mostly (>= 70 %) inside the
    other is the same bird. Boxes are normalised; ``image_aspect`` (w / h) makes
    the overlap areas true pixel areas.
    """
    from bird_sharpness.analyzer import DUPLICATE_CONTAINMENT, box_overlap

    def px(b):
        return (b[0] * image_aspect, b[1], b[2] * image_aspect, b[3])

    merged = list(first)
    for box in added:
        if not any(box_overlap(px(box), px(kept)) >= DUPLICATE_CONTAINMENT for kept in merged):
            merged.append(box)
    return merged


def has_small_birds(boxes, image_size) -> bool:
    """Same trigger as bird sharpness: a bird shorter than 1/16 of the image long edge."""
    from bird_sharpness.analyzer import DETECT_LONG_EDGE, FLOCK_BIRD_SIDE

    width, height = image_size
    limit = FLOCK_BIRD_SIDE / float(DETECT_LONG_EDGE) * max(width, height)
    return any(max((b[2] - b[0]) * width, (b[3] - b[1]) * height) < limit for b in boxes)


@lru_cache(maxsize=1)
def default_detector() -> BirdBodyDetector:
    return BirdBodyDetector()


def _viewer_focus_box(path: str, width: int, height: int):
    """Worker-thread focus lookup shared with the preview overlay (metadata, then report.db)."""
    from .focus_preview_loader import _load_focus_box_for_preview

    return _load_focus_box_for_preview(path, width, height, allow_report_db_fallback=True)


class MissedBirdFinder:
    """Second look for camouflaged birds, shared with bird sharpness (``find_missed_bird``).

    Runs only when the preview's first pass found no bird. Uses the sharpness
    detector (segmentation model preferred) without the eye model.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._analyzer = None
        self._unavailable = ""

    def _get(self):
        with self._lock:
            if self._analyzer is None and not self._unavailable:
                from bird_sharpness.analyzer import BirdSharpnessAnalyzer
                from bird_sharpness.models import BirdSharpnessModels, ModelPaths, check_runtime, resolve_model_paths

                reason = check_runtime()
                if reason:
                    self._unavailable = reason
                    _log.info("[bird.body] camouflaged-bird recheck unavailable: %s", reason)
                else:
                    paths = resolve_model_paths()
                    models = BirdSharpnessModels(ModelPaths(paths.segmentation, None, paths.detection))
                    self._analyzer = BirdSharpnessAnalyzer(models, focus_provider=_viewer_focus_box)
            return self._analyzer

    def find(self, path: str, *, cancelled=lambda: False):
        """Normalised camera-frame box of a missed bird, or ``None``."""
        analyzer = self._get()
        if analyzer is None or cancelled():
            return None
        missed = analyzer.find_missed_bird(path, cancelled=cancelled)
        if missed is None:
            return None
        _log.info("[bird.body] recheck found bird path=%r source=%s conf=%.2f",
                  path, missed.source, missed.confidence)
        return camera_box_from_raw(missed.box, missed.camera_crop)


@lru_cache(maxsize=1)
def default_missed_bird_finder() -> MissedBirdFinder:
    return MissedBirdFinder()


def main(argv=None) -> int:
    """CLI shares exactly the worker's cache and detection path."""
    import argparse
    from .bird_body_worker import BirdBodyAction

    parser = argparse.ArgumentParser(description="检测主鸟体框并缓存到同名 XMP 侧车")
    parser.add_argument("source")
    parser.add_argument("--no-write", action="store_true", help="只读缓存或检测，不写侧车")
    parser.add_argument("--output", type=Path, help="保存 UTF-8 JSON 结果，支持 Windows windowed EXE")
    args = parser.parse_args(argv)
    outcome = BirdBodyAction(args.source, write_xmp=not args.no_write).execute()
    output = json.dumps({"source": outcome.source_path, "cache_hit": outcome.cache_hit,
                         "written": outcome.written, "error": outcome.error,
                         "result": json.loads(outcome.result.to_json()) if outcome.result else None},
                        ensure_ascii=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 1 if outcome.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
