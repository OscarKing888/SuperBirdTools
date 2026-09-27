from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

from birdstamp.decoders.image_decoder import decode_image
from app_common.exif_io import find_same_stem_xmp_sidecar
from app_common.exif_io.exiftool_runner import exiftool_worker_session, exiftool_read_request
from birdstamp.gui.editor_utils import path_key
from birdstamp.image_dejitter import ReferenceRegionTracker
from birdstamp.image_dejitter.region_consensus import select_translation
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from birdstamp.image_pipeline import ImageProcContext, ImageProcPipeline
from birdstamp.image_pipeline.image_proc_stage.image_proc_sequence_align_stage import ImageProcSequenceAlignStage
from .render_job_seed import prepare_render_jobs
from .sequence_analysis import analyze_sequence_frames
from .video_export_cancelled_error import VideoExportCancelledError


REFERENCE_KEYS = ('dejitter_reference_regions', 'dejitter_reference_source',
                  'dejitter_reference_strength', 'dejitter_pad_to_union')


def sequence_files(seeds, template_paths=None) -> tuple[Path, ...]:
    files = set()
    for seed in seeds:
        paths = [seed.path]
        reference = seed.settings.get('dejitter_reference_source')
        if reference:
            paths.append(Path(reference))
        for path in paths:
            sidecar = find_same_stem_xmp_sidecar(str(path))
            files.update((path.resolve(strict=False),
                          (Path(sidecar) if sidecar else path.with_suffix('.xmp')).resolve(strict=False)))
    return tuple(sorted(files, key=str))


def file_signatures(paths):
    return tuple((str(path), image_file_signature(path)) for path in paths)


def sequence_input_key(seeds, template_paths=None) -> str:
    # 独立流程只依赖原图、参考选区、强度和补边选项；模板/手动裁切/输出叠加不参与。
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
        return file_signatures(sequence_files(self.jobs.values())) == self.signatures


def common_alignment_crop(regions, tracking, source_sizes, reference_size, strength=100, *, pad_to_union=False):
    """选区并集提供平移证据；最终对全部画面求交集，补边时改求并集。"""
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
        translation = select_translation(regions, result, (width, height), reference_size)
        if translation is None:
            raise ValueError(f'{Path(key).name}：多个参考区运动不一致且没有可区分的可靠匹配，请调整参考选区。')
        dx, dy, _ = translation
        shifts[key] = (round(dx * blend), round(dy * blend))
    lower, upper = (min, max) if pad_to_union else (max, min)
    left = lower(-dx for dx, dy in shifts.values())
    top = lower(-dy for dx, dy in shifts.values())
    right = upper(source_sizes[key][0] - dx for key, (dx, dy) in shifts.items())
    bottom = upper(source_sizes[key][1] - dy for key, (dx, dy) in shifts.items())
    if right <= left or bottom <= top:
        raise ValueError('对齐后没有整组共同覆盖的画面，请开启补边保留完整画面，或调整照片范围及参考区。')
    boxes = {key: (left + dx, top + dy, right + dx, bottom + dy) for key, (dx, dy) in shifts.items()}
    return boxes, (right - left, bottom - top)


def prepare_sequence_preview(seeds, template_paths=None, *, cancel_event, progress=lambda message: None,
                             bird_boxes=None, preview_source=None, tracking_ready=None,
                             progress_counts=lambda current, total, stage: None,
                             analysis_workers=0) -> SequencePreview:
    seeds = tuple(seeds)
    if not seeds:
        raise ValueError('请先导入照片。')
    if cancel_event.is_set():
        raise VideoExportCancelledError('已取消去抖动分析。')
    signatures = file_signatures(sequence_files(seeds))
    key = sequence_input_key(seeds)
    settings = seeds[0].settings
    regions = tuple(tuple(box) for box in settings.get('dejitter_reference_regions') or ())
    reference = settings.get('dejitter_reference_source')
    if not reference or not regions:
        raise ValueError('请先框选一个或多个参考区。')
    progress_counts(0, 0, '准备元数据')
    # 保留整批元数据原有的 120 秒预算，同时允许取消自己的独立会话。
    with exiftool_worker_session(), exiftool_read_request(cancel_event.is_set, timeout=120):
        jobs = prepare_render_jobs(seeds, cancelled=cancel_event.is_set, progress=progress)
    reference = Path(reference)
    progress_counts(0, 0, '准备参考图')
    progress('正在准备参考图…')
    with decode_image(reference, decoder='auto') as image:
        if cancel_event.is_set():
            raise VideoExportCancelledError('已取消去抖动分析。')
        tracker = ReferenceRegionTracker(image, regions)
        reference_size = image.size
        if preview_source is not None:
            preview_source(reference, image)
    tracking, sizes = analyze_sequence_frames(
        jobs, tracker, reference, cancel_event=cancel_event, preview_source=preview_source,
        progress=progress, progress_counts=progress_counts, analysis_workers=analysis_workers)
    if cancel_event.is_set():
        raise VideoExportCancelledError('已取消去抖动分析。')
    if tracking_ready is not None and not cancel_event.is_set():
        tracking_ready(key, dict(tracking), signatures)
    progress_counts(0, 0, '计算共同画幅')
    boxes, output_size = common_alignment_crop(regions, tracking, sizes, reference_size,
                                              settings.get('dejitter_reference_strength', 100),
                                              pad_to_union=settings.get('dejitter_pad_to_union', False) is True)
    result = SequencePreview(key, {path_key(job.path): job for job in jobs}, signatures,
                             tracking=tracking, bird_boxes=dict(bird_boxes or {}),
                             pixel_boxes=boxes, source_sizes=sizes, output_size=output_size)
    if not result.files_current():
        raise ValueError('照片或 XMP 在分析期间发生变化，请重新分析。')
    if cancel_event.is_set():
        raise VideoExportCancelledError('已取消去抖动分析。')
    return result


def render_sequence_preview_frame(sequence: SequencePreview, path: Path, *, validate_files=True,
                                  source_paths=None):
    # 批量导出在整批前后校验签名；逐帧重扫整组会使文件检查变成 O(N²)。
    if validate_files and not sequence.files_current():
        raise ValueError('照片或 XMP 已变化，请重新分析。')
    key = path_key(path)
    job = sequence.jobs[key]
    with decode_image(path, decoder='auto') as image:
        if image.size != sequence.source_sizes[key]:
            raise ValueError('照片尺寸已变化，请重新分析。')
        context = ImageProcContext(image=image, settings=job.settings, source_path=path,
                                   source_paths=(source_paths if source_paths is not None else
                                                 tuple(job.path for job in sequence.jobs.values())),
                                   raw_metadata=job.raw_metadata,
                                   precomputed={'sequence_crop_pixels': sequence.pixel_boxes[key]})
        return ImageProcPipeline((ImageProcSequenceAlignStage(),)).process(context)
