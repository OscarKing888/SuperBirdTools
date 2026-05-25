# -*- coding: utf-8 -*-
"""SuperViewer file list with custom photo tags."""
from __future__ import annotations

import os
from typing import Iterable

from app_common.file_browser import FileListPanel
from app_common.file_browser._browser_core import _thumb_disk_cache_path
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log
from app_common.log import get_logger
from app_common.exif_io import PhotoMetaDataXMP

from .photo_tags import PhotoTagConfig, PhotoTagSidecarStore, find_superpicky_tag_config_path
from .qt_compat import QHBoxLayout, QLabel, QMenu, QToolButton
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


class SuperViewerTaggedFileListPanel(FileListPanel):
    """FileListPanel extension that adds configured custom tags."""

    use_report_db = False
    use_preview_cache = False

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
        self._active_tag_filters: set[str] = set()
        self._tag_filter_buttons: dict[str, QToolButton] = {}
        self._tag_filter_menu_button: QToolButton | None = None
        self._tag_filter_clear_button: QToolButton | None = None
        self._photo_tag_store = tag_store or PhotoTagSidecarStore()
        self._photo_tag_cache: dict[str, set[str]] = {}
        self._tag_filter_bar: QHBoxLayout | None = None
        super().__init__(parent)
        self._load_tag_config_if_changed(force=True)
        self._install_tag_filter_bar()

    def close_tag_store(self) -> None:
        self._photo_tag_store.close()

    def available_photo_tags(self) -> list[str]:
        """Return the current configured SuperViewer tag vocabulary."""
        self._load_tag_config_if_changed()
        return list(self._available_tags)

    def photo_tags_for_path(self, path: str) -> set[str]:
        """Return configured tags currently assigned to *path*."""
        self._load_tag_config_if_changed()
        if not path:
            return set()
        return self._tags_for_path(path)

    def set_photo_tag_for_paths(self, paths: Iterable[str], tag: str, enabled: bool) -> None:
        """Set or unset one configured tag for one or more photo paths."""
        self._load_tag_config_if_changed()
        self._set_tag_for_paths(_norm_paths(paths), tag, enabled)

    def clear_photo_tags_for_paths(self, paths: Iterable[str]) -> None:
        """Clear all configured tags for one or more photo paths."""
        self._load_tag_config_if_changed()
        self._clear_tags_for_paths(_norm_paths(paths))

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
        self._refresh_photo_tag_cache(files)
        super()._apply_directory_listing_result(
            path,
            files,
            {},
            None,
            recursive=recursive,
            report_row_by_path={},
            from_cache=from_cache,
        )

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
        fields: dict[str, int] = {}
        if rating is not None:
            fields["XMP-xmp:Rating"] = max(0, min(5, int(rating)))
        if pick is not None:
            fields["XMP-xmpDM:pick"] = max(-1, min(1, int(pick)))
        if not fields:
            return []
        probe_t0 = perf_counter()
        updated_paths: list[str] = []
        xmp = PhotoMetaDataXMP()
        write_count = 0
        for path in self._unique_norm_paths(paths):
            if not path:
                continue
            try:
                ok = xmp.write(path, fields)
            except Exception as exc:
                _log.warning("[_apply_rating_state_via_sidecar] source=%r failed: %s", path, exc)
                continue
            if not ok:
                _log.warning("[_apply_rating_state_via_sidecar] source=%r write returned False", path)
                continue
            write_count += 1
            self._apply_rating_state_to_meta_cache(path, rating=rating, pick=pick)
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

    def _path_matches_active_filters(self, path: str) -> bool:
        if not super()._path_matches_active_filters(path):
            return False
        if not self._active_tag_filters:
            return True
        tags = self._photo_tag_cache.get(os.path.normpath(path), set())
        return bool(self._active_tag_filters.intersection(tags))

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
        self._tag_filter_menu_button = None
        self._tag_filter_clear_button = None

        title = QLabel("标签过滤:")
        title.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(title)

        if not self._available_tags:
            empty = QLabel("tags.cfg 未配置")
            empty.setStyleSheet("color: #777; font-size: 11px;")
            layout.addWidget(empty)
            layout.addStretch()
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
        btn.setToolTip(f"只显示包含 TAG「{tag}」的照片")
        btn.setCheckable(True)
        btn.setChecked(tag in self._active_tag_filters)
        btn.setAutoRaise(False)
        btn.setStyleSheet(_TAG_FILTER_BUTTON_STYLE)
        btn.clicked.connect(lambda checked=False, t=tag: self._on_tag_filter_toggled(t, bool(checked)))
        return btn

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
        self._available_tags = new_tags
        self._active_tag_filters.intersection_update(new_tags)
        self._rebuild_tag_filter_bar()
        return True

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
        tag_menu.setEnabled(bool(norm_paths))
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
        act_clear.setEnabled(any(tag_sets))
        act_clear.triggered.connect(lambda checked=False, p=list(norm_paths): self._clear_tags_for_paths(p))

    def _set_tag_for_paths(self, paths: list[str], tag: str, enabled: bool) -> None:
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
