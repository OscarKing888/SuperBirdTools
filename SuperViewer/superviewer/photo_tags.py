# -*- coding: utf-8 -*-
"""Custom per-photo tags for SuperViewer.

Tags are configured by a UTF-8 text file and persisted as XMP sidecar
``dc:subject`` values.  The store only manages tags from the configured
SuperViewer vocabulary; unrelated XMP keywords are preserved.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable

from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.log import get_logger


_log = get_logger("superviewer.photo_tags")


def _normalise_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.fspath(path))


def _normalise_tags(tags: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        clean = str(tag or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _normalise_paths(paths: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths or []:
        if not path:
            continue
        norm = _normalise_path(path)
        key = os.path.normcase(os.path.abspath(norm))
        if key in seen:
            continue
        seen.add(key)
        result.append(norm)
    return result


class PhotoTagConfig:
    """Loads the available tag vocabulary from tags.cfg."""

    def __init__(self, path: str | os.PathLike[str] | None) -> None:
        self.path = Path(path) if path else None

    def load(self) -> list[str]:
        if self.path is None or not self.path.is_file():
            return []
        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            _log.warning("[PhotoTagConfig.load] failed path=%r: %s", str(self.path), exc)
            return []

        tags: list[str] = []
        seen: set[str] = set()
        for line in text.splitlines():
            tag = line.strip()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
        return tags


class PhotoTagSidecarStore:
    """XMP sidecar-backed many-to-many photo/tag store."""

    def __init__(self, metadata: PhotoMetaDataXMP | None = None) -> None:
        self._metadata = metadata or PhotoMetaDataXMP()
        self._lock = threading.RLock()

    def close(self) -> None:
        """Compatibility no-op; sidecar writes do not keep a DB connection open."""
        return None

    def load_tags_for_paths(
        self,
        paths: Iterable[str],
        *,
        allowed_tags: Iterable[str] | None = None,
    ) -> dict[str, set[str]]:
        allowed = set(_normalise_tags(allowed_tags))
        use_allowed_filter = allowed_tags is not None
        result: dict[str, set[str]] = {}
        with self._lock:
            for path in _normalise_paths(paths):
                try:
                    subjects = set(self._metadata.read_subjects(path))
                except Exception as exc:
                    _log.warning("[PhotoTagSidecarStore.load_tags_for_paths] failed path=%r: %s", path, exc)
                    subjects = set()
                if use_allowed_filter:
                    subjects.intersection_update(allowed)
                result[path] = subjects
        return result

    def get_tags(
        self,
        path: str,
        *,
        allowed_tags: Iterable[str] | None = None,
    ) -> set[str]:
        norm = _normalise_path(path)
        return set(self.load_tags_for_paths([norm], allowed_tags=allowed_tags).get(norm, set()))

    def set_tag_for_paths(
        self,
        paths: Iterable[str],
        tag: str,
        enabled: bool,
        *,
        allowed_tags: Iterable[str] | None = None,
    ) -> int:
        clean_tag = str(tag or "").strip()
        if not clean_tag:
            return 0
        if allowed_tags is not None and clean_tag not in set(_normalise_tags(allowed_tags)):
            return 0

        updated = 0
        with self._lock:
            for path in _normalise_paths(paths):
                subjects = self._metadata.read_subjects(path)
                subject_set = set(subjects)
                if enabled:
                    if clean_tag in subject_set:
                        updated += 1
                        continue
                    new_subjects = subjects + [clean_tag]
                else:
                    if clean_tag not in subject_set:
                        updated += 1
                        continue
                    new_subjects = [value for value in subjects if value != clean_tag]
                if self._metadata.write_subjects(path, new_subjects):
                    updated += 1
        return updated

    def clear_tags_for_paths(
        self,
        paths: Iterable[str],
        tags: Iterable[str] | None = None,
        *,
        allowed_tags: Iterable[str] | None = None,
    ) -> int:
        remove_tags = _normalise_tags(tags)
        if not remove_tags and allowed_tags is not None:
            remove_tags = _normalise_tags(allowed_tags)
        remove_set = set(remove_tags)
        remove_all = not remove_tags and allowed_tags is None

        updated = 0
        with self._lock:
            for path in _normalise_paths(paths):
                subjects = self._metadata.read_subjects(path)
                if remove_all:
                    new_subjects: list[str] = []
                else:
                    new_subjects = [value for value in subjects if value not in remove_set]
                if new_subjects == subjects:
                    updated += 1
                    continue
                if self._metadata.write_subjects(path, new_subjects):
                    updated += 1
        return updated
