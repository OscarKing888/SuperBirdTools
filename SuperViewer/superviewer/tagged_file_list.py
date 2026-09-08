# -*- coding: utf-8 -*-
"""SuperViewer file list with custom photo tags."""
from __future__ import annotations

import os
import threading
import time as _time
from pathlib import Path
from typing import Iterable

from app_common.command_history import CommandHistory
from app_common.file_browser import FileListPanel
from app_common.file_browser._browser_core import (
    _metadata_comment_from_meta,
    _thumb_disk_cache_path,
)
from app_common.file_browser._permissions import mark_write_action_disabled
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log
from app_common.log import get_logger
from app_common.exif_io import PhotoMetaDataJSON

from .photo_tags import (
    PhotoTagConfig,
    PhotoTagSidecarStore,
    TagTreeNode,
    TagStates,
    TagWriteResult,
    find_superpicky_tag_config_path,
    photo_tag_filter_matches,
)
from .qt_compat import QCheckBox, QHBoxLayout, QLabel, QMenu, QMessageBox, QThread, QTimer, QToolButton, pyqtSignal
from .tag_commands import ClearPhotoTagsCommand, SetPhotoTagCommand
from .tag_menu import add_filterable_tag_actions


_log = get_logger("superviewer.tagged_file_list")

_TAG_FILTER_BUTTON_STYLE = (
    "QToolButton {"
    "font-size: 11px; padding: 1px 7px; min-width: 38px; "
    "border-radius: 9px; border: 1px solid rgba(160, 160, 160, 120); "
    "background: rgba(160, 160, 160, 26); color: #d7d7d7;"
    "}"
    "QToolButton:hover { background: rgba(160, 160, 160, 48); }"
    "QToolButton:checked {"
    "background: rgba(80, 150, 120, 120); border: 1px solid #5fb68e; color: #ffffff;"
    "}"
)
_TAG_FILTER_CLEAR_BUTTON_STYLE = (
    "QToolButton {"
    "font-size: 11px; padding: 1px 7px; min-width: 38px; "
    "border-radius: 9px; border: 1px solid rgba(180, 110, 110, 120); "
    "background: rgba(180, 80, 80, 28); color: #e3c4c4;"
    "}"
    "QToolButton:hover { background: rgba(180, 80, 80, 52); color: #ffffff; }"
)
_TAG_FILTER_INLINE_LIMIT = 8
_PHOTO_TAG_CACHE_BATCH_SIZE = 256
_PHOTO_TAG_FILTER_REFRESH_MS = 750


def _exec_menu(menu: QMenu, pos) -> None:
    if hasattr(menu, "exec"):
        menu.exec(pos)
    else:
        menu.exec_(pos)  # type: ignore[attr-defined]


def _config_signature(path: os.PathLike[str] | None) -> tuple[str, int, int] | None:
    if path is None:
        return None
    path_key = os.path.normcase(os.path.abspath(os.fspath(path)))
    try:
        stat = os.stat(path_key)
    except OSError:
        return path_key, -1, -1
    return path_key, int(stat.st_mtime_ns), int(stat.st_size)


def _norm_paths(paths: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in paths or []:
        if not path:
            continue
        norm = os.path.normpath(path)
        key = os.path.normcase(norm)
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def filter_text_tokens_match(
    tokens: Iterable[str],
    *,
    name: str = "",
    comment: str = "",
    photo_tags: Iterable[str] | None = None,
) -> bool:
    """Return whether every whitespace token matches name, comment, or tags.

    Tokens are AND'd together.  Each token may hit filename, comment, or any
    photo tag via case-insensitive substring matching.
    """
    token_list = [str(token or "").strip() for token in (tokens or [])]
    token_list = [token for token in token_list if token]
    if not token_list:
        return True
    name_cf = str(name or "").casefold()
    comment_cf = str(comment or "").casefold()
    tag_values = [
        str(tag or "").casefold()
        for tag in (photo_tags or [])
        if str(tag or "").strip()
    ]
    for token in token_list:
        needle = token.casefold()
        if needle in name_cf or needle in comment_cf:
            continue
        if any(needle in value for value in tag_values):
            continue
        return False
    return True


class PhotoTagCacheWorker(QThread):
    """Load configured photo tags in small batches off the UI thread."""

    batch_ready = pyqtSignal(object)
    progress_updated = pyqtSignal(int, int)
    finished_summary = pyqtSignal(int, int)

    def __init__(
        self,
        paths: Iterable[str],
        *,
        allowed_tags: Iterable[str],
        batch_size: int = _PHOTO_TAG_CACHE_BATCH_SIZE,
    ) -> None:
        super().__init__()
        self._paths = paths if isinstance(paths, list) else list(paths or [])
        self._allowed_tags = list(allowed_tags or [])
        self._batch_size = max(1, int(batch_size or _PHOTO_TAG_CACHE_BATCH_SIZE))
        self._stop_event = threading.Event()
        self._finished_processed = 0
        self._finished_total = 0

    def stop(self) -> None:
        self._stop_event.set()
        self.requestInterruption()

    def _stopped(self) -> bool:
        return self._stop_event.is_set() or self.isInterruptionRequested()

    def finished_counts(self) -> tuple[int, int]:
        return int(self._finished_processed), int(self._finished_total)

    def run(self) -> None:
        paths = _norm_paths(self._paths)
        total = len(paths)
        processed = 0
        self._finished_total = total
        if total <= 0 or self._stopped():
            self._finished_processed = 0
            self.finished_summary.emit(0, total)
            return

        store = PhotoTagSidecarStore()
        started_at = perf_counter()
        try:
            self.progress_updated.emit(0, total)
            for start in range(0, total, self._batch_size):
                if self._stopped():
                    break
                batch_paths = paths[start : start + self._batch_size]
                try:
                    fresh = store.load_tags_for_paths(batch_paths, allowed_tags=self._allowed_tags)
                except Exception as exc:
                    _log.warning(
                        "[PhotoTagCacheWorker.run] batch failed start=%s size=%s: %s",
                        start,
                        len(batch_paths),
                        exc,
                    )
                    fresh = {}
                payload = {path: set(fresh.get(path, set())) for path in batch_paths}
                processed += len(batch_paths)
                if not self._stopped():
                    self.batch_ready.emit(payload)
                    self.progress_updated.emit(min(processed, total), total)
        finally:
            try:
                store.close()
            except Exception:
                pass
            _log.info(
                "[PhotoTagCacheWorker.run] END processed=%s total=%s elapsed_ms=%.1f stopped=%s",
                processed,
                total,
                elapsed_ms(started_at),
                self._stopped(),
            )
            self._finished_processed = processed
            self._finished_total = total
            self.finished_summary.emit(processed, total)


class SuperViewerTaggedFileListPanel(FileListPanel):
    """FileListPanel extension that adds configured custom tags."""

    use_report_db = False
    use_preview_cache = False
    enable_key_navigation_playback = True
    enable_in_memory_fast_preview = True
    skip_uncached_fast_preview = True
    command_history_changed = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        tag_config_path: str | os.PathLike[str] | None = None,
        tag_store: PhotoTagSidecarStore | None = None,
    ) -> None:
        self._static_tag_config_path = os.fspath(tag_config_path) if tag_config_path else None
        self._tag_config = PhotoTagConfig(self._static_tag_config_path)
        self._tag_config_signature: tuple[str, int, int] | None = None
        self._tag_config_scope_key = ""
        self._available_tags: list[str] = []
        self._available_tag_tree: list[TagTreeNode] = []
        self._active_tag_filters: set[str] = set()
        self._tag_filter_partial_match: bool = True
        self._tag_filter_buttons: dict[str, QToolButton] = {}
        self._tag_filter_exact_match_checkbox: QCheckBox | None = None
        self._tag_filter_menu_button: QToolButton | None = None
        self._tag_filter_clear_button: QToolButton | None = None
        self._photo_tag_store = tag_store or PhotoTagSidecarStore()
        self._photo_tag_cache: dict[str, set[str]] = {}
        self._local_metadata_updates_by_path: dict[str, dict] = {}
        self._photo_tag_generation_by_path: dict[str, int] = {}
        self._photo_tag_loader: PhotoTagCacheWorker | None = None
        self._photo_tag_stopping_loader: PhotoTagCacheWorker | None = None
        self._photo_tag_cache_complete = False
        self._photo_tag_cache_done = 0
        self._photo_tag_cache_total = 0
        self._photo_tag_filter_refresh_timer: QTimer | None = None
        self._photo_tag_pending_paths: list[str] = []
        self._photo_tag_loader_covers_all_files = False
        self._tag_shutdown_requested = False
        self._tag_shutdown_complete = False
        self._tag_shutdown_workers: list[QThread] = []
        self._tag_filter_bar: QHBoxLayout | None = None
        self._command_history = CommandHistory()
        self._command_history.add_observer(self._emit_command_history_changed)
        super().__init__(parent)
        self._load_tag_config_if_changed(force=True)
        self._install_tag_filter_bar()
        if self._filter_edit is not None:
            self._filter_edit.setPlaceholderText("过滤文件名/注释/标签…")

    def close_tag_store(self) -> None:
        self.shutdown()

    def request_shutdown(self) -> None:
        """Cooperatively stop owned workers without a long GUI-thread wait."""
        if self._tag_shutdown_requested:
            return
        self._tag_shutdown_requested = True
        # 先锁住 app_common 的所有重调度入口并停止计时器；否则旧 worker
        # 的 queued finished 可能在分片等待期间创建快照之外的新线程。
        super()._request_background_shutdown()
        workers = [
            self._photo_tag_loader,
            self._photo_tag_stopping_loader,
            self._thumbnail_loader,
            self._metadata_loader,
            self._persistent_thumb_cache_worker,
            self._directory_scan_worker,
            *list(self._path_lookup_workers),
            *list(self._pending_loaders),
        ]
        seen: set[int] = set()
        self._tag_shutdown_workers = []
        for worker in workers:
            if worker is None or id(worker) in seen:
                continue
            seen.add(id(worker))
            self._tag_shutdown_workers.append(worker)
        try:
            self.stop_key_navigation_playback(commit=False)
        except Exception:
            pass
        self._pause_thumb_model_population()
        self._pause_tree_model_population()
        self._stop_photo_tag_cache_loader()
        super()._stop_all_loaders()
        self._stop_persistent_thumb_cache_worker()
        self._stop_directory_scan_worker()

    def shutdown(self, *, wait_timeout_ms: int | None = None) -> bool:
        """Finish shutdown within the supplied wait budget when possible."""
        if self._tag_shutdown_complete:
            return True
        self.request_shutdown()
        deadline = None
        if wait_timeout_ms is not None:
            deadline = _time.monotonic() + max(0, int(wait_timeout_ms)) / 1000.0
        complete = True
        for worker in self._tag_shutdown_workers:
            try:
                if not worker.isRunning():
                    continue
                if deadline is None:
                    worker.wait()
                else:
                    remaining_ms = max(0, int((deadline - _time.monotonic()) * 1000.0))
                    if remaining_ms > 0:
                        worker.wait(remaining_ms)
                if worker.isRunning():
                    complete = False
            except RuntimeError:
                # Qt may already have deleted a finished worker wrapper.
                continue
            except Exception:
                complete = False
        if not complete:
            return False
        # All QThreads are already stopped, so the base finalizer's waits are
        # normally immediate. A bounded GUI close must not wait indefinitely
        # for queued thumbnail JPEG writes; executor shutdown still prevents
        # new submissions and lets already queued writes finish safely.
        super()._shutdown_background_work(
            thumb_disk_writer_wait=wait_timeout_ms is None,
        )
        self._photo_tag_store.close()
        tag_worker = self._photo_tag_loader or self._photo_tag_stopping_loader
        if tag_worker is not None:
            try:
                tag_worker.deleteLater()
            except Exception:
                pass
        self._photo_tag_loader = None
        self._photo_tag_stopping_loader = None
        self._tag_shutdown_workers = []
        self._tag_shutdown_complete = True
        return True

    def _stop_all_loaders(self) -> None:
        self._stop_photo_tag_cache_loader()
        super()._stop_all_loaders()

    def available_photo_tags(self) -> list[str]:
        """Return the current configured SuperViewer tag vocabulary."""
        self._load_tag_config_if_changed()
        return list(self._available_tags)

    def available_photo_tag_tree(self) -> list[TagTreeNode]:
        """Return the configured tag hierarchy (groups + leaf tags)."""
        self._load_tag_config_if_changed()
        return list(self._available_tag_tree)

    def rating_writes_allowed(self) -> bool:
        return self.sidecar_writes_allowed()

    def rating_writes_disabled_tooltip(self, action: str = "写入操作") -> str:
        return self.sidecar_writes_disabled_tooltip(action)

    def photo_tags_for_path(self, path: str) -> set[str]:
        """Return configured tags currently assigned to *path*."""
        self._load_tag_config_if_changed()
        if not path:
            return set()
        return self._tags_for_path(path)

    def configured_tags_snapshot(self, paths: Iterable[str]) -> dict[str, set[str]]:
        """Read configured tags for *paths* from the sidecar store (authoritative)."""
        self._load_tag_config_if_changed()
        result: dict[str, set[str]] = {}
        for path in _norm_paths(paths):
            result[path] = set(
                self._photo_tag_store.get_tags(path, allowed_tags=self._available_tags)
            )
        return result

    def set_photo_tag_for_paths(self, paths: Iterable[str], tag: str, enabled: bool) -> None:
        """Set or unset one configured tag for one or more photo paths."""
        if not self._sidecar_writes_allowed("保存标签", warn=True):
            return
        self._load_tag_config_if_changed()
        self._set_tag_for_paths(_norm_paths(paths), tag, enabled)

    def clear_photo_tags_for_paths(self, paths: Iterable[str]) -> None:
        """Clear all configured tags for one or more photo paths."""
        if not self._sidecar_writes_allowed("清除标签", warn=True):
            return
        self._load_tag_config_if_changed()
        self._clear_tags_for_paths(_norm_paths(paths))

    @property
    def can_undo(self) -> bool:
        return self._command_history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._command_history.can_redo

    def undo(self) -> None:
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("撤销标签", warn=True):
            return
        self._load_tag_config_if_changed()
        if not self.can_undo:
            return
        try:
            self._command_history.undo()
        except Exception as exc:
            self._show_tag_write_error("撤销标签", exc)

    def redo(self) -> None:
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("重做标签", warn=True):
            return
        self._load_tag_config_if_changed()
        if not self.can_redo:
            return
        try:
            self._command_history.redo()
        except Exception as exc:
            self._show_tag_write_error("重做标签", exc)

    def _emit_command_history_changed(self) -> None:
        self.command_history_changed.emit()

    def _clear_command_history(self) -> None:
        self._command_history.clear()

    def _set_tag_config_directory(self, path: str | os.PathLike[str] | None) -> bool:
        if self._static_tag_config_path:
            scope_key = os.path.normcase(os.path.abspath(self._static_tag_config_path))
            config_path = self._static_tag_config_path
        else:
            config_path_obj = find_superpicky_tag_config_path(path)
            config_path = os.fspath(config_path_obj) if config_path_obj is not None else None
            scope_dir = os.path.dirname(config_path) if config_path else ""
            scope_key = os.path.normcase(os.path.abspath(scope_dir)) if scope_dir else ""
        current_path = os.fspath(self._tag_config.path) if self._tag_config.path is not None else None
        if scope_key == self._tag_config_scope_key and config_path == current_path:
            return False
        self._tag_config_scope_key = scope_key
        self._tag_config = PhotoTagConfig(config_path)
        self._tag_config_signature = None
        self._clear_command_history()
        return True

    def load_directory(
        self,
        path: str,
        force_reload: bool = False,
        *,
        preserve_meta_cache: bool = False,
        reuse_cached_listing: bool = False,
    ) -> None:
        if self._tag_shutdown_requested:
            return
        tag_config_scope_changed = self._set_tag_config_directory(path)
        self._load_tag_config_if_changed(force=tag_config_scope_changed)
        if force_reload or os.path.normcase(os.path.normpath(path)) != os.path.normcase(self.get_current_dir() or ""):
            self._local_metadata_updates_by_path.clear()
        self._photo_tag_cache = {}
        self._photo_tag_generation_by_path = {}
        self._photo_tag_cache_complete = False
        self._photo_tag_cache_done = 0
        self._photo_tag_cache_total = 0
        self._photo_tag_pending_paths = []
        self._photo_tag_loader_covers_all_files = False
        self._stop_photo_tag_cache_loader()
        super().load_directory(
            path,
            force_reload=force_reload,
            preserve_meta_cache=preserve_meta_cache,
            reuse_cached_listing=reuse_cached_listing,
        )

    def _apply_directory_listing_result(
        self,
        path: str,
        files: list[str],
        report_cache: dict,
        full_report_cache,
        *,
        recursive: bool,
        report_row_by_path: dict | None = None,
        from_cache: bool = False,
    ) -> None:
        apply_t0 = perf_counter()
        self._probe_log("photo_tag_cache.deferred", files=len(files), active_filters=bool(self._active_tag_filters))
        super()._apply_directory_listing_result(
            path,
            files,
            {},
            None,
            recursive=recursive,
            report_row_by_path={},
            from_cache=from_cache,
        )
        self._start_photo_tag_cache_loader_if_needed(files, reason="directory_listing")
        self._probe_log("photo_tag_cache.after_listing", files=len(files), elapsed_ms=elapsed_ms(apply_t0))

    def resolve_preview_path(self, path: str, prefer_fast_preview: bool = False) -> str:
        """SuperViewer 正常预览原图；方向键 fast preview 优先使用当前缩略图尺寸缓存。"""
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path or not prefer_fast_preview:
            return norm_path
        source_path = self._get_actual_path_for_display(norm_path) or norm_path
        if not source_path or not os.path.isfile(source_path):
            return norm_path
        try:
            mtime = float(os.path.getmtime(source_path))
        except Exception:
            mtime = 0.0
        thumb_path = _thumb_disk_cache_path(source_path, mtime, self._thumb_size)
        if thumb_path and os.path.isfile(thumb_path):
            _log.info(
                "[resolve_preview_path] fast source=%r thumb_disk=%r size=%s",
                norm_path,
                thumb_path,
                self._thumb_size,
            )
            return thumb_path
        return norm_path

    def _record_local_metadata_updates(self, path: str, fields: dict) -> None:
        if not path:
            return
        updates = {str(name): value for name, value in fields.items() if str(name) and value is not None}
        if updates:
            key = os.path.normcase(os.path.normpath(path))
            self._local_metadata_updates_by_path.setdefault(key, {}).update(updates)

    def sync_metadata_edit_for_path(
        self, path: str, *, report_fields: dict | None = None, meta_updates: dict | None = None,
    ) -> bool:
        updated = super().sync_metadata_edit_for_path(
            path, report_fields=report_fields, meta_updates=meta_updates,
        )
        if updated:
            self._record_local_metadata_updates(path, meta_updates or {})
        return updated

    def _resolve_rating_write_source(
        self,
        path: str,
        *,
        report_db_available: bool,
    ) -> str:
        return "json_sidecar"

    def _apply_rating_state_via_exif(
        self,
        paths: list[str],
        *,
        rating: int | None = None,
        pick: int | None = None,
    ) -> list[str]:
        if not self._sidecar_writes_allowed("修改评级"):
            return []
        fields: dict[str, int] = {}
        if rating is not None:
            fields["XMP-xmp:Rating"] = max(0, min(5, int(rating)))
        if pick is not None:
            fields["XMP-xmpDM:pick"] = max(-1, min(1, int(pick)))
        if not fields:
            return []
        probe_t0 = perf_counter()
        updated_paths: list[str] = []
        json_meta = PhotoMetaDataJSON()
        write_count = 0
        for path in self._unique_norm_paths(paths):
            if not path:
                continue
            try:
                ok = json_meta.write(path, fields)
            except Exception as exc:
                _log.warning("[_apply_rating_state_via_sidecar] source=%r failed: %s", path, exc)
                continue
            if not ok:
                _log.warning("[_apply_rating_state_via_sidecar] source=%r write returned False", path)
                continue
            write_count += 1
            self._apply_rating_state_to_meta_cache(path, rating=rating, pick=pick)
            self._record_local_metadata_updates(path, {
                **({"rating": fields["XMP-xmp:Rating"]} if rating is not None else {}),
                **({"pick": fields["XMP-xmpDM:pick"]} if pick is not None else {}),
            })
            updated_paths.append(path)
        perf_log(
            _log,
            "[rating.sidecar] selected=%s writes=%s updated=%s rating=%r pick=%r total_ms=%.1f",
            len(paths),
            write_count,
            len(updated_paths),
            rating,
            pick,
            elapsed_ms(probe_t0),
        )
        return updated_paths

    def _has_any_filter(self) -> bool:
        return super()._has_any_filter() or bool(self._active_tag_filters)

    def _filter_text_tokens(self) -> list[str]:
        if not getattr(self, "_filter_edit", None):
            return []
        return [token for token in str(self._filter_edit.text() or "").split() if token]

    def _text_filter_needs_photo_tags(self) -> bool:
        return bool(self._filter_text_tokens())

    def _photo_tag_lookup_needed_for_filters(self) -> bool:
        return bool(self._active_tag_filters) or self._text_filter_needs_photo_tags()

    def _photo_tags_for_filter_path(self, path: str) -> set[str]:
        norm = os.path.normpath(path)
        tags = self._photo_tag_cache.get(norm)
        if tags is None:
            tags = self._photo_tags_from_meta_cache(norm)
        return set(tags or set())

    def _path_matches_active_filters(self, path: str) -> bool:
        # Skip base text filter so tag tokens are not rejected by the
        # filename/comment-only substring check; pick/rating/focus still apply.
        if not self._path_matches_filters(
            path,
            filter_text="",
            filter_pick=self._filter_pick,
            filter_reject=self._filter_reject,
            filter_min_rating=self._filter_min_rating,
            filter_focus_status=self._filter_focus_status,
        ):
            return False

        tokens = self._filter_text_tokens()
        if tokens:
            norm = os.path.normpath(path) if path else ""
            name = Path(norm).name if norm else ""
            meta = self._meta_cache.get(norm, {}) if norm else {}
            comment = _metadata_comment_from_meta(meta if isinstance(meta, dict) else {})
            tags = self._photo_tags_for_filter_path(norm) if norm else set()
            if not filter_text_tokens_match(
                tokens,
                name=name,
                comment=comment,
                photo_tags=tags,
            ):
                return False

        if not self._active_tag_filters:
            return True
        return photo_tag_filter_matches(
            self._active_tag_filters,
            self._photo_tags_for_filter_path(path),
            partial_match=self._tag_filter_partial_match,
        )

    def _refresh_filter_scope(self) -> None:
        if self._photo_tag_lookup_needed_for_filters():
            self._start_photo_tag_cache_loader_if_needed(self._all_files, reason="tag_filter")
        super()._refresh_filter_scope()

    def _add_species_menu_actions(self, menu, primary_path: str | None, paths: list[str]) -> None:
        # SuperViewer 已切到原始目录 + sidecar 模式，不再暴露 report.db 鸟种菜单。
        return

    def _install_tag_filter_bar(self) -> None:
        if not getattr(self, "_create_filter_bar", True):
            return
        self._tag_filter_bar = QHBoxLayout()
        self._tag_filter_bar.setSpacing(3)
        layout = self.layout()
        if layout is None:
            return
        stack_index = layout.indexOf(getattr(self, "_stack", None))
        if stack_index >= 0:
            layout.insertLayout(stack_index, self._tag_filter_bar)
        else:
            layout.addLayout(self._tag_filter_bar)
        self._rebuild_tag_filter_bar()

    def _clear_tag_filter_bar(self) -> None:
        layout = self._tag_filter_bar
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_tag_filter_bar(self) -> None:
        layout = self._tag_filter_bar
        if layout is None:
            return
        self._clear_tag_filter_bar()
        self._tag_filter_buttons = {}
        self._tag_filter_exact_match_checkbox = None
        self._tag_filter_menu_button = None
        self._tag_filter_clear_button = None

        title = QLabel("标签过滤:")
        title.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(title)

        exact_match_checkbox = QCheckBox("完全匹配")
        exact_match_checkbox.setChecked(not self._tag_filter_partial_match)
        exact_match_checkbox.setToolTip("勾选：照片必须包含所有已选标签；取消勾选：任意已选标签部分匹配即可。")
        exact_match_checkbox.setStyleSheet("QCheckBox { color: #d7d7d7; font-size: 11px; }")
        exact_match_checkbox.toggled.connect(self._on_tag_exact_match_toggled)
        self._tag_filter_exact_match_checkbox = exact_match_checkbox
        layout.addWidget(exact_match_checkbox)

        if not self._available_tags:
            empty = QLabel("tags.cfg 未配置")
            empty.setStyleSheet("color: #777; font-size: 11px;")
            layout.addWidget(empty)
            layout.addStretch()
            self._sync_tag_filter_widgets()
            return

        inline_tags = self._inline_tag_filter_tags()
        for tag in inline_tags:
            btn = self._create_tag_filter_button(tag)
            self._tag_filter_buttons[tag] = btn
            layout.addWidget(btn)

        if len(inline_tags) < len(self._available_tags):
            more_btn = QToolButton()
            more_btn.setAutoRaise(False)
            more_btn.setStyleSheet(_TAG_FILTER_BUTTON_STYLE)
            more_btn.clicked.connect(lambda checked=False, b=more_btn: self._show_tag_filter_menu(b))
            self._tag_filter_menu_button = more_btn
            layout.addWidget(more_btn)

        clear_btn = QToolButton()
        clear_btn.setText("清除")
        clear_btn.setToolTip("清除所有标签过滤")
        clear_btn.setAutoRaise(False)
        clear_btn.setStyleSheet(_TAG_FILTER_CLEAR_BUTTON_STYLE)
        clear_btn.clicked.connect(lambda checked=False: self._clear_tag_filters())
        self._tag_filter_clear_button = clear_btn
        layout.addWidget(clear_btn)
        layout.addStretch()
        self._sync_tag_filter_widgets()

    def _inline_tag_filter_tags(self) -> list[str]:
        """Return compact inline tags; full tag set lives in the filterable menu."""
        inline: list[str] = []
        for tag in self._available_tags:
            if tag in self._active_tag_filters:
                inline.append(tag)
                if len(inline) >= _TAG_FILTER_INLINE_LIMIT:
                    return inline
        for tag in self._available_tags:
            if tag in self._active_tag_filters or tag in inline:
                continue
            inline.append(tag)
            if len(inline) >= _TAG_FILTER_INLINE_LIMIT:
                break
        return inline

    def _create_tag_filter_button(self, tag: str) -> QToolButton:
        btn = QToolButton()
        btn.setText(tag)
        btn.setToolTip(self._tag_filter_button_tooltip(tag))
        btn.setCheckable(True)
        btn.setChecked(tag in self._active_tag_filters)
        btn.setAutoRaise(False)
        btn.setStyleSheet(_TAG_FILTER_BUTTON_STYLE)
        btn.clicked.connect(lambda checked=False, t=tag: self._on_tag_filter_toggled(t, bool(checked)))
        return btn

    def _tag_filter_button_tooltip(self, tag: str) -> str:
        if self._tag_filter_partial_match:
            return f"筛选标签「{tag}」；当前为部分匹配，任意已选标签命中即可"
        return f"筛选标签「{tag}」；当前为完全匹配，照片需同时包含所有已选标签"

    def _sync_tag_filter_widgets(self) -> None:
        for key, btn in self._tag_filter_buttons.items():
            btn.setChecked(key in self._active_tag_filters)
        active_count = len(self._active_tag_filters)
        menu_button = self._tag_filter_menu_button
        if menu_button is not None:
            hidden_count = max(0, len(self._available_tags) - len(self._tag_filter_buttons))
            if active_count:
                menu_button.setText(f"全部标签({active_count})")
            else:
                menu_button.setText(f"更多({hidden_count})")
            menu_button.setToolTip(
                f"打开全部 {len(self._available_tags)} 个标签，可输入过滤文本后勾选过滤"
            )
        clear_button = self._tag_filter_clear_button
        if clear_button is not None:
            clear_button.setVisible(bool(active_count))
        exact_match_checkbox = self._tag_filter_exact_match_checkbox
        if exact_match_checkbox is not None:
            exact_match_checkbox.setChecked(not self._tag_filter_partial_match)
            exact_match_checkbox.setToolTip(
                "勾选：照片必须包含所有已选标签；取消勾选：任意已选标签部分匹配即可。"
            )
        for tag, btn in self._tag_filter_buttons.items():
            btn.setToolTip(self._tag_filter_button_tooltip(tag))

    def _show_tag_filter_menu(self, button: QToolButton) -> None:
        self._load_tag_config_if_changed()
        if not self._available_tags:
            return
        menu = QMenu(self)
        add_filterable_tag_actions(
            menu,
            on_triggered=lambda tag, checked=False: self._on_tag_filter_toggled(tag, bool(checked)),
            tree=self._available_tag_tree,
            checkable=True,
            checked_provider=lambda tag: tag in self._active_tag_filters,
            filter_placeholder="过滤标签",
            no_match_text="没有匹配的标签",
        )
        menu.addSeparator()
        clear_action = menu.addAction("清除标签过滤")
        clear_action.setEnabled(bool(self._active_tag_filters))
        clear_action.triggered.connect(lambda checked=False: self._clear_tag_filters())
        _exec_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))

    def _load_tag_config_if_changed(self, *, force: bool = False) -> bool:
        signature = _config_signature(self._tag_config.path)
        if not force and signature == self._tag_config_signature:
            return False
        self._tag_config_signature = signature
        new_tree, new_tags = self._tag_config.load_tree_and_tags()
        if new_tags == self._available_tags and not force:
            self._available_tag_tree = new_tree
            return False
        if new_tags != self._available_tags:
            self._stop_photo_tag_cache_loader()
            self._photo_tag_cache = {}
            self._photo_tag_generation_by_path = {}
            self._photo_tag_cache_complete = False
            self._photo_tag_cache_done = 0
            self._photo_tag_cache_total = 0
            self._photo_tag_pending_paths = []
            self._photo_tag_loader_covers_all_files = False
        self._available_tags = new_tags
        self._available_tag_tree = new_tree
        self._active_tag_filters.intersection_update(new_tags)
        self._rebuild_tag_filter_bar()
        return True

    def _ensure_photo_tag_filter_refresh_timer(self) -> None:
        if self._photo_tag_filter_refresh_timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._flush_photo_tag_filter_refresh)
        self._photo_tag_filter_refresh_timer = timer

    def _stop_photo_tag_cache_loader(self) -> None:
        self._photo_tag_pending_paths = []
        self._photo_tag_loader_covers_all_files = False
        timer = self._photo_tag_filter_refresh_timer
        if timer is not None and timer.isActive():
            timer.stop()
        worker = self._photo_tag_loader or self._photo_tag_stopping_loader
        self._photo_tag_loader = None
        if worker is None:
            return
        self._photo_tag_stopping_loader = worker
        try:
            worker.stop()
        except Exception:
            pass
        try:
            if worker.isRunning():
                if worker not in self._pending_loaders:
                    self._pending_loaders.append(worker)
                worker.finished.connect(
                    lambda ldr=worker: (
                        self._pending_loaders.remove(ldr)
                        if ldr in self._pending_loaders else None
                    )
                )
        except Exception:
            pass

    def _photo_tags_from_meta_cache(self, path: str) -> set[str]:
        norm = os.path.normpath(path) if path else ""
        meta = self._meta_cache.get(norm)
        if not isinstance(meta, dict):
            return set()
        values: list[str] = []
        raw = meta.get("tags")
        if isinstance(raw, (list, tuple, set)):
            values.extend(str(tag or "").strip() for tag in raw)
        elif raw:
            values.extend(part.strip() for part in str(raw).replace(";", ",").split(","))
        return {tag for tag in values if tag}

    def _meta_cache_has_photo_tags(self, path: str) -> bool:
        norm = os.path.normpath(path) if path else ""
        meta = self._meta_cache.get(norm)
        # An explicit empty list is a cache hit: do not reread its sidecar on
        # every selection merely because the photo has no configured tags.
        return isinstance(meta, dict) and "tags" in meta

    def _seed_photo_tag_cache_from_meta(self, paths: Iterable[str]) -> list[str]:
        allowed = set(self._available_tags)
        if not allowed:
            return []
        changed: list[str] = []
        for path in _norm_paths(paths):
            if not self._meta_cache_has_photo_tags(path):
                continue
            tags = self._photo_tags_from_meta_cache(path).intersection(allowed)
            if path in self._photo_tag_cache and self._photo_tag_cache.get(path) == tags:
                continue
            self._photo_tag_cache[path] = tags
            changed.append(path)
        if self._all_files:
            all_norm = _norm_paths(self._all_files)
            self._photo_tag_cache_complete = bool(all_norm) and all(
                path in self._photo_tag_cache for path in all_norm
            )
        return changed

    def _start_photo_tag_cache_loader_if_needed(
        self,
        paths: Iterable[str],
        *,
        reason: str,
        allow_without_filters: bool = False,
    ) -> None:
        if self._tag_shutdown_requested or not self._available_tags:
            return
        if not allow_without_filters and not self._photo_tag_lookup_needed_for_filters():
            return
        path_list = _norm_paths(paths)
        if not path_list:
            return
        seeded_paths = self._seed_photo_tag_cache_from_meta(path_list)
        path_list = [path for path in path_list if path not in self._photo_tag_cache]
        if not path_list:
            if seeded_paths:
                self.metadata_cache_updated.emit(seeded_paths)
            return
        worker = self._photo_tag_loader or self._photo_tag_stopping_loader
        if worker is not None:
            # The QThread may have returned while its queued ``finished`` slot
            # is still pending.  Keep ownership until that slot runs so a new
            # worker can never overlap or replace the pointer prematurely.
            pending_keys = {os.path.normcase(path) for path in self._photo_tag_pending_paths}
            for path in path_list:
                key = os.path.normcase(path)
                if key in pending_keys:
                    continue
                pending_keys.add(key)
                self._photo_tag_pending_paths.append(path)
            return
        self._photo_tag_cache_done = 0
        self._photo_tag_cache_total = len(path_list)
        all_norm = _norm_paths(self._all_files)
        covered = set(self._photo_tag_cache)
        covered.update(path_list)
        self._photo_tag_loader_covers_all_files = bool(all_norm) and all(
            path in covered for path in all_norm
        )
        if self._photo_tag_lookup_needed_for_filters():
            self._show_meta_progress_status("正在读取照片标签", value=0, total=len(path_list))
        self._probe_log(
            "photo_tag_cache.start",
            files=len(path_list),
            seeded=len(seeded_paths),
            reason=reason,
        )

        worker = PhotoTagCacheWorker(path_list, allowed_tags=self._available_tags)
        worker._tag_generation_snapshot = {
            os.path.normcase(path): self._photo_tag_generation(path)
            for path in path_list
        }
        self._photo_tag_loader = worker
        worker.batch_ready.connect(
            lambda batch, ldr=worker: self._on_photo_tag_cache_batch_ready(ldr, batch)
        )
        worker.progress_updated.connect(
            lambda done, total, ldr=worker: self._on_photo_tag_cache_progress(ldr, done, total)
        )
        worker.finished_summary.connect(
            lambda done, total, ldr=worker: self._on_photo_tag_cache_finished(ldr, done, total)
        )
        worker.finished.connect(lambda ldr=worker: self._on_photo_tag_cache_thread_finished(ldr))
        worker.start()

    def _on_photo_tag_cache_batch_ready(self, worker: PhotoTagCacheWorker, batch: dict[str, set[str]]) -> None:
        if worker is not self._photo_tag_loader or not batch:
            return
        snapshot = getattr(worker, "_tag_generation_snapshot", {})
        accepted: dict[str, set[str]] = {}
        for path, tags in batch.items():
            norm_path = os.path.normpath(path)
            key = os.path.normcase(norm_path)
            if int(snapshot.get(key, 0)) != self._photo_tag_generation(norm_path):
                # GUI 写入发生在此 worker 启动之后；旧读取结果不得覆盖刚写回
                # 的 JSON/XMP 与内存 cache。
                continue
            accepted[norm_path] = set(tags or set())
            self._photo_tag_cache[norm_path] = accepted[norm_path]
        if not accepted:
            return
        updated_paths = list(accepted)
        self._sync_photo_tags_to_meta_cache(updated_paths)
        self.metadata_cache_updated.emit(updated_paths)
        self._schedule_photo_tag_filter_refresh()

    def _on_photo_tag_cache_progress(self, worker: PhotoTagCacheWorker, done: int, total: int) -> None:
        if worker is not self._photo_tag_loader:
            return
        self._photo_tag_cache_done = max(0, int(done or 0))
        self._photo_tag_cache_total = max(0, int(total or 0))
        if self._photo_tag_lookup_needed_for_filters():
            self._show_meta_progress_status(
                "正在读取照片标签",
                value=self._photo_tag_cache_done,
                total=self._photo_tag_cache_total,
            )
        self._probe_log(
            "photo_tag_cache.progress",
            done=self._photo_tag_cache_done,
            total=self._photo_tag_cache_total,
        )

    def _schedule_photo_tag_filter_refresh(self) -> None:
        if not self._photo_tag_lookup_needed_for_filters():
            return
        self._ensure_photo_tag_filter_refresh_timer()
        timer = self._photo_tag_filter_refresh_timer
        if timer is None or timer.isActive():
            return
        timer.start(_PHOTO_TAG_FILTER_REFRESH_MS)

    def _flush_photo_tag_filter_refresh(self) -> None:
        if not self._photo_tag_lookup_needed_for_filters():
            return
        self._probe_log("photo_tag_cache.filter_refresh", cached=len(self._photo_tag_cache))
        self._apply_filter()

    def _on_photo_tag_cache_finished(self, worker: PhotoTagCacheWorker, done: int, total: int) -> None:
        if worker is not self._photo_tag_loader:
            return
        # ``finished_summary`` is emitted inside run() before QThread has
        # actually stopped.  Record counts only; ownership is released by the
        # real ``QThread.finished`` handler below.
        self._photo_tag_cache_done = max(0, int(done or 0))
        self._photo_tag_cache_total = max(0, int(total or 0))

    def _on_photo_tag_cache_thread_finished(self, worker: PhotoTagCacheWorker) -> None:
        if worker is self._photo_tag_stopping_loader:
            self._photo_tag_stopping_loader = None
            pending = self._photo_tag_pending_paths
            self._photo_tag_pending_paths = []
            if pending and not self._tag_shutdown_requested:
                self._start_photo_tag_cache_loader_if_needed(
                    pending,
                    reason="pending_after_stop",
                    allow_without_filters=True,
                )
            try:
                worker.deleteLater()
            except Exception:
                pass
            return
        if worker is not self._photo_tag_loader:
            try:
                worker.deleteLater()
            except Exception:
                pass
            return
        done, total = worker.finished_counts()
        self._photo_tag_loader = None
        self._photo_tag_cache_done = max(0, int(done or 0))
        self._photo_tag_cache_total = max(0, int(total or 0))
        worker_complete = (
            self._photo_tag_loader_covers_all_files
            and self._photo_tag_cache_total > 0
            and self._photo_tag_cache_done >= self._photo_tag_cache_total
        )
        self._photo_tag_loader_covers_all_files = False
        all_norm = _norm_paths(self._all_files)
        self._photo_tag_cache_complete = bool(worker_complete) or (
            bool(all_norm) and all(path in self._photo_tag_cache for path in all_norm)
        )
        timer = self._photo_tag_filter_refresh_timer
        if timer is not None and timer.isActive():
            timer.stop()
        if self._photo_tag_lookup_needed_for_filters():
            self._apply_filter()
        if self._photo_tag_lookup_needed_for_filters() and self._photo_tag_cache_total:
            self._show_meta_progress_status(
                "照片标签读取完成",
                value=self._photo_tag_cache_total,
                total=self._photo_tag_cache_total,
            )
            QTimer.singleShot(400, self._meta_progress.hide)
        self._probe_log(
            "photo_tag_cache.done",
            done=self._photo_tag_cache_done,
            total=self._photo_tag_cache_total,
            complete=bool(self._photo_tag_cache_complete),
        )
        pending = self._photo_tag_pending_paths
        self._photo_tag_pending_paths = []
        if pending and not self._tag_shutdown_requested:
            self._start_photo_tag_cache_loader_if_needed(
                pending,
                reason="pending_on_demand",
                allow_without_filters=True,
            )
        try:
            worker.deleteLater()
        except Exception:
            pass

    def _refresh_photo_tag_cache(self, paths: Iterable[str]) -> None:
        norm_paths = _norm_paths(paths)
        self._bump_photo_tag_generations(norm_paths)
        cache = {path: set() for path in norm_paths}
        try:
            cache.update(self._photo_tag_store.load_tags_for_paths(norm_paths, allowed_tags=self._available_tags))
        except Exception as exc:
            _log.warning("[_refresh_photo_tag_cache] failed paths=%s: %s", len(norm_paths), exc)
        self._photo_tag_cache = cache
        self._sync_photo_tags_to_meta_cache(norm_paths)
        self.metadata_cache_updated.emit(norm_paths)

    def _update_photo_tag_cache_for_paths(self, paths: Iterable[str]) -> None:
        norm_paths = _norm_paths(paths)
        if not norm_paths:
            return
        self._bump_photo_tag_generations(norm_paths)
        try:
            fresh = self._photo_tag_store.load_tags_for_paths(norm_paths, allowed_tags=self._available_tags)
        except Exception as exc:
            _log.warning("[_update_photo_tag_cache_for_paths] failed paths=%s: %s", len(norm_paths), exc)
            fresh = {}
        for path in norm_paths:
            self._photo_tag_cache[path] = set(fresh.get(path, set()))
        self._sync_photo_tags_to_meta_cache(norm_paths)
        self.metadata_cache_updated.emit(norm_paths)

    def _photo_tag_generation(self, path: str) -> int:
        key = os.path.normcase(os.path.normpath(path)) if path else ""
        return int(self._photo_tag_generation_by_path.get(key, 0)) if key else 0

    def _bump_photo_tag_generations(self, paths: Iterable[str]) -> None:
        for path in _norm_paths(paths):
            key = os.path.normcase(path)
            self._photo_tag_generation_by_path[key] = int(
                self._photo_tag_generation_by_path.get(key, 0)
            ) + 1

    def _merge_metadata_batch_with_photo_tag_cache(self, meta_dict: dict) -> dict:
        """Keep successful local field/tag writes ahead of older metadata batches."""
        if not isinstance(meta_dict, dict) or not meta_dict:
            return meta_dict
        order = {tag: index for index, tag in enumerate(self._available_tags)}
        merged: dict = {}
        for path, metadata in meta_dict.items():
            norm_path = os.path.normpath(path) if path else path
            item = dict(metadata) if isinstance(metadata, dict) else {}
            item.update(self._local_metadata_updates_by_path.get(os.path.normcase(norm_path), {}))
            cached_tags = self._photo_tag_cache.get(norm_path)
            if cached_tags is not None:
                item["tags"] = sorted(
                    cached_tags,
                    key=lambda tag: (order.get(tag, len(order)), tag),
                )
            merged[norm_path] = item
        return merged

    def _on_metadata_batch_ready(self, meta_dict: dict) -> None:
        # MetadataLoader 可能早于 GUI 标签写入开始、晚于写入完成才回到主线程。
        # 合并时以 photo-tag cache 为准，避免旧 sidecar 快照覆盖新标签。
        super()._on_metadata_batch_ready(
            self._merge_metadata_batch_with_photo_tag_cache(meta_dict)
        )

    def _sync_photo_tags_to_meta_cache(self, paths: Iterable[str]) -> None:
        order = {tag: i for i, tag in enumerate(self._available_tags)}
        for path in _norm_paths(paths):
            tags = sorted(
                self._photo_tag_cache.get(path, set()),
                key=lambda tag: (order.get(tag, len(order)), tag),
            )
            meta = self._meta_cache.get(path)
            if not isinstance(meta, dict):
                meta = {}
                self._meta_cache[path] = meta
            meta["tags"] = tags

    def _tags_for_path(self, path: str) -> set[str]:
        norm = os.path.normpath(path)
        cached = self._photo_tag_cache.get(norm)
        if cached is not None:
            return set(cached)
        if self._meta_cache_has_photo_tags(norm):
            self._seed_photo_tag_cache_from_meta([norm])
            return set(self._photo_tag_cache.get(norm, set()))
        self._queue_photo_tag_lookup(norm)
        return set()

    def _queue_photo_tag_lookup(self, path: str) -> None:
        """Queue at most one same-directory batch for an uncached selection."""
        norm = os.path.normpath(path) if path else ""
        if not norm or self._tag_shutdown_requested:
            return
        parent_key = os.path.normcase(os.path.dirname(os.path.abspath(norm)))
        batch = [norm]
        seen = {os.path.normcase(norm)}
        for candidate in self._all_files:
            if len(batch) >= _PHOTO_TAG_CACHE_BATCH_SIZE:
                break
            candidate_norm = os.path.normpath(candidate)
            candidate_key = os.path.normcase(candidate_norm)
            if candidate_key in seen or candidate_norm in self._photo_tag_cache:
                continue
            if self._meta_cache_has_photo_tags(candidate_norm):
                continue
            if os.path.normcase(os.path.dirname(os.path.abspath(candidate_norm))) != parent_key:
                continue
            seen.add(candidate_key)
            batch.append(candidate_norm)
        self._start_photo_tag_cache_loader_if_needed(
            batch,
            reason="on_demand",
            allow_without_filters=True,
        )

    def _on_tag_filter_toggled(self, tag: str, checked: bool) -> None:
        if checked:
            self._active_tag_filters.add(tag)
        else:
            self._active_tag_filters.discard(tag)
        current_inline = set(self._tag_filter_buttons)
        desired_inline = set(self._inline_tag_filter_tags())
        if current_inline != desired_inline:
            self._rebuild_tag_filter_bar()
        else:
            self._sync_tag_filter_widgets()
        self._refresh_filter_scope()

    def _on_tag_exact_match_toggled(self, checked: bool) -> None:
        partial_match = not bool(checked)
        if self._tag_filter_partial_match == partial_match:
            self._sync_tag_filter_widgets()
            return
        self._tag_filter_partial_match = partial_match
        self._sync_tag_filter_widgets()
        if self._active_tag_filters:
            self._refresh_filter_scope()

    def _clear_tag_filters(self) -> None:
        if not self._active_tag_filters:
            return
        self._active_tag_filters.clear()
        self._rebuild_tag_filter_bar()
        self._refresh_filter_scope()

    def _add_photo_tag_menu_actions(self, menu, paths: list[str]) -> None:
        self._load_tag_config_if_changed()
        tag_menu = menu.addMenu("打标签")
        norm_paths = _norm_paths(paths)
        writes_allowed = self._sidecar_writes_allowed("保存标签")
        tag_menu.setEnabled(bool(norm_paths) and writes_allowed)
        if not writes_allowed:
            mark_write_action_disabled(
                tag_menu.menuAction(),
                self.sidecar_writes_disabled_tooltip("保存标签"),
            )
            return
        if not norm_paths:
            return
        if not self._available_tags:
            act_empty = tag_menu.addAction("tags.cfg 未配置")
            act_empty.setEnabled(False)
            return

        tag_sets = [self._tags_for_path(path) for path in norm_paths]
        target_paths = list(norm_paths)
        add_filterable_tag_actions(
            tag_menu,
            on_triggered=lambda tag, checked=False, p=target_paths: self._set_tag_for_paths(
                p, tag, bool(checked)
            ),
            tree=self._available_tag_tree,
            checkable=True,
            checked_provider=lambda tag: bool(tag_sets) and all(tag in tags for tags in tag_sets),
            keep_open=True,
        )

        tag_menu.addSeparator()
        # Undo/Redo: clear_photo_tags_for_paths → ClearPhotoTagsCommand → CommandHistory
        act_clear = tag_menu.addAction("清除所有TAG")
        act_clear.setEnabled(any(tag_sets) and writes_allowed)
        act_clear.triggered.connect(
            lambda checked=False, p=list(norm_paths): self.clear_photo_tags_for_paths(p)
        )

    def _set_tag_for_paths(self, paths: list[str], tag: str, enabled: bool) -> None:
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("保存标签", warn=True):
            return
        self._load_tag_config_if_changed()
        command = SetPhotoTagCommand(self, _norm_paths(paths), tag, enabled)
        try:
            self._command_history.add_command(command)
        except Exception as exc:
            self._show_tag_write_error("保存标签", exc)

    def _clear_tags_for_paths(self, paths: list[str]) -> None:
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("清除标签", warn=True):
            return
        self._load_tag_config_if_changed()
        command = ClearPhotoTagsCommand(self, _norm_paths(paths), self._available_tags)
        try:
            self._command_history.add_command(command)
        except Exception as exc:
            self._show_tag_write_error("清除标签", exc)

    def _show_tag_write_error(self, action: str, exc: Exception) -> None:
        _log.warning("[tag.write] action=%s failed: %s", action, exc)
        QMessageBox.warning(
            self,
            action + "未全部完成",
            str(exc) + "\n成功的修改及其撤销/重做记录已保留；失败文件未更新。"
            + ("\n排除问题后可再次执行此操作。" if action in {"撤销标签", "重做标签"} else ""),
        )

    def _apply_photo_tag_states(self, states: TagStates) -> TagWriteResult:
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("保存标签"):
            return TagWriteResult(failed_paths={path: "当前无法写入标签。" for path in states})
        started_at = perf_counter()
        result = self._photo_tag_store.apply_tag_states(states, allowed_tags=self._available_tags)
        updated_paths = list(result.current_tags)
        if updated_paths:
            try:
                self._photo_tag_cache.update({path: set(tags) for path, tags in result.current_tags.items()})
                self._bump_photo_tag_generations(updated_paths)
            except Exception as exc:
                _log.warning("[tag.write] saved tags but tag cache update failed: %s", exc)
            for component, refresh in (
                ("metadata cache", self._sync_photo_tags_to_meta_cache),
                ("tag panels", self.metadata_cache_updated.emit),
                ("file list", self._refresh_metadata_state_for_paths),
            ):
                try:
                    refresh(updated_paths)
                except Exception as exc:
                    _log.warning("[tag.write] saved tags but %s refresh failed: %s", component, exc)
            try:
                if self._photo_tag_lookup_needed_for_filters():
                    self._apply_filter()
            except Exception as exc:
                _log.warning("[tag.write] saved tags but filter refresh failed: %s", exc)
        perf_log(
            _log,
            "[tag.write] requested=%s changed=%s failed=%s total_ms=%.1f",
            len(states), len(result.inverse_states), len(result.failed_paths), elapsed_ms(started_at),
        )
        return result
