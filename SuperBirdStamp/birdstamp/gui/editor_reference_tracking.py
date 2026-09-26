from __future__ import annotations

from pathlib import Path

from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from .editor_reference_tracking_worker import EditorReferenceTrackingWorker
from .editor_utils import path_key


class _BirdStampReferenceTrackingMixin:
    """参考选区跟踪的状态与线程归属；不把工作线程结果写回参考选区。"""

    def _init_reference_tracking(self) -> None:
        self._reference_tracking_worker = None
        self._reference_tracking_token = 0
        self._reference_tracking_shutdown = False
        self._reference_tracking_results = {}
        self._reference_tracking_definition = None
        self._reference_tracking_signature = None
        self._reference_tracking_message = "框选参考区后点击预处理，可切图查看跟踪结果。"

    def _reference_tracking_input(self):
        source = getattr(self, "_dejitter_reference_source", None)
        regions = tuple(getattr(self, "_dejitter_reference_regions", ()))
        return (path_key(Path(source)), regions) if source and regions else None

    def _reference_regions_editable(self) -> bool:
        source = getattr(self, "_dejitter_reference_source", None)
        current = getattr(self, "current_path", None)
        return not source or (current is not None and path_key(Path(source)) == path_key(current))

    def _tracking_result_for_current(self):
        current = getattr(self, "current_path", None)
        definition = self._reference_tracking_input()
        if current is None or definition is None or definition != self._reference_tracking_definition:
            return None
        if image_file_signature(Path(self._dejitter_reference_source)) != self._reference_tracking_signature:
            return None
        result = self._reference_tracking_results.get(path_key(current))
        if result is None or result.signature != image_file_signature(current):
            return None
        return result

    def _invalidate_reference_tracking(self, message="参考区或照片列表已变化，请重新预处理。", *, shutdown=False) -> None:
        had_results = bool(self._reference_tracking_results)
        self._reference_tracking_token += 1
        self._reference_tracking_shutdown = self._reference_tracking_shutdown or shutdown
        self._reference_tracking_results.clear()
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
        self.dejitter_edit_reference_btn.setEnabled(bool(source) and not self._reference_regions_editable())
        message = self._reference_tracking_message
        if source and not self._reference_regions_editable():
            result = self._tracking_result_for_current()
            if result is not None:
                detail = result.error or ("未匹配区域不绘制" if result.matched_count < len(result.boxes) else "只读预览")
                message += f"\n当前图：{result.matched_count}/{len(result.boxes)} 个区域 · {detail}"
            else:
                message += "\n当前图暂无有效跟踪结果，请预处理。"
        self.dejitter_tracking_status.setText(message)

    def _on_reference_preprocess_clicked(self) -> None:
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
            regions=definition[1], paths=paths, parent=self,
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
                and self._reference_tracking_input() == (path_key(worker.reference), worker.regions))

    def _on_reference_tracking_results(self, token: int, results: object) -> None:
        if not self._accept_reference_tracking_signal(token):
            return
        worker = self._reference_tracking_worker
        if image_file_signature(worker.reference) != worker.reference_signature:
            self._invalidate_reference_tracking("参考照片已变化，请重新预处理。")
            return
        self._reference_tracking_results = dict(results)
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
