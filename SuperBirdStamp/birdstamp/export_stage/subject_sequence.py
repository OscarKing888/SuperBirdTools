"""局部观测 → 平移裁切计划。整数采样沿用独立导出管线，无二次 warp。"""
from dataclasses import dataclass
from datetime import datetime
import numpy as np

from birdstamp.image_dejitter.recognition import SubjectSettings
from birdstamp.image_dejitter.region_consensus import region_offsets
from .sequence_photo_error import SequencePhotoError
from .video_export_cancelled_error import VideoExportCancelledError


@dataclass(frozen=True, slots=True)
class SubjectFramePlan:
    status: str
    observed: tuple
    applied: tuple  # 裁切中心位移，图像 warp 为相反符号
    target: tuple
    timeline: str

    def __post_init__(self):
        for name in ('observed','applied','target'):
            object.__setattr__(self,name,tuple(getattr(self,name)))


def observation_displacement(regions, result, size, reference_size):
    if result.observation is not None and result.observation.displacement is not None:
        return result.observation.displacement
    offsets = region_offsets(regions,result,size,reference_size)
    if len(offsets) != len(regions):
        return None
    return tuple(np.median(list(offsets.values()),axis=0))


def trajectory_times(jobs, keys):
    values = []
    for key in keys:
        raw = jobs[key].raw_metadata or {}
        value = raw.get('EXIF:DateTimeOriginal') or raw.get('DateTimeOriginal')
        try:
            values.append(datetime.strptime(str(value), '%Y:%m:%d %H:%M:%S').timestamp())
        except (ValueError, TypeError, OverflowError, OSError):
            return np.arange(len(keys),dtype=float), 'frame_order'
    if any(b <= a for a,b in zip(values,values[1:])):
        return np.arange(len(keys),dtype=float), 'frame_order'
    return np.array(values)-values[0], 'capture_time'


def smooth_path(displacements, times, window):
    """局部线性回归保留匀速趋势，端点不做重复值填充；两帧不外推趋势。"""
    if len(displacements) < 3:
        return np.zeros_like(displacements)
    output = np.empty_like(displacements)
    for i,t in enumerate(times):
        start = max(0,min(i-window//2,len(times)-window))
        end = min(len(times),start+window)
        x = times[start:end]-t
        design = np.column_stack((np.ones(len(x)),x))
        output[i] = np.linalg.lstsq(design,displacements[start:end],rcond=None)[0][0]
    return output


def prepare_subject_geometry(regions, tracking, sizes, reference_size, settings, *, jobs, cancelled):
    options = SubjectSettings.from_settings(settings)
    keys = tuple(tracking)
    measured = []
    for key in keys:
        if cancelled():
            raise VideoExportCancelledError('已取消局部主体分析')
        result = tracking[key]
        delta = observation_displacement(regions,result,sizes[key],reference_size)
        if delta is None:
            raise SequencePhotoError(key,f'局部主体需要关键帧：{result.error or "没有可靠观测"}。请手动修正匹配位置，或更换参考图。')
        measured.append(delta)
    measured = np.asarray(measured,dtype=float)
    times,timeline = trajectory_times(jobs,keys)
    target = np.zeros_like(measured)
    boundaries = sorted({0, len(keys), *(i for i,key in enumerate(keys)
        if tracking[key].observation and tracking[key].observation.status == 'user_override')})
    if options.mode == 'follow':
        for start,end in zip(boundaries,boundaries[1:]):
            target[start:end] = smooth_path(measured[start:end],times[start:end],options.window)
    # 大拍摄间隔明确停止，不把分开的拍摄强行接成一个轨迹。
    if timeline == 'capture_time' and len(times) > 1:
        gaps = np.diff(times)
        limit = 10.  # 超过十秒的拍摄间隔必须由人工关键帧确认，避免桥接不同拍摄。
        jumps = [i for i in np.flatnonzero(gaps > limit) if int(i)+1 not in boundaries]
        if len(jumps):
            raise SequencePhotoError(keys[int(jumps[0])+1],'拍摄间隔过大，请把不连续拍摄分别分析。')
    blend = max(0,min(100,float(settings.get('dejitter_reference_strength',100))))/100
    shifts = np.rint((measured-target)*blend).astype(int)
    union = settings.get('dejitter_pad_to_union',False) is True
    bounds = None
    for key,(dx,dy) in zip(keys,shifts):
        w,h = sizes[key]
        current = (-int(dx),-int(dy),w-int(dx),h-int(dy))
        if bounds is None:
            bounds = current
        else:
            low,high = (min,max) if union else (max,min)
            bounds = (low(bounds[0],current[0]),low(bounds[1],current[1]),high(bounds[2],current[2]),high(bounds[3],current[3]))
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise SequencePhotoError(key,'局部稳定后没有共同画幅，请减弱强度、补边或分段。')
    l,t,r,b = bounds
    plans = {key:SubjectFramePlan(tracking[key].observation.status if tracking[key].observation else 'reference',
                                  tuple(map(float,measured[i])),tuple(map(int,shifts[i])),tuple(map(float,target[i])),timeline)
             for i,key in enumerate(keys)}
    return {key:(l+int(dx),t+int(dy),r+int(dx),b+int(dy)) for key,(dx,dy) in zip(keys,shifts)}, (r-l,b-t), plans


def analyze_subject_sequence(jobs, tracker, reference, *, cancel_event, **kwargs):
    """完整人工修正帧同时作为后续片段的显式关键帧，绝不自动改换局部。"""
    from dataclasses import replace
    from pathlib import Path
    from birdstamp.decoders.image_decoder import decode_image
    from birdstamp.gui.editor_utils import path_key
    from birdstamp.image_dejitter.manual_region_matches import MANUAL_MATCHES_KEY, valid_manual_boxes
    from birdstamp.image_dejitter.subject_local_tracker import SubjectLocalTracker, LocalObservation
    from birdstamp.image_dejitter.region_tracking_result import RegionTrackingResult, image_file_signature
    from .sequence_analysis import analyze_sequence_frames
    regions, reference_size = tracker.regions, tracker.reference_size
    def prepare_targets(local_tracker, local_reference, local_jobs):
        if hasattr(local_tracker,'prepare_targets'):
            try:
                local_tracker.prepare_targets(local_reference,[job.path for job in local_jobs],
                    cancelled=cancel_event.is_set,progress=kwargs.get('progress',lambda text:None))
            except InterruptedError as exc:
                raise VideoExportCancelledError('已取消目标鸟关联') from exc
    def recover_segments(local_jobs, local_tracker, results):
        from birdstamp.image_dejitter.subject_keyframes import recover_keyframe_segments
        try:
            return recover_keyframe_segments(local_jobs,local_tracker,results,
                cancelled=cancel_event.is_set,progress=kwargs.get('progress',lambda text:None))
        except InterruptedError as exc:
            raise VideoExportCancelledError('已取消分段关键帧核验') from exc
    boundaries = []
    for index,job in enumerate(jobs):
        boxes = valid_manual_boxes(job.settings.get(MANUAL_MATCHES_KEY),job.path,reference,regions)
        if boxes and all(box is not None for box in boxes) and path_key(job.path) != path_key(reference):
            boundaries.append((index,boxes))
    if not boundaries:
        prepare_targets(tracker,reference,jobs)
        tracker.cache_reference = image_file_signature(Path(reference))
        results, sizes = analyze_sequence_frames(jobs,tracker,reference,cancel_event=cancel_event,**kwargs)
        key = path_key(reference)
        if key in results:
            results[key] = replace(results[key],observation=LocalObservation('reference',displacement=(0.,0.),source_size=reference_size))
        return recover_segments(jobs,tracker,results), sizes
    tracking,sizes = {},{}
    segments = [(0,None),*boundaries] if boundaries[0][0] != 0 else boundaries
    total_progress = kwargs.pop('progress_counts',lambda *args: None)
    for n,(start,boxes) in enumerate(segments):
        if cancel_event.is_set():
            raise VideoExportCancelledError('已取消局部主体分析')
        end = segments[n+1][0] if n+1 < len(segments) else len(jobs)
        local_jobs = jobs[start:end]
        local_reference, local_tracker = reference,tracker
        offset = np.zeros(2)
        if boxes:
            local_reference = jobs[start].path
            with decode_image(local_reference,decoder='auto') as image:
                if image.size != reference_size:
                    raise SequencePhotoError(local_reference,'关键帧尺寸/方向不同，请单独分析此段')
                checked = tracker.resolve_manual(RegionTrackingResult((None,)*len(regions)),boxes,image.size)
                if checked.observation is None or checked.observation.displacement is None:
                    raise SequencePhotoError(local_reference,checked.error)
                offset = np.array(checked.observation.displacement)
                local_tracker = (tracker.rekeyframe(image,boxes) if hasattr(tracker,'rekeyframe')
                                 else SubjectLocalTracker(image,boxes,options=tracker.options))
            # 人工坐标属于全局参考；当前段的参考位置已由关键帧定义。
            local_jobs = [replace(job,settings={k:v for k,v in job.settings.items() if k != MANUAL_MATCHES_KEY}) for job in local_jobs]
        local_tracker.cache_reference = image_file_signature(Path(local_reference))
        prepare_targets(local_tracker,local_reference,local_jobs)
        current,current_sizes = analyze_sequence_frames(
            local_jobs,local_tracker,local_reference,cancel_event=cancel_event,
            progress_counts=lambda done,total,stage: total_progress(start+done,len(jobs),stage),**kwargs)
        current = recover_segments(local_jobs,local_tracker,current)
        for key,result in current.items():
            delta = observation_displacement(local_tracker.regions,result,current_sizes[key],reference_size)
            if delta is not None:
                delta = tuple(map(float,np.array(delta)+offset))
                w,h = reference_size
                global_boxes = tuple((l+delta[0]/w,t+delta[1]/h,r+delta[0]/w,b+delta[1]/h) for l,t,r,b in regions)
                obs = result.observation or LocalObservation('user_override' if boxes else 'reference',source_size=reference_size)
                points = tuple((p[0],p[1],p[2]-offset[0],p[3]-offset[1],*p[4:]) for p in obs.points)
                result = replace(result,boxes=global_boxes,observation=replace(obs,displacement=delta,points=points,
                                 reference_origin=tuple(np.array(obs.reference_origin)-offset)),
                                 manual_indices=tuple(range(len(regions))) if boxes and key == path_key(local_reference) else result.manual_indices)
            tracking[key] = result
        sizes.update(current_sizes)
        if kwargs.get('photo_errors'):
            break
    return tracking,sizes
