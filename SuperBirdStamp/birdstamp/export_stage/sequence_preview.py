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
from birdstamp.image_dejitter.recognition import SubjectSettings, SUBJECT_KEYS, recognition_strategy
from birdstamp.image_dejitter.region_consensus import select_translation
from birdstamp.image_dejitter.matching_options import MATCHING_KEYS, MatchingOptions, normalize_matching_settings
from birdstamp.image_dejitter.rigid_alignment import ALIGNMENT_MODE_KEY, normalize_alignment_mode, estimate_alignment
from birdstamp.image_dejitter.alignment_bounds import intersect_convex, outward_bounds, largest_pixel_rectangle, has_complete_pixel
from birdstamp.image_dejitter.region_tracking_result import image_file_signature
from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY
from birdstamp.image_pipeline import ImageProcContext, ImageProcPipeline
from birdstamp.image_pipeline.image_proc_stage.image_proc_sequence_align_stage import ImageProcSequenceAlignStage
from .render_job_seed import prepare_render_jobs
from .sequence_analysis import analyze_sequence_frames
from .sequence_photo_error import SequencePhotoError, sequence_photo_errors
from .video_export_cancelled_error import VideoExportCancelledError


REFERENCE_KEYS = ('dejitter_reference_regions', 'dejitter_reference_source',
                  'dejitter_reference_strength', 'dejitter_pad_to_union', ALIGNMENT_MODE_KEY, *MATCHING_KEYS, *SUBJECT_KEYS)
SEQUENCE_ANALYSIS_VERSION = 11


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
    payload = []
    for seed in seeds:
        settings = {**seed.settings, **normalize_matching_settings(seed.settings),
                    **SubjectSettings.from_settings(seed.settings).as_settings()}
        settings[ALIGNMENT_MODE_KEY] = normalize_alignment_mode(settings.get(ALIGNMENT_MODE_KEY))
        relevant = {key: settings.get(key) for key in REFERENCE_KEYS}
        if settings.get(MANUAL_MATCHES_KEY):
            relevant[MANUAL_MATCHES_KEY] = settings[MANUAL_MATCHES_KEY]
        payload.append((path_key(seed.path), relevant))
    identity_signature = None
    if any(SubjectSettings.from_settings(s.settings).method=='subject_local'
           and (s.settings.get('dejitter_region_recommendation') or {}).get('local_analysis') for s in seeds):
        from birdstamp.image_dejitter.bird_observation_cache import detector_signature
        from birdstamp.image_dejitter.target_trajectory import TRAJECTORY_VERSION
        identity_signature = (TRAJECTORY_VERSION,detector_signature())
    data = (SEQUENCE_ANALYSIS_VERSION, payload, file_signatures(sequence_files(seeds)),identity_signature)
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
    # 部分预览仍对完整输入检查签名；jobs 只含可预览的连续成功前缀。
    input_jobs: dict = field(default_factory=dict)
    failure: SequencePhotoError | None = None
    alignments: dict = field(default_factory=dict)
    canvas_box: tuple = ()
    # Relative to the displayed output canvas, including padded previews.
    intersection_box: tuple | None = None
    union_box: tuple | None = None
    subject_plans: dict = field(default_factory=dict)

    def frame_crop_plan(self, key):
        from birdstamp.image_dejitter.sequence_geometry import aligned_crop_plan
        if key in self.pixel_boxes:
            return aligned_crop_plan(self.source_sizes[key],self.pixel_boxes[key])
        # Rotated frames carry their real geometry in alignments/canvas_box.
        return (0.,0.,1.,1.), (0,0,0,0)

    @property
    def partial(self) -> bool:
        return self.failure is not None

    @property
    def all_jobs(self):
        return self.input_jobs or self.jobs

    def files_current(self) -> bool:
        return file_signatures(sequence_files(self.all_jobs.values())) == self.signatures


def common_alignment_crop(regions, tracking, source_sizes, reference_size, strength=100, *, pad_to_union=False,
                          options=MatchingOptions()):
    """选区并集提供平移证据；最终对全部画面求交集，补边时改求并集。"""
    shifts = {}
    bounds = None
    rw, rh = reference_size
    blend = max(0, min(100, float(strength))) / 100
    lower, upper = (min, max) if pad_to_union else (max, min)
    for key, result in tracking.items():
        width, height = source_sizes[key]
        offsets = [(((box[0] + box[2]) * width - (region[0] + region[2]) * rw) / 2,
                    ((box[1] + box[3]) * height - (region[1] + region[3]) * rh) / 2)
                   for region, box in zip(regions, result.boxes) if box is not None]
        if not offsets:
            raise SequencePhotoError(key, f'参考区失配，{result.error or "没有可靠匹配"}。请在参考图调整或追加选区后重新分析，无需逐张框选。')
        translation = select_translation(regions, result, (width, height), reference_size, options=options)
        if translation is None:
            raise SequencePhotoError(key, '多个参考区运动不一致且没有可区分的可靠匹配，请调整参考选区。')
        dx, dy, _ = translation
        dx, dy = round(dx * blend), round(dy * blend)
        shifts[key] = (dx, dy)
        current = (-dx, -dy, width - dx, height - dy)
        bounds = current if bounds is None else (
            lower(bounds[0], current[0]), lower(bounds[1], current[1]),
            upper(bounds[2], current[2]), upper(bounds[3], current[3]))
        left, top, right, bottom = bounds
        if right <= left or bottom <= top:
            # 分析结果已按列表排序，记录加入后首次使共同画幅为空的照片。
            raise SequencePhotoError(key, '对齐后没有整组共同覆盖的画面，请开启补边保留完整画面，或调整照片范围及参考区。')
    boxes = {key: (left + dx, top + dy, right + dx, bottom + dy) for key, (dx, dy) in shifts.items()}
    return boxes, (right - left, bottom - top)


def prepare_rigid_geometry(regions, tracking, sizes, reference_size, reference_key, settings, *, cancelled):
    alignments = {}
    footprints = []
    intersection = None
    union = settings.get('dejitter_pad_to_union',False) is True
    options = MatchingOptions.from_settings(settings)
    for key,result in tracking.items():
        if cancelled():
            raise VideoExportCancelledError('已取消去抖动分析。')
        try:
            alignment = estimate_alignment(regions,result,sizes[key],reference_size,mode='rigid',
                strength=settings.get('dejitter_reference_strength',100),options=options,is_reference=key==reference_key)
        except ValueError as exc:
            raise SequencePhotoError(key,f'参考区失配：{result.error or exc}') from exc
        alignments[key] = alignment
        footprint = alignment.footprint(sizes[key],safe=not union)
        if not footprint:
            raise SequencePhotoError(key,'旋转后没有可用的完整画幅。')
        footprints.append(footprint)
        if not union:
            intersection = footprint if intersection is None else intersect_convex(intersection,footprint)
            try:
                usable = has_complete_pixel(intersection,cancelled=cancelled)
            except InterruptedError as exc:
                raise VideoExportCancelledError('已取消去抖动分析。') from exc
            if not usable:
                raise SequencePhotoError(key,'对齐后没有整组共同覆盖的画面，请开启补边或调整照片范围。')
    if union:
        canvas = outward_bounds(footprints)
    else:
        try:
            canvas = largest_pixel_rectangle(intersection,cancelled=cancelled)
        except InterruptedError as exc:
            raise VideoExportCancelledError('已取消去抖动分析。') from exc
    if canvas is None:
        raise SequencePhotoError(next(reversed(tracking)),'对齐后没有完整像素的共同画面，请开启补边。')
    boxes = {key:box for key,alignment in alignments.items()
             if (box:=alignment.source_pixel_box(canvas)) is not None}
    return boxes,(canvas[2]-canvas[0],canvas[3]-canvas[1]),alignments,canvas


def prepare_sequence_preview(seeds, template_paths=None, *, cancel_event, progress=lambda message: None,
                             bird_boxes=None, preview_source=None, tracking_ready=None,
                             progress_counts=lambda current, total, stage: None,
                             analysis_workers=0, allow_partial=False) -> SequencePreview:
    """allow_partial 供交互预览保留失败前缀；默认保持整组失败即抛错的契约。"""
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
    with sequence_photo_errors(reference), decode_image(reference, decoder='auto') as image:
        if cancel_event.is_set():
            raise VideoExportCancelledError('已取消去抖动分析。')
        tracker = (recognition_strategy(settings).create_tracker(image, regions, options=MatchingOptions.from_settings(settings), settings=settings)
                   if SubjectSettings.from_settings(settings).method == "subject_local"
                   else ReferenceRegionTracker(image, regions, options=MatchingOptions.from_settings(settings)))
        reference_size = image.size
        if preview_source is not None:
            preview_source(reference, image)
    photo_errors = {}
    from .subject_sequence import analyze_subject_sequence
    analyzer = analyze_subject_sequence if SubjectSettings.from_settings(settings).method == "subject_local" else analyze_sequence_frames
    tracking, sizes = analyzer(
        jobs, tracker, reference, cancel_event=cancel_event, preview_source=preview_source,
        progress=progress, progress_counts=progress_counts, analysis_workers=analysis_workers,
        photo_errors=photo_errors if allow_partial else None)
    if cancel_event.is_set():
        raise VideoExportCancelledError('已取消去抖动分析。')
    if tracking_ready is not None and not cancel_event.is_set():
        tracking_ready(key, dict(tracking), signatures)
    progress_counts(0, 0, '计算共同画幅')
    input_jobs = {path_key(job.path): job for job in jobs}
    failure = next((photo_errors[k] for k in input_jobs if k in photo_errors), None)
    accepted = {}
    for job_key, job in input_jobs.items():
        if failure is not None and job_key == path_key(failure.source_path):
            break
        accepted[job_key] = job

    alignments,canvas_box = {},()
    subject_plans = {}
    def crop(selected):
        nonlocal alignments,canvas_box,subject_plans
        if SubjectSettings.from_settings(settings).method == 'subject_local':
            from .subject_sequence import prepare_subject_geometry
            boxes, size, subject_plans = prepare_subject_geometry(
                regions, {k:tracking[k] for k in selected}, sizes, reference_size, settings,
                jobs=selected, cancelled=cancel_event.is_set)
            return boxes, size
        if normalize_alignment_mode(settings.get(ALIGNMENT_MODE_KEY)) == 'rigid':
            boxes,size,alignments,canvas_box = prepare_rigid_geometry(
                regions,{k:tracking[k] for k in selected},sizes,reference_size,path_key(reference),settings,
                cancelled=cancel_event.is_set)
            return boxes,size
        return common_alignment_crop(regions, {k: tracking[k] for k in selected}, sizes, reference_size,
                                     settings.get('dejitter_reference_strength', 100),
                                     pad_to_union=settings.get('dejitter_pad_to_union', False) is True,
                                     options=tracker.options)

    if failure is not None and not accepted:
        raise failure
    try:
        boxes, output_size = crop(accepted)
    except SequencePhotoError as exc:
        if not allow_partial:
            raise
        failure = exc
        prefix = {}
        for job_key, job in accepted.items():
            if job_key == path_key(exc.source_path):
                break
            prefix[job_key] = job
        if not prefix:
            raise
        accepted = prefix
        # 只复用成功帧的跟踪坐标重算画幅，不重新解码或匹配。
        boxes, output_size = crop(accepted)
    if failure is not None:
        # 预览持有错误说明即可；异常栈可能引用 action、线程池及大幅图像临时数组。
        failure = SequencePhotoError(failure.source_path, failure.message)
    result = SequencePreview(key, accepted, signatures,
                             tracking=tracking, bird_boxes=dict(bird_boxes or {}),
                             pixel_boxes=boxes, source_sizes={k: sizes[k] for k in accepted},
                             output_size=output_size, input_jobs=input_jobs if failure else {}, failure=failure,
                             alignments=alignments,canvas_box=canvas_box,subject_plans=subject_plans)
    from .sequence_intersection import compute_intersection_box, compute_union_box
    result.intersection_box = (compute_intersection_box(result, cancelled=cancel_event.is_set)
                               if settings.get('dejitter_pad_to_union', False) is True
                               else (0, 0, *output_size))
    result.union_box = compute_union_box(result, cancelled=cancel_event.is_set)
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
    with sequence_photo_errors(path), decode_image(path, decoder='auto') as image:
        if image.size != sequence.source_sizes[key]:
            raise ValueError('照片尺寸已变化，请重新分析。')
        context = ImageProcContext(image=image, settings=job.settings, source_path=path,
                                   source_paths=(source_paths if source_paths is not None else
                                                 tuple(job.path for job in sequence.jobs.values())),
                                   raw_metadata=job.raw_metadata,
                                   precomputed=({'sequence_alignment':sequence.alignments[key],
                                                 'sequence_canvas_box':sequence.canvas_box} if sequence.alignments else
                                                {'sequence_crop_pixels': sequence.pixel_boxes[key]}))
        return ImageProcPipeline((ImageProcSequenceAlignStage(),)).process(context)
