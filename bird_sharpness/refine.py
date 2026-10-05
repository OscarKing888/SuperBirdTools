"""SAM2 mask refinement: the visible bird's own pixels inside a detector's box.

A detector's mask of a bird behind twigs or leaves often takes in what is in
front of it (and its blur). SAM2, prompted with the bird's box, follows the
bird's outline instead (DSC05639: 14-16 % fewer pixels, the twig in front left
out). SAM2 does not know what a bird is; it only segments what it is pointed at.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np

from app_common.log import get_logger

_log = get_logger("bird_sharpness")

MIN_KEEP_FRACTION = 0.2  # a SAM mask smaller than this share of the detector's mask is not trusted


class SamRefiner:
    """Lazily loaded SAM / SAM2 model (Ultralytics), thread-safe."""

    def __init__(self, name: str):
        self.name = str(name)
        self._lock = threading.RLock()
        self._model = None
        self.device = "cpu"
        self._torch = None

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            from .model_catalog import locate
            from .models import BirdSharpnessModelError, select_device

            path = locate(self.name)
            if path is None:
                raise BirdSharpnessModelError(f"找不到 SAM 模型 {self.name}：请在「设置 → 用户选项 → 鸟清晰度」中下载")
            try:
                import torch
                from ultralytics import SAM
            except Exception as exc:
                raise BirdSharpnessModelError(f"无法加载 Torch/Ultralytics：{exc}") from exc
            self._torch = torch
            self.device = select_device(torch)
            _log.info("[BirdSharpness] loading SAM=%s device=%s", path, self.device)
            self._model = SAM(str(Path(path)))

    def mask(self, rgb: np.ndarray, box) -> Optional[np.ndarray]:
        """Boolean mask (``rgb``'s size) of the object in ``box`` (x1, y1, x2, y2, ``rgb`` px)."""
        import cv2

        self.load()
        bgr = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
        with self._lock:
            try:
                result = self._model.predict(bgr, bboxes=[list(map(float, box))], device=self.device, verbose=False)[0]
            except Exception as exc:
                if self.device == "cpu":
                    raise
                _log.warning("[BirdSharpness] SAM failed on device=%s, retrying on CPU: %s", self.device, exc)
                self.device = "cpu"
                result = self._model.predict(bgr, bboxes=[list(map(float, box))], device="cpu", verbose=False)[0]
        masks = getattr(result, "masks", None)
        if masks is None or len(masks.data) == 0:
            return None
        mask = masks.data[0].cpu().numpy() > 0.5
        if mask.shape != rgb.shape[:2]:
            mask = cv2.resize(mask.astype(np.uint8), (rgb.shape[1], rgb.shape[0]),
                              interpolation=cv2.INTER_NEAREST).astype(bool)
        return mask

    def segment(self, rgb: np.ndarray, *, boxes=None, points=None, labels=None) -> list:
        """``(mask, score)`` per object for the model preview (masks at ``rgb``'s size).

        Without points every box is its own object; with points (``labels`` 1 = keep,
        0 = exclude) the box (at most one) and the points describe one object.
        """
        import cv2

        self.load()
        kwargs = {}
        if points:
            kwargs["points"] = [[list(map(float, p)) for p in points]]
            kwargs["labels"] = [[int(v) for v in labels]]
            if boxes:
                kwargs["bboxes"] = [list(map(float, boxes[0]))]
        elif boxes:
            kwargs["bboxes"] = [list(map(float, b)) for b in boxes]
        else:
            return []
        bgr = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
        with self._lock:
            try:
                result = self._model.predict(bgr, device=self.device, verbose=False, **kwargs)[0]
            except Exception as exc:
                if self.device == "cpu":
                    raise
                _log.warning("[BirdSharpness] SAM failed on device=%s, retrying on CPU: %s", self.device, exc)
                self.device = "cpu"
                result = self._model.predict(bgr, device="cpu", verbose=False, **kwargs)[0]
        masks = getattr(result, "masks", None)
        if masks is None or len(masks.data) == 0:
            return []
        scores = (result.boxes.conf.cpu().numpy().tolist()
                  if getattr(result, "boxes", None) is not None else [None] * len(masks.data))
        out = []
        for mask, score in zip(masks.data.cpu().numpy() > 0.5, scores):
            if mask.shape != rgb.shape[:2]:
                mask = cv2.resize(mask.astype(np.uint8), (rgb.shape[1], rgb.shape[0]),
                                  interpolation=cv2.INTER_NEAREST).astype(bool)
            out.append((mask, None if score is None else float(score)))
        return out

    def release(self) -> None:
        with self._lock:
            self._model = None
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


_SHARED_LOCK = threading.Lock()
_SHARED: dict = {}


def shared_refiner(name: str) -> SamRefiner:
    with _SHARED_LOCK:
        refiner = _SHARED.get(name)
        if refiner is None:
            refiner = _SHARED[name] = SamRefiner(name)
        return refiner


def release_shared_refiners() -> None:
    with _SHARED_LOCK:
        refiners = list(_SHARED.values())
    for item in refiners:
        item.release()
