# -*- coding: utf-8 -*-
"""SuperViewer file list with custom photo tags."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Iterable

from app_common.file_browser import FileListPanel
from app_common.file_browser._browser_core import _thumb_disk_cache_path
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log
from app_common.log import get_logger

from .photo_tags import (
    PhotoTagConfig,
    PhotoTagSidecarStore,
    find_superpicky_tag_config_path,
    photo_tag_filter_matches,
)
from .qt_compat import QCheckBox, QHBoxLayout, QLabel, QMenu, QThread, QTimer, QToolButton, pyqtSignal
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


def _default_tag_config_path() -> str:
    return os.fspath(Path(__file__).resolve().parents[1] / "tags.cfg")


def mark_write_action_disabled(target, tooltip: str = "") -> None:
    if target is not None and tooltip:
        try:
            target.setToolTip(tooltip)
        except Exception:
            pass


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

    def stop(self) -> None:
        self._stop_event.set()
        self.requestInterruption()

    def _stopped(self) -> bool:
        return self._stop_event.is_set() or self.isInterruptionRequested()

    def run(self) -> None:
        paths = _norm_paths(self._paths)
        total = len(paths)
        processed = 0
        if total <= 0 or self._stopped():
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
            self.finished_summary.emit(processed, total)


class SuperViewerTaggedFileListPanel(FileListPanel):
    """FileListPanel extension that adds configured custom tags."""

    use_report_db = True
    use_preview_cache = True

    def __init__(
        self,
        parent=None,
        *,
        tag_config_path: str | os.PathLike[str] | None = None,
        tag_store: PhotoTagSidecarStore | None = None,
    ) -> None:
        self._fallback_tag_config_path = os.fspath(tag_config_path) if tag_config_path else _default_tag_config_path()
        self._tag_config = PhotoTagConfig(self._fallback_tag_config_path)
        self._tag_config_signature: tuple[str, int, int] | None = None
        self._tag_config_scope_key = ""
        self._available_tags: list[str] = []
        self._active_tag_filters: set[str] = set()
        self._tag_filter_partial_match: bool = True
        self._tag_filter_buttons: dict[str, QToolButton] = {}
        self._tag_filter_exact_match_checkbox: QCheckBox | None = None
        self._tag_filter_menu_button: QToolButton | None = None
        self._tag_filter_clear_button: QToolButton | None = None
        self._photo_tag_store = tag_store or PhotoTagSidecarStore()
        self._photo_tag_cache: dict[str, set[str]] = {}
        self._photo_tag_loader: PhotoTagCacheWorker | None = None
        self._photo_tag_cache_complete = False
        self._photo_tag_cache_done = 0
        self._photo_tag_cache_total = 0
        self._photo_tag_filter_refresh_timer: QTimer | None = None
        self._tag_filter_bar: QHBoxLayout | None = None
        super().__init__(parent)
        self._load_tag_config_if_changed(force=True)
        self._install_tag_filter_bar()

    def close_tag_store(self) -> None:
        self._stop_photo_tag_cache_loader()
        self._photo_tag_store.close()

    def _stop_all_loaders(self) -> None:
        self._stop_photo_tag_cache_loader()
        super()._stop_all_loaders()

    def available_photo_tags(self) -> list[str]:
        """Return the current configured SuperViewer tag vocabulary."""
        self._load_tag_config_if_changed()
        return list(self._available_tags)

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

    def _set_tag_config_directory(self, path: str | os.PathLike[str] | None) -> bool:
        config_path_obj = find_superpicky_tag_config_path(path)
        if config_path_obj is not None and config_path_obj.is_file():
            config_path = os.fspath(config_path_obj)
            scope_dir = os.path.dirname(config_path)
            scope_key = "superpicky:" + os.path.normcase(os.path.abspath(scope_dir))
        else:
            config_path = self._fallback_tag_config_path
            scope_key = "fallback:" + os.path.normcase(os.path.abspath(config_path)) if config_path else ""
        current_path = os.fspath(self._tag_config.path) if self._tag_config.path is not None else None
        if scope_key == self._tag_config_scope_key and config_path == current_path:
            return False
        self._tag_config_scope_key = scope_key
        self._tag_config = PhotoTagConfig(config_path)
        self._tag_config_signature = None
        return True

    def load_directory(
        self,
        path: str,
        force_reload: bool = False,
        *,
        preserve_meta_cache: bool = False,
        reuse_cached_listing: bool = False,
    ) -> None:
        tag_config_scope_changed = self._set_tag_config_directory(path)
        self._load_tag_config_if_changed(force=tag_config_scope_changed)
        self._photo_tag_cache = {}
        self._photo_tag_cache_complete = False
        self._photo_tag_cache_done = 0
        self._photo_tag_cache_total = 0
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
            report_cache,
            full_report_cache,
            recursive=recursive,
            report_row_by_path=report_row_by_path,
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

    def _resolve_rating_write_source(
        self,
        path: str,
        *,
        report_db_available: bool,
    ) -> str:
        return "xmp_sidecar"

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
            fields["rating"] = max(0, min(5, int(rating)))
        if pick is not None:
            fields["pick"] = max(-1, min(1, int(pick)))
        if not fields:
            return []
        probe_t0 = perf_counter()
        updated_paths: list[str] = []
        write_count = 0
        for path in self._unique_norm_paths(paths):
            if not path:
                continue
            try:
                target_path = self._resolve_metadata_write_target(path)
                ok = bool(target_path and self._meta_proxy.write(target_path, fields))
            except Exception as exc:
                _log.warning("[_apply_rating_state_via_xmp] source=%r failed: %s", path, exc)
                continue
            if not ok:
                _log.warning("[_apply_rating_state_via_xmp] source=%r write returned False", path)
                continue
            try:
                from app_common.exif_io.writer import invalidate_metadata_cache
                invalidate_metadata_cache([path, target_path])
            except Exception:
                pass
            write_count += 1
            self._apply_rating_state_to_meta_cache(path, rating=rating, pick=pick)
            updated_paths.append(path)
        perf_log(
            _log,
            "[rating.xmp_sidecar] selected=%s writes=%s updated=%s rating=%r pick=%r total_ms=%.1f",
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

    def _path_matches_active_filters(self, path: str) -> bool:
        if not super()._path_matches_active_filters(path):
            return False
        if not self._active_tag_filters:
            return True
        norm = os.path.normpath(path)
        tags = self._photo_tag_cache.get(norm)
        if tags is None:
            tags = self._photo_tags_from_meta_cache(norm)
        return photo_tag_filter_matches(
            self._active_tag_filters,
            tags,
            partial_match=self._tag_filter_partial_match,
        )

    def _refresh_filter_scope(self) -> None:
        if self._active_tag_filters:
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
            self._available_tags,
            lambda tag, checked=False: self._on_tag_filter_toggled(tag, bool(checked)),
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
        new_tags = self._tag_config.load()
        if new_tags == self._available_tags and not force:
            return False
        if new_tags != self._available_tags:
            self._stop_photo_tag_cache_loader()
            self._photo_tag_cache = {}
            self._photo_tag_cache_complete = False
            self._photo_tag_cache_done = 0
            self._photo_tag_cache_total = 0
        self._available_tags = new_tags
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
        timer = self._photo_tag_filter_refresh_timer
        if timer is not None and timer.isActive():
            timer.stop()
        worker = self._photo_tag_loader
        self._photo_tag_loader = None
        if worker is None:
            return
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
            else:
                worker.deleteLater()
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

    def _seed_photo_tag_cache_from_meta(self, paths: Iterable[str]) -> int:
        allowed = set(self._available_tags)
        if not allowed:
            return 0
        seeded = 0
        for path in _norm_paths(paths):
            if path in self._photo_tag_cache:
                continue
            tags = self._photo_tags_from_meta_cache(path).intersection(allowed)
            if not tags:
                continue
            self._photo_tag_cache[path] = tags
            seeded += 1
        return seeded

    def _start_photo_tag_cache_loader_if_needed(self, paths: Iterable[str], *, reason: str) -> None:
        if not self._active_tag_filters or not self._available_tags:
            return
        if self._photo_tag_cache_complete:
            return
        worker = self._photo_tag_loader
        if worker is not None and worker.isRunning():
            return
        path_list = paths if isinstance(paths, list) else list(paths or [])
        if not path_list:
            return
        seeded = self._seed_photo_tag_cache_from_meta(path_list)
        self._photo_tag_cache_done = 0
        self._photo_tag_cache_total = len(path_list)
        self._show_meta_progress_status("正在读取照片标签", value=0, total=len(path_list))
        self._probe_log("photo_tag_cache.start", files=len(path_list), seeded=seeded, reason=reason)

        worker = PhotoTagCacheWorker(path_list, allowed_tags=self._available_tags)
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
        worker.finished.connect(lambda ldr=worker: ldr.deleteLater())
        worker.start()

    def _on_photo_tag_cache_batch_ready(self, worker: PhotoTagCacheWorker, batch: dict[str, set[str]]) -> None:
        if worker is not self._photo_tag_loader or not batch:
            return
        for path, tags in batch.items():
            self._photo_tag_cache[os.path.normpath(path)] = set(tags or set())
        self._sync_photo_tags_to_meta_cache(batch.keys())
        self._schedule_photo_tag_filter_refresh()

    def _on_photo_tag_cache_progress(self, worker: PhotoTagCacheWorker, done: int, total: int) -> None:
        if worker is not self._photo_tag_loader:
            return
        self._photo_tag_cache_done = max(0, int(done or 0))
        self._photo_tag_cache_total = max(0, int(total or 0))
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
        if not self._active_tag_filters:
            return
        self._ensure_photo_tag_filter_refresh_timer()
        timer = self._photo_tag_filter_refresh_timer
        if timer is None or timer.isActive():
            return
        timer.start(_PHOTO_TAG_FILTER_REFRESH_MS)

    def _flush_photo_tag_filter_refresh(self) -> None:
        if not self._active_tag_filters:
            return
        self._probe_log("photo_tag_cache.filter_refresh", cached=len(self._photo_tag_cache))
        self._apply_filter()

    def _on_photo_tag_cache_finished(self, worker: PhotoTagCacheWorker, done: int, total: int) -> None:
        if worker is not self._photo_tag_loader:
            return
        self._photo_tag_loader = None
        self._photo_tag_cache_done = max(0, int(done or 0))
        self._photo_tag_cache_total = max(0, int(total or 0))
        self._photo_tag_cache_complete = self._photo_tag_cache_total > 0 and self._photo_tag_cache_done >= self._photo_tag_cache_total
        timer = self._photo_tag_filter_refresh_timer
        if timer is not None and timer.isActive():
            timer.stop()
        if self._active_tag_filters:
            self._apply_filter()
        if self._photo_tag_cache_total:
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

    def _refresh_photo_tag_cache(self, paths: Iterable[str]) -> None:
        norm_paths = _norm_paths(paths)
        cache = {path: set() for path in norm_paths}
        try:
            cache.update(self._photo_tag_store.load_tags_for_paths(norm_paths, allowed_tags=self._available_tags))
        except Exception as exc:
            _log.warning("[_refresh_photo_tag_cache] failed paths=%s: %s", len(norm_paths), exc)
        self._photo_tag_cache = cache
        self._sync_photo_tags_to_meta_cache(norm_paths)

    def _update_photo_tag_cache_for_paths(self, paths: Iterable[str]) -> None:
        norm_paths = _norm_paths(paths)
        if not norm_paths:
            return
        try:
            fresh = self._photo_tag_store.load_tags_for_paths(norm_paths, allowed_tags=self._available_tags)
        except Exception as exc:
            _log.warning("[_update_photo_tag_cache_for_paths] failed paths=%s: %s", len(norm_paths), exc)
            fresh = {}
        for path in norm_paths:
            self._photo_tag_cache[path] = set(fresh.get(path, set()))
        self._sync_photo_tags_to_meta_cache(norm_paths)

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
        self._update_photo_tag_cache_for_paths([norm])
        return set(self._photo_tag_cache.get(norm, set()))

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
            self._available_tags,
            lambda tag, checked=False, p=target_paths: self._set_tag_for_paths(p, tag, bool(checked)),
            checkable=True,
            checked_provider=lambda tag: bool(tag_sets) and all(tag in tags for tags in tag_sets),
        )

        tag_menu.addSeparator()
        act_clear = tag_menu.addAction("清除所有TAG")
        act_clear.setEnabled(any(tag_sets) and writes_allowed)
        act_clear.triggered.connect(lambda checked=False, p=list(norm_paths): self._clear_tags_for_paths(p))

    def _set_tag_for_paths(self, paths: list[str], tag: str, enabled: bool) -> None:
        if not self._sidecar_writes_allowed("保存标签", warn=True):
            return
        probe_t0 = perf_counter()
        try:
            write_t0 = perf_counter()
            self._photo_tag_store.set_tag_for_paths(paths, tag, enabled, allowed_tags=self._available_tags)
            write_ms = elapsed_ms(write_t0)
        except Exception as exc:
            _log.warning("[_set_tag_for_paths] failed tag=%r enabled=%s paths=%s: %s", tag, enabled, len(paths), exc)
            return
        cache_t0 = perf_counter()
        self._update_photo_tag_cache_for_paths(paths)
        cache_ms = elapsed_ms(cache_t0)
        refresh_t0 = perf_counter()
        self._refresh_metadata_state_for_paths(paths)
        refresh_ms = elapsed_ms(refresh_t0)
        filter_ms = 0.0
        if self._active_tag_filters:
            filter_t0 = perf_counter()
            self._apply_filter()
            filter_ms = elapsed_ms(filter_t0)
        perf_log(
            _log,
            "[tag.write] action=set enabled=%s tag=%r paths=%s write_ms=%.1f cache_ms=%.1f refresh_ms=%.1f filter_ms=%.1f total_ms=%.1f",
            enabled,
            tag,
            len(paths),
            write_ms,
            cache_ms,
            refresh_ms,
            filter_ms,
            elapsed_ms(probe_t0),
        )

    def _clear_tags_for_paths(self, paths: list[str]) -> None:
        if not self._sidecar_writes_allowed("清除标签", warn=True):
            return
        probe_t0 = perf_counter()
        try:
            write_t0 = perf_counter()
            self._photo_tag_store.clear_tags_for_paths(paths, allowed_tags=self._available_tags)
            write_ms = elapsed_ms(write_t0)
        except Exception as exc:
            _log.warning("[_clear_tags_for_paths] failed paths=%s: %s", len(paths), exc)
            return
        cache_t0 = perf_counter()
        self._update_photo_tag_cache_for_paths(paths)
        cache_ms = elapsed_ms(cache_t0)
        refresh_t0 = perf_counter()
        self._refresh_metadata_state_for_paths(paths)
        refresh_ms = elapsed_ms(refresh_t0)
        filter_ms = 0.0
        if self._active_tag_filters:
            filter_t0 = perf_counter()
            self._apply_filter()
            filter_ms = elapsed_ms(filter_t0)
        perf_log(
            _log,
            "[tag.write] action=clear paths=%s write_ms=%.1f cache_ms=%.1f refresh_ms=%.1f filter_ms=%.1f total_ms=%.1f",
            len(paths),
            write_ms,
            cache_ms,
            refresh_ms,
            filter_ms,
            elapsed_ms(probe_t0),
        )
