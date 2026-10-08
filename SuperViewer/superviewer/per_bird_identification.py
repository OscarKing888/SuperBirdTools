# -*- coding: utf-8 -*-
"""逐只识鸟：复用清晰度结果、临时裁图和原子 XMP 提交；不依赖 Qt。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from app_common.exif_io.photo_meta import PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_identification import (BirdIDCancelled, BirdIDClient, BirdIDError, BirdIDOptions,
                                  BirdIDResult, _fingerprint, collect_paths, parse_response)

FIELD = "birdid_individuals"
INFO_FIELD = "birdid_individuals_info"


@dataclass(frozen=True)
class PerBirdOptions:
    min_width: int = 64
    min_height: int = 64
    padding_percent: float = 0.0

    def validate(self):
        for value in (self.min_width, self.min_height):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16384:
                raise ValueError("鸟框最小宽高须为 1–16384 像素")
        if not math.isfinite(self.padding_percent) or not 0 <= self.padding_percent <= 50:
            raise ValueError("裁图外扩比例须为 0–50%")


def make_analyzer(params, *, denoised_lookup=None):
    from bird_sharpness.analyzer import BirdSharpnessAnalyzer
    from bird_sharpness.params import AnalysisParams
    # 逐只模式不受批量清晰度的数量上限影响；大小由本功能在原尺寸鸟框上过滤。
    options = AnalysisParams.from_params({**params, "max_birds": 0, "min_bird_side": 0})
    return BirdSharpnessAnalyzer(params=options, denoised_lookup=denoised_lookup)


def camera_box(box, width, height, crop=None):
    """原尺寸、已旋转的像素框 -> 相机画幅归一化坐标，保留 RAW 边缘区。"""
    left, top, right, bottom = crop or (0., 0., 1., 1.)
    x0, y0, x1, y1 = box
    return [(x0 / width - left) / (right - left), (y0 / height - top) / (bottom - top),
            (x1 / width - left) / (right - left), (y1 / height - top) / (bottom - top)]


def identify_individuals(source, client, analyzer, options=PerBirdOptions(), *, on_progress=lambda text: None):
    """每张照片一次提交。取消/过期/全失败保留旧列表，单只失败不阻止其它鸟。"""
    source = os.path.normpath(os.path.abspath(source))
    store = PhotoMetaDataXMP()
    try:
        options.validate()
        client.check_cancelled()
        if Path(source).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS or not Path(source).is_file():
            raise BirdIDError("不是存在的受支持照片")
        sidecar = store.sidecar_path_for(source)
        with xmp_sidecar_write_lock(source):
            before = _fingerprint(source), _fingerprint(sidecar)
            if store._load_or_create_xmp_tree(sidecar) is None:
                raise BirdIDError("已有 XMP 损坏，未写入逐只识别结果")
            if client.options.skip_existing and FIELD in store.read(source):
                return BirdIDResult(source, "skipped", "已有逐只识别记录")
        from PIL import Image
        from bird_sharpness.image_source import load_analysis_image

        on_progress(f"清晰度算法定位鸟体：{Path(source).name}")
        analyzer.load()
        client.check_cancelled()
        image = (analyzer.image_loader() or load_analysis_image)(source)
        result = analyzer.analyze(source, image_loader=lambda _: image, cancelled=client.cancelled.is_set)
        client.check_cancelled()
        if not result.ok:
            raise BirdIDError(f"清晰度检测失败：{result.error}")
        height, width = image.rgb8.shape[:2]
        records = []
        # TemporaryDirectory 先拥有目录；Windows 上先关闭图像文件再发 HTTP 请求。
        with TemporaryDirectory(prefix="superbird-birdid-") as directory:
            for ordinal, bird in enumerate(result.birds):
                client.check_cancelled()
                x0, y0, x1, y1 = bird["box"]
                box = [max(0, math.floor(x0)), max(0, math.floor(y0)),
                       min(width, math.ceil(x1)), min(height, math.ceil(y1))]
                x0, y0, x1, y1 = box
                bw, bh = x1 - x0, y1 - y0
                if bw <= 0 or bh <= 0:
                    raise BirdIDError("清晰度算法返回无效鸟框")
                record = {"index": int(bird.get("index", ordinal)), "box_px": box,
                          "box": camera_box(box, width, height, image.camera_crop),
                          "detection_confidence": float(bird["confidence"]),
                          "cn_name": "", "en_name": "", "confidence": None, "status": "skipped"}
                records.append(record)
                if bw < options.min_width or bh < options.min_height:
                    record["message"] = f"鸟框小于 {options.min_width} × {options.min_height} px，未送识别"
                    continue
                pad_x = math.ceil(bw * options.padding_percent / 100)
                pad_y = math.ceil(bh * options.padding_percent / 100)
                crop = [max(0, x0 - pad_x), max(0, y0 - pad_y),
                        min(width, x1 + pad_x), min(height, y1 + pad_y)]
                record["crop_px"] = crop
                on_progress(f"{Path(source).name} · 鸟 #{record['index'] + 1}（{ordinal + 1}/{len(result.birds)}）")
                temporary = Path(directory) / f"bird-{ordinal + 1}.jpg"
                try:
                    a, b, c, d = crop
                    with Image.fromarray(image.rgb8[b:d, a:c]) as cut:
                        cut.save(temporary, format="JPEG", quality=95, subsampling=0)
                    response = client.recognize_crop(str(temporary))
                    best = parse_response(response)
                    record.update(cn_name=best.get("cn_name", ""), en_name=best.get("en_name", ""),
                                  confidence=float(best["confidence"]), response=response,
                                  status="confirmed" if float(best["confidence"]) >= client.options.threshold else "candidate")
                except BirdIDCancelled:
                    raise
                except Exception as exc:
                    record.update(status="failed", message=str(exc))
                finally:
                    temporary.unlink(missing_ok=True)
        client.check_cancelled()
        identified = sum(r["status"] in {"confirmed", "candidate"} for r in records)
        failed = sum(r["status"] == "failed" for r in records)
        if failed and not identified:
            raise BirdIDError(f"所有可识别鸟体均失败，原列表保留：{next(r['message'] for r in records if r['status'] == 'failed')}")
        info = {"schema": 1, "coordinate_space": "oriented_camera_normalized_xyxy",
                "image_size": [width, height], "camera_crop": image.camera_crop,
                "image_source": image.source, "analysis_version": result.version,
                "analysis_params": analyzer.params.as_params(),
                "min_width": options.min_width, "min_height": options.min_height,
                "padding_percent": options.padding_percent, "threshold": client.options.threshold,
                "source_size": before[0][2], "source_mtime_ns": before[0][3],
                "source_extension": Path(source).suffix.lower()}
        values = {FIELD: json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                  INFO_FIELD: json.dumps(info, ensure_ascii=False, allow_nan=False, separators=(",", ":"))}
        fields = {f"XMP-superpicky:{k}": v for k, v in values.items()}
        with xmp_sidecar_write_lock(source):
            client.check_cancelled()
            if before != (_fingerprint(source), _fingerprint(sidecar)):
                return BirdIDResult(source, "skipped", "识别期间照片或 XMP 已变化，请重试")
            if not store.write(source, fields):
                raise BirdIDError("XMP 保存失败，原元数据已保留")
            saved = _fingerprint(sidecar)
        message = (f"找到 {len(records)} 只鸟，识别 {identified} 只，"
                   f"过滤 {sum(r['status'] == 'skipped' for r in records)} 只，失败 {failed} 只")
        return BirdIDResult(source, "partial" if failed else "success", message,
                            updates={**values, **fields}, saved_fingerprint=saved, source_fingerprint=before[0])
    except BirdIDCancelled:
        return BirdIDResult(source, "cancelled", "已停止，此照片原列表保留")
    except Exception as exc:
        return BirdIDResult(source, "failed", f"[BirdID 逐只识别] {exc}")


def read_individuals(metadata):
    """仅解析缓存元数据；损坏或未知版本不给预览提供坐标。"""
    try:
        info = json.loads(metadata.get(f"XMP-superpicky:{INFO_FIELD}", metadata.get(INFO_FIELD, "{}")))
        items = json.loads(metadata.get(f"XMP-superpicky:{FIELD}", metadata.get(FIELD, "[]")))
        if info.get("schema") != 1 or info.get("coordinate_space") != "oriented_camera_normalized_xyxy" or not isinstance(items, list):
            return []
        valid = []
        for item in items:
            box, px = item["box"], item["box_px"]
            if any(len(b) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in b)
                   or b[0] >= b[2] or b[1] >= b[3] for b in (box, px)):
                continue
            if not isinstance(item["index"], int) or item["index"] < 0:
                continue
            if not all(isinstance(item.get(k, ""), str) for k in ("cn_name", "en_name", "message")):
                continue
            score, det = item.get("confidence"), item["detection_confidence"]
            if score is not None and (not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100):
                continue
            if not isinstance(det, (int, float)) or not math.isfinite(det):
                continue
            valid.append(item)
        return valid
    except (ValueError, TypeError, KeyError, AttributeError):
        return []


def main(argv=None):
    parser = argparse.ArgumentParser(description="复用清晰度算法逐只识鸟，保存原图区域到 XMP")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:5156")
    parser.add_argument("--threshold", type=float, default=50)
    parser.add_argument("--min-width", type=int, default=64)
    parser.add_argument("--min-height", type=int, default=64)
    parser.add_argument("--padding", type=float, default=0)
    parser.add_argument("--source", choices=("raw", "jpeg"), default="jpeg")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args(argv)
    options = PerBirdOptions(args.min_width, args.min_height, args.padding)
    options.validate()
    client = BirdIDClient(BirdIDOptions(args.url, args.threshold, args.skip_existing))
    failed = False
    try:
        client.health()
        analyzer = make_analyzer({"image_source": args.source})
        for path in collect_paths(args.paths, recursive=args.recursive):
            result = identify_individuals(path, client, analyzer, options)
            print(json.dumps({"source": result.source, "status": result.status, "message": result.message}, ensure_ascii=False))
            failed |= result.status in {"failed", "partial"}
    finally:
        client.cancel()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
