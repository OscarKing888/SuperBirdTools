from __future__ import annotations

from typing import Iterable, Sequence

from .image_proc_context import ImageProcContext
from .image_proc_stage.image_proc_stage import ImageProcStage
from .image_proc_stage_descriptor import ImageProcStageDescriptor


class ImageProcPipeline:
    def __init__(self, stages: Iterable[ImageProcStage]) -> None:
        self._stages = tuple(stages)

    @property
    def stages(self) -> tuple[ImageProcStage, ...]:
        return self._stages

    def ui_descriptors(self) -> tuple[ImageProcStageDescriptor, ...]:
        return tuple(stage.ui_descriptor() for stage in self._stages)

    def process(self, context: ImageProcContext) -> ImageProcContext:
        current = context
        for stage in self._stages:
            if not stage.is_enabled(current.settings):
                continue
            current = stage.process(current)
        return current

    def process_batch(self, contexts: Sequence[ImageProcContext]) -> list[ImageProcContext]:
        current = list(contexts)
        for stage in self._stages:
            current = stage.process_batch(current)
        return current
