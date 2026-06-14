from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from birdstamp.gui import template_context as _template_context

@dataclass(slots=True)
class VideoFrameJob:
    """单帧渲染所需的最小快照。"""

    path: Path
    settings: dict[str, Any]
    raw_metadata: dict[str, Any]
    metadata_context: dict[str, str]
    photo_info: _template_context.PhotoInfo | None = None
    source_image: Image.Image | None = None
    crop_plan: tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]] | None = None
    source_paths: tuple[Path, ...] = ()


