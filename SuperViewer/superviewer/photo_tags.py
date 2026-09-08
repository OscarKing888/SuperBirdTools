# -*- coding: utf-8 -*-
"""Custom per-photo tags for SuperViewer.

Tags are configured by a UTF-8 text file and persisted as SuperViewer JSON
sidecar subject values.  Existing XMP sidecar subjects remain readable as
fallback data, but new writes go to JSON sidecars.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app_common.exif_io.photo_meta import PhotoMetaData, PhotoMetaDataJSON, PhotoMetaDataXMP
from app_common.log import get_logger


_log = get_logger("superviewer.photo_tags")

SUPERPICKY_DIRNAME = ".superpicky"
PHOTO_TAG_CONFIG_FILENAME = "tags.cfg"


@dataclass
class TagTreeNode:
    """One node in a tags.cfg hierarchy.

    Nodes with children are display-only groups. Leaf nodes (no children) are
    assignable photo tags.
    """

    name: str
    children: list[TagTreeNode] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return bool(self.children)

    @property
    def is_leaf(self) -> bool:
        return not self.children


def iter_tag_tree_leaves(nodes: Iterable[TagTreeNode] | None) -> list[str]:
    """Return DFS leaf tag names, deduped while preserving first-seen order."""
    result: list[str] = []
    seen: set[str] = set()

    def walk(node: TagTreeNode) -> None:
        if node.is_leaf:
            if node.name not in seen:
                seen.add(node.name)
                result.append(node.name)
            return
        for child in node.children:
            walk(child)

    for root in nodes or []:
        walk(root)
    return result


def parse_tag_tree_text(text: str) -> list[TagTreeNode]:
    """Parse indented tags.cfg text into a forest of ``TagTreeNode`` roots.

    Indent may use spaces or tabs. Blank lines and ``#`` comments are ignored.
    Sibling names are deduped (first wins). A node becomes a group once it has
    at least one child; only leaves are assignable tags.
    """
    roots: list[TagTreeNode] = []
    # Stack entries: (indent_width, node, sibling_names_at_this_level)
    stack: list[tuple[int, TagTreeNode, set[str]]] = []
    root_names: set[str] = set()

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        expanded = raw_line.expandtabs(4)
        stripped = expanded.lstrip(" ")
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(expanded) - len(stripped)
        name = stripped.strip()
        if not name:
            continue

        while stack and indent <= stack[-1][0]:
            stack.pop()

        node = TagTreeNode(name=name)
        if not stack:
            if name in root_names:
                continue
            root_names.add(name)
            roots.append(node)
            stack.append((indent, node, set()))
            continue

        parent_indent, parent, sibling_names = stack[-1]
        if indent <= parent_indent:
            # Defensive: should have been popped above.
            continue
        if name in sibling_names:
            continue
        sibling_names.add(name)
        parent.children.append(node)
        stack.append((indent, node, set()))

    return roots


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


def photo_tag_filter_matches(
    selected_filters: Iterable[str],
    photo_tags: Iterable[str],
    *,
    partial_match: bool = False,
) -> bool:
    """Return whether a photo's tags satisfy selected tag filters."""
    filters = _normalise_tags(selected_filters)
    if not filters:
        return True
    tags = _normalise_tags(photo_tags)
    if not tags:
        return False

    if partial_match:
        tag_values = [tag.casefold() for tag in tags]
        return any(
            needle in value
            for needle in (filter_tag.casefold() for filter_tag in filters)
            for value in tag_values
        )

    tag_set = set(tags)
    return all(filter_tag in tag_set for filter_tag in filters)


def find_superpicky_tag_config_path(
    path: str | os.PathLike[str] | None,
    *,
    max_levels: int | None = None,
) -> Path | None:
    """Return the nearest parent .superpicky/tags.cfg path for *path*."""
    if not path:
        return None
    candidate = os.path.normpath(os.fspath(path))
    if os.path.basename(candidate) == SUPERPICKY_DIRNAME and os.path.isdir(candidate):
        return Path(candidate) / PHOTO_TAG_CONFIG_FILENAME

    depth = 0
    while candidate:
        if max_levels is not None and depth > max_levels:
            break
        superpicky_dir = os.path.join(candidate, SUPERPICKY_DIRNAME)
        if os.path.isdir(superpicky_dir):
            return Path(superpicky_dir) / PHOTO_TAG_CONFIG_FILENAME
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
        depth += 1
    return None


class PhotoTagConfig:
    """Loads the available tag vocabulary from tags.cfg."""

    def __init__(self, path: str | os.PathLike[str] | None) -> None:
        self.path = Path(path) if path else None

    def _read_text(self) -> str | None:
        if self.path is None or not self.path.is_file():
            return None
        try:
            return self.path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            _log.warning("[PhotoTagConfig.load] failed path=%r: %s", str(self.path), exc)
            return None

    def load_tree(self) -> list[TagTreeNode]:
        """Load the full tag hierarchy (groups + leaf tags)."""
        text = self._read_text()
        if text is None:
            return []
        return parse_tag_tree_text(text)

    def load_tree_and_tags(self) -> tuple[list[TagTreeNode], list[str]]:
        """Load hierarchy and flattened leaf tags in one read."""
        tree = self.load_tree()
        return tree, iter_tag_tree_leaves(tree)

    def load(self) -> list[str]:
        """Load assignable leaf tags in DFS order (groups excluded)."""
        return iter_tag_tree_leaves(self.load_tree())


TagStates = dict[str, dict[str, bool]]


@dataclass
class TagWriteResult:
    inverse_states: TagStates = field(default_factory=dict)
    failed_paths: dict[str, str] = field(default_factory=dict)
    current_tags: dict[str, set[str]] = field(default_factory=dict)


class PhotoTagSidecarStore:
    """JSON sidecar-backed many-to-many photo/tag store."""

    def __init__(self, metadata: PhotoMetaData | None = None) -> None:
        self._metadata = metadata or PhotoMetaDataJSON(fallback=PhotoMetaDataXMP())
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

    def apply_tag_states(
        self,
        states: TagStates,
        *,
        allowed_tags: Iterable[str] | None = None,
    ) -> TagWriteResult:
        """Apply requested memberships and record only successful changes.

        Strict reads prevent unreadable sidecars from becoming empty snapshots.
        Each path gets one write preserving all unrelated subjects/properties.
        """
        allowed = set(_normalise_tags(allowed_tags)) if allowed_tags is not None else None
        normalized: TagStates = {}
        paths_by_key: dict[str, str] = {}
        for path, values in states.items():
            if not path:
                continue
            norm = _normalise_path(path)
            key = os.path.normcase(os.path.abspath(norm))
            norm = paths_by_key.setdefault(key, norm)
            desired = normalized.setdefault(norm, {})
            for tag, enabled in values.items():
                clean = str(tag or "").strip()
                if clean and (allowed is None or clean in allowed):
                    desired[clean] = bool(enabled)

        result = TagWriteResult()
        with self._lock:
            for path, desired in normalized.items():
                if not desired:
                    continue
                try:
                    if not os.path.isfile(path):
                        raise FileNotFoundError("源照片不存在或不可访问，可能已被移动、重命名或删除。")
                    subjects = (
                        self._metadata.read_subjects(path, strict=True)
                        if isinstance(self._metadata, (PhotoMetaDataJSON, PhotoMetaDataXMP))
                        else self._metadata.read_subjects(path)
                    )
                    before = set(subjects)
                    inverse = {
                        tag: tag in before
                        for tag, enabled in desired.items()
                        if enabled != (tag in before)
                    }
                    if inverse:
                        after = [tag for tag in subjects if desired.get(tag, True)]
                        after.extend(
                            tag for tag, enabled in desired.items()
                            if enabled and tag not in before
                        )
                        if not self._metadata.write_subjects(path, after):
                            raise OSError("无法保存标签侧车，请检查文件权限和磁盘空间。")
                        result.inverse_states[path] = inverse
                        current = set(after)
                    else:
                        current = before
                    result.current_tags[path] = current if allowed is None else current.intersection(allowed)
                except Exception as exc:
                    result.failed_paths[path] = str(exc) or type(exc).__name__
        return result

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
                    if self._metadata.add_subjects(path, [clean_tag]):
                        updated += 1
                else:
                    if clean_tag not in subject_set:
                        updated += 1
                        continue
                    if self._metadata.remove_subjects(path, [clean_tag]):
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
                if remove_all:
                    saved = self._metadata.write_subjects(path, [])
                else:
                    saved = self._metadata.remove_subjects(path, remove_tags)
                if saved:
                    updated += 1
        return updated
