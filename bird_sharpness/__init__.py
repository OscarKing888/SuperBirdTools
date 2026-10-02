"""Bird sharpness detection shared by SuperViewer, SuperBirdStamp and the CLI.

Heavy dependencies (OpenCV, Torch, Ultralytics, rawpy) are imported only by the
submodules that need them, so ``bird_sharpness.scoring`` and
``bird_sharpness.models.check_runtime`` stay cheap for UI code.

CLI: ``python -m bird_sharpness [--write-xmp] [--recursive] PATH...``
"""

from .scoring import ALGORITHM_VERSION, classify, sigma_to_score, verdict_label

__all__ = ["ALGORITHM_VERSION", "classify", "sigma_to_score", "verdict_label"]
