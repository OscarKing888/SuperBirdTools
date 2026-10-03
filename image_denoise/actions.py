"""每张照片一个可取消的 WorkerAction；不持有窗口或控件。"""
from __future__ import annotations

from app_common.file_browser._work_action import WorkerAction

from .types import DenoiseResult


class DenoiseAction(WorkerAction):
    def __init__(self, source, destination, options, *, engine, cancelled=lambda: False,
                 progress=None, processor=None):
        super().__init__(cancelled=cancelled)
        self.source, self.destination = str(source), str(destination)
        self.options, self.engine = options, engine
        self.progress, self.processor = progress, processor

    def execute(self) -> DenoiseResult:
        if self.is_cancelled():
            return DenoiseResult(self.source, self.destination, "cancelled")
        processor = self.processor
        if processor is None:
            from .pipeline import denoise_file

            processor = denoise_file
        return processor(self.source, self.destination, self.options, engine=self.engine,
                         cancelled=self.is_cancelled, progress=self.progress)
