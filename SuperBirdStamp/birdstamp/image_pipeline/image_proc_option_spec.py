from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .image_proc_option_choice import ImageProcOptionChoice


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
