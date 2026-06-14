from __future__ import annotations

from pathlib import Path

class VideoExportCancelledError(RuntimeError):
    """视频导出已被用户中断。"""

    def __init__(
        self,
        message: str,
        *,
        preserved_frames_dir: Path | None = None,
        partial_output_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.preserved_frames_dir = preserved_frames_dir
        self.partial_output_path = partial_output_path

