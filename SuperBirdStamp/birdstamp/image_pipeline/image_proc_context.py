from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any, MutableMapping

from PIL import Image

from .image_proc_crop_plan import ImageProcCropPlan


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
