from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence

from .image_proc_context import ImageProcContext
from .image_proc_option_spec import ImageProcOptionSpec
from .image_proc_stage_descriptor import ImageProcStageDescriptor


class ImageProcStage(ABC):
    stage_id = "stage"
    label = "Process Stage"
    description = ""
    enabled_option_key: str | None = None
    enabled_by_default = True
    is_export_stage = False

    def ui_descriptor(self) -> ImageProcStageDescriptor:
        enabled_option = None
        if self.enabled_option_key:
            enabled_option = ImageProcOptionSpec(
                key=self.enabled_option_key,
                label="执行",
                value_type="bool",
                default=self.enabled_by_default,
                description=f"是否执行 {self.label}。",
            )
        return ImageProcStageDescriptor(
            stage_id=self.stage_id,
            label=self.label,
            description=self.description,
            enabled_by_default=self.enabled_by_default,
            enabled_option=enabled_option,
            parameter_options=self.parameter_options(),
        )

    def parameter_options(self) -> tuple[ImageProcOptionSpec, ...]:
        return ()

    def is_enabled(self, settings: Mapping[str, Any]) -> bool:
        if not self.enabled_option_key:
            return self.enabled_by_default
        raw = settings.get(self.enabled_option_key, self.enabled_by_default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}

    @abstractmethod
    def process(self, context: ImageProcContext) -> ImageProcContext:
        raise NotImplementedError

    def process_batch(self, contexts: Sequence[ImageProcContext]) -> list[ImageProcContext]:
        return [
            self.process(context) if self.is_enabled(context.settings) else context
            for context in contexts
        ]
