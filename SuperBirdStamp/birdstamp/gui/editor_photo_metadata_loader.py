from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from app_common.exif_io import extract_many_with_xmp_priority
from app_common.log import get_logger

_log = get_logger("editor.photo_metadata_loader")
_PHOTO_LIST_METADATA_CHUNK_SIZE = 48


class EditorPhotoListMetadataLoader(QThread):
    """后台批量读取列表、模板预览和导出共用的完整 EXIF/XMP 快照。"""

    metadata_batch_ready = pyqtSignal(object)  # dict[norm_path, metadata_dict]
    progress_updated = pyqtSignal(int, int)  # (current_count, total_count)

    def __init__(self, paths: list[str | Path], parent=None) -> None:
        super().__init__(parent)
        self._paths = self._normalize_paths(paths)
        self._stop_flag = False

    def stop(self) -> None:
        self._stop_flag = True
        self.requestInterruption()

    def run(self) -> None:
        total = len(self._paths)
        if total <= 0 or self._stop_flag:
            return
        processed = 0
        chunk_size = max(1, _PHOTO_LIST_METADATA_CHUNK_SIZE)
        _log.info("[EditorPhotoListMetadataLoader.run] START paths=%s chunk=%s", total, chunk_size)
        try:
            for index in range(0, total, chunk_size):
                if self._should_stop():
                    _log.info("[EditorPhotoListMetadataLoader.run] interrupted before chunk")
                    return
                chunk = self._paths[index : index + chunk_size]
                batch = self._read_chunk(chunk)
                if self._should_stop():
                    _log.info("[EditorPhotoListMetadataLoader.run] interrupted after chunk")
                    return
                if batch:
                    self.metadata_batch_ready.emit(batch)
                processed += len(chunk)
                self.progress_updated.emit(min(processed, total), total)
        except Exception as exc:
            _log.warning("[EditorPhotoListMetadataLoader.run] failed: %s", exc)
        _log.info("[EditorPhotoListMetadataLoader.run] END")

    def _read_chunk(self, chunk: list[str]) -> dict[str, dict[str, Any]]:
        if not chunk:
            return {}
        try:
            # 限定标签的浏览器缓存可能只有尺寸/report 字段，且不会总是合并 sidecar。
            # 模板消费只读快照，必须在后台一次读全，并让 XMP 覆盖同语义字段。
            raw_batch = extract_many_with_xmp_priority([Path(path) for path in chunk])
        except Exception as exc:
            _log.warning("[EditorPhotoListMetadataLoader._read_chunk] batch read failed: %s", exc)
            raw_batch = {}

        normalized_batch: dict[str, dict[str, Any]] = {}
        for raw_path in chunk:
            if self._should_stop():
                return {}
            norm_path = os.path.normpath(raw_path)
            record = raw_batch.get(Path(raw_path).resolve(strict=False))
            if isinstance(record, dict):
                normalized = dict(record)
            else:
                normalized = {}
            normalized.setdefault("SourceFile", raw_path)
            normalized_batch[norm_path] = normalized
        return normalized_batch

    def _should_stop(self) -> bool:
        return self._stop_flag or self.isInterruptionRequested()

    @staticmethod
    def _normalize_paths(paths: list[str | Path]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_path in paths or []:
            try:
                resolved = Path(raw_path).resolve(strict=False)
            except Exception:
                continue
            norm_path = os.path.normpath(str(resolved))
            dedup_key = os.path.normcase(norm_path)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            normalized.append(norm_path)
        return normalized
