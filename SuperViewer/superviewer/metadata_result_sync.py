# -*- coding: utf-8 -*-
"""后台 XMP 编辑结果的当前目录缓存同步；不重新选择/加载预览。"""
from __future__ import annotations

import os

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from .bird_identification import _fingerprint
try:
    from PyQt6.QtCore import QSignalBlocker
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QSignalBlocker


class MetadataResultSync:
    def __init__(self, files):
        self.files = files
        self._stamp = None

    def sync(self, results, display_paths=()):
        files = self.files
        all_files = getattr(files, "_all_files", ())
        stamp = (id(all_files), len(all_files))
        if stamp != self._stamp:
            self._files, self._stamp = all_files, stamp
            self._listed = set(all_files)
            self._by_stem = {}
            for path in all_files:
                key = os.path.normcase(os.path.splitext(os.path.abspath(path))[0])
                self._by_stem.setdefault(key, []).append(path)
        aliases = {}
        for display, source in display_paths:
            if display in self._listed:
                aliases.setdefault(os.path.normcase(os.path.abspath(source)), []).append(display)
        updates = {}
        for result in results:
            if not result.updates:
                continue
            if result.saved_fingerprint != _fingerprint(PhotoMetaDataXMP().sidecar_path_for(result.source)):
                continue
            key = os.path.normcase(os.path.splitext(result.source)[0])
            paths = [*self._by_stem.get(key, []), *aliases.get(os.path.normcase(result.source), [])]
            if paths:
                for path in [*paths, result.source]:
                    updates.setdefault(path, {}).update(result.updates)
        if not updates:
            return
        blocker = QSignalBlocker(files)
        try:
            files.sync_metadata_edits_for_paths(updates)
        finally:
            del blocker
        files.photo_metadata_cache_updated.emit(list(updates))
