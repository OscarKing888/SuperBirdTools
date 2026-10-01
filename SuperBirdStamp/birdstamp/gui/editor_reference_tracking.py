from __future__ import annotations

import json
from pathlib import Path

from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature
from birdstamp.image_dejitter.manual_region_matches import (
    valid_manual_boxes, manual_match_record, apply_manual_boxes,
    editable_match_boxes, normalize_match_box, without_manual_boxes,
)
from birdstamp.image_dejitter.matching_options import MatchingOptions
from birdstamp.image_dejitter.recognition import SubjectSettings
from .editor_reference_tracking_worker import EditorReferenceTrackingWorker
from .editor_utils import path_key


class _BirdStampReferenceTrackingMixin:
    """参考选区跟踪的状态与线程归属；不把工作线程结果写回参考选区。"""

    def _init_reference_tracking(self) -> None:
        self._reference_tracking_worker = None
        self._reference_tracking_token = 0
        self._reference_tracking_shutdown = False
        self._reference_tracking_results = {}
        self._reference_tracking_manual_matches = {}
        self._reference_tracking_definition = None
        self._reference_tracking_signature = None
        self._dejitter_manual_matches = {}
        self._reference_tracking_message = "框选参考区后点击预处理，可切图查看跟踪结果。"

    def _reference_tracking_input(self):
        source = getattr(self, "_dejitter_reference_source", None)
        regions = tuple(getattr(self, "_dejitter_reference_regions", ()))
        controls = getattr(self, 'dejitter_matching_controls', None)
        options = MatchingOptions.from_settings(controls.settings() if controls else None)
        method = (SubjectSettings.from_settings(self.dejitter_subject_controls.settings()).method
                  if hasattr(self,"dejitter_subject_controls") else "reference_region")
        panel = getattr(self, "dejitter_recommendation", None)
        # 目标鸟/局部裁片元数据也是跟踪器定义；与导出管线使用同一份，冻结成可比较的字符串。
        recommendation = (json.dumps(panel.settings(), sort_keys=True, ensure_ascii=False)
                          if method == "subject_local" and panel is not None else "")
        return (path_key(Path(source)), regions, options, method, recommendation) if source and regions else None

    def _reference_regions_editable(self) -> bool:
        return self._is_reference_photo(getattr(self, 'current_path', None))

    def _is_reference_photo(self, path):
        source = getattr(self, "_dejitter_reference_source", None)
        return not source or (path is not None and path_key(Path(source)) == path_key(path))

    def _region_edit_enabled(self, path, *, original=None):
        from .edit_modes import EDIT_MODE_REFERENCE_REGION
        original = not self._sequence_result_mode() if original is None else original
        return (path is not None and self._dejitter_tab_active() and original
                and self._current_edit_mode_id() == EDIT_MODE_REFERENCE_REGION
                and not self._sequence_fast_preview_active() and not self._sequence_shutdown)

    def _manual_boxes_for_path(self, path):
        record = self._dejitter_manual_matches.get(path_key(path)) if path is not None else None
        return valid_manual_boxes(record, path, self._dejitter_reference_source, self._dejitter_reference_regions)

    def _manual_record_for_path(self, path):
        return self._dejitter_manual_matches.get(path_key(path)) if any(self._manual_boxes_for_path(path)) else None

    def _tracking_result_for_current(self):
        return self._tracking_result_for_path(getattr(self, 'current_path', None))

    def _tracking_result_for_path(self, current):
        definition = self._reference_tracking_input()
        if current is None or definition is None:
            return None
        result = None
        if (definition == self._reference_tracking_definition
                and image_file_signature(Path(self._dejitter_reference_source)) == self._reference_tracking_signature):
            result = self._reference_tracking_results.get(path_key(current))
            if result is not None and result.signature != image_file_signature(current):
                result = None
        # 高级方法的后续观测依赖人工关键帧；更改/撤销后不能继续显示旧链为成功。
        if (result is not None and definition[3] == 'subject_local'
                and self._reference_tracking_manual_matches != self._dejitter_manual_matches):
            result = RegionTrackingResult((None,) * len(definition[1]), signature=result.signature,
                error='关键帧已变化，请重新分析', predicted_boxes=editable_match_boxes(definition[1],result))
        record = self._manual_record_for_path(current)
        if result is not None and record and record == self._reference_tracking_manual_matches.get(path_key(current)):
            return result  # 保留本次分析的冲突/失败诊断，不能把手动位置重新标成成功。
        return apply_manual_boxes(without_manual_boxes(result), self._manual_boxes_for_path(current),
                                  signature=image_file_signature(current))

    def _tracking_diagnostics_for_path(self, path):
        return self._tracking_result_for_path(path) or RegionTrackingResult((None,) * len(self._dejitter_reference_regions))

    def _editable_regions_for_path(self, path):
        if self._is_reference_photo(path):
            return self._dejitter_reference_regions
        boxes = editable_match_boxes(self._dejitter_reference_regions, self._tracking_result_for_path(path))
        manual = self._manual_boxes_for_path(path)
        return tuple((manual[i] if i < len(manual) and manual[i] is not None else box) for i, box in enumerate(boxes))

    def _region_texture_hint(self) -> str:
        """高级方法：框选后即时说明各选区是二维纹理还是只约束单一方向的边缘；最终以分析为准。"""
        regions = tuple(getattr(self, "_dejitter_reference_regions", ()))
        image = getattr(self, "current_source_image", None)
        path = getattr(self, "current_path", None)
        definition = self._reference_tracking_input()
        if not regions or image is None or definition is None or definition[3] != "subject_local" \
                or not self._is_reference_photo(path):
            return ""
        key = (path_key(path), regions, image.size)
        cached = getattr(self, "_region_texture_hint_cache", None)
        if cached is None or cached[0] != key:
            from birdstamp.image_dejitter.aperture import classify_regions, texture_summary, aperture_problems
            try:
                textures = classify_regions(image, regions)
            except (ValueError, OSError):
                return ""
            text = "选区纹理：" + texture_summary(textures)
            problems = aperture_problems(textures)
            if problems:
                text += "\n⚠ " + "；".join(problems)
            cached = (key, text)
            self._region_texture_hint_cache = cached
        return cached[1]

    def _commit_source_reference_regions(self, path, regions):
        """显式绑定画布源路径，A 图提交不能借用 B 图的路径或裁切坐标。"""
        if path is None or not self._is_reference_photo(path):
            return
        source_regions = tuple(box for value in regions or () if (box := normalize_match_box(value)) is not None)
        self._dejitter_manual_matches.clear()
        self._invalidate_reference_tracking()
        self._dejitter_reference_regions = source_regions
        if hasattr(self,"dejitter_recommendation"):
            self.dejitter_recommendation.edited_regions(source_regions)
        self._dejitter_reference_source = str(path) if source_regions else None
        self.dejitter_reference_check.setChecked(bool(source_regions))
        self._update_dejitter_reference_clear_enabled()
        self._apply_preview_overlay_options_from_ui()
        self._on_output_settings_changed()
        self._update_reference_tracking_controls()  # 刷新选区纹理提示
        self._schedule_workspace_autosave()

    def _commit_manual_region_match(self, path, index, box, *, original=None):
        if not self._region_edit_enabled(path, original=original) or self._is_reference_photo(path):
            return
        regions = self._dejitter_reference_regions
        if not 0 <= index < len(regions) or (box is not None and normalize_match_box(box) is None):
            return
        boxes = list(self._manual_boxes_for_path(path) or (None,) * len(regions))
        boxes[index] = normalize_match_box(box) if box is not None else None
        key = path_key(path)
        if any(b is not None for b in boxes):
            self._dejitter_manual_matches[key] = manual_match_record(path, self._dejitter_reference_source, regions, boxes)
        else:
            self._dejitter_manual_matches.pop(key, None)
        # 旧分析和旧后台回调失效，已知自动诊断留作其它编号的编辑底图。
        self._reference_tracking_token += 1
        if self._reference_tracking_worker is not None:
            self._reference_tracking_worker.requestInterruption()
        self._invalidate_sequence_preview()
        self._sequence_message = f'{path.name}：选区 {index + 1} 已{"手动修正" if box is not None else "恢复自动匹配"}，请重新分析。'
        self._update_dejitter_controls()
        self._refresh_preview_label(preserve_view=True)
        self._schedule_workspace_autosave()

    def _restore_manual_region_matches(self, records):
        self._dejitter_manual_matches.clear()
        for record in records if isinstance(records, list) else ():
            if not isinstance(record, dict) or not isinstance(record.get('path'), str):
                continue
            path = Path(record['path'])
            boxes = valid_manual_boxes(record, path, self._dejitter_reference_source, self._dejitter_reference_regions)
            if any(boxes):
                self._dejitter_manual_matches[path_key(path)] = manual_match_record(
                    path, self._dejitter_reference_source, self._dejitter_reference_regions, boxes)

    def _invalidate_reference_tracking(self, message="参考区或照片列表已变化，请重新预处理。", *, shutdown=False) -> None:
        self._invalidate_sequence_preview(shutdown=shutdown)
        had_results = bool(self._reference_tracking_results)
        self._reference_tracking_token += 1
        self._reference_tracking_shutdown = self._reference_tracking_shutdown or shutdown
        self._reference_tracking_results.clear()
        self._reference_tracking_manual_matches.clear()
        self._reference_tracking_definition = None
        self._reference_tracking_signature = None
        self._reference_tracking_message = message
        worker = self._reference_tracking_worker
        if worker is not None:
            worker.requestInterruption()
        self._update_reference_tracking_controls()
        if had_results and not shutdown and not self._reference_regions_editable():
            self._refresh_preview_label(preserve_view=True)

    def _update_reference_tracking_controls(self) -> None:
        button = getattr(self, "dejitter_preprocess_btn", None)
        if button is None:
            return
        worker = self._reference_tracking_worker
        stopping = worker is not None and worker.isInterruptionRequested()
        button.setText("正在停止…" if stopping else "停止预处理" if worker is not None else "预处理跟踪")
        button.setEnabled(not self._reference_tracking_shutdown and not stopping
                          and (worker is not None or self._reference_tracking_input() is not None))
        source = getattr(self, "_dejitter_reference_source", None)
        self.dejitter_edit_reference_btn.setEnabled(bool(source))
        message = self._reference_tracking_message
        hint = self._region_texture_hint()
        if hint:
            message += "\n" + hint
        if source and not self._reference_regions_editable():
            result = self._tracking_result_for_current()
            if result is not None:
                detail = result.error or ("未匹配区域只显示预计位置" if result.matched_count < len(result.boxes)
                                          else "可在原图修正匹配位置" if self._dejitter_tab_active() else "只读预览")
                message += f"\n当前图：{result.matched_count}/{len(result.boxes)} 个区域 · {detail}"
            else:
                message += "\n当前图暂无有效跟踪结果，请预处理。"
        self.dejitter_tracking_status.setText(message)
        self._update_dejitter_controls()

    def _on_reference_preprocess_clicked(self) -> None:
        if self._dejitter_tab_active():
            self._on_dejitter_analyze()
            return
        if self._reference_tracking_shutdown:
            return
        if self._reference_tracking_worker is not None:
            self._invalidate_reference_tracking("已取消预处理，可重新执行。")
            self._refresh_preview_label(preserve_view=True)
            return
        definition = self._reference_tracking_input()
        paths = tuple(self._list_photo_paths())
        if definition is None or not paths:
            self._show_error("无法预处理", "请先导入照片并在参考图上框选一个或多个参考区。")
            return
        self._invalidate_reference_tracking("正在预处理跟踪…")
        worker = EditorReferenceTrackingWorker(
            token=self._reference_tracking_token, reference=Path(self._dejitter_reference_source),
            regions=definition[1], paths=paths, options=definition[2], method=definition[3],
            recommendation=definition[4], parent=self,
        )
        self._reference_tracking_worker = worker
        worker.resultsReady.connect(self._on_reference_tracking_results)
        worker.progressChanged.connect(self._on_reference_tracking_progress)
        worker.failed.connect(self._on_reference_tracking_failed)
        worker.finished.connect(self._on_reference_tracking_finished)
        self._update_reference_tracking_controls()
        self._refresh_preview_label(preserve_view=True)
        worker.start()

    def _accept_reference_tracking_signal(self, token: int) -> bool:
        worker = self.sender()
        return (worker is not None and worker is self._reference_tracking_worker
                and not self._reference_tracking_shutdown and token == self._reference_tracking_token
                and not worker.isInterruptionRequested()
                and self._reference_tracking_input() == (path_key(worker.reference), worker.regions, worker.options, worker.method,
                                                         worker.recommendation))

    def _on_reference_tracking_results(self, token: int, results: object) -> None:
        if not self._accept_reference_tracking_signal(token):
            return
        worker = self._reference_tracking_worker
        if image_file_signature(worker.reference) != worker.reference_signature:
            self._invalidate_reference_tracking("参考照片已变化，请重新预处理。")
            return
        self._reference_tracking_results = dict(results)
        self._reference_tracking_manual_matches.clear()
        self._reference_tracking_definition = self._reference_tracking_input()
        self._reference_tracking_signature = worker.reference_signature
        failed = sum(result.matched_count < len(result.boxes) for result in results.values())
        self._reference_tracking_message = f"预处理完成：{len(results)} 张，{failed} 张存在未匹配区域。"
        self._refresh_preview_label(preserve_view=True)
        self._update_reference_tracking_controls()

    def _on_reference_tracking_progress(self, token: int, current: int, total: int) -> None:
        if self._accept_reference_tracking_signal(token):
            self._reference_tracking_message = f"正在预处理跟踪：{current}/{total}"
            self._update_reference_tracking_controls()

    def _on_reference_tracking_failed(self, token: int, message: str) -> None:
        if self._accept_reference_tracking_signal(token):
            self._reference_tracking_message = f"预处理失败：{message}"
            self._update_reference_tracking_controls()

    def _on_reference_tracking_finished(self) -> None:
        worker = self.sender()
        if worker is None or worker is not self._reference_tracking_worker:
            return
        # 业务结果信号不能释放仍在运行的 QThread；直到真实 finished 才允许下一次启动。
        self._reference_tracking_worker = None
        worker.deleteLater()
        if not self._reference_tracking_shutdown:
            self._update_reference_tracking_controls()

    def _on_edit_reference_photo(self) -> None:
        if self._dejitter_tab_active():
            self._set_dejitter_view('edit')
        source = getattr(self, "_dejitter_reference_source", None)
        if not source:
            return
        item = self._find_photo_item_by_path(Path(source))
        if item is None:
            self._show_error("参考照片不在列表中", "请把参考照片重新添加到列表后编辑选区。")
            return
        from .edit_modes import EDIT_MODE_REFERENCE_REGION
        self._set_edit_mode_button_checked(EDIT_MODE_REFERENCE_REGION)
        self.photo_list.setCurrentItem(item)
        self._refresh_preview_label(preserve_view=True)
        self._schedule_workspace_autosave()
