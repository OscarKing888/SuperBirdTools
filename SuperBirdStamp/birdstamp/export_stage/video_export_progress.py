from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

@dataclass(slots=True)
class VideoExportProgress:
    """导出进度通知。"""

    phase: str
    current: int
    total: int
    message: str

VideoExportProgressCallback = Callable[[VideoExportProgress], None]
