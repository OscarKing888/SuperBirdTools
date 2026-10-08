"""显式运行的打包诊断：隔离模型设置，不读取或修改用户工作区。"""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import tempfile


def check_export(directory: Path) -> dict:
    from PIL import Image
    from app_common.exif_io import get_exiftool_executable_path, close_exiftool_process
    from app_common.exif_io.exiftool_runner import run_exiftool
    from birdstamp.export_metadata import save_export_image

    executable = get_exiftool_executable_path()
    if not executable:
        raise RuntimeError("Packaged ExifTool unavailable")

    def run(*args):
        result = run_exiftool(executable, list(args), timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout

    source = directory / "中文 原图.jpg"
    text = directory / "文字.txt"
    text.write_text("小勺子，原始拍摄信息。", encoding="utf-8")
    try:
        with Image.new("RGB", (120, 80), "#6395bd") as original:
            original.save(source)
        run("-overwrite_original", "-charset", "filename=UTF8", "-Make=SONY", "-ISO=1600",
            f"-UserComment<={text}", str(source))
        before = source.read_bytes()
        for suffix, format in (("jpg", "JPEG"), ("png", "PNG")):
            output = directory / f"中文 导出.{suffix}"
            with Image.new("RGB", (40, 60), "#6395bd") as rendered:
                save_export_image(rendered, output, source_path=source, format=format)
            tags = json.loads(run("-j", "-s", "-n", str(output)))[0]
            assert tags["UserComment"] == text.read_text(encoding="utf-8")
            assert tags["Make"] == "SONY" and tags["ISO"] == 1600
            assert tags["ExifImageWidth"] == 40 and tags["ExifImageHeight"] == 60
            assert source.read_bytes() == before
        return {"exiftool": executable, "jpeg_png_export": "ok", "chinese_exif": "ok"}
    finally:
        close_exiftool_process()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="JSON report for windowed executables")
    parser.add_argument("--export-only", action="store_true", help="Check EXIF export without model inference")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="birdstamp-runtime-check-") as directory:
        previous = os.environ.get("YOLO_CONFIG_DIR")
        os.environ["YOLO_CONFIG_DIR"] = directory
        try:
            report = {}
            if not args.export_only:
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
                report.update({"torch": torch.__version__, "model_config": str(config),
                               "cpu_inference": "ok", "pretrained_weights": False})
            # Preserve Torch-before-Qt loading on Windows. The metadata package
            # can import Qt, whose bundled MSVC runtime conflicts with Torch.
            report.update(check_export(Path(directory)))
            payload = json.dumps(report, ensure_ascii=False, indent=2)
            if args.output:
                args.output.write_text(payload, encoding="utf-8")
            print(payload)
        finally:
            if previous is None:
                os.environ.pop("YOLO_CONFIG_DIR", None)
            else:
                os.environ["YOLO_CONFIG_DIR"] = previous
    return 0
