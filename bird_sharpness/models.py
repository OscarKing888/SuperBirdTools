"""Model discovery and lazy loading for bird sharpness detection.

Models (none is committed to git):

* bird detector: a YOLO *segmentation* model (``yolo11l-seg.pt`` …, per-bird
  pixel masks) is preferred; a plain detection model (``yolo11n.pt`` …, the one
  SuperViewer's bird overlay ships) is the fallback and gives per-bird boxes;
* optional CUB-200 eye/beak keypoint model ``cub200_keypoint_resnet50_slim.pth``
  (same weights SuperPicky uses). Without it each bird is measured as a whole
  instead of at the head.

They are searched, in order, in ``$SUPERBIRD_SHARPNESS_MODEL_DIR``, the
bundled ``models`` resource directories of a packaged app, the repository's
``SuperViewer/models`` and ``SuperBirdStamp/models`` folders, and finally an
installed or sibling SuperPicky checkout.
"""

from __future__ import annotations

import os
import platform
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from app_common.log import get_logger

_log = get_logger("bird_sharpness")

MODEL_DIR_ENV = "SUPERBIRD_SHARPNESS_MODEL_DIR"
DEVICE_ENV = "SUPERBIRD_SHARPNESS_DEVICE"
SEG_MODEL_NAMES = ("yolo11l-seg.pt", "yolo11m-seg.pt", "yolo11s-seg.pt", "yolo11n-seg.pt")
DET_MODEL_NAMES = ("yolo11n.pt", "yolo11s.pt", "yolov8n.pt")
BIRD_CONFIDENCE_MIN = 0.25
# How a bird was found (BirdDetection.source).
FOUND_FULL = "full"              # whole frame, confidence >= BIRD_CONFIDENCE_MIN
FOUND_FULL_FINE = "full_fine"    # whole frame at the finer recheck input size
FOUND_FOCUS_WEAK = "focus_weak"  # weak whole-frame candidate lying on the camera focus box
FOUND_FOCUS_ZOOM = "focus_zoom"  # zoomed window around the focus point, confirmed by a weak candidate
KEYPOINT_MODEL_NAME = "cub200_keypoint_resnet50_slim.pth"
BIRD_CLASS_ID = 14
KEYPOINT_INPUT_SIZE = 416

_REPO_ROOT = Path(__file__).resolve().parent.parent


class BirdSharpnessModelError(RuntimeError):
    """Raised when a required model or ML dependency is unavailable."""


def _candidate_model_dirs() -> List[Path]:
    dirs: List[Path] = []
    env_dir = os.environ.get(MODEL_DIR_ENV, "").strip()
    if env_dir:
        dirs.append(Path(env_dir))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "models")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(exe_dir.parent / "Resources" / "models")  # macOS .app
        dirs.append(exe_dir / "_internal" / "models")
        dirs.append(exe_dir / "models")
    dirs.append(_REPO_ROOT / "SuperViewer" / "models")
    dirs.append(_REPO_ROOT / "SuperBirdStamp" / "models")
    # SuperPicky ships the same weights; reuse an installed copy or a sibling checkout.
    if sys.platform == "darwin":
        dirs.append(Path("/Applications/SuperPicky.app/Contents/Resources/models"))
        dirs.append(Path.home() / "Applications" / "SuperPicky.app" / "Contents" / "Resources" / "models")
    elif os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                dirs.append(Path(base) / "SuperPicky" / "_internal" / "models")
                dirs.append(Path(base) / "SuperPicky" / "models")
    for parent in (_REPO_ROOT.parent, _REPO_ROOT.parent.parent):
        dirs.append(parent / "SuperPicky" / "models")
    unique: List[Path] = []
    seen = set()
    for d in dirs:
        key = os.path.normcase(str(d))
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def find_model(names: Iterable[str]) -> Optional[Path]:
    names = tuple(names)
    for directory in _candidate_model_dirs():
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


@dataclass(frozen=True)
class ModelPaths:
    segmentation: Optional[Path]
    keypoint: Optional[Path]
    detection: Optional[Path] = None

    @property
    def detector(self) -> Optional[Path]:
        return self.segmentation or self.detection

    @property
    def complete(self) -> bool:
        """A bird detector is required; the keypoint model only refines head measurement."""
        return self.detector is not None

    def missing_description(self) -> str:
        if self.detector is None:
            return " / ".join(SEG_MODEL_NAMES + DET_MODEL_NAMES)
        return ""


def resolve_model_paths() -> ModelPaths:
    return ModelPaths(
        find_model(SEG_MODEL_NAMES),
        find_model((KEYPOINT_MODEL_NAME,)),
        find_model(DET_MODEL_NAMES),
    )


def check_runtime() -> Optional[str]:
    """Return a user-facing reason why detection cannot run, or ``None`` when ready."""
    for module in ("numpy", "cv2", "torch", "torchvision", "ultralytics", "rawpy", "PIL"):
        try:
            __import__(module)
        except Exception as exc:  # ImportError or broken native libs
            return f"缺少运行依赖 {module}：{exc}"
    paths = resolve_model_paths()
    if not paths.complete:
        return (
            f"找不到鸟体识别模型：{paths.missing_description()}。"
            f"请放入 SuperViewer/models，或设置环境变量 {MODEL_DIR_ENV}，或安装 SuperPicky。"
        )
    return None


def select_device(torch_module) -> str:
    forced = os.environ.get(DEVICE_ENV, "").strip().lower()
    if forced:
        return forced
    try:
        if torch_module.cuda.is_available():
            return "cuda:0"
        if (
            sys.platform == "darwin"
            and platform.machine() == "arm64"
            and torch_module.backends.mps.is_available()
        ):
            return "mps"
    except Exception:
        pass
    return "cpu"


class _KeypointNet:
    """Factory for SuperPicky's PartLocalizer (ResNet50 + coordinate/visibility heads)."""

    @staticmethod
    def build(torch_module):
        import torch.nn as nn
        import torchvision.models as tv_models

        class PartLocalizer(nn.Module):
            def __init__(self, num_parts: int = 3, hidden_dim: int = 512, dropout: float = 0.2):
                super().__init__()
                self.num_parts = num_parts
                self.backbone = tv_models.resnet50(weights=None)
                in_features = self.backbone.fc.in_features
                self.backbone.fc = nn.Identity()
                self.head = nn.Sequential(
                    nn.Linear(in_features, hidden_dim),
                    nn.BatchNorm1d(hidden_dim),
                    nn.ReLU(),
                    nn.Dropout(dropout),
                    nn.Linear(hidden_dim, hidden_dim // 2),
                    nn.BatchNorm1d(hidden_dim // 2),
                    nn.ReLU(),
                    nn.Dropout(dropout),
                )
                self.coord_head = nn.Linear(hidden_dim // 2, num_parts * 2)
                self.vis_head = nn.Linear(hidden_dim // 2, num_parts)

            def forward(self, x):
                features = self.head(self.backbone(x))
                coords = torch_module.sigmoid(self.coord_head(features)).view(-1, self.num_parts, 2)
                vis = torch_module.sigmoid(self.vis_head(features))
                return coords, vis

        return PartLocalizer()


@dataclass
class BirdDetection:
    """One detected bird at detection-image resolution."""

    confidence: float
    box: tuple  # (x1, y1, x2, y2) in detection-image pixels
    mask: Optional[object] = None  # uint8 HxW mask at detection resolution (segmentation models)
    source: str = FOUND_FULL


class BirdSharpnessModels:
    """Holds the loaded detector + keypoint model; thread-safe lazy initialisation."""

    def __init__(self, paths: Optional[ModelPaths] = None):
        self._paths = paths
        self._lock = threading.RLock()
        self._seg = None
        self._kp = None
        self._masks = False
        self._torch = None
        self.device = "cpu"

    @property
    def loaded(self) -> bool:
        return self._seg is not None

    @property
    def has_keypoints(self) -> bool:
        return self._kp is not None

    @property
    def has_masks(self) -> bool:
        return self._masks

    def load(self) -> None:
        with self._lock:
            if self.loaded:
                return
            paths = self._paths or resolve_model_paths()
            if not paths.complete:
                raise BirdSharpnessModelError(f"找不到模型文件：{paths.missing_description()}")
            try:
                import torch
                from ultralytics import YOLO
            except Exception as exc:
                raise BirdSharpnessModelError(f"无法加载 Torch/Ultralytics：{exc}") from exc
            self._torch = torch
            self.device = select_device(torch)
            detector_path = paths.detector
            masks = paths.segmentation is not None
            _log.info("[BirdSharpness] loading YOLO=%s (masks=%s) keypoint=%s device=%s",
                      detector_path, masks, paths.keypoint, self.device)
            seg = YOLO(str(detector_path))
            names = getattr(seg, "names", {}) or {}
            if names and str(names.get(BIRD_CLASS_ID, "")).lower() != "bird":
                raise BirdSharpnessModelError(f"YOLO 模型类别 {BIRD_CLASS_ID} 不是 bird：{detector_path}")
            kp = None
            if paths.keypoint is not None:
                try:
                    kp = _KeypointNet.build(torch)
                    checkpoint = torch.load(str(paths.keypoint), map_location="cpu", weights_only=True)
                    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
                        checkpoint = checkpoint["model_state_dict"]
                    kp.load_state_dict(checkpoint)
                    kp.eval()
                    try:
                        kp.to(self.device)
                    except Exception as exc:
                        _log.warning("[BirdSharpness] Keypoint model cannot use device=%s, falling back to CPU: %s", self.device, exc)
                        self.device = "cpu"
                        kp.to("cpu")
                except Exception as exc:
                    # Optional refinement: measure whole birds instead of failing the job.
                    _log.warning("[BirdSharpness] Keypoint model unusable, measuring whole birds: %s", exc)
                    kp = None
            else:
                _log.info("[BirdSharpness] Keypoint model not found; measuring whole birds")
            self._seg = seg
            self._kp = kp
            self._masks = masks

    def release(self) -> None:
        with self._lock:
            self._seg = None
            self._kp = None
            torch = self._torch
            if torch is not None and self.device == "mps":
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass
            elif torch is not None and self.device.startswith("cuda"):
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

    def detect_birds(self, bgr_small, *, conf: float = BIRD_CONFIDENCE_MIN, imgsz: Optional[int] = None) -> list:
        """Every bird in a small BGR image as :class:`BirdDetection`, strongest first.

        ``conf`` below :data:`BIRD_CONFIDENCE_MIN` returns weak candidates too (the
        analyzer only uses them next to the camera focus point); ``imgsz`` is the
        network input size (Ultralytics default when ``None``).
        """
        self.load()
        with self._lock:
            kwargs = dict(classes=[BIRD_CLASS_ID], conf=conf, verbose=False)
            if imgsz:
                kwargs["imgsz"] = int(imgsz)
            if self._masks:
                kwargs["retina_masks"] = True
            try:
                det = self._seg.predict(bgr_small, device=self.device, **kwargs)[0]
            except Exception as exc:
                if self.device == "cpu":
                    raise
                _log.warning("[BirdSharpness] YOLO failed on device=%s, retrying on CPU: %s", self.device, exc)
                det = self._seg.predict(bgr_small, device="cpu", **kwargs)[0]
        boxes = getattr(det, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        import numpy as np

        confs = boxes.conf.cpu().numpy()
        xyxy = boxes.xyxy.cpu().numpy()
        masks = getattr(det, "masks", None)
        mask_data = masks.data.cpu().numpy() if (self._masks and masks is not None) else None
        out = []
        for i in range(len(confs)):
            mask = mask_data[i].astype(np.uint8) if mask_data is not None and i < len(mask_data) else None
            out.append(BirdDetection(float(confs[i]), tuple(float(v) for v in xyxy[i]), mask))
        out.sort(key=lambda d: d.confidence * max(0.0, d.box[2] - d.box[0]) * max(0.0, d.box[3] - d.box[1]),
                 reverse=True)
        return out

    def keypoints(self, rgb_crop):
        """Return ``(coords[3,2] normalised, visibility[3])`` for left eye, right eye, beak.

        ``None`` when the optional keypoint model is unavailable.
        """
        self.load()
        if self._kp is None:
            return None
        import numpy as np
        from PIL import Image

        torch = self._torch
        img = Image.fromarray(rgb_crop).resize((KEYPOINT_INPUT_SIZE, KEYPOINT_INPUT_SIZE), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        tensor = torch.from_numpy(arr.transpose(2, 0, 1).copy()).unsqueeze(0)
        with self._lock:
            try:
                with torch.inference_mode():
                    coords, vis = self._kp(tensor.to(self.device))
            except Exception as exc:
                if self.device == "cpu":
                    raise
                _log.warning("[BirdSharpness] Keypoint failed on device=%s, retrying on CPU: %s", self.device, exc)
                self._kp.to("cpu")
                self.device = "cpu"
                with torch.inference_mode():
                    coords, vis = self._kp(tensor)
        return coords[0].float().cpu().numpy(), vis[0].float().cpu().numpy()
