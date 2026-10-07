# -*- coding: utf-8 -*-
"""按已有鸟名补全 XMP 拼音；本地查表，不调用识鸟服务，不写原图/报告。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from app_common.bird_pinyin import PINYIN_FIELD, PINYIN_SOURCE_FIELD, PINYIN_ALIASES, bird_name, pinyin_for, stored_pinyin
from app_common.exif_io.photo_meta import (
    PhotoMetaDataEXIFEmbeded, PhotoMetaDataReportDB, PhotoMetaDataXMP, xmp_sidecar_write_lock,
)
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_identification import _fingerprint, collect_paths


@dataclass
class PinyinResult:
    source: str
    status: str
    message: str
    updates: dict = field(default_factory=dict)
    saved_fingerprint: tuple | None = None


class PinyinUpdater:
    """在一个后台批次中复用只读报告解析器，已有拼音保持原值。"""

    def __init__(self):
        self.store = PhotoMetaDataXMP()
        self.report = PhotoMetaDataReportDB()

    def _metadata(self, path: str) -> dict:
        xmp = self.store.read(path)
        from app_common.report_db import report_row_to_exiftool_style
        row = self.report.row_for(path) or {}
        report = {**row, **report_row_to_exiftool_style(row, path)}
        if bird_name(xmp) and bird_name(xmp) != bird_name(report):
            report = {key: value for key, value in report.items()
                      if key.removeprefix("XMP-superpicky:").removeprefix("report.") not in (*PINYIN_ALIASES, PINYIN_SOURCE_FIELD)}
        name = bird_name(xmp) or bird_name(report)
        embedded = {} if name else PhotoMetaDataEXIFEmbeded().read(path)
        name = name or bird_name(embedded)
        metadata = {**embedded, **report, **xmp}
        # 侧车中的标题也必须胜过旧报告鸟名，界面与写入使用同一鸟名。
        metadata["bird_species_cn"] = name
        metadata["XMP-superpicky:bird_species_cn"] = name
        return metadata

    def update(self, path: str, *, cancelled=lambda: False) -> PinyinResult:
        path = os.path.normpath(os.path.abspath(path))
        try:
            if cancelled():
                return PinyinResult(path, "cancelled", "已取消")
            if not Path(path).is_file() or Path(path).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                raise ValueError("照片不存在或格式不支持")
            with xmp_sidecar_write_lock(path):
                sidecar = self.store.sidecar_path_for(path)
                before = _fingerprint(path), _fingerprint(sidecar)
                if self.store._load_or_create_xmp_tree(sidecar) is None:
                    raise ValueError("已有 XMP 损坏，未覆盖")
            metadata = self._metadata(path)
            name = bird_name(metadata)
            if not name:
                return PinyinResult(path, "skipped", "没有鸟名")
            if stored_pinyin(metadata, name):
                return PinyinResult(path, "skipped", "已有拼音")
            text = pinyin_for(name)
            if not text:
                return PinyinResult(path, "skipped", f"词表未收录：{name}")
            fields = {f"XMP-superpicky:{PINYIN_FIELD}": text,
                      f"XMP-superpicky:{PINYIN_SOURCE_FIELD}": name}
            with xmp_sidecar_write_lock(path):
                if cancelled():
                    return PinyinResult(path, "cancelled", "已取消")
                if before != (_fingerprint(path), _fingerprint(self.store.sidecar_path_for(path))):
                    return PinyinResult(path, "skipped", "照片或 XMP 已变化，请重试")
                if not self.store.write(path, fields):
                    raise OSError("XMP 保存失败，原有数据已保留")
                saved = _fingerprint(self.store.sidecar_path_for(path))
            updates = {**fields, PINYIN_FIELD: text, PINYIN_SOURCE_FIELD: name}
            return PinyinResult(path, "success", f"{name}：{text}", updates, saved)
        except Exception as exc:
            return PinyinResult(path, "failed", f"[BirdPinyin] {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="根据已有鸟名补全缺失拼音到 XMP")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--recursive", action="store_true")
    args = parser.parse_args(argv)
    from app_common.exif_io import close_exiftool_process

    try:
        updater = PinyinUpdater()
        paths = collect_paths(args.paths, recursive=args.recursive)
        if not paths:
            print("没有找到受支持的照片")
            return 1
        failed = False
        for path in paths:
            result = updater.update(path)
            print(json.dumps({"source": path, "status": result.status, "message": result.message}, ensure_ascii=False))
            failed |= result.status == "failed"
        return int(failed)
    finally:
        close_exiftool_process()
