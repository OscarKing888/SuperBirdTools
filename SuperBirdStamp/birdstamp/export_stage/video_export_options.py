from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import DEFAULT_VIDEO_BACKGROUND_COLOR, DEFAULT_VIDEO_RENDER_WORKERS

VIDEO_UNCOMPRESSED_CONTAINER = "raw"
VIDEO_CODEC_RAWVIDEO = "rawvideo"


def video_container_file_suffix(container: str) -> str:
    normalized = str(container or "mp4").strip().lower().lstrip(".")
    if normalized == VIDEO_UNCOMPRESSED_CONTAINER:
        return "avi"
    return normalized or "mp4"


def is_uncompressed_video_container(container: str) -> bool:
    return str(container or "").strip().lower().lstrip(".") == VIDEO_UNCOMPRESSED_CONTAINER


@dataclass(slots=True)
class VideoExportOptions:
    """视频编码参数。"""

    output_path: Path
    container: str = "mp4"
    codec: str = "h264"
    fps: float = 25.0
    preset: str = "medium"
    crf: int = 20
    frame_size_mode: str = "auto"
    frame_width: int = 0
    frame_height: int = 0
    background_color: str = DEFAULT_VIDEO_BACKGROUND_COLOR
    render_workers: int = DEFAULT_VIDEO_RENDER_WORKERS
    overwrite: bool = True
    preserve_temp_files: bool = True

    def normalized_output_path(self) -> Path:
        suffix = video_container_file_suffix(self.container)
        output = self.output_path.resolve(strict=False)
        if output.suffix.lower() != f".{suffix}":
            output = output.with_suffix(f".{suffix}")
        return output

