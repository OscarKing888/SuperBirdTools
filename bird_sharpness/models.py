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
FOUND_FULL_LIFTED = "full_lifted"  # whole frame with dark mid-tones lifted
FOUND_FULL_SMALL = "full_small"  # high-resolution pass for small birds (flocks)
FOUND_FULL_FINE = "full_fine"    # whole frame at the finer recheck input size
FOUND_FOCUS_WEAK = "focus_weak"  # weak whole-frame candidate lying on the camera focus box
FOUND_FOCUS_ZOOM = "focus_zoom"  # zoomed window around the focus point, confirmed by a weak candidate
FOUND_ENHANCED = "enhanced"      # enhanced search: zoomed windows over the centre region (optional)
FOUND_GIVEN = "given"            # handed in by the caller (the trace window's model chain); no detection ran
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
    from .model_catalog import user_model_dir

    dirs.append(user_model_dir())  # models downloaded from 设置 → 鸟清晰度
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


RUNTIME_MODULES = ("numpy", "cv2", "torch", "torchvision", "ultralytics", "rawpy", "PIL")


def _program_replaced() -> bool:
    """The working directory is gone: the app (or checkout) was replaced while running.

    Lazily imported modules then fail with a bare ``[Errno 2]`` (seen when
    dist/SuperViewer.app was re-packaged a minute after launch).
    """
    try:
        os.getcwd()
    except FileNotFoundError:
        return True
    return bool(getattr(sys, "frozen", False)) and not os.path.exists(sys.executable)


def check_runtime() -> Optional[str]:
    """Return a user-facing reason why detection cannot run, or ``None`` when ready."""
    for module in RUNTIME_MODULES:
        try:
            __import__(module)
        except Exception as exc:  # ImportError or broken native libs
            if _program_replaced():
                return (f"程序文件在运行期间被替换或删除（例如重新打包、更新），无法加载 {module}。"
                        "请退出并重新打开程序。")
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
    """Holds the loaded detector + keypoint model; thread-safe lazy initialisation.

    ``detector``: a model file name from :mod:`bird_sharpness.model_catalog` (looked
    up like every model), or ``"auto"`` for the built-in choice.
    """

    def __init__(self, paths: Optional[ModelPaths] = None, *, detector: str = "auto"):
        self._paths = paths
        self.detector = str(detector or "auto")
        self.detector_path: Optional[Path] = None
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
            if self._paths is None and self.detector != "auto":
                chosen = find_model((self.detector,))
                if chosen is None:
                    raise BirdSharpnessModelError(
                        f"找不到检测模型 {self.detector}：请在「设置 → 用户选项 → 鸟清晰度」中下载")
                paths = ModelPaths(chosen if "-seg" in chosen.name else None, paths.keypoint,
                                   None if "-seg" in chosen.name else chosen)
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
            task = getattr(seg, "task", None)
            if task in ("segment", "detect"):
                masks = task == "segment"  # the model knows; the file name is only a convention
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
            self.detector_path = Path(detector_path)

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

    def _predict(self, bgr, *, conf: float, imgsz: Optional[int], classes):
        """``(confs, xyxy, class ids, masks or None)`` of one YOLO run (masks binarised uint8)."""
        self.load()
        with self._lock:
            kwargs = dict(conf=conf, verbose=False)
            if classes is not None:
                kwargs["classes"] = list(classes)
            if imgsz:
                kwargs["imgsz"] = int(imgsz)
            if self._masks:
                kwargs["retina_masks"] = True
            try:
                det = self._seg.predict(bgr, device=self.device, **kwargs)[0]
            except Exception as exc:
                if self.device == "cpu":
                    raise
                _log.warning("[BirdSharpness] YOLO failed on device=%s, retrying on CPU: %s", self.device, exc)
                det = self._seg.predict(bgr, device="cpu", **kwargs)[0]
        boxes = getattr(det, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return None
        import numpy as np

        masks = getattr(det, "masks", None)
        # Binarise on the device: a flock at 2048 px has ~60 frame-sized masks, 4x larger as float32.
        mask_data = ((masks.data > 0.5).to(dtype=self._torch.uint8).cpu().numpy()
                     if (self._masks and masks is not None and self._torch is not None) else
                     (masks.data.cpu().numpy() > 0.5).astype(np.uint8) if (self._masks and masks is not None) else None)
        return boxes.conf.cpu().numpy(), boxes.xyxy.cpu().numpy(), boxes.cls.cpu().numpy(), mask_data

    def detect_birds(self, bgr_small, *, conf: float = BIRD_CONFIDENCE_MIN, imgsz: Optional[int] = None) -> list:
        """Every bird in a small BGR image as :class:`BirdDetection`, strongest first.

        ``conf`` below :data:`BIRD_CONFIDENCE_MIN` returns weak candidates too (the
        default analyzer only uses them next to the camera focus point; the configurable
        detection confidence can explicitly admit them for the whole frame); ``imgsz`` is the
        network input size (Ultralytics default when ``None``).
        """
        found = self._predict(bgr_small, conf=conf, imgsz=imgsz, classes=[BIRD_CLASS_ID])
        if found is None:
            return []
        confs, xyxy, _cls, mask_data = found
        out = []
        for i in range(len(confs)):
            mask = mask_data[i] if mask_data is not None and i < len(mask_data) else None
            out.append(BirdDetection(float(confs[i]), tuple(float(v) for v in xyxy[i]), mask))
        out.sort(key=lambda d: d.confidence * max(0.0, d.box[2] - d.box[0]) * max(0.0, d.box[3] - d.box[1]),
                 reverse=True)
        return out

    def detect_objects(self, bgr, *, conf: float, imgsz: Optional[int] = None, birds_only: bool = True) -> list:
        """Raw detector output for the model preview: ``(class name, confidence, box, mask)``
        per object, strongest first; every COCO class unless ``birds_only``."""
        found = self._predict(bgr, conf=conf, imgsz=imgsz, classes=[BIRD_CLASS_ID] if birds_only else None)
        if found is None:
            return []
        confs, xyxy, cls, mask_data = found
        names = getattr(self._seg, "names", {}) or {}
        out = [(str(names.get(int(cls[i]), int(cls[i]))), float(confs[i]), tuple(float(v) for v in xyxy[i]),
                mask_data[i] if mask_data is not None and i < len(mask_data) else None) for i in range(len(confs))]
        out.sort(key=lambda o: -o[1])
        return out

    @property
    def detector_name(self) -> str:
        """File name of the loaded detector (the requested one before loading)."""
        return self.detector_path.name if self.detector_path is not None else self.detector

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


_SHARED_LOCK = threading.Lock()
_SHARED: dict = {}


def shared_models(detector: str = "auto") -> BirdSharpnessModels:
    """One :class:`BirdSharpnessModels` per detector for the whole process, so batch
    detection and trace windows using the same detector load it once."""
    key = str(detector or "auto")
    with _SHARED_LOCK:
        models = _SHARED.get(key)
        if models is None:
            models = _SHARED[key] = BirdSharpnessModels(detector=key)
        return models


def release_shared_models() -> None:
    with _SHARED_LOCK:
        models = list(_SHARED.values())
    for item in models:
        item.release()
