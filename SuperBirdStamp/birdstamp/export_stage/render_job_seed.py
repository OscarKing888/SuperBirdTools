from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_common.exif_io import extract_many_with_xmp_priority
from app_common.log import get_logger
from birdstamp.gui import editor_utils, template_context
from .video_frame_job import VideoFrameJob
from .video_export_cancelled_error import VideoExportCancelledError


@dataclass(slots=True)
class RenderJobSeed:
    """GUI 只快照数据；预览与视频共用后台元数据/作业准备。"""

    path: Path
    settings: dict[str, Any]
    raw_metadata: dict[str, Any]
    metadata_complete: bool
    photo_info: object | None = None
    crop_plan: tuple | None = None
    crop_plan_prepared: bool = False
    source_paths: tuple[Path, ...] = ()


def prepare_render_jobs(seeds, *, cancelled=lambda: False, progress=lambda message: None,
                        metadata_loader=None) -> list[VideoFrameJob]:
    def check():
        if cancelled():
            raise VideoExportCancelledError("已中断任务准备。")

    check()
    seeds = tuple(seeds)
    paths = [seed.path.resolve(strict=False) for seed in seeds if not seed.metadata_complete]
    progress(f"正在后台准备元数据，共 {len(seeds)} 张。")
    loader = metadata_loader or extract_many_with_xmp_priority
    try:
        loaded = loader(paths, mode="auto") if paths else {}
    except Exception as exc:
        get_logger('render_job_seed').warning('渲染元数据读取失败，沿用快照: %s', exc)
        loaded = {}
    check()
    source_paths = tuple(seed.path for seed in seeds)
    jobs = []
    for index, seed in enumerate(seeds, 1):
        check()
        raw = dict(seed.raw_metadata or {})
        if not seed.metadata_complete:
            raw.update(loaded.get(seed.path.resolve(strict=False)) or {})
        raw.setdefault("SourceFile", str(seed.path))
        info = template_context.ensure_editor_photo_info(
            seed.photo_info if isinstance(seed.photo_info, template_context.PhotoInfo) else seed.path,
            raw_metadata=raw,
        )
        jobs.append(VideoFrameJob(
            path=seed.path, settings=dict(seed.settings), raw_metadata=raw,
            metadata_context=editor_utils.build_metadata_context(info, raw), photo_info=info,
            source_paths=seed.source_paths or source_paths, crop_plan=seed.crop_plan,
            crop_plan_prepared=seed.crop_plan_prepared,
        ))
        progress(f"正在后台准备任务 {index}/{len(seeds)}")
    return jobs
