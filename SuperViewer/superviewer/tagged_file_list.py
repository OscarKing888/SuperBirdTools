# -*- coding: utf-8 -*-
"""SuperViewer file list with custom photo tags."""
from __future__ import annotations

import os
import threading
from contextlib import ExitStack
from pathlib import Path
from typing import Iterable

from app_common.bird_rarity import RARITY_LEVELS, rarity_level, rarity_metadata
from app_common.file_browser import FileListPanel
from app_common.file_browser._browser_core import (
    _get_cached_actual_path,
    _metadata_comment_from_meta,
    _metadata_lens_model_text,
    _metadata_species_text,
    _report_row_candidate_paths,
    _report_row_from_cache_for_path,
)
from app_common.filterable_combo import FilterableComboBox
from app_common.image_formats import HEIF_EXTENSIONS, RAW_EXTENSIONS
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log
from app_common.log import get_logger
from app_common.qt_theme import is_theme_change_event, scheme_from_palette
from app_common.toggle_button import TOGGLE_CHECKED_STYLE

from .filter_controls import FilterDropdown, FilterFlowLayout
from .species_filter import species_filter_name
from .directory_selection import DirectorySelectionCache
from .rarity_badge import rarity_badge_style
from .file_context_menu import build_file_context_menu, species_shortcut_sequence
from .photo_tags import (
    PhotoTagConfig,
    PhotoTagSidecarStore,
    commit_photo_tag_states,
    TagTreeNode,
    TagStates,
    TagWriteResult,
    find_superpicky_tag_config_path,
    photo_tag_filter_matches,
)
from .qt_compat import QMenu, QMessageBox, QPixmap, QThread, QTimer, QToolButton, QWidget, pyqtSignal
from .photo_tag_commands import ClearPhotoTagsCommand, SetPhotoTagCommand, PhotoTagCommandHistory
from .tag_menu import add_filterable_tag_actions
from .ui_theme import PanelThemeColors, panel_theme_colors
from .rarity_file_table import RarityFileTableModel, RarityBadgeDelegate
from .thumbnail_metadata import BirdThumbnailModel, BirdThumbnailDelegate
from .file_drag_views import DragFileTableModel, DragThumbnailModel, DragFileTableView, DragThumbnailView

try:
    from PyQt6.QtCore import QItemSelectionModel, QSignalBlocker, Qt
    from PyQt6.QtGui import QShortcut
    _DeselectRows = QItemSelectionModel.SelectionFlag.Deselect | QItemSelectionModel.SelectionFlag.Rows
except ImportError:
    from PyQt5.QtCore import QItemSelectionModel, QSignalBlocker, Qt
    from PyQt5.QtWidgets import QShortcut
    _DeselectRows = QItemSelectionModel.Deselect | QItemSelectionModel.Rows


_log = get_logger("superviewer.tagged_file_list")

_PHOTO_TAG_CACHE_BATCH_SIZE = 256
_PHOTO_TAG_FILTER_REFRESH_MS = 750
_FOCUS_SOURCE_PREFERRED_EXTENSIONS = (".arw", ".hif", ".heif", ".heic")
_LENS_FILTER_ALL_TEXT = "全部镜头"


def _tag_filter_button_style(colors: PanelThemeColors) -> str:
    return (
        "QToolButton {"
        "padding: 4px 20px 4px 8px; "
        f"border-radius: 4px; border: 1px solid {colors.chip_border}; "
        f"background: {colors.chip_bg}; color: {colors.chip_text};"
        "}"
        f"QToolButton:hover {{ background: {colors.button_hover}; }}"
        "QToolButton::menu-indicator { subcontrol-origin: padding; "
        "subcontrol-position: center right; right: 5px; }"
    ) + TOGGLE_CHECKED_STYLE


def _focus_source_extension_ranks() -> dict[str, int]:
    ordered = (
        list(_FOCUS_SOURCE_PREFERRED_EXTENSIONS)
        + sorted(RAW_EXTENSIONS)
        + sorted(HEIF_EXTENSIONS)
    )
    ranks: dict[str, int] = {}
    for extension in ordered:
        normalized = str(extension or "").lower()
        if normalized and normalized not in ranks:
            ranks[normalized] = len(ranks)
    return ranks


_FOCUS_SOURCE_EXTENSION_RANKS = _focus_source_extension_ranks()


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


def filter_text_tokens_match(
    tokens: Iterable[str],
    *,
    name: str = "",
    comment: str = "",
    photo_tags: Iterable[str] | None = None,
) -> bool:
    """Match every token against filename, comment, or any photo tag."""
    values = [str(name or "").casefold(), str(comment or "").casefold()]
    values.extend(str(tag or "").casefold() for tag in photo_tags or [])
    return all(
        any(str(token).strip().casefold() in value for value in values)
        for token in tokens or []
        if str(token or "").strip()
    )


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
        return self._finished_processed, self._finished_total

    def run(self) -> None:
        paths = _norm_paths(self._paths)
        total = len(paths)
        processed = 0
        self._finished_total = total
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
            self._finished_processed = processed
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

    photo_tags_cache_updated = pyqtSignal(object)
    photo_metadata_cache_updated = pyqtSignal(object)
    command_history_changed = pyqtSignal()
    tag_library_changed = pyqtSignal()
    photo_selection_changed = pyqtSignal()
    use_report_db = True
    include_videos = True
    use_unified_worker_pool = True
    show_thumbnail_sort_controls = True
    show_cut_state = True
    file_table_model_class = DragFileTableModel
    thumbnail_model_class = DragThumbnailModel
    file_table_view_class = DragFileTableView
    thumbnail_view_class = DragThumbnailView
    thumbnail_delegate_class = BirdThumbnailDelegate
    video_playback_stop_requested = pyqtSignal()
    playback_state_changed = pyqtSignal(bool)
    use_preview_cache = True
    fast_thumbnail_cache_check = True
    enable_key_navigation_playback = True
    enable_range_mark_shortcuts = True
    enable_in_memory_fast_preview = True
    skip_uncached_fast_preview = True

    def selected_photo_paths(self) -> list[str]:
        """Snapshot selected sources using cached path repairs, never preview assets."""
        return self._unique_norm_paths([
            _get_cached_actual_path(path) or path
            for path in self._active_view_selected_paths()
        ])

    def _update_selection_status(self) -> None:
        super()._update_selection_status()
        self.photo_selection_changed.emit()

    def select_display_path_silently(self, path: str) -> bool:
        """Reflect an A/B viewport activation in both views without loading a photo."""
        normalized = os.path.normpath(path) if path else ""
        if not normalized:
            return False
        tree_index = self._tree_index_for_path(normalized)
        thumb_index = self._thumb_index_for_path(normalized)
        if not tree_index.isValid() and not thumb_index.isValid():
            return False
        with ExitStack() as blockers:
            for view in (self._tree_widget, self._list_widget):
                blockers.enter_context(QSignalBlocker(view))
                model = view.selectionModel()
                if model is not None:
                    blockers.enter_context(QSignalBlocker(model))
            for view, index in ((self._tree_widget, tree_index),
                                (self._list_widget, thumb_index)):
                if index.isValid():
                    view.clearSelection()
                    view.setCurrentIndex(index)
            self._selected_display_path = normalized
            self._update_selection_status()
        return True

    def deselect_display_paths_silently(self, paths: Iterable[str]) -> None:
        """按显示路径取消两种视图的选择，保留当前预览及编辑草稿。"""
        normalized = {os.path.normpath(path) for path in paths if path}
        if not normalized:
            return
        with ExitStack() as blockers:
            for view, index_for_path in (
                (self._tree_widget, self._tree_index_for_path),
                (self._list_widget, self._thumb_index_for_path),
            ):
                model = view.selectionModel()
                if model is None:
                    continue
                blockers.enter_context(QSignalBlocker(view))
                blockers.enter_context(QSignalBlocker(model))
                for path in normalized:
                    index = index_for_path(path)
                    if index.isValid():
                        model.select(index, _DeselectRows)
                view.viewport().update()
            if self._pending_selection_paths:
                keys = {os.path.normcase(path) for path in normalized}
                self._pending_selection_paths = [
                    path for path in self._pending_selection_paths
                    if os.path.normcase(os.path.normpath(path)) not in keys
                ] or None
                if os.path.normcase(self._pending_selection_current_path) in keys:
                    self._pending_selection_current_path = next(iter(self._pending_selection_paths or ()), '')
            self._update_selection_status()

    def __init__(
        self,
        parent=None,
        *,
        tag_config_path: str | os.PathLike[str] | None = None,
        tag_store: PhotoTagSidecarStore | None = None,
    ) -> None:
        self._directory_selections = DirectorySelectionCache()
        self._fallback_tag_config_path = os.fspath(tag_config_path) if tag_config_path else _default_tag_config_path()
        self._tag_config = PhotoTagConfig(self._fallback_tag_config_path)
        self._tag_config_signature: tuple[str, int, int] | None = None
        self._tag_config_scope_key = ""
        self._available_tags: list[str] = []
        self._available_tag_tree: list[TagTreeNode] = []
        self._active_tag_filters: set[str] = set()
        self._tag_filter_partial_match: bool = True
        self._tag_filter_exact_match_action = None
        self._tag_filter_clear_action = None
        self._tag_filter_menu_button: QToolButton | None = None
        self._active_rarity_filters: set[str] = set()
        self._rarity_filter_button: FilterDropdown | None = None
        self._rarity_filter_clear_action = None
        self._active_species_filters: set[str] = set()
        self._species_filter_button: FilterDropdown | None = None
        self._species_filter_clear_action = None
        self._tag_filter_theme_applying = False
        self._lens_filter_value = ""
        self._lens_filter_combo: FilterableComboBox | None = None
        self._photo_tag_store = tag_store or PhotoTagSidecarStore()
        self._photo_tag_cache: dict[str, set[str]] = {}
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
        self._tag_filter_bar: FilterFlowLayout | None = None
        self._focus_source_index: dict[tuple[str, str], str] = {}
        self._command_history = PhotoTagCommandHistory(self, max_commands=100)
        super().__init__(parent)
        from app_common.file_browser._browser_core import _TREE_COL_SPECIES
        rarity_column = self._file_table_model.rarity_column
        self._tree_widget.setItemDelegateForColumn(rarity_column, RarityBadgeDelegate(self._tree_widget))
        self._tree_widget.setColumnWidth(rarity_column, 88)
        header = self._tree_widget.header()
        header.moveSection(header.visualIndex(rarity_column), header.visualIndex(_TREE_COL_SPECIES) + 1)
        # 保留评级徽章的紧凑形状；选中态与预览 Toggle 共用高对比蓝底。
        for button in self._rating_filter_badge_buttons:
            button.setStyleSheet(button.styleSheet() + TOGGLE_CHECKED_STYLE)
        self._command_history.add_observer(self.command_history_changed.emit)
        from .tag_library_controller import TagLibraryController
        self.tag_library = TagLibraryController(self)
        self._load_tag_config_if_changed(force=True)
        self._install_tag_filter_bar()
        self._install_species_shortcuts()
        if self._filter_edit is not None:
            self._filter_edit.setPlaceholderText("过滤文件名/备注/标签…")
            self._filter_edit.setToolTip("空格分隔多个关键词；每个关键词可匹配文件名、备注或照片标签，需全部命中。")

    def _install_species_shortcuts(self) -> None:
        self._species_shortcuts = []
        for view in (self._tree_widget, self._list_widget):
            for kind in ("copy", "paste"):
                shortcut = QShortcut(species_shortcut_sequence(kind), view)
                # 限定照片视图自身，编辑鸟名/备注/过滤文本时保留编辑器的按键行为。
                shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
                shortcut.setAutoRepeat(False)
                shortcut.activated.connect(lambda k=kind: self._trigger_species_shortcut(k))
                self._species_shortcuts.append(shortcut)

    def _trigger_species_shortcut(self, kind: str) -> None:
        if self._tag_shutdown_requested:
            return
        paths = self._active_view_selected_paths()
        if not paths:
            return
        if kind == "copy":
            current = self._active_view_current_path()
            self._copy_species_from_path(current if current in paths else paths[0])
        elif self._report_root_dir or self._current_dir:
            # 复用菜单的快照、XMP 写入及权限检查，支持粘贴到多选照片。
            self._paste_species_to_paths(paths)

    def apply_user_options(self) -> None:
        super().apply_user_options()
        self._file_table_model.refresh_badges()
        self._list_widget.viewport().update()
        self._sync_rarity_filter_widgets()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if getattr(self, "_tag_filter_bar", None) is not None and is_theme_change_event(event):
            self._apply_tag_filter_theme()

    def _apply_tag_filter_theme(self) -> None:
        """Restyle existing filter controls without changing photo or filter state."""
        if self._tag_filter_bar is None or self._tag_filter_theme_applying or self._tag_shutdown_requested:
            return
        self._tag_filter_theme_applying = True
        try:
            colors = panel_theme_colors(scheme_from_palette(self.palette()))
            button_style = _tag_filter_button_style(colors)
            styled_widgets = [
                (self._tag_filter_menu_button, button_style),
                (self._rarity_filter_button, button_style),
                (self._species_filter_button, button_style),
            ]
            for widget, style in styled_widgets:
                if widget is not None and widget.styleSheet() != style:
                    widget.setStyleSheet(style)
        finally:
            self._tag_filter_theme_applying = False

    def close_tag_store(self) -> None:
        self._stop_photo_tag_cache_loader()
        self._photo_tag_store.close()

    def request_shutdown(self) -> None:
        if self._tag_shutdown_requested:
            return
        self._tag_shutdown_requested = True
        self.tag_library.request_shutdown()
        self._request_background_shutdown()
        try:
            self.stop_key_navigation_playback(commit=False)
        except Exception:
            pass
        self._stop_photo_tag_cache_loader()
        self._pause_thumb_model_population()
        self._pause_tree_model_population()
        self._stop_all_loaders()
        self._stop_persistent_thumb_cache_worker()
        self._stop_directory_scan_worker()
        self._request_worker_pool_shutdown()

    def shutdown(self) -> None:
        """Stop all owned workers even when the child widget gets no closeEvent."""
        if self._tag_shutdown_complete:
            return
        self.request_shutdown()
        super()._shutdown_background_work()
        self._photo_tag_store.close()
        worker = self._photo_tag_stopping_loader
        if worker is not None and not worker.isRunning():
            self._on_photo_tag_cache_thread_finished(worker)
        self._tag_shutdown_complete = True

    def _stop_all_loaders(self) -> None:
        self._stop_photo_tag_cache_loader()
        super()._stop_all_loaders()

    def available_photo_tags(self) -> list[str]:
        """Return the current configured SuperViewer tag vocabulary."""
        self._load_tag_config_if_changed()
        return list(self._available_tags)

    def available_photo_tag_tree(self) -> list[TagTreeNode]:
        """Return the active config's display groups and assignable leaves."""
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

    def set_photo_tag_for_paths(self, paths: Iterable[str], tag: str, enabled: bool) -> None:
        """Set or unset one configured tag for one or more photo paths."""
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("保存标签", warn=True):
            return
        self._load_tag_config_if_changed()
        self._set_tag_for_paths(_norm_paths(paths), tag, enabled)

    def clear_photo_tags_for_paths(self, paths: Iterable[str]) -> None:
        """Clear all configured tags for one or more photo paths."""
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("清除标签", warn=True):
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
        self._command_history.clear()
        return True

    def _reload_after_trash(self, moved_paths: list[str]) -> None:
        """Restore the nearest surviving predecessor in the displayed order."""
        if self._view_mode == self._MODE_THUMB:
            ordered = self._thumb_list_model.all_paths()
        else:
            model = self._tree_widget.model()
            ordered = [
                self._tree_path_from_index(model.index(row, 0))
                for row in range(model.rowCount())
            ]
        removed = {os.path.normcase(os.path.normpath(path)) for path in moved_paths}
        current = self._active_view_current_path()
        target = ""
        if current and os.path.normcase(current) not in removed:
            target = current
        else:
            anchor = next((i for i, path in enumerate(ordered) if path == current), None)
            if anchor is None:
                anchor = next((i for i, path in enumerate(ordered)
                               if os.path.normcase(path) in removed), 0)
            candidates = list(reversed(ordered[:anchor])) + ordered[anchor:]
            target = next((path for path in candidates
                           if os.path.normcase(path) not in removed), "")
        super()._reload_after_trash(moved_paths)
        # load_directory clears/rebuilds the old model synchronously. Queue the
        # target afterwards so that empty rebuild cannot consume the request.
        self.set_pending_selection([target] if target else [], apply_immediately=False)

    def _remember_directory_selection(self) -> None:
        view = self._list_widget if self._view_mode == self._MODE_THUMB else self._tree_widget
        index = view.currentIndex()
        # 切换太快时旧目录可能还未填完；无有效当前项不能覆盖上一次记忆。
        if index.isValid():
            self._directory_selections.remember(
                self.get_current_dir(), self._active_view_current_path(), index.row(),
            )

    def _select_first_file_if_needed(self, *, reason: str = "") -> None:
        if self._background_shutdown_requested:
            return
        if self._pending_selection_paths or self._selected_display_path or not self._filtered_files:
            return
        if self._active_view_selected_paths():
            return
        remembered = self._directory_selections.get(self.get_current_dir())
        if remembered is None:
            super()._select_first_file_if_needed(reason=reason)
            return
        # 等当前视图填充、排序完成，避免把尚未加入模型的照片误判为已删除。
        if self._view_mode == self._MODE_THUMB:
            if self._thumb_model_dirty:
                return
            ordered = self._thumb_list_model.all_paths()
        else:
            if self._tree_view_dirty:
                return
            model = self._tree_widget.model()
            ordered = [self._tree_path_from_index(model.index(row, 0)) for row in range(model.rowCount())]
        target = remembered.resolve(
            ordered, lambda path: os.path.isfile(self._resolve_source_path_for_action(path)),
        )
        if target and self.select_display_path_silently(target):
            self._request_selection_visibility_restore(target, reason="directory_selection")
            self._schedule_selection_visibility_restore(target, reason="directory_selection")
            self._emit_file_selected_for_path(target)

    def _populate_thumb_model_batch(self) -> None:
        super()._populate_thumb_model_batch()
        if (self._view_mode == self._MODE_THUMB and not self._thumb_model_dirty
                and self._directory_selections.get(self.get_current_dir()) is not None):
            self._select_first_file_if_needed(reason="thumb_model_population_done")

    def load_directory(
        self,
        path: str,
        force_reload: bool = False,
        *,
        preserve_meta_cache: bool = False,
        reuse_cached_listing: bool = False,
    ) -> None:
        if self.tag_library.busy or self.tag_library.refreshing:
            return
        self._remember_directory_selection()
        self.tag_library.dirty_paths.clear()
        tag_config_scope_changed = self._set_tag_config_directory(path)
        self._load_tag_config_if_changed(force=tag_config_scope_changed)
        if ((force_reload and not preserve_meta_cache)
                or os.path.normcase(os.path.normpath(path)) != os.path.normcase(self.get_current_dir() or "")):
            self._local_metadata_updates_by_path = {}
        self._photo_tag_cache = {}
        self._photo_tag_generation_by_path = {}
        self._photo_tag_cache_complete = False
        self._photo_tag_cache_done = 0
        self._photo_tag_cache_total = 0
        self._photo_tag_pending_paths = []
        self._photo_tag_loader_covers_all_files = False
        self._focus_source_index = {}
        self._stop_photo_tag_cache_loader()
        pending_paths = self._pending_selection_paths
        pending_current = self._pending_selection_current_path
        super().load_directory(
            path,
            force_reload=force_reload,
            preserve_meta_cache=preserve_meta_cache,
            reuse_cached_listing=reuse_cached_listing,
        )
        # 列表模式的空模型重建会消费待选请求；保留外部打开文件的明确选择，
        # 等真正扫描结果到达后应用，优先于目录记忆。
        if pending_paths and self._directory_scan_worker is not None and not self._pending_selection_paths:
            self.set_pending_selection(pending_paths, pending_current, apply_immediately=False)

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
        self._rebuild_focus_source_index(files)
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

    def _apply_store_metadata_snapshot(self, batch: dict) -> None:
        """A newer shared revision supersedes the prior local UI protection."""
        from app_common.exif_io.metadata_store import get_store
        store = get_store()
        accepted = {}
        overrides = getattr(self, "_local_metadata_updates_by_path", {})
        for path, metadata in batch.items():
            norm = os.path.normpath(path)
            revision = int(metadata.get("_metadata_revision", 0))
            current = max(store.current_revision(norm),
                          int(self._meta_cache.get(norm, {}).get("_metadata_revision", 0)))
            if revision < current or metadata == self._meta_cache.get(norm):
                continue
            key = os.path.normcase(norm)
            overrides.pop(key, None)
            self._photo_tag_generation_by_path.pop(key, None)
            self._photo_tag_cache.pop(norm, None)
            accepted[norm] = metadata
        if accepted:
            super()._apply_store_metadata_snapshot(accepted)

    def _on_metadata_batch_ready(self, meta_dict: dict) -> None:
        if not self._is_current_metadata_sender():
            return
        # Check the read generation before local overlays can replace it with
        # an edit's newer generation. Unversioned legacy deltas remain valid.
        from app_common.exif_io.metadata_store import get_store
        store = get_store()
        meta_dict = {path: metadata for path, metadata in meta_dict.items()
                     if "_metadata_revision" not in metadata or
                     int(metadata["_metadata_revision"]) >= max(
                         int(self._meta_cache.get(os.path.normpath(path), {}).get("_metadata_revision", 0)),
                         store.current_revision(path))}
        library = getattr(self, "tag_library", None)
        if library is not None:
            meta_dict = {path: ({key: value for key, value in meta.items() if key != "tags"}
                               if library.busy or library.refreshing or os.path.normpath(path) in library.dirty_paths
                               else meta) for path, meta in meta_dict.items()}
        meta_dict = self._merge_metadata_batch_with_photo_tag_cache(meta_dict)
        super()._on_metadata_batch_ready(meta_dict)
        changed = self._seed_photo_tag_cache_from_meta(meta_dict.keys())
        if changed:
            self.photo_tags_cache_updated.emit(changed)
        if meta_dict:
            self.photo_metadata_cache_updated.emit(list(meta_dict))

    def _on_metadata_loader_finished(self) -> None:
        if self._background_shutdown_requested or self.sender() is not self._metadata_loader:
            return
        all_norm = _norm_paths(self._all_files)
        self._photo_tag_cache_complete = bool(all_norm) and all(
            path in self._photo_tag_cache for path in all_norm
        )
        super()._on_metadata_loader_finished()

    def cached_photo_metadata_for_path(self, path: str) -> dict:
        """Return current browser/report fields without waiting for EXIF I/O."""
        metadata = dict(self.get_report_row_for_path(path) or {})
        metadata.update(self.get_photo_metadata_for_path(path, allow_slow_read=False))
        return metadata

    def cached_preview_metadata_for_path(self, path: str) -> dict:
        """Read only this viewport's source caches, never another selected photo."""
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path:
            return {}
        row = self._report_row_by_path.get(norm_path)
        if not isinstance(row, dict):
            row = _report_row_from_cache_for_path(norm_path, self._report_full_cache or self._report_cache)
        metadata = dict(row or {})
        aliases = _report_row_candidate_paths(row, str((row or {}).get("_report_root_dir") or self._report_root_dir))
        selected = self._selected_display_path
        actual = _get_cached_actual_path(selected) if selected else None
        if actual and os.path.normcase(os.path.normpath(actual)) == os.path.normcase(norm_path):
            aliases.append(os.path.normpath(selected))
        for candidate in (*aliases, norm_path):
            metadata.update(self._meta_cache.get(candidate) or {})
        return metadata

    def sync_metadata_edits_for_paths(self, updates_by_path: dict) -> int:
        """Single and batch metadata syncs both record local overrides here."""
        updated = super().sync_metadata_edits_for_paths(updates_by_path)
        if updated:
            overrides = getattr(self, "_local_metadata_updates_by_path", None)
            if overrides is None:
                overrides = self._local_metadata_updates_by_path = {}
            for path, meta_updates in (updates_by_path or {}).items():
                if not path:
                    continue
                values = {
                    str(name): value for name, value in (meta_updates or {}).items()
                    if str(name) and value is not None
                }
                if values:
                    key = os.path.normcase(os.path.normpath(path))
                    overrides.setdefault(key, {}).update(values)
        return updated

    def replace_photo_metadata_for_paths(self, metadata_by_path: dict) -> int:
        """整份 XMP 被替换后清除旧字段和标签；晚到的后台批次不能恢复旧值。"""
        updates = {}
        allowed_tags = set(self._available_tags)
        overrides = getattr(self, '_local_metadata_updates_by_path', {})
        for path, metadata in metadata_by_path.items():
            norm = os.path.normpath(path)
            previous = {**(self.get_report_row_for_path(norm) or {}),
                        **self._meta_cache.get(norm, {}),
                        **overrides.get(os.path.normcase(norm), {})}
            # 用显式空值覆盖旧字段，同时保留重新读到的原图/报告回退信息。
            fresh = {key: '' for key in previous}
            fresh.update({key: value if value is not None else '' for key, value in metadata.items()})
            fresh['SourceFile'] = norm
            updates[norm] = fresh
            self._photo_tag_cache[norm] = set(fresh.get('tags') or ()).intersection(allowed_tags)
        self._bump_photo_tag_generations(updates)
        return self.sync_metadata_edits_for_paths(updates)

    def _start_key_navigation_playback(self, event, *, view_name: str) -> None:
        self.video_playback_stop_requested.emit()
        super()._start_key_navigation_playback(event, view_name=view_name)

    def _on_key_navigation_playback_tick(self) -> None:
        # 缺少缩略图时快切信号可能不发出，仍须在首帧之前暂停后台鸟体分析。
        if self._key_navigation_playback_active and not getattr(self, "_playback_notified", False):
            self._playback_notified = True
            self.playback_state_changed.emit(True)
        super()._on_key_navigation_playback_tick()

    def _stop_key_navigation_playback(self, *, commit: bool) -> None:
        notified = getattr(self, "_playback_notified", False)
        super()._stop_key_navigation_playback(commit=commit)
        self._playback_notified = False
        if notified:
            self.playback_state_changed.emit(False)

    def cached_quick_preview_for_path(self, path: str, size: int) -> QPixmap | None:
        """Reuse the same exact-tier cache resolver as held-key playback."""
        if int(size) != int(self._thumb_size):
            return None
        pixmap = self._current_thumbnail_fast_preview_pixmap(path)
        if pixmap is not None and not pixmap.isNull():
            return pixmap
        cache_path = self._resolve_existing_sized_preview_image_path(path, exact_size_only=True)
        if not cache_path:
            return None
        pixmap = QPixmap(cache_path)
        return pixmap if not pixmap.isNull() else None

    def _rebuild_focus_source_index(self, paths: Iterable[str]) -> None:
        """Index RAW/HEIF siblings once per directory listing for focus lookup."""
        ranked: dict[tuple[str, str], tuple[int, str]] = {}
        for raw_path in paths or []:
            if not raw_path:
                continue
            norm_path = os.path.normpath(os.path.abspath(raw_path))
            source = Path(norm_path)
            rank = _FOCUS_SOURCE_EXTENSION_RANKS.get(source.suffix.lower())
            if rank is None or not source.stem:
                continue
            key = (
                os.path.normcase(os.path.normpath(str(source.parent))),
                source.stem.casefold(),
            )
            candidate = (rank, norm_path)
            existing = ranked.get(key)
            if existing is None or (candidate[0], os.path.normcase(candidate[1])) < (
                existing[0],
                os.path.normcase(existing[1]),
            ):
                ranked[key] = candidate
        self._focus_source_index = {key: candidate[1] for key, candidate in ranked.items()}

    def focus_source_for_sibling(self, path: str) -> str | None:
        """Return the indexed same-directory/same-stem RAW or HEIF source."""
        if not path:
            return None
        try:
            source = Path(os.path.normpath(os.path.abspath(path)))
            key = (
                os.path.normcase(os.path.normpath(str(source.parent))),
                source.stem.casefold(),
            )
        except (OSError, TypeError, ValueError):
            return None
        candidate = self._focus_source_index.get(key)
        return candidate if candidate and os.path.isfile(candidate) else None

    def resolve_preview_path(self, path: str, prefer_fast_preview: bool = False) -> str:
        """SuperViewer 正常预览原图；方向键 fast preview 优先使用当前缩略图尺寸缓存。"""
        norm_path = os.path.normpath(path) if path else ""
        if not norm_path or not prefer_fast_preview:
            return norm_path
        cached_path = self._resolve_existing_sized_preview_image_path(
            norm_path,
            exact_size_only=True,
        )
        return cached_path or norm_path

    def _resolve_rating_write_source(
        self,
        path: str,
        *,
        report_db_available: bool,
    ) -> str:
        return "xmp_sidecar"

    def _apply_rating_state_via_exif(self, paths, *, rating=None, pick=None):
        return super()._apply_rating_state_via_exif(paths, rating=rating, pick=pick)

    def _has_any_filter(self) -> bool:
        return (super()._has_any_filter() or bool(self._active_tag_filters)
                or bool(self._lens_filter_value) or bool(self._active_rarity_filters)
                or bool(self._active_species_filters))

    def _filter_text_tokens(self) -> list[str]:
        if not getattr(self, "_filter_edit", None):
            return []
        return str(self._filter_edit.text() or "").split()

    def _photo_tag_lookup_needed_for_filters(self) -> bool:
        return bool(self._active_tag_filters) or bool(self._filter_text_tokens())

    def _photo_tags_for_filter_path(self, path: str) -> set[str]:
        norm = os.path.normpath(path)
        tags = self._photo_tag_cache.get(norm)
        if tags is None:
            tags = self._photo_tags_from_meta_cache(norm)
        return set(tags or set())

    def _path_matches_active_filters(self, path: str) -> bool:
        if not self._path_matches_filters(
            path,
            filter_text="",
            filter_pick=self._filter_pick,
            filter_reject=self._filter_reject,
            filter_min_rating=self._filter_min_rating,
            filter_focus_status=self._filter_focus_status,
        ):
            return False
        if self._lens_filter_value and self._lens_model_for_path(path) != self._lens_filter_value:
            return False
        if self._active_species_filters and self._species_for_path(path) not in self._active_species_filters:
            return False
        if self._active_rarity_filters:
            meta = self._meta_cache.get(os.path.normpath(path), {})
            score = rarity_metadata(meta if isinstance(meta, dict) else {})[0]
            if rarity_level(score) not in self._active_rarity_filters:
                return False
        tokens = self._filter_text_tokens()
        if tokens:
            norm = os.path.normpath(path)
            meta = self._meta_cache.get(norm, {})
            if not filter_text_tokens_match(
                tokens,
                name=Path(norm).name,
                comment=_metadata_comment_from_meta(meta if isinstance(meta, dict) else {}),
                photo_tags=self._photo_tags_for_filter_path(norm),
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

    def _get_species_payload_for_path(self, path: str, *, read_sidecar: bool = False) -> dict | None:
        # 打开菜单只读内存；点击复制或按快捷键时才读取源照片的最新快照。
        return super()._get_species_payload_for_path(
            path, read_sidecar=read_sidecar, allow_metadata_read=read_sidecar,
        )

    def _show_file_context_menu(
        self, viewport, pos, *, paths: list[str], primary_path: str | None, log_prefix: str,
    ) -> None:
        menu_paths = self._unique_norm_paths(paths)
        if primary_path:
            primary_norm = os.path.normpath(primary_path)
            if primary_norm and primary_norm not in menu_paths:
                menu_paths.insert(0, primary_norm)
        if not menu_paths:
            self._show_empty_file_context_menu(viewport, pos)
            return
        primary = os.path.normpath(primary_path) if primary_path else menu_paths[0]
        menu = build_file_context_menu(self, menu_paths, primary, log_prefix=log_prefix)
        try:
            _exec_menu(menu, viewport.mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def _lens_model_for_path(self, path: str) -> str:
        meta = self._meta_cache.get(os.path.normpath(path), {})
        return _metadata_lens_model_text(meta if isinstance(meta, dict) else {}).strip()

    def _lens_model_counts(self) -> dict[str, int]:
        """Count lens models over every listed photo, ignoring the active filters."""
        counts: dict[str, int] = {}
        for path in self._all_files:
            lens = self._lens_model_for_path(path)
            if lens:
                counts[lens] = counts.get(lens, 0) + 1
        return counts

    def _install_lens_filter_combo(self, layout) -> None:
        combo = FilterableComboBox()
        combo.setFilterPlaceholderText("过滤镜头...")
        combo.setSizeAdjustPolicy(FilterableComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
        combo.setMaximumWidth(220)
        combo.popupAboutToShow.connect(self._refresh_lens_filter_options)
        combo.currentIndexChanged.connect(self._on_lens_filter_index_changed)
        self._lens_filter_combo = combo
        layout.addWidget(combo)
        self._refresh_lens_filter_options()

    def _refresh_lens_filter_options(self) -> None:
        """Rebuild options from the lens models of the photos currently listed."""
        combo = self._lens_filter_combo
        if combo is None:
            return
        counts = self._lens_model_counts()
        active = self._lens_filter_value
        if active and active not in counts:
            # Keep the active choice selectable so it stays visible and can be cleared.
            counts[active] = 0
        with QSignalBlocker(combo):
            combo.clear()
            combo.addItem(_LENS_FILTER_ALL_TEXT, "")
            for lens in sorted(counts, key=str.casefold):
                combo.addItem(f"{lens} ({counts[lens]})", lens)
            index = combo.findData(active) if active else 0
            combo.setCurrentIndex(max(0, index))
        self._sync_lens_filter_tooltip()

    def _sync_lens_filter_tooltip(self) -> None:
        combo = self._lens_filter_combo
        if combo is None:
            return
        if self._lens_filter_value:
            combo.setToolTip(f"只显示镜头「{self._lens_filter_value}」拍摄的照片")
        else:
            combo.setToolTip("按镜头型号过滤；选项来自当前列表中的照片")

    def _on_lens_filter_index_changed(self, index: int) -> None:
        combo = self._lens_filter_combo
        if combo is None:
            return
        self.set_lens_filter(str(combo.itemData(index) or "") if index >= 0 else "")

    def set_lens_filter(self, lens: str) -> None:
        lens = str(lens or "").strip()
        if lens == self._lens_filter_value:
            return
        self._lens_filter_value = lens
        self._refresh_lens_filter_options()
        self._refresh_filter_scope()

    def _install_tag_filter_bar(self) -> None:
        if not getattr(self, "_create_filter_bar", True):
            return
        layout = self.layout()
        if layout is None:
            return
        self._filter_controls = QWidget(self)
        row = FilterFlowLayout(self._filter_controls)
        self._tag_filter_bar = row
        self._install_lens_filter_combo(row)
        self._species_filter_button = FilterDropdown(self)
        self._species_filter_button.menu().aboutToShow.connect(self._populate_species_filter_menu)
        row.addWidget(self._species_filter_button)
        self._rarity_filter_button = FilterDropdown(self)
        self._rarity_filter_button.menu().aboutToShow.connect(self._populate_rarity_filter_menu)
        row.addWidget(self._rarity_filter_button)
        self._tag_filter_menu_button = FilterDropdown(self)
        self._tag_filter_menu_button.menu().aboutToShow.connect(self._populate_tag_filter_menu)
        row.addWidget(self._tag_filter_menu_button)
        stack_index = layout.indexOf(getattr(self, "_stack", None))
        if stack_index >= 0:
            layout.insertWidget(stack_index, self._filter_controls)
        else:
            layout.addWidget(self._filter_controls)
        self._rebuild_tag_filter_bar()
        self._sync_species_filter_widgets()
        self._sync_rarity_filter_widgets()

    def _rebuild_tag_filter_bar(self) -> None:
        # 标签库重载只同步摘要；按钮和已展开菜单的生命周期不随勾选变化。
        self._apply_tag_filter_theme()
        self._sync_tag_filter_widgets()

    def _sync_tag_filter_widgets(self) -> None:
        button = self._tag_filter_menu_button
        if button is None:
            return
        count = len(self._active_tag_filters)
        button.setText(f"标签({count})" if count else "全部标签")
        button.setChecked(bool(count))
        mode = "完全匹配：同时包含所有已选标签" if not self._tag_filter_partial_match else "部分匹配：任意已选标签命中"
        selected = "、".join(tag for tag in self._available_tags if tag in self._active_tag_filters)
        button.setToolTip(f"{mode}\n已选：{selected or '不限'}\n打开菜单搜索、多选或清除标签过滤")
        if self._tag_filter_clear_action is not None:
            self._tag_filter_clear_action.setEnabled(bool(count))
        if self._tag_filter_exact_match_action is not None:
            with QSignalBlocker(self._tag_filter_exact_match_action):
                self._tag_filter_exact_match_action.setChecked(not self._tag_filter_partial_match)

    def _populate_tag_filter_menu(self) -> None:
        self._load_tag_config_if_changed()
        menu = self._tag_filter_menu_button.menu()
        self._tag_filter_exact_match_action = None
        self._tag_filter_clear_action = None
        menu.clear()
        exact = menu.addAction("完全匹配（包含全部已选标签）")
        exact.setCheckable(True)
        exact.setChecked(not self._tag_filter_partial_match)
        exact.toggled.connect(self._on_tag_exact_match_toggled)
        self._tag_filter_exact_match_action = exact
        clear = menu.addAction("清除标签过滤")
        clear.setEnabled(bool(self._active_tag_filters))
        clear.triggered.connect(lambda checked=False: self._clear_tag_filters())
        self._tag_filter_clear_action = clear
        menu.addSeparator()
        if self._available_tags:
            add_filterable_tag_actions(
                menu, self._available_tags, self._on_tag_filter_toggled,
                tag_tree=self._available_tag_tree, checkable=True, keep_open=True,
                checked_provider=lambda tag: tag in self._active_tag_filters,
                filter_placeholder="过滤标签", no_match_text="没有匹配的标签",
            )
        else:
            menu.addAction("tags.cfg 未配置标签").setEnabled(False)

    def _species_for_path(self, path: str) -> str:
        """与列表鸟名列共用字段优先级；过滤和打开菜单只读已有缓存。"""
        return species_filter_name(_metadata_species_text(self._meta_cache.get(os.path.normpath(path))))

    def _species_filter_options(self) -> list[str]:
        # 从未过滤的全集取选项；换目录后保留已选鸟名，仍可取消或清除。
        names = {self._species_for_path(path) for path in self._all_files}
        return sorted((names - {""}) | self._active_species_filters, key=str.casefold)

    def _populate_species_filter_menu(self) -> None:
        menu = self._species_filter_button.menu()
        self._species_filter_clear_action = None
        menu.clear()
        clear = menu.addAction("清除鸟种名过滤")
        clear.setEnabled(bool(self._active_species_filters))
        clear.triggered.connect(lambda checked=False: self.set_species_filters(set()))
        self._species_filter_clear_action = clear
        menu.addSeparator()
        names = self._species_filter_options()
        if not names:
            menu.addAction("当前照片暂无鸟种名").setEnabled(False)
            return
        # FilterMenu 已支持连续勾选，使用原生动作保留长菜单滚动和键盘导航。
        actions = add_filterable_tag_actions(
            menu, names, self._on_species_filter_toggled,
            checkable=True, checked_provider=lambda name: name in self._active_species_filters,
            filter_placeholder="搜索鸟种名…", no_match_text="没有匹配的鸟种名",
        )
        for action, name in zip(actions, names):
            action.setData(name)

    def _on_species_filter_toggled(self, name: str, checked: bool) -> None:
        names = set(self._active_species_filters)
        if checked:
            names.add(name)
        else:
            names.discard(name)
        self.set_species_filters(names)

    def set_species_filters(self, names: Iterable[str]) -> None:
        selected = {species_filter_name(name) for name in names} - {""}
        if selected == self._active_species_filters:
            return
        self._active_species_filters = selected
        self._sync_species_filter_widgets()
        self._refresh_filter_scope()

    def _sync_species_filter_widgets(self) -> None:
        button = self._species_filter_button
        if button is None:
            return
        count = len(self._active_species_filters)
        button.setText(f"鸟种名({count})" if count else "全部鸟种名")
        button.setChecked(bool(count))
        selected = "、".join(sorted(self._active_species_filters, key=str.casefold))
        button.setToolTip(
            "多选鸟种名，命中任一鸟种即可；与其他过滤条件同时生效。\n"
            "同一鸟名的待确定等状态和置信度标注合并过滤。\n"
            f"已选：{selected or '不限'}\n选项来自当前目录的全部照片；可搜索或清除过滤"
        )
        if self._species_filter_clear_action is not None:
            self._species_filter_clear_action.setEnabled(bool(count))
        for action in button.menu().actions():
            if action.isCheckable():
                with QSignalBlocker(action):
                    action.setChecked(action.data() in self._active_species_filters)

    def _populate_rarity_filter_menu(self) -> None:
        menu = self._rarity_filter_button.menu()
        self._rarity_filter_clear_action = None
        menu.clear()
        clear = menu.addAction("清除稀有度过滤")
        clear.setEnabled(bool(self._active_rarity_filters))
        clear.triggered.connect(lambda checked=False: self.set_rarity_filters(set()))
        self._rarity_filter_clear_action = clear
        menu.addSeparator()
        for level, description, _text, _color in RARITY_LEVELS:
            text = rarity_badge_style(None, level=level)[0]
            action = menu.addAction(text)
            action.setData(level)
            action.setToolTip(description)
            action.setCheckable(True)
            action.setChecked(level in self._active_rarity_filters)
            action.toggled.connect(lambda checked, key=level: self._on_rarity_filter_toggled(key, checked))

    def _on_rarity_filter_toggled(self, level: str, checked: bool) -> None:
        levels = set(self._active_rarity_filters)
        if checked:
            levels.add(level)
        else:
            levels.discard(level)
        self.set_rarity_filters(levels)

    def set_rarity_filters(self, levels: Iterable[str]) -> None:
        selected = set(levels).intersection(level[0] for level in RARITY_LEVELS)
        if selected == self._active_rarity_filters:
            return
        self._active_rarity_filters = selected
        self._sync_rarity_filter_widgets()
        self._refresh_filter_scope()

    def _sync_rarity_filter_widgets(self) -> None:
        button = self._rarity_filter_button
        if button is None:
            return
        count = len(self._active_rarity_filters)
        button.setText(f"稀有度({count})" if count else "全部稀有度")
        button.setChecked(bool(count))
        selected = "、".join(rarity_badge_style(None, level=key)[0]
                            for key, *_ in RARITY_LEVELS if key in self._active_rarity_filters)
        button.setToolTip(f"多选稀有度，命中任一等级即可；与其他过滤条件同时生效。\n已选：{selected or '不限'}")
        if self._rarity_filter_clear_action is not None:
            self._rarity_filter_clear_action.setEnabled(bool(count))
        for action in button.menu().actions():
            if action.isCheckable():
                with QSignalBlocker(action):
                    action.setChecked(action.data() in self._active_rarity_filters)
                    action.setText(rarity_badge_style(None, level=action.data())[0])

    def _filters_require_rebuild_after_metadata_refresh(self, paths: list[str]) -> bool:
        # 手动改等级/识鸟结果也可能使照片进入或离开当前过滤集合。
        if super()._filters_require_rebuild_after_metadata_refresh(paths):
            return True
        if not (self._active_species_filters or self._active_rarity_filters
                or self._active_tag_filters or self._lens_filter_value):
            return False
        visible = {os.path.normcase(os.path.normpath(path)) for path in self._filtered_files}
        listed = {os.path.normcase(os.path.normpath(path)) for path in self._all_files}
        return any(
            (os.path.normcase(path) in visible) != self._path_matches_active_filters(path)
            for path in self._unique_norm_paths(paths) if os.path.normcase(path) in listed
        )

    def _load_tag_config_if_changed(self, *, force: bool = False, preserve_history: bool = False) -> bool:
        library = getattr(self, "tag_library", None)
        if library is not None and library.busy and not preserve_history:
            return False
        signature = _config_signature(self._tag_config.path)
        if not force and signature == self._tag_config_signature:
            return False
        self._tag_config_signature = signature
        new_tree, new_tags = self._tag_config.load_tree_and_tags()
        if new_tree == self._available_tag_tree and new_tags == self._available_tags and not force:
            return False
        if new_tags != self._available_tags:
            if set(new_tags) != set(self._available_tags) and not preserve_history:
                self._command_history.clear()
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
        if library is not None:
            library.check_recovery()
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
        return isinstance(meta, dict) and "tags" in meta

    def _seed_photo_tag_cache_from_meta(self, paths: Iterable[str]) -> list[str]:
        allowed = set(self._available_tags)
        if not allowed:
            return []
        changed: list[str] = []
        for path in _norm_paths(paths):
            library = getattr(self, "tag_library", None)
            if library is not None and path in library.dirty_paths:
                continue
            if not self._meta_cache_has_photo_tags(path):
                continue
            tags = self._photo_tags_from_meta_cache(path).intersection(allowed)
            if self._photo_tag_cache.get(path) == tags and path in self._photo_tag_cache:
                continue
            self._photo_tag_cache[path] = tags
            changed.append(path)
        # Called for every small metadata batch and individual tag lookup.
        # Scanning the directory here turns N files into O(N * batch_count)
        # GUI-thread work. Reconcile completeness once when metadata finishes.
        return changed

    def _start_photo_tag_cache_loader_if_needed(
        self, paths: Iterable[str], *, reason: str, allow_without_filters: bool = False,
    ) -> None:
        """Tags are fields of the shared metadata snapshot, never a second XML scan."""
        if self._tag_shutdown_requested or not self._available_tags:
            return
        path_list = _norm_paths(paths)
        changed = self._seed_photo_tag_cache_from_meta(path_list)
        if changed:
            self.photo_tags_cache_updated.emit(changed)
        missing = [path for path in path_list if path not in self._photo_tag_cache]
        if missing and self._metadata_loader is None:
            self._start_metadata_loader(missing)

    def _on_photo_tag_cache_batch_ready(self, worker: PhotoTagCacheWorker, batch: dict[str, set[str]]) -> None:
        if worker is not self._photo_tag_loader or not batch:
            return
        snapshot = getattr(worker, "_tag_generation_snapshot", {})
        accepted: list[str] = []
        for path, tags in batch.items():
            norm_path = os.path.normpath(path)
            if snapshot.get(os.path.normcase(norm_path), 0) != self._photo_tag_generation(norm_path):
                continue
            self._photo_tag_cache[norm_path] = set(tags or set())
            library = getattr(self, "tag_library", None)
            if library is not None and norm_path in library.dirty_paths:
                library.dirty_paths.discard(norm_path)
                self._bump_photo_tag_generations([norm_path])
            accepted.append(norm_path)
        if not accepted:
            return
        self._sync_photo_tags_to_meta_cache(accepted)
        self.photo_tags_cache_updated.emit(accepted)
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
        # The summary is emitted inside run(); the thread still owns its work.
        self._photo_tag_cache_done = max(0, int(done or 0))
        self._photo_tag_cache_total = max(0, int(total or 0))

    def _on_photo_tag_cache_thread_finished(self, worker: PhotoTagCacheWorker) -> None:
        if worker in self._pending_loaders:
            self._pending_loaders.remove(worker)
        if worker is self._photo_tag_stopping_loader:
            self._photo_tag_stopping_loader = None
            pending = self._photo_tag_pending_paths
            self._photo_tag_pending_paths = []
            if pending and not self._tag_shutdown_requested:
                self._start_photo_tag_cache_loader_if_needed(
                    pending, reason="pending_after_stop", allow_without_filters=True,
                )
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
            return
        if worker is not self._photo_tag_loader:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
            return
        self._photo_tag_loader = None
        done, total = worker.finished_counts()
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
        worker.deleteLater()

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
        self.photo_tags_cache_updated.emit(norm_paths)

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
        self.photo_tags_cache_updated.emit(norm_paths)

    def _photo_tag_generation(self, path: str) -> int:
        key = os.path.normcase(os.path.normpath(path)) if path else ""
        return self._photo_tag_generation_by_path.get(key, 0)

    def _bump_photo_tag_generations(self, paths: Iterable[str]) -> None:
        for path in _norm_paths(paths):
            key = os.path.normcase(path)
            self._photo_tag_generation_by_path[key] = (
                self._photo_tag_generation_by_path.get(key, 0) + 1
            )

    def _merge_metadata_batch_with_photo_tag_cache(self, meta_dict: dict) -> dict:
        """Keep local edits when an older metadata read arrives late."""
        order = {tag: index for index, tag in enumerate(self._available_tags)}
        merged = {}
        for path, metadata in meta_dict.items():
            norm_path = os.path.normpath(path) if path else path
            tags = self._photo_tag_cache.get(norm_path)
            item = dict(metadata) if isinstance(metadata, dict) else {}
            item.update(getattr(self, "_local_metadata_updates_by_path", {}).get(os.path.normcase(norm_path), {}))
            if tags is not None and self._photo_tag_generation(norm_path):
                item["tags"] = sorted(tags, key=lambda tag: (order.get(tag, len(order)), tag))
            merged[norm_path] = item
        return merged

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
        self._start_photo_tag_cache_loader_if_needed([path], reason="selection", allow_without_filters=True)

    def _on_tag_filter_toggled(self, tag: str, checked: bool) -> None:
        if checked:
            self._active_tag_filters.add(tag)
        else:
            self._active_tag_filters.discard(tag)
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
        self._sync_tag_filter_widgets()
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

        target_paths = list(norm_paths)

        def refresh_clear() -> None:
            act_clear.setEnabled(
                any(self._tags_for_path(path) for path in target_paths) and writes_allowed
            )

        def apply_tag(tag: str, checked: bool) -> None:
            try:
                self._set_tag_for_paths(target_paths, tag, checked)
            finally:
                refresh_clear()

        add_filterable_tag_actions(
            tag_menu,
            self._available_tags,
            apply_tag,
            tag_tree=self._available_tag_tree,
            checkable=True,
            checked_provider=lambda tag: all(tag in self._tags_for_path(path) for path in target_paths),
            keep_open=True,
            state_changed=self.photo_tags_cache_updated,
            after_state_refresh=refresh_clear,
        )

        tag_menu.addSeparator()
        act_clear = tag_menu.addAction("清除所有TAG")
        act_clear.setEnabled(any(self._tags_for_path(path) for path in target_paths) and writes_allowed)
        act_clear.triggered.connect(lambda checked=False, p=list(norm_paths): self._clear_tags_for_paths(p))

    @property
    def can_undo(self) -> bool:
        return self._command_history.can_undo and self.tag_library.available and not self._command_history.busy

    @property
    def can_redo(self) -> bool:
        return self._command_history.can_redo and self.tag_library.available and not self._command_history.busy

    def clear_tag_history(self) -> None:
        """Discard path-bound tag history after an explicit file rename."""
        self._command_history.clear()

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

    def _show_tag_write_error(self, action: str, exc: Exception) -> None:
        from .tag_library_transaction import TagLibraryFailure
        if isinstance(exc, TagLibraryFailure):
            QMessageBox.warning(self, action + "未完成", str(exc))
            return
        _log.warning("[tag.write] action=%s failed: %s", action, exc)
        QMessageBox.warning(
            self,
            action + "未全部完成",
            str(exc) + "\n成功的修改及其撤销/重做记录已保留；失败文件未更新。"
            + ("\n排除问题后可再次执行此操作。" if action in {"撤销标签", "重做标签"} else ""),
        )

    def _set_tag_for_paths(self, paths: list[str], tag: str, enabled: bool) -> None:
        if not self.tag_edits_allowed():
            return
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("保存标签", warn=True):
            return
        self._load_tag_config_if_changed()
        command = SetPhotoTagCommand(self, _norm_paths(paths), tag, enabled)
        try:
            self._command_history.add_command(command)
        except Exception as exc:
            self._show_tag_write_error("保存标签", exc)

    def _clear_tags_for_paths(self, paths: list[str]) -> None:
        if not self.tag_edits_allowed():
            return
        if self._tag_shutdown_requested or not self._sidecar_writes_allowed("清除标签", warn=True):
            return
        self._load_tag_config_if_changed()
        command = ClearPhotoTagsCommand(self, _norm_paths(paths), self._available_tags)
        try:
            self._command_history.add_command(command)
        except Exception as exc:
            self._show_tag_write_error("清除标签", exc)

    def _apply_photo_tag_states(self, states: TagStates) -> TagWriteResult:
        """Synchronous command adapter is worker-only; UI uses the async entry."""
        if QThread.currentThread() is self.thread():
            raise RuntimeError("标签提交必须通过异步元数据入口。")
        return commit_photo_tag_states(states, allowed_tags=self._available_tags)

    def _apply_photo_tag_states_async(self, states: TagStates, complete):
        from app_common.file_browser._metadata_commit import get_metadata_commit_bridge
        allowed = list(self._available_tags)
        targets = {}
        aliases = {}
        for path, values in states.items():
            source = _get_cached_actual_path(path) or path
            targets.setdefault(source, {}).update(values)
            aliases[source] = path

        def committed(result):
            for source, display in aliases.items():
                if source != display:
                    for mapping in (result.current_tags, result.inverse_states, result.failed_paths, result.revisions):
                        if source in mapping:
                            mapping[display] = mapping.pop(source)
            self._sync_photo_tag_result(result)
            complete(result)

        def failed(exc):
            complete(TagWriteResult(failed_paths={path: str(exc) for path in states}))

        return get_metadata_commit_bridge(self).execute(
            lambda: commit_photo_tag_states(targets, allowed_tags=allowed), committed, failed)

    def _sync_photo_tag_result(self, result: TagWriteResult) -> None:
        from .metadata_edit_sync import metadata_receipt_is_current
        cache_tags = {path: tags for path, tags in result.current_tags.items()
                      if metadata_receipt_is_current(self, path, result.revisions.get(path))}
        revisions = {path: result.revisions[path] for path in cache_tags if path in result.revisions}
        if cache_tags:
            by_source = {os.path.normcase(path): tags for path, tags in cache_tags.items()}
            by_source_revision = {os.path.normcase(path): value for path, value in revisions.items()}
            # 实际源文件写入成功后，同步仍以报告旧路径展示的行；不扫描或修改 report.db。
            for display in self._all_files:
                actual = _get_cached_actual_path(display)
                if actual and os.path.normcase(actual) in by_source:
                    revision = by_source_revision.get(os.path.normcase(actual))
                    if not metadata_receipt_is_current(self, display, revision):
                        continue
                    cache_tags[display] = by_source[os.path.normcase(actual)]
                    if revision is not None:
                        revisions[display] = revision
        updated_paths = list(cache_tags)
        if updated_paths:
            try:
                self._photo_tag_cache.update({path: set(tags) for path, tags in cache_tags.items()})
                self._bump_photo_tag_generations(updated_paths)
                for path, revision in revisions.items():
                    self._meta_cache.setdefault(path, {})["_metadata_revision"] = revision
            except Exception as exc:
                _log.warning("[tag.write] saved tags but tag cache update failed: %s", exc)
            for component, refresh in (
                ("metadata cache", self._sync_photo_tags_to_meta_cache),
                ("tag panels", self.photo_tags_cache_updated.emit),
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
    def tag_edits_allowed(self) -> bool:
        """Operational transaction guard, independent of filesystem permissions."""
        return self.tag_library.available and not self._tag_shutdown_requested and not self._command_history.busy
