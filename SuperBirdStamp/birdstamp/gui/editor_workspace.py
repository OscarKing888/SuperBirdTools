from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from app_common.log import get_logger
from birdstamp.config import get_config_path
from birdstamp.constants import SUPPORTED_EXTENSIONS
from birdstamp.gui.editor_photo_list import PHOTO_COL_ROW, PHOTO_COL_SEQ, PHOTO_LIST_PATH_ROLE, PHOTO_LIST_SEQUENCE_ROLE
from birdstamp.workspace import (
    WORKSPACE_FILE_EXTENSION,
    WorkspaceFormatError,
    read_workspace_json,
    resolve_workspace_path,
    serialize_workspace_path,
    write_workspace_json,
)

_WORKSPACE_AUTOSAVE_FILE_NAME = f"editor_autosave{WORKSPACE_FILE_EXTENSION}"
_WORKSPACE_AUTOSAVE_INTERVAL_MS = 1200
_workspace_log = get_logger("birdstamp.workspace")


def _block_widget_signals(*widgets: object) -> list[tuple[object, bool]]:
    previous_states: list[tuple[object, bool]] = []
    for widget in widgets:
        if widget is None or not hasattr(widget, "blockSignals"):
            continue
        try:
            previous_states.append((widget, bool(widget.blockSignals(True))))
        except Exception:
            continue
    return previous_states


def _restore_widget_signals(previous_states: Iterable[tuple[object, bool]]) -> None:
    for widget, old_state in reversed(list(previous_states)):
        try:
            widget.blockSignals(old_state)
        except Exception:
            continue


class _BirdStampWorkspaceMixin:
    def _init_workspace_autosave(self) -> None:
        self._workspace_autosave_suspend_depth = 0
        self._workspace_autosave_timer = QTimer(self)
        self._workspace_autosave_timer.setSingleShot(True)
        self._workspace_autosave_timer.setInterval(_WORKSPACE_AUTOSAVE_INTERVAL_MS)
        self._workspace_autosave_timer.timeout.connect(self._autosave_workspace_now)

    def _workspace_autosave_path(self) -> Path:
        return get_config_path().parent / _WORKSPACE_AUTOSAVE_FILE_NAME

    def _workspace_autosave_enabled(self) -> bool:
        return int(getattr(self, "_workspace_autosave_suspend_depth", 0) or 0) <= 0

    @contextmanager
    def _workspace_autosave_suspended(self):
        self._workspace_autosave_suspend_depth = int(getattr(self, "_workspace_autosave_suspend_depth", 0) or 0) + 1
        timer = getattr(self, "_workspace_autosave_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        try:
            yield
        finally:
            depth = int(getattr(self, "_workspace_autosave_suspend_depth", 0) or 0) - 1
            self._workspace_autosave_suspend_depth = max(0, depth)

    def _schedule_workspace_autosave(self) -> None:
        if not self._workspace_autosave_enabled():
            return
        timer = getattr(self, "_workspace_autosave_timer", None)
        if timer is None:
            return
        try:
            timer.start()
        except Exception:
            pass

    def _autosave_workspace_now(self) -> None:
        if not self._workspace_autosave_enabled():
            return
        try:
            workspace_path = self._workspace_autosave_path()
            payload = self._collect_workspace_payload(workspace_path)
            write_workspace_json(workspace_path, payload)
        except Exception as exc:
            _workspace_log.warning("workspace autosave failed: %s", exc)

    def _restore_autosave_workspace_on_startup(self) -> bool:
        workspace_path = self._workspace_autosave_path()
        if not workspace_path.is_file():
            return False
        try:
            payload = read_workspace_json(workspace_path)
        except WorkspaceFormatError as exc:
            _workspace_log.warning("workspace autosave restore skipped: %s", exc)
            return False
        try:
            self._restore_workspace_payload(
                payload,
                workspace_path,
                status_label="已恢复自动保存工作区",
                mark_as_current_workspace=False,
                autosave_after_restore=False,
            )
        except Exception as exc:
            _workspace_log.warning("workspace autosave restore failed: %s", exc)
            return False
        return True

    def _load_workspace_last_dir(self) -> Path | None:
        return self._load_remembered_output_dir("last_workspace_dir")

    def _save_workspace_last_dir(self, directory: Path) -> None:
        self._save_remembered_output_dir("last_workspace_dir", directory)

    def _workspace_dialog_filter(self) -> str:
        return (
            "BirdStamp Workspace (*.birdstamp-workspace.json *.json);;"
            "JSON (*.json);;All Files (*.*)"
        )

    def _normalize_workspace_target_path(self, incoming_path: Path | str) -> Path:
        try:
            target = Path(str(incoming_path)).expanduser().resolve(strict=False)
        except Exception:
            target = Path(str(incoming_path))
        if target.suffix.lower() != ".json":
            target = Path(f"{target}{WORKSPACE_FILE_EXTENSION}")
        return target

    def _suggest_workspace_path(self) -> Path:
        current_workspace = getattr(self, "_workspace_path", None)
        if isinstance(current_workspace, Path):
            return current_workspace

        last_dir = self._load_workspace_last_dir()
        if isinstance(last_dir, Path) and last_dir.is_dir():
            base_dir = last_dir
        else:
            base_dir = None

        candidate_path = self.current_path
        if candidate_path is None or self._is_placeholder_active():
            paths = self._list_photo_paths()
            candidate_path = paths[0] if paths else None

        if candidate_path is not None:
            base_dir = candidate_path.parent
            stem = candidate_path.stem or "workspace"
            return base_dir / f"{stem}{WORKSPACE_FILE_EXTENSION}"

        if base_dir is None:
            base_dir = Path.cwd()
        return base_dir / f"superbirdstamp{WORKSPACE_FILE_EXTENSION}"

    def _has_workspace_session_content(self) -> bool:
        return bool(self.photo_list.topLevelItemCount() or self._report_db_entries)

    def _confirm_replace_workspace_session(self) -> bool:
        if not self._has_workspace_session_content():
            return True
        answer = QMessageBox.question(
            self,
            "加载工作区",
            "加载工作区会清空当前照片列表、report.db 列表和当前编辑设置，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _current_workspace_selected_photo_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self.photo_list.selectedItems():
            raw = item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
            if isinstance(raw, str):
                paths.append(Path(raw))
        if not paths and self.current_path is not None and not self._is_placeholder_active():
            item = self._find_photo_item_by_path(self.current_path)
            if item is not None:
                paths.append(self.current_path)
        return paths

    def _collect_workspace_image_export_state(self, workspace_path: Path) -> dict[str, Any]:
        gif_request = self.gif_export_panel.current_request()
        return {
            "output_format": self._selected_output_suffix(),
            "gif_fps": gif_request.fps,
            "gif_loop": gif_request.loop,
            "gif_keep_frame_images": gif_request.keep_frame_images,
            "gif_scale_factors": list(gif_request.scale_factors),
            "last_image_output_dir": serialize_workspace_path(
                self._image_export_last_output_dir,
                workspace_path=workspace_path,
            ),
            "last_batch_output_dir": serialize_workspace_path(
                self._batch_export_last_output_dir,
                workspace_path=workspace_path,
            ),
        }

    def _apply_workspace_image_export_state(self, state: dict[str, Any], workspace_path: Path) -> None:
        if not isinstance(state, dict):
            return
        widgets_state = _block_widget_signals(
            self.output_format_combo,
            self.gif_export_panel.fps_combo,
            self.gif_export_panel.loop_spin,
            self.gif_export_panel.keep_frames_check,
            *[check for _scale, check in getattr(self.gif_export_panel, "_scale_checks", [])],
        )
        try:
            output_format = str(state.get("output_format") or "").strip().lower()
            if output_format:
                format_index = self.output_format_combo.findData(output_format)
                if format_index >= 0:
                    self.output_format_combo.setCurrentIndex(format_index)
            self.gif_export_panel.set_state(
                fps=state.get("gif_fps"),
                loop=state.get("gif_loop"),
                keep_frame_images=state.get("gif_keep_frame_images"),
                scale_factors=state.get("gif_scale_factors"),
            )
        finally:
            _restore_widget_signals(widgets_state)

        self._image_export_last_output_dir = resolve_workspace_path(
            state.get("last_image_output_dir"),
            workspace_path=workspace_path,
        )
        self._batch_export_last_output_dir = resolve_workspace_path(
            state.get("last_batch_output_dir"),
            workspace_path=workspace_path,
        )
        self._save_image_export_preferences()
        self._refresh_image_export_action_states()

    def _collect_workspace_video_export_state(self, workspace_path: Path) -> dict[str, Any]:
        state = self.video_export_panel.current_state()
        state["last_video_output_dir"] = serialize_workspace_path(
            self._video_export_last_output_dir,
            workspace_path=workspace_path,
        )
        return state

    def _apply_workspace_video_export_state(self, state: dict[str, Any], workspace_path: Path) -> None:
        if not isinstance(state, dict):
            return
        self.video_export_panel.set_state(state)
        self._video_export_last_output_dir = resolve_workspace_path(
            state.get("last_video_output_dir"),
            workspace_path=workspace_path,
        )

    def _collect_workspace_preview_state(self) -> dict[str, Any]:
        return {
            "show_crop_effect": bool(self.show_crop_effect_check.isChecked()),
            "crop_edit_mode": bool(self.crop_edit_mode_check.isChecked()),
            "crop_effect_alpha": int(self.crop_effect_alpha_slider.value()),
            "show_focus_box": bool(self.show_focus_box_check.isChecked()),
            "show_bird_box": bool(self.show_bird_box_check.isChecked()),
            "composition_grid_mode": self.preview_grid_combo.currentData(),
            "composition_grid_line_width": self.preview_grid_line_width_combo.currentData(),
            "preview_scale_percent": self.preview_label.current_display_scale_percent(),
        }

    def _apply_workspace_preview_state(self, state: dict[str, Any]) -> float | None:
        if not isinstance(state, dict):
            return None
        widgets_state = _block_widget_signals(
            self.show_crop_effect_check,
            self.crop_edit_mode_check,
            self.crop_effect_alpha_slider,
            self.show_focus_box_check,
            self.show_bird_box_check,
            self.preview_grid_combo,
            self.preview_grid_line_width_combo,
        )
        try:
            self.show_crop_effect_check.setChecked(bool(state.get("show_crop_effect", True)))
            self.crop_edit_mode_check.setChecked(bool(state.get("crop_edit_mode", False)))
            try:
                alpha = int(state.get("crop_effect_alpha", self.crop_effect_alpha_slider.value()))
            except Exception:
                alpha = int(self.crop_effect_alpha_slider.value())
            self.crop_effect_alpha_slider.setValue(
                max(self.crop_effect_alpha_slider.minimum(), min(self.crop_effect_alpha_slider.maximum(), alpha))
            )
            self.show_focus_box_check.setChecked(bool(state.get("show_focus_box", True)))
            self.show_bird_box_check.setChecked(bool(state.get("show_bird_box", True)))

            grid_mode = state.get("composition_grid_mode")
            grid_index = self.preview_grid_combo.findData(grid_mode)
            if grid_index >= 0:
                self.preview_grid_combo.setCurrentIndex(grid_index)

            grid_line_width = state.get("composition_grid_line_width")
            line_width_index = self.preview_grid_line_width_combo.findData(grid_line_width)
            if line_width_index >= 0:
                self.preview_grid_line_width_combo.setCurrentIndex(line_width_index)
        finally:
            _restore_widget_signals(widgets_state)

        self.crop_effect_alpha_value_label.setText(str(int(self.crop_effect_alpha_slider.value())))
        self._apply_preview_overlay_options_from_ui()
        preview_scale = state.get("preview_scale_percent")
        try:
            scale_percent = float(preview_scale)
        except Exception:
            return None
        return scale_percent if scale_percent > 0 else None

    def _collect_workspace_payload(self, workspace_path: Path) -> dict[str, Any]:
        photo_entries: list[dict[str, Any]] = []
        for idx in range(self.photo_list.topLevelItemCount()):
            item = self.photo_list.topLevelItem(idx)
            if item is None:
                continue
            raw_path = item.data(PHOTO_COL_ROW, PHOTO_LIST_PATH_ROLE)
            if not isinstance(raw_path, str):
                continue
            path = Path(raw_path)
            try:
                sequence = int(item.data(PHOTO_COL_ROW, PHOTO_LIST_SEQUENCE_ROLE))
            except Exception:
                sequence = None
            photo_entries.append(
                {
                    "path": serialize_workspace_path(path, workspace_path=workspace_path),
                    "sequence": sequence,
                    "render_settings": self._photo_override_settings_from_snapshot(
                        self._render_settings_for_path(path, prefer_current_ui=False)
                    ),
                }
            )

        header = self.photo_list.header()
        try:
            sort_column = int(header.sortIndicatorSection())
        except Exception:
            sort_column = PHOTO_COL_SEQ
        try:
            sort_order = header.sortIndicatorOrder()
        except Exception:
            sort_order = Qt.SortOrder.AscendingOrder

        current_photo = None
        if self.current_path is not None and not self._is_placeholder_active():
            if self._find_photo_item_by_path(self.current_path) is not None:
                current_photo = self.current_path

        return {
            "report_databases": [
                serialize_workspace_path(path, workspace_path=workspace_path)
                for path in self._report_db_entries
            ],
            "photos": photo_entries,
            "selection": {
                "current_photo": serialize_workspace_path(current_photo, workspace_path=workspace_path),
                "selected_photos": [
                    serialize_workspace_path(path, workspace_path=workspace_path)
                    for path in self._current_workspace_selected_photo_paths()
                ],
                "sort_column": sort_column,
                "sort_order": "desc" if sort_order == Qt.SortOrder.DescendingOrder else "asc",
            },
            "editor_state": {
                "current_render_settings": self._build_current_render_settings(),
                "global_export_settings": self._current_global_export_settings(),
                "image_export": self._collect_workspace_image_export_state(workspace_path),
                "video_export": self._collect_workspace_video_export_state(workspace_path),
                "preview": self._collect_workspace_preview_state(),
            },
        }

    def _apply_workspace_global_export_state(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        widgets_state = _block_widget_signals(
            self.draw_banner_check,
            self.draw_text_check,
            self.draw_focus_check,
        )
        try:
            self.draw_banner_check.setChecked(bool(state.get("draw_banner", True)))
            self.draw_text_check.setChecked(bool(state.get("draw_text", True)))
            self.draw_focus_check.setChecked(bool(state.get("draw_focus", False)))
        finally:
            _restore_widget_signals(widgets_state)
        self._last_global_export_settings = self._current_global_export_settings()

    def _apply_workspace_photo_list_sort(self, selection_state: dict[str, Any]) -> None:
        if not isinstance(selection_state, dict):
            return
        try:
            column = int(selection_state.get("sort_column", PHOTO_COL_SEQ))
        except Exception:
            column = PHOTO_COL_SEQ
        try:
            column_count = int(self.photo_list.header().count())
        except Exception:
            column_count = PHOTO_COL_SEQ + 1
        if column < 0 or column >= column_count:
            column = PHOTO_COL_SEQ
        sort_order_text = str(selection_state.get("sort_order") or "asc").strip().lower()
        sort_order = (
            Qt.SortOrder.DescendingOrder
            if sort_order_text == "desc"
            else Qt.SortOrder.AscendingOrder
        )
        try:
            self.photo_list.header().setSortIndicator(column, sort_order)
        except Exception:
            pass
        self.photo_list.resort()
        self.photo_list.refresh_row_numbers()

    def _restore_workspace_payload(
        self,
        payload: dict[str, Any],
        workspace_path: Path,
        *,
        status_label: str = "工作区已加载",
        mark_as_current_workspace: bool = True,
        autosave_after_restore: bool = True,
    ) -> None:
        if self._video_export_worker is not None and self._video_export_worker.isRunning():
            self._show_error("视频导出进行中", "请先中断当前视频导出，再加载工作区。")
            return

        with self._workspace_autosave_suspended():
            self._reload_template_combo(preferred=str(self.template_combo.currentText() or "default"))
            self._clear_report_dbs_state(status_message=None)
            self._clear_photos_state(status_message=None)

            editor_state = payload.get("editor_state")
            if not isinstance(editor_state, dict):
                editor_state = {}
            current_render_settings = self._normalize_render_settings(
                editor_state.get("current_render_settings"),
                fallback=self._build_current_render_settings(),
            )
            global_export_state = editor_state.get("global_export_settings")
            image_export_state = editor_state.get("image_export")
            video_export_state = editor_state.get("video_export")
            preview_state = editor_state.get("preview")
            selection_state = payload.get("selection")
            if not isinstance(selection_state, dict):
                selection_state = {}

            report_db_entries = payload.get("report_databases")
            if not isinstance(report_db_entries, list):
                report_db_entries = []
            resolved_report_dbs: list[Path] = []
            missing_report_dbs: list[str] = []
            for entry in report_db_entries:
                resolved = resolve_workspace_path(entry, workspace_path=workspace_path)
                if resolved is None or not resolved.is_file():
                    if resolved is not None:
                        missing_report_dbs.append(str(resolved))
                    continue
                resolved_report_dbs.append(resolved)
            if resolved_report_dbs:
                self._add_report_db_paths(resolved_report_dbs)

            photo_entries = payload.get("photos")
            if not isinstance(photo_entries, list):
                photo_entries = []

            existing_keys: set[str] = set()
            added_paths: list[Path] = []
            missing_photos: list[str] = []
            missing_template_names: set[str] = set()

            self.photo_list.setSortingEnabled(False)
            try:
                for entry in photo_entries:
                    if not isinstance(entry, dict):
                        continue
                    path = resolve_workspace_path(entry.get("path"), workspace_path=workspace_path)
                    if path is None or not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                        if path is not None:
                            missing_photos.append(str(path))
                        continue
                    normalized_settings = self._photo_override_settings_from_snapshot(
                        self._normalize_render_settings(
                            entry.get("render_settings"),
                            fallback=current_render_settings,
                        )
                    )
                    template_name = str(normalized_settings.get("template_name") or "").strip()
                    if template_name and template_name not in self.template_paths:
                        missing_template_names.add(template_name)

                    try:
                        sequence = int(entry.get("sequence"))
                    except Exception:
                        sequence = None
                    added, _item = self._append_photo_path_to_list(
                        path,
                        existing_keys=existing_keys,
                        default_settings=normalized_settings,
                        sequence_value=sequence,
                    )
                    if added:
                        added_paths.append(path)
            finally:
                self.photo_list.setSortingEnabled(True)

            self._next_photo_sequence_number = 0
            self._apply_workspace_photo_list_sort(selection_state)
            if added_paths:
                self._restart_photo_list_metadata_loader()

            self._apply_workspace_global_export_state(global_export_state if isinstance(global_export_state, dict) else {})
            self._apply_workspace_image_export_state(image_export_state if isinstance(image_export_state, dict) else {}, workspace_path)
            self._apply_workspace_video_export_state(video_export_state if isinstance(video_export_state, dict) else {}, workspace_path)
            preview_scale_percent = self._apply_workspace_preview_state(
                preview_state if isinstance(preview_state, dict) else {}
            )

            current_photo = resolve_workspace_path(selection_state.get("current_photo"), workspace_path=workspace_path)
            selected_photos_raw = selection_state.get("selected_photos")
            selected_photos: list[Path] = []
            if isinstance(selected_photos_raw, list):
                for entry in selected_photos_raw:
                    resolved = resolve_workspace_path(entry, workspace_path=workspace_path)
                    if resolved is not None:
                        selected_photos.append(resolved)

            current_item = self._find_photo_item_by_path(current_photo) if current_photo is not None else None
            if current_item is None and selected_photos:
                for path in selected_photos:
                    current_item = self._find_photo_item_by_path(path)
                    if current_item is not None:
                        break
            if current_item is None and self.photo_list.topLevelItemCount() > 0:
                current_item = self.photo_list.topLevelItem(0)

            if current_item is not None:
                self.photo_list.setCurrentItem(current_item)
                for idx in range(self.photo_list.topLevelItemCount()):
                    item = self.photo_list.topLevelItem(idx)
                    if item is not None:
                        item.setSelected(False)
                current_item.setSelected(True)
                for path in selected_photos:
                    item = self._find_photo_item_by_path(path)
                    if item is not None:
                        item.setSelected(True)
            else:
                self._apply_render_settings_to_ui(current_render_settings)
                self.render_preview()

            if preview_scale_percent is not None:
                self.preview_label.set_display_scale_percent(preview_scale_percent, preserve_view=True)
                self._sync_preview_scale_combo(self.preview_label.current_display_scale_percent())

            if mark_as_current_workspace:
                self._workspace_path = workspace_path
                self._save_workspace_last_dir(workspace_path.parent)
            else:
                self._workspace_path = None

            loaded_photo_count = len(added_paths)
            loaded_report_db_count = len(self._report_db_entries)
            status = f"{status_label}：{loaded_photo_count} 张照片，{loaded_report_db_count} 个 report.db。"
            if missing_photos or missing_report_dbs:
                status = (
                    f"{status} 跳过 {len(missing_photos)} 个缺失照片、"
                    f"{len(missing_report_dbs)} 个缺失 report.db。"
                )
            self._set_status(status)

            warning_lines: list[str] = []
            if missing_photos:
                preview = "\n".join(missing_photos[:6])
                extra = "" if len(missing_photos) <= 6 else f"\n……另有 {len(missing_photos) - 6} 个文件"
                warning_lines.append(f"以下照片未找到，已跳过：\n{preview}{extra}")
            if missing_report_dbs:
                preview = "\n".join(missing_report_dbs[:6])
                extra = "" if len(missing_report_dbs) <= 6 else f"\n……另有 {len(missing_report_dbs) - 6} 个文件"
                warning_lines.append(f"以下 report.db 未找到，已跳过：\n{preview}{extra}")
            if missing_template_names:
                warning_lines.append(
                    "以下模板文件当前不在模板库中，已使用工作区内嵌模板快照恢复：\n"
                    + "\n".join(sorted(missing_template_names))
                )
            if warning_lines:
                QMessageBox.warning(self, "工作区加载完成", "\n\n".join(warning_lines))

        if autosave_after_restore:
            self._schedule_workspace_autosave()

    def save_workspace(self) -> None:
        workspace_path = getattr(self, "_workspace_path", None)
        if not isinstance(workspace_path, Path):
            self.save_workspace_as()
            return
        self._save_workspace_to_path(workspace_path)

    def save_workspace_as(self) -> None:
        suggested_path = self._suggest_workspace_path()
        file_path, _selected = QFileDialog.getSaveFileName(
            self,
            "保存工作区",
            str(suggested_path),
            self._workspace_dialog_filter(),
        )
        if not file_path:
            return
        self._save_workspace_to_path(self._normalize_workspace_target_path(file_path))

    def _save_workspace_to_path(self, workspace_path: Path) -> None:
        try:
            payload = self._collect_workspace_payload(workspace_path)
            written_path = write_workspace_json(workspace_path, payload)
        except WorkspaceFormatError as exc:
            self._show_error("保存工作区失败", str(exc))
            return
        self._workspace_path = written_path
        self._save_workspace_last_dir(written_path.parent)
        self._set_status(f"工作区已保存：{written_path}")

    def load_workspace(self) -> None:
        if not self._confirm_replace_workspace_session():
            return

        suggested_path = self._suggest_workspace_path()
        file_path, _selected = QFileDialog.getOpenFileName(
            self,
            "加载工作区",
            str(suggested_path.parent),
            self._workspace_dialog_filter(),
        )
        if not file_path:
            return

        workspace_path = self._normalize_workspace_target_path(file_path)
        try:
            payload = read_workspace_json(workspace_path)
        except WorkspaceFormatError as exc:
            self._show_error("加载工作区失败", str(exc))
            return
        self._restore_workspace_payload(payload, workspace_path)
