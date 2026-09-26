from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path

from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter import ReferenceRegionTracker
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature
from .core import prepare_uniform_auto_crop_plans, render_video_frame_context
from .render_job_seed import prepare_render_jobs
from .video_export_cancelled_error import VideoExportCancelledError


def sequence_files(seeds, template_paths) -> tuple[Path, ...]:
    files = set()
    for seed in seeds:
        paths = [seed.path]
        reference = seed.settings.get("dejitter_reference_source")
        if reference:
            paths.append(Path(reference))
        for path in paths:
            files.update((path.resolve(strict=False), path.with_suffix('.xmp').resolve(strict=False)))
        template = template_paths.get(seed.settings.get("template_name"))
        if template:
            files.add(Path(template).resolve(strict=False))
    return tuple(sorted(files, key=str))


def file_signatures(paths):
    return tuple((str(path), image_file_signature(path)) for path in paths)


def sequence_input_key(seeds, template_paths) -> str:
    # 只比较渲染输入；metadata_complete 是加载状态，不是成片内容。
    # 原始元数据以源图/XMP 签名校验，后台缓存补齐不会使已分析结果自我失效。
    payload = [(path_key(seed.path), seed.settings,
                getattr(seed.photo_info, 'editor_row_number', None)) for seed in seeds]
    data = (payload, file_signatures(sequence_files(seeds, template_paths)))
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()


@dataclass(slots=True)
class SequencePreview:
    input_key: str
    jobs: dict
    signatures: tuple
    template_paths: dict
    tracking: dict = field(default_factory=dict)
    bird_boxes: dict = field(default_factory=dict)

    def files_current(self) -> bool:
        return file_signatures(Path(path) for path, _ in self.signatures) == self.signatures


def prepare_sequence_preview(seeds, template_paths, *, cancel_event, progress=lambda message: None,
                             bird_boxes=None) -> SequencePreview:
    """整组分析不携带 GUI 位图，返回可用于预览和导出子集的同一份裁切计划。"""
    seeds = tuple(seeds)
    signatures = file_signatures(sequence_files(seeds, template_paths))
    key = sequence_input_key(seeds, template_paths)
    cache = dict(bird_boxes or {})
    jobs = prepare_render_jobs(seeds, cancelled=cancel_event.is_set, progress=progress)
    prepare_uniform_auto_crop_plans(
        jobs, bird_box_cache=cache, cancel_event=cancel_event,
        progress_callback=lambda current, total: progress(f"计算稳定裁切 {current}/{total}"),
    )
    tracking = {}
    settings = seeds[0].settings if seeds else {}
    regions = tuple(tuple(box) for box in settings.get('dejitter_reference_regions') or ())
    reference = settings.get('dejitter_reference_source')
    if regions and reference:
        reference = Path(reference)
        with decode_image(reference, decoder='auto') as image:
            tracker = ReferenceRegionTracker(image, regions)
        for index, seed in enumerate(seeds, 1):
            if cancel_event.is_set():
                raise VideoExportCancelledError('已取消去抖动分析。')
            if path_key(seed.path) == path_key(reference):
                tracked = RegionTrackingResult(regions)
            else:
                with decode_image(seed.path, decoder='auto') as image:
                    tracked = tracker.track(image, cancelled=cancel_event.is_set)
            tracking[path_key(seed.path)] = replace(tracked, signature=image_file_signature(seed.path))
            progress(f"跟踪参考选区 {index}/{len(seeds)}")
    result = SequencePreview(key, {path_key(job.path): job for job in jobs}, signatures,
                             dict(template_paths), tracking, cache)
    if not result.files_current():
        raise ValueError('照片、XMP 或模板在分析期间发生变化，请重新分析。')
    return result


def render_sequence_preview_frame(sequence: SequencePreview, path: Path):
    if not sequence.files_current():
        raise ValueError('照片、XMP 或模板已变化，请重新分析。')
    job = sequence.jobs[path_key(path)]
    context = render_video_frame_context(job, template_paths=sequence.template_paths,
                                         bird_box_cache=sequence.bird_boxes)
    # 无批量去抖时，单帧管线才产生实际裁切几何，同样保留供导出复用。
    job.crop_plan = context.crop_plan
    return context


def apply_sequence_plans(sequence: SequencePreview, jobs) -> None:
    """调用方先验证完整输入签名；原顺序供模板上下文和子集输出继续使用。"""
    paths = tuple(job.path for job in sequence.jobs.values())
    for job in jobs:
        prepared = sequence.jobs.get(path_key(job.path))
        if prepared is not None:
            job.crop_plan = prepared.crop_plan
            job.crop_plan_prepared = True
            job.source_paths = paths
