# -*- coding: utf-8 -*-
"""手动修改稀有度：GUI/CLI 共用，仅原子写入同名 XMP。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path

from app_common.bird_pinyin import bird_name
from app_common.bird_rarity import (
    IUCN_FIELD, RARITY_FIELD, RARITY_SOURCE_FIELD, RARITY_MISSING_FIELD,
    RARITY_COMPAT_FIELDS, rarity_metadata, rarity_score,
)
from app_common.exif_io.photo_meta import PhotoMetaDataProxy, PhotoMetaDataXMP, xmp_sidecar_write_lock
from app_common.image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .bird_identification import _fingerprint, collect_paths


@dataclass
class RarityResult:
    source: str
    message: str = ""
    updates: dict = field(default_factory=dict)
    saved_fingerprint: tuple | None = None


def save_rarity(path: str, score: float, *, cancelled=lambda: False) -> RarityResult:
    """保留当前鸟种的保护等级；旧鸟名标记不得遮蔽本次手动编辑。"""
    value = rarity_score(score)
    if value is None:
        raise ValueError("稀有度必须是 0–100 的有效数值")
    value = round(value, 2)
    source = os.path.normpath(os.path.abspath(path))
    if not Path(source).is_file() or Path(source).suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError(f"照片不存在或格式不支持：{source}")
    store = PhotoMetaDataXMP()
    sidecar = store.sidecar_path_for(source)
    with xmp_sidecar_write_lock(source):
        before = _fingerprint(source), _fingerprint(sidecar)
        if store._load_or_create_xmp_tree(sidecar) is None:
            raise ValueError(f"已有 XMP 损坏，未覆盖：{sidecar}")
    metadata = PhotoMetaDataProxy(xmp=store).read(source)
    # 旧报告的私有鸟名不可胜过侧车新标题。
    name = bird_name(store.read(source)) or bird_name(metadata)
    metadata["XMP-superpicky:bird_species_cn"] = name
    _, category = rarity_metadata(metadata)
    values = {
        RARITY_FIELD: value, IUCN_FIELD: category, RARITY_SOURCE_FIELD: name,
        RARITY_MISSING_FIELD: "" if category else IUCN_FIELD,
    }
    fields = {f"XMP-superpicky:{key}": val for key, val in values.items()}
    fields.update({RARITY_COMPAT_FIELDS[RARITY_FIELD]: f"{value:.2f}",
                   RARITY_COMPAT_FIELDS[IUCN_FIELD]: category})
    with xmp_sidecar_write_lock(source):
        if cancelled():
            return RarityResult(source, "已取消")
        if before != (_fingerprint(source), _fingerprint(sidecar)):
            raise RuntimeError("照片或 XMP 已变化，请重试")
        if not store.write(source, fields):
            raise OSError(f"稀有度写入失败，原有 XMP 已保留：{sidecar}")
        stamp = _fingerprint(sidecar)
    updates = {**fields, **values, **{f"report.{key}": val for key, val in values.items()}}
    return RarityResult(source, updates=updates, saved_fingerprint=stamp)


def main(argv=None):
    parser = argparse.ArgumentParser(description="批量设置照片稀有度，只写 XMP")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--score", type=float, required=True, help="0–100，最多保留两位小数")
    args = parser.parse_args(argv)
    if rarity_score(args.score) is None:
        parser.error("--score 必须为 0–100 的有效数值")
    from app_common.exif_io import close_exiftool_process
    try:
        paths = collect_paths(args.paths)
        failed = not paths
        for path in paths:
            try:
                save_rarity(path, args.score)
                print(f"{path}：已保存 {args.score:g}")
            except Exception as exc:
                failed = True
                print(f"{path}：{exc}")
        return int(failed)
    finally:
        close_exiftool_process()


if __name__ == "__main__":
    raise SystemExit(main())
