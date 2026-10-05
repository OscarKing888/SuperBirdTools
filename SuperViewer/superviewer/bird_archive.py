# -*- coding: utf-8 -*-
"""珍禽入册：无 Qt 的鸟名归档、命名规划及照片/XMP 事务。"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

from app_common.exif_io.photo_meta import (
    PhotoMetaDataEXIFEmbeded, PhotoMetaDataReportDB, PhotoMetaDataXMP,
    xmp_sidecar_write_lock,
)
from app_common.exif_io.xmp_sidecar import _photo_descriptions, find_same_stem_xmp_sidecar
from app_common.file_transactions import transfer_file_pairs
from app_common.image_formats import (
    HEIF_IMAGE_EXTENSIONS, IMAGE_EXTENSIONS, JPEG_IMAGE_EXTENSIONS,
    PHOTOSHOP_IMAGE_EXTENSIONS, RAW_IMAGE_EXTENSIONS,
)


@dataclass(frozen=True)
class ArchiveOptions:
    directory: str = ""
    mode: str = "move"
    date_prefix: bool = True


@dataclass
class ArchiveResult:
    sources: tuple[str, ...]
    destinations: tuple[str, ...] = ()
    status: str = "success"
    message: str = ""


def name_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def safe_component(value: str, *, max_bytes: int = 140) -> str:
    """中文保留，跨 Windows/macOS 清理非法字符、设备名和尾部点/空格。"""
    value = unicodedata.normalize("NFC", value.strip())
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).rstrip(" .")
    if not value or value in {".", ".."}:
        raise ValueError("鸟名或文件名为空，无法入册")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)]}:
        value = "_" + value
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1]
    return value.rstrip(" .")


def capture_prefix(metadata: dict) -> str:
    for key in ("XMP-exif:DateTimeOriginal", "ExifIFD:DateTimeOriginal", "EXIF:DateTimeOriginal", "IFD0:DateTimeOriginal",
                "date_time_original", "DateTimeOriginal", "capture_time"):
        value = str(metadata.get(key) or "")
        match = re.match(r"^(\d{4})[:-](\d{2})[:-](\d{2})(?:[ T](\d{2}):(\d{2}):(\d{2}))?", value)
        if match:
            parts = [int(v or 0) for v in match.groups()]
            try:
                date = datetime(*parts)
            except ValueError:
                continue
            return date.strftime("%Y%m%d_%H%M%S_") if match[4] else date.strftime("%Y%m%d_")
    return "日期未知_"


def _species(metadata: dict) -> str:
    # Title 是 Viewer 中可编辑的鸟名；显式清空的标题不能被旧报告复活。
    for key in ("XMP-dc:Title", "Title", "XMP-superpicky:bird_species_cn", "bird_species_cn", "title"):
        if key in metadata:
            value = str(metadata[key] or "").strip()
            return "" if value.casefold() in {"n/a", "unknown", "未知", "未知鸟种", "未识别"} else value
    return ""


def read_archive_metadata(path: str, report_row: dict | None = None) -> tuple[dict, dict]:
    """慢读取只在 worker/CLI 调用；侧车优先，报告始终只读。"""
    sidecar = find_same_stem_xmp_sidecar(path)
    if sidecar:
        if Path(sidecar).is_symlink():
            raise ValueError(f"XMP 为符号链接，请先处理为独立侧车：{sidecar}")
        ET.parse(sidecar)  # 损坏的现有 XMP 必须报错，不能被当成没有元数据。
    if report_row is None:
        report = PhotoMetaDataReportDB().read(path)
    else:
        from app_common.report_db import report_row_to_exiftool_style
        report = {**report_row, **report_row_to_exiftool_style(report_row, path)}
    embedded = PhotoMetaDataEXIFEmbeded().read(path)
    xmp = PhotoMetaDataXMP().read(path)
    metadata = {**embedded, **report, **xmp}
    for layer in (xmp, report, embedded):
        if any(key in layer for key in ("XMP-dc:Title", "Title", "bird_species_cn", "XMP-superpicky:bird_species_cn", "title")):
            metadata["archive_species"] = _species(layer)
            break
    return metadata, report


def _preserve_report(path: str, report: dict) -> None:
    """离开报告目录前把缺少的报告字段固化到同 stem XMP，不覆盖用户编辑。"""
    if not report:
        return
    from app_common.report_db import PHOTO_COLUMNS
    xmp = PhotoMetaDataXMP()
    existing = xmp.read(path)
    values = {key: report[key] for key, *_ in PHOTO_COLUMNS
              if key in report and report[key] is not None and str(report[key]).strip()
              and key not in existing and f"XMP-superpicky:{key}" not in existing}
    # 标准 XMP 的当前用户值高于旧报告的原始列。
    protected = xmp._protected_report_fields_from_write_fields(existing)
    for key in ("XMP-exif:DateTimeOriginal", "XMP-exif:DateTimeDigitized"):
        if key in existing:
            protected["date_time_original"] = existing[key]
    for key in protected:
        values.pop(key, None)
    if values and not xmp.write_superpicky_fields(path, values, _protected_report_fields=protected):
        raise OSError(f"无法保存报告元数据到 XMP：{path}")


def _make_sidecar_portable(sidecar: str, sources: tuple[str, ...]) -> None:
    """将指向当前源图的 RDF 文件引用改成同文件主资源，改名后仍可读取。"""
    tree = ET.parse(sidecar)
    changed = False
    about_key = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"
    for source in sources:
        for desc in _photo_descriptions(tree.getroot(), source):
            about = desc.get(about_key, "")
            if about and not about.lower().startswith(("uuid:", "urn:uuid:")):
                desc.set(about_key, "")
                changed = True
    if changed and not PhotoMetaDataXMP._write_tree_atomic(tree, Path(sidecar)):
        raise OSError(f"无法保存可迁移的 XMP：{sidecar}")


def archive_format_directory(path: Path) -> str:
    """使用共享格式定义：相机源片、PSD 和 PNG/JPEG 成片分别入子目录。"""
    extension = path.suffix.lower()
    if extension in RAW_IMAGE_EXTENSIONS or extension in HEIF_IMAGE_EXTENSIONS:
        return "RAW"
    if extension in PHOTOSHOP_IMAGE_EXTENSIONS:
        return "PSD"
    if extension in JPEG_IMAGE_EXTENSIONS or extension == ".png":
        return "Export"
    return ""  # 未指定分类的其它受支持图片仍放在鸟名目录。


@dataclass
class ArchiveSession:
    """单批次目录索引；同 stem 的 RAW/JPEG 共用一个命名分配。"""
    options: ArchiveOptions
    _folders: dict = field(default_factory=dict)
    _stems: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.options.directory.strip():
            raise ValueError("请先选择归档目录")
        if self.options.mode not in {"move", "copy"}:
            raise ValueError("归档方式必须是 move 或 copy")
        self.root = Path(self.options.directory).expanduser().absolute()
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("归档位置不是目录")
        if self.root.exists():
            self._folders[str(self.root)] = {name_key(p.name): p for p in self.root.iterdir()}

    def _child_directory(self, parent: Path, name: str) -> Path:
        key = str(parent)
        if key not in self._folders:
            self._folders[key] = {name_key(p.name): p for p in parent.iterdir()} if parent.exists() else {}
        folder = self._folders[key].setdefault(name_key(name), parent / name)
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"归档目录被同名文件占用：{folder}")
        if not folder.resolve().is_relative_to(parent.resolve()):
            raise ValueError(f"归档目录指向所属目录之外：{folder}")
        return folder

    def destination(self, paths: tuple[Path, ...], species: str, metadata: dict) -> tuple[tuple[Path, ...], str]:
        bird_folder = self._child_directory(self.root, safe_component(species))
        folders = tuple(self._child_directory(bird_folder, category) if category else bird_folder
                        for category in (archive_format_directory(p) for p in paths))
        if any(path.parent.resolve() == folder.resolve() for path, folder in zip(paths, folders)):
            raise ValueError("照片已经位于对应鸟名的格式目录，无需重复入册")
        used_sets = []
        for folder in dict.fromkeys(folders):
            key = str(folder.resolve())
            if key not in self._stems:
                self._stems[key] = {name_key(p.stem) for p in folder.iterdir()} if folder.exists() else set()
            used_sets.append(self._stems[key])
        prefix = capture_prefix(metadata) if self.options.date_prefix else ""
        original = safe_component(paths[0].stem)
        base = original if prefix and original.startswith(prefix) else prefix + original
        candidate = base
        suffix = 2
        # 任一格式目录发生冲突时整组加相同序号，保留 RAW/成片对应关系。
        while any(name_key(candidate) in used for used in used_sets):
            candidate = f"{base}_{suffix:03d}"
            suffix += 1
        for used in used_sets:
            used.add(name_key(candidate))
        return folders, candidate

    def archive_group(self, paths: tuple[Path, ...], report_rows: dict) -> ArchiveResult:
        sources = tuple(str(p) for p in paths)
        if any(not p.is_file() or p.is_symlink() for p in paths):
            raise ValueError("源照片缺失或为符号链接，请检查后重试")
        with xmp_sidecar_write_lock(sources[0]):
            records = [read_archive_metadata(str(p), report_rows.get(str(p))) for p in paths]
            species = {m.get("archive_species", "") for m, _ in records}
            if "" in species:
                return ArchiveResult(sources, status="skipped", message="缺少鸟名，请先填写鸟名再入册")
            if len(species) != 1:
                raise ValueError("同名 RAW/JPEG 的鸟名不一致，请先统一鸟名")
            folders, stem = self.destination(paths, species.pop(), records[0][0])
            # 保留报告中的鸟名、评分、标签等，不能让移动后失去报告上下文。
            for path, (_, report) in zip(paths, records):
                _preserve_report(str(path), report)
            sidecar = find_same_stem_xmp_sidecar(sources[0])
            pairs = [(str(p), str(folder / (stem + p.suffix))) for p, folder in zip(paths, folders)]
            copied_sidecars = []
            with ExitStack() as cleanup:
                if sidecar:
                    _make_sidecar_portable(sidecar, sources)
                    sidecar_folders = list(dict.fromkeys(folders))
                    pairs.append((sidecar, str(sidecar_folders[0] / (stem + Path(sidecar).suffix))))
                    if len(sidecar_folders) > 1:
                        # 每个目标目录各放一份 XMP；额外副本有独立源路径，
                        # 与原侧车一起参加共享事务，避免多目标回滚混淆同一个源。
                        temporary = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix="sbt-archive-xmp-")))
                        for index, folder in enumerate(sidecar_folders[1:], 1):
                            replica = temporary / f"{index}.xmp"
                            shutil.copy2(sidecar, replica)
                            pairs.append((str(replica), str(folder / (stem + Path(sidecar).suffix))))
                            copied_sidecars.append(str(replica))
                    selected = set(sources)
                    # 未选择的同名照片仍依赖源 XMP；只复制侧车，不顺带移动未选照片。
                    if any(str(p) not in selected and name_key(p.stem) == name_key(paths[0].stem)
                           and p.suffix.lower() in IMAGE_EXTENSIONS for p in paths[0].parent.iterdir()):
                        copied_sidecars.append(sidecar)
                transfer_file_pairs(pairs, action="cut" if self.options.mode == "move" else "copy",
                                    copy_sources=tuple(copied_sidecars), no_replace=True)
        return ArchiveResult(sources, tuple(dest for _, dest in pairs[:len(paths)]))


def archive_photos(paths, options: ArchiveOptions, *, report_rows=None, cancelled=lambda: False,
                   on_result=lambda result: None, on_progress=lambda done, total: None) -> list[ArchiveResult]:
    session = ArchiveSession(options)
    groups = {}
    results = []
    seen = set()
    for value in paths:
        path = Path(value).absolute()
        if str(path) in seen:
            continue
        seen.add(str(path))
        groups.setdefault((str(path.parent), name_key(path.stem)), []).append(path)
    total = len(seen)
    done = 0
    for group in groups.values():
        if cancelled():
            break
        sources = tuple(str(p) for p in group)
        try:
            if any(p.suffix.lower() not in IMAGE_EXTENSIONS for p in group):
                result = ArchiveResult(sources, status="skipped", message="仅支持照片入册")
            elif len({name_key(p.suffix) for p in group}) != len(group):
                raise ValueError("同组文件仅大小写不同，请先更名以兼容 Windows")
            else:
                result = session.archive_group(tuple(group), report_rows or {})
        except Exception as exc:
            result = ArchiveResult(sources, status="failed", message=str(exc))
        results.append(result)
        done += len(group)
        on_result(result)
        on_progress(done, total)
    return results


def main(argv=None):
    import argparse
    from app_common.exif_io import close_exiftool_process

    parser = argparse.ArgumentParser(description="珍禽入册：按鸟名归档照片与 XMP")
    parser.add_argument("photos", nargs="+")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--mode", choices=("move", "copy"), default="move")
    parser.add_argument("--no-date-prefix", action="store_true")
    args = parser.parse_args(argv)
    try:
        results = archive_photos(args.photos, ArchiveOptions(args.directory, args.mode, not args.no_date_prefix))
        for result in results:
            print(json.dumps(result.__dict__, ensure_ascii=False))
        return int(any(r.status != "success" for r in results))
    finally:
        close_exiftool_process()


if __name__ == "__main__":
    raise SystemExit(main())
