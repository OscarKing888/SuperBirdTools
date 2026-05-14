from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from PIL import Image


ImageProcCropPlan = tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]


@dataclass(frozen=True, slots=True)
class ImageProcOptionChoice:
    label: str
    value: Any


@dataclass(frozen=True, slots=True)
class ImageProcOptionSpec:
    key: str
    label: str
    value_type: str
    default: Any = None
    description: str = ""
    choices: tuple[ImageProcOptionChoice, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


@dataclass(frozen=True, slots=True)
class ImageProcStageDescriptor:
    stage_id: str
    label: str
    description: str = ""
    enabled_by_default: bool = True
    enabled_option: ImageProcOptionSpec | None = None
    parameter_options: tuple[ImageProcOptionSpec, ...] = ()


@dataclass(slots=True)
class ImageProcContext:
    image: Image.Image
    settings: dict[str, Any]
    source_path: Path | None = None
    source_paths: tuple[Path, ...] = ()
    index: int = 0
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    metadata_context: dict[str, str] = field(default_factory=dict)
    photo_info: Any | None = None
    template_paths: dict[str, Path] = field(default_factory=dict)
    precomputed: dict[str, Any] = field(default_factory=dict)
    crop_plan: ImageProcCropPlan | None = None
    crop_box: tuple[float, float, float, float] | None = None
    outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0)
    source_size: tuple[int, int] | None = None
    bird_box_cache: MutableMapping[str, tuple[float, float, float, float] | None] | None = None
    bird_box_lock: threading.Lock | None = None

    def __post_init__(self) -> None:
        if self.source_size is None:
            self.source_size = (int(self.image.width), int(self.image.height))
        if not self.source_paths and self.source_path is not None:
            self.source_paths = (self.source_path,)


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


class ImageProcExportStage(ImageProcStage):
    """Marker base for terminal export stages.

    Export stages describe the final output target. The editor owns file dialogs
    and long-running export workers, so the default ``process`` is intentionally
    a no-op for the image context.
    """

    is_export_stage = True
    export_kind = "export"

    def process(self, context: ImageProcContext) -> ImageProcContext:
        return context


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


__all__ = [
    "ImageProcContext",
    "ImageProcCropPlan",
    "ImageProcExportStage",
    "ImageProcOptionChoice",
    "ImageProcOptionSpec",
    "ImageProcPipeline",
    "ImageProcStage",
    "ImageProcStageDescriptor",
]
