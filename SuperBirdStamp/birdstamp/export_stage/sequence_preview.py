from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
from statistics import median

from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter import ReferenceRegionTracker
from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature
from birdstamp.image_pipeline import ImageProcContext, ImageProcPipeline
from birdstamp.image_pipeline.image_proc_stage.image_proc_sequence_align_stage import ImageProcSequenceAlignStage
from .render_job_seed import prepare_render_jobs
from .video_export_cancelled_error import VideoExportCancelledError


REFERENCE_KEYS = ('dejitter_reference_regions', 'dejitter_reference_source',
                  'dejitter_reference_strength')


def sequence_files(seeds, template_paths=None) -> tuple[Path, ...]:
    files = set()
    for seed in seeds:
        paths = [seed.path]
        reference = seed.settings.get('dejitter_reference_source')
        if reference:
            paths.append(Path(reference))
        for path in paths:
            files.update((path.resolve(strict=False), path.with_suffix('.xmp').resolve(strict=False)))
    return tuple(sorted(files, key=str))


def file_signatures(paths):
    return tuple((str(path), image_file_signature(path)) for path in paths)


def sequence_input_key(seeds, template_paths=None) -> str:
    # 独立流程只依赖原图、参考选区和强度；模板/手动裁切/输出叠加不参与。
    seeds = tuple(seeds)
    payload = [(path_key(seed.path), {key: seed.settings.get(key) for key in REFERENCE_KEYS}) for seed in seeds]
    data = (payload, file_signatures(sequence_files(seeds)))
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()


@dataclass(slots=True)
class SequencePreview:
    input_key: str
    jobs: dict
    signatures: tuple
    template_paths: dict = field(default_factory=dict)
    tracking: dict = field(default_factory=dict)
    bird_boxes: dict = field(default_factory=dict)
    pixel_boxes: dict = field(default_factory=dict)
    source_sizes: dict = field(default_factory=dict)
    output_size: tuple[int, int] = (0, 0)

    def files_current(self) -> bool:
        return file_signatures(Path(path) for path, _ in self.signatures) == self.signatures


def common_alignment_crop(regions, tracking, source_sizes, reference_size, strength=100):
    """在参考原图像素坐标内求交集；整数平移避免二次重采样和空白边。"""
    shifts = {}
    rw, rh = reference_size
    blend = max(0, min(100, float(strength))) / 100
    for key, result in tracking.items():
        width, height = source_sizes[key]
        offsets = [(((box[0] + box[2]) * width - (region[0] + region[2]) * rw) / 2,
                    ((box[1] + box[3]) * height - (region[1] + region[3]) * rh) / 2)
                   for region, box in zip(regions, result.boxes) if box is not None]
        if not offsets:
            raise ValueError(f'{Path(key).name}：参考区失配，{result.error or "没有可靠匹配"}。请在参考图调整或追加选区后重新分析，无需逐张框选。')
        mx, my = median(x for x, y in offsets), median(y for x, y in offsets)
        tolerance = max(1, min(width, height) * .003)
        agreeing = [(x, y) for x, y in offsets if ((x - mx)**2 + (y - my)**2)**.5 <= tolerance]
        if len(offsets) > 1 and len(agreeing) <= len(offsets) // 2:
            raise ValueError(f'{Path(key).name}：多个参考区运动不一致，请保留同一主体的选区。')
        dx, dy = median(x for x, y in agreeing), median(y for x, y in agreeing)
        shifts[key] = (round(dx * blend), round(dy * blend))
    left = max(-dx for dx, dy in shifts.values())
    top = max(-dy for dx, dy in shifts.values())
    right = min(source_sizes[key][0] - dx for key, (dx, dy) in shifts.items())
    bottom = min(source_sizes[key][1] - dy for key, (dx, dy) in shifts.items())
    if right <= left or bottom <= top:
        raise ValueError('对齐后没有整组共同覆盖的画面，请调整照片范围或参考区。')
    boxes = {key: (left + dx, top + dy, right + dx, bottom + dy) for key, (dx, dy) in shifts.items()}
    return boxes, (right - left, bottom - top)


def prepare_sequence_preview(seeds, template_paths=None, *, cancel_event, progress=lambda message: None,
                             bird_boxes=None, preview_source=None) -> SequencePreview:
    seeds = tuple(seeds)
    if not seeds:
        raise ValueError('请先导入照片。')
    signatures = file_signatures(sequence_files(seeds))
    key = sequence_input_key(seeds)
    settings = seeds[0].settings
    regions = tuple(tuple(box) for box in settings.get('dejitter_reference_regions') or ())
    reference = settings.get('dejitter_reference_source')
    if not reference or not regions:
        raise ValueError('请先框选一个或多个参考区。')
    jobs = prepare_render_jobs(seeds, cancelled=cancel_event.is_set, progress=progress)
    tracking, sizes = {}, {}
    reference = Path(reference)
    with decode_image(reference, decoder='auto') as image:
        tracker = ReferenceRegionTracker(image, regions)
        reference_size = image.size
        if preview_source is not None:
            preview_source(reference, image)
    for index, seed in enumerate(seeds, 1):
        if cancel_event.is_set():
            raise VideoExportCancelledError('已取消去抖动分析。')
        frame_key = path_key(seed.path)
        if frame_key == path_key(reference):
            tracked, sizes[frame_key] = RegionTrackingResult(regions), reference_size
        else:
            with decode_image(seed.path, decoder='auto') as image:
                tracked = tracker.track(image, cancelled=cancel_event.is_set)
                sizes[frame_key] = image.size
                if preview_source is not None:
                    preview_source(seed.path, image)
        tracking[frame_key] = replace(tracked, signature=image_file_signature(seed.path))
        progress(f'对齐参考选区 {index}/{len(seeds)}')
    # 只挽救前后相邻帧均有证据的孤立遮挡，不将推测位移级联到其它失配帧。
    first_pass = dict(tracking)
    for index in range(1, len(seeds)-1):
        path = seeds[index].path
        frame_key = path_key(path)
        result = first_pass[frame_key]
        if result.matched_count == len(regions):
            continue
        previous = first_pass[path_key(seeds[index-1].path)]
        following = first_pass[path_key(seeds[index+1].path)]
        if not any(box is None and previous.boxes[i] is not None and following.boxes[i] is not None
                   for i, box in enumerate(result.boxes)):
            continue
        if cancel_event.is_set():
            raise VideoExportCancelledError('已取消去抖动分析。')
        progress(f'核验局部遮挡 {index+1}/{len(seeds)}')
        with decode_image(path, decoder='auto') as image:
            tracking[frame_key] = tracker.recover(image, result, previous, following, cancelled=cancel_event.is_set)
    boxes, output_size = common_alignment_crop(regions, tracking, sizes, reference_size,
                                              settings.get('dejitter_reference_strength', 100))
    result = SequencePreview(key, {path_key(job.path): job for job in jobs}, signatures,
                             tracking=tracking, bird_boxes=dict(bird_boxes or {}),
                             pixel_boxes=boxes, source_sizes=sizes, output_size=output_size)
    if not result.files_current():
        raise ValueError('照片或 XMP 在分析期间发生变化，请重新分析。')
    return result


def render_sequence_preview_frame(sequence: SequencePreview, path: Path):
    if not sequence.files_current():
        raise ValueError('照片或 XMP 已变化，请重新分析。')
    key = path_key(path)
    job = sequence.jobs[key]
    with decode_image(path, decoder='auto') as image:
        if image.size != sequence.source_sizes[key]:
            raise ValueError('照片尺寸已变化，请重新分析。')
        context = ImageProcContext(image=image, settings=job.settings, source_path=path,
                                   source_paths=tuple(job.path for job in sequence.jobs.values()),
                                   raw_metadata=job.raw_metadata,
                                   precomputed={'sequence_crop_pixels': sequence.pixel_boxes[key]})
        return ImageProcPipeline((ImageProcSequenceAlignStage(),)).process(context)
