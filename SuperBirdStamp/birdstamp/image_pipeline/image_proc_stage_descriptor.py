from __future__ import annotations

from dataclasses import dataclass

from .image_proc_option_spec import ImageProcOptionSpec


@dataclass(frozen=True, slots=True)
class ImageProcStageDescriptor:
    stage_id: str
    label: str
    description: str = ""
    enabled_by_default: bool = True
    enabled_option: ImageProcOptionSpec | None = None
    parameter_options: tuple[ImageProcOptionSpec, ...] = ()
