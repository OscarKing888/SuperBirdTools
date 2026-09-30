"""生产 CLI：GUI 同一分析与导出核心，输入只读，报告不含个人绝对路径。"""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading

from birdstamp.export_stage.render_job_seed import RenderJobSeed
from birdstamp.export_stage.sequence_preview import prepare_sequence_preview
from birdstamp.export_stage.sequence_export import export_aligned_sequence
from birdstamp.image_dejitter.recognition import METHOD_KEY, MODE_KEY, WINDOW_KEY
from birdstamp.image_dejitter.manual_region_matches import manual_match_record, MANUAL_MATCHES_KEY, normalize_match_box
from birdstamp.image_dejitter.region_tracking_result import image_file_signature


def stabilize_files(frames, reference, regions_file, output, *, method='subject_local', mode='lock', strength=100,
                    window=5, pad=False, debug=False, progress=lambda text: None):
    if method not in ('reference_region','subject_local') or mode not in ('lock','follow'):
        raise ValueError('未知的识别方法或稳定模式')
    if not 0 <= strength <= 100:
        raise ValueError('补偿强度应在 0–100 之间')
    frames = tuple(Path(p).resolve() for p in frames)
    reference = Path(reference).resolve()
    payload = json.loads(Path(regions_file).read_text(encoding='utf-8'))
    regions = payload.get('regions') if isinstance(payload,dict) else payload
    if not isinstance(regions,list) or not regions:
        raise ValueError('ROI 文件需要非空的归一化矩形列表 regions')
    if any(normalize_match_box(r) is None for r in regions):
        raise ValueError('ROI 必须是图像内的归一化矩形 [left, top, right, bottom]')
    if len(set(frames)) != len(frames):
        raise ValueError('输入序列包含重复照片')
    settings = {METHOD_KEY:method,MODE_KEY:mode,WINDOW_KEY:window,'dejitter_reference_source':str(reference),
                'dejitter_reference_regions':regions,'dejitter_reference_strength':strength,'dejitter_pad_to_union':pad}
    seeds = [RenderJobSeed(path,dict(settings),{},False) for path in frames]
    for item in payload.get('keyframes',[]) if isinstance(payload,dict) else ():
        index = item['index']  # 显式序列中的零起始下标，不随文件名排序。
        if type(index) is not int or not 0 <= index < len(seeds):
            raise ValueError('关键帧 index 超出输入序列')
        boxes = item.get('regions',[])
        if method != 'subject_local' or len(boxes) != len(regions) or any(normalize_match_box(b) is None for b in boxes):
            raise ValueError('关键帧仅支持局部主体方法，且须指定每个局部的有效位置')
        record = manual_match_record(frames[index],reference,regions,boxes)
        seeds[index] = replace(seeds[index],settings={**settings,MANUAL_MATCHES_KEY:record})
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    tracking = {}
    def ready(key,results,signatures):
        tracking.update(results)
    event = threading.Event()
    sequence = None
    error = None
    try:
        sequence = prepare_sequence_preview(seeds,cancel_event=event,progress=progress,tracking_ready=ready)
        folder = export_aligned_sequence(sequence,output,cancel_event=event,progress=progress)
    except Exception as exc:
        error = exc
        folder = Path(tempfile.mkdtemp(prefix='stabilization_failed_',dir=output))
    records=[]
    from birdstamp.gui.editor_utils import path_key
    for index,path in enumerate(frames):
        key=path_key(path)
        result=tracking.get(key)
        signature=image_file_signature(path)
        record=dict(index=index,file=path.name,signature=hashlib.sha256(repr(signature[1:] if signature else None).encode()).hexdigest())
        if result:
            record.update(error=result.error,boxes=result.boxes,
                          observation=asdict(result.observation) if result.observation else None)
        if sequence and key in sequence.pixel_boxes:
            record['crop_pixels']=sequence.pixel_boxes[key]
            record['plan']=asdict(sequence.subject_plans[key]) if key in sequence.subject_plans else None
        records.append(record)
    # 异常原文可能含绝对路径；共享报告只写逐帧算法原因和异常类型。
    report=dict(version=1,method=method,mode=mode,strength=strength,reference=reference.name,regions=regions,
                status='failed' if error else 'complete',error_type=type(error).__name__ if error else None,
                coordinate_system='EXIF-oriented source pixels',frames=records)
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    if debug:
        import numpy as np
        arrays={}
        for i,path in enumerate(frames):
            result=tracking.get(path_key(path))
            if result and result.observation:
                # NPZ 只保存真实对应点；None 前后向误差表示无有效测量。
                arrays[f'frame_{i}']=np.asarray([[float('nan') if v is None else float(v) for v in p]
                                               for p in result.observation.points],dtype=float).reshape(-1,8)
        np.savez_compressed(folder/'tracks.npz',**arrays)
    if error:
        raise ValueError(f'{error}；诊断报告：{folder / "report.json"}') from error
    return folder
