# -*- coding: utf-8 -*-
"""后台 XMP 编辑结果的当前目录缓存同步；不重新选择/加载预览。"""
from __future__ import annotations

import os

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.xmp_sidecar import same_stem_key
from .bird_identification import _fingerprint
try:
    from PyQt6.QtCore import QSignalBlocker
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QSignalBlocker


class MetadataResultSync:
    def __init__(self, files):
        self.files = files
        self._stamp = None

    def sync(self, results, display_paths=(), *, replace=False):
        files = self.files
        all_files = getattr(files, "_all_files", ())
        stamp = (id(all_files), len(all_files))
        if stamp != self._stamp:
            self._files, self._stamp = all_files, stamp
            self._listed = set(all_files)
            self._by_stem = {}
            for path in all_files:
                key = same_stem_key(os.path.abspath(path))
                self._by_stem.setdefault(key, []).append(path)
        aliases = {}
        for display, source in display_paths:
            if display in self._listed:
                aliases.setdefault(os.path.normcase(os.path.abspath(source)), []).append(display)
        updates = {}
        for result in results:
            if not result.updates:
                continue
            revision = getattr(result, "revision", None)
            if revision is not None:
                from app_common.exif_io.metadata_store import get_store
                if get_store().current_revision(result.source) != revision:
                    continue
            elif result.saved_fingerprint != _fingerprint(PhotoMetaDataXMP().sidecar_path_for(result.source)):
                continue
            key = same_stem_key(os.path.abspath(result.source))
            paths = [*self._by_stem.get(key, []), *aliases.get(os.path.normcase(result.source), [])]
            if paths:
                for path in [*paths, result.source]:
                    fresh = result.metadata_by_path.get(path, result.updates) if replace else result.updates
                    updates.setdefault(path, {}).update(fresh)
        if not updates:
            return
        blocker = QSignalBlocker(files)
        try:
            if replace:
                files.replace_photo_metadata_for_paths(updates)
            else:
                files.sync_metadata_edits_for_paths(updates)
        finally:
            del blocker
        if replace:
            # 整份替换改变了标签快照，旧撤销记录不再适用于新侧车。
            files.clear_tag_history()
        files.photo_metadata_cache_updated.emit(list(updates))
        if replace:
            files.photo_tags_cache_updated.emit(list(updates))
