from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .subject_local_tracker import LocalObservation

from .normalized_box import NormalizedBox


def image_file_signature(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None


@dataclass(frozen=True, slots=True)
class RegionTrackingResult:
    """按原选区顺序保存每个匹配框；None 表示该区失配，不压缩编号。"""

    boxes: tuple[NormalizedBox | None, ...]
    signature: tuple[str, int, int] | None = None
    error: str = ""
    scores: tuple[float, ...] = ()
    reasons: tuple[str, ...] = ()
    predicted_boxes: tuple[NormalizedBox, ...] = ()
    manual_indices: tuple[int, ...] = ()

    observation: LocalObservation | None = None

    @property
    def matched_count(self) -> int:
        return sum(box is not None for box in self.boxes)
