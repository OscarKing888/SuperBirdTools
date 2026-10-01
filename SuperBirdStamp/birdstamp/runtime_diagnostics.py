"""显式运行的打包诊断：隔离模型设置，不读取或修改用户工作区。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="birdstamp-runtime-check-") as directory:
        previous = os.environ.get("YOLO_CONFIG_DIR")
        os.environ["YOLO_CONFIG_DIR"] = directory
        try:
            import numpy as np
            import torch
            import ultralytics
            from ultralytics import YOLO
            config = Path(ultralytics.__file__).parent / "cfg/models/11/yolo11.yaml"
            if not config.is_file():
                raise RuntimeError(f"打包模型结构资源缺失: {config}")
            model = YOLO(str(config), task="detect")
            result = model.predict(np.zeros((64, 64, 3), dtype=np.uint8), imgsz=64, device="cpu",
                                   verbose=False, save=False)
            assert len(result) == 1 and result[0].orig_shape == (64, 64)
            print(json.dumps({"torch": torch.__version__, "model_config": str(config),
                              "cpu_inference": "ok", "pretrained_weights": False}))
        finally:
            if previous is None:
                os.environ.pop("YOLO_CONFIG_DIR", None)
            else:
                os.environ["YOLO_CONFIG_DIR"] = previous
    return 0
