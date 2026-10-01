"""GUI/CLI 共用推荐与抽样预检；候选、语义识别及实际匹配分别留诊断。"""
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
from PIL import Image
from .bird_candidates import detect_bird_candidates, associate_target, iou
from .matching_options import MatchingOptions
from .manual_region_matches import normalize_match_box

PARTS_RELEASE_VALIDATED = False  # 真实样本仍有高置信度错误关节，不能作为正式默认能力。


def normalize_recommendation(raw):
    if not isinstance(raw,dict) or raw.get('version') != 1:
        return {}
    target = normalize_match_box(raw.get('target'))
    part = raw.get('part','auto')
    if part not in ('auto','head','torso','legs','background'):
        part = 'auto'
    values = raw.get('auto_regions',[])
    values = values if isinstance(values,(tuple,list)) else ()
    auto = [tuple(box) for value in values if (box := normalize_match_box(value))]
    return dict(version=1,target=target,part=part,auto_regions=auto,
                local_analysis=bool(raw.get('local_analysis') and target),
                experimental=raw.get('experimental') is True,model=str(raw.get('model',''))[:100],
                resolved_part=str(raw.get('resolved_part',''))[:30])


def sample_paths(paths, reference):
    paths = tuple(dict.fromkeys(Path(p) for p in paths))
    reference = Path(reference)
    all_paths = tuple(dict.fromkeys((reference,*paths)))
    if len(all_paths) <= 7:
        return all_paths
    index = paths.index(reference) if reference in paths else -1
    indices = [index+1,index+2,round((len(paths)-1)*.25),round((len(paths)-1)*.5),
               round((len(paths)-1)*.75),len(paths)-1]
    return tuple(dict.fromkeys((reference,*(paths[i] for i in indices if 0 <= i < len(paths)))))


@dataclass
class Recommendation:
    status: str
    message: str
    regions: tuple = ()
    part: str = ''
    target: tuple | None = None
    birds: tuple = ()
    diagnostics: list = field(default_factory=list)
    samples: tuple = ()
    metadata: dict = field(default_factory=dict)


def background_candidates(image, birds=(), existing=()):
    from .auto_regions import suggest_reference_regions, _overlaps_with_margin
    proposals = list(suggest_reference_regions(image,existing,target_count=9))
    # 多尺度均匀覆盖；宽轮廓也能成为候选，最终必须通过匹配器唯一性检查。
    for bw,bh in ((.22,.25),(.32,.36),(.42,.48)):
        for cy in (.205,.5,.8):
            for cx in (.16,.5,.84):
                l,t = max(.02,min(1-bw-.02,cx-bw/2)), max(.02,min(1-bh-.02,cy-bh/2))
                proposals.append((l,t,l+bw,t+bh))
    result = []
    with image.resize((512,384),Image.Resampling.BILINEAR).convert('L') as gray:
        for box in proposals:
            if any(_overlaps_with_margin(box,b.box,.01) for b in birds):
                continue
            if any(_overlaps_with_margin(box,b,.01) for b in existing):
                continue
            with gray.crop(tuple(round(v*s) for v,s in zip(box,(512,384,512,384)))) as crop:
                pixels = np.asarray(crop,dtype=float)
                gy,gx = np.gradient(pixels)
                eigen = np.linalg.eigvalsh([[np.mean(gx*gx),np.mean(gx*gy)],
                                            [np.mean(gx*gy),np.mean(gy*gy)]])
                if pixels.std() >= 8 and eigen[0] >= .02*max(eigen[1],1e-9):
                    result.append(tuple(box))
    return tuple(dict.fromkeys(result))[:36]


def recommend_regions(reference, paths, *, method='reference_region', existing=(), target=None,
                      part='auto', target_count=9, experimental=False, options=MatchingOptions(),
                      cancelled=lambda:False, progress=lambda text:None,
                      detector=detect_bird_candidates, pose_predictor=None):
    from birdstamp.decoders.image_decoder import decode_image
    from .reference_region_tracker import ReferenceRegionTracker
    from .local_crop_tracker import LocalCropSubjectTracker
    from .bird_parts.pose import candidates_from_pose
    from .bird_parts.model_store import MODEL_ID
    reference = Path(reference)
    samples = sample_paths(paths,reference)
    if method not in ('reference_region','subject_local'):
        raise ValueError('未知的选区推荐方法')
    if part not in ('auto','head','torso','legs'):
        raise ValueError('部位必须为 auto/head/torso/legs')
    if target is not None:
        target=normalize_match_box(target)
        if target is None:raise ValueError('目标鸟框必须是图像内的归一化矩形')
    if method == 'subject_local' and not (experimental or PARTS_RELEASE_VALIDATED):
        return Recommendation('unvalidated','部位识别尚未通过发布验证；可显式开启实验功能，或手动框选。')
    if not 1 <= target_count <= 36:
        raise ValueError('目标选区数量应在 1–36 之间')
    if method == 'reference_region' and len(existing) >= target_count:
        return Recommendation('unchanged','已有人工选区达到目标数量，保留原选区。')
    def check():
        if cancelled():
            raise InterruptedError('已取消选区推荐')
    check()
    from .bird_observation_cache import detect_cached,pose_cached
    def detect(path,image):
        return (detect_cached(path,image,cancelled=cancelled) if detector is detect_bird_candidates
                else detector(image,cancelled=cancelled))
    def infer(path,image,box):
        return (pose_cached(path,image,box,cancelled=cancelled) if pose_predictor is None
                else pose_predictor(image,box,cancelled=cancelled))
    diagnostics=[]
    with decode_image(reference,decoder='auto') as image:
        progress('识别参考图中的鸟…')
        birds=detect(reference,image)
        if method == 'subject_local':
            if target is None:
                if len(birds) != 1:
                    return Recommendation('choose_target' if birds else 'no_target',
                        '请选择要稳定的目标鸟。' if birds else '未识别到鸟，可手动框选。',birds=birds)
                target=birds[0].box
            pose=infer(reference,image,target)
            candidates=[c for c in candidates_from_pose(pose,target) if part == 'auto' or c.part == part]
            if existing:
                # 手工局部可能属于另一运动；不把未知语义的旧框自动混入。
                return Recommendation('manual_conflict','保留了人工选区。请先确认或移除人工区，再推荐同一部位。',target=target)
            trackers=[(c.part,c.regions,LocalCropSubjectTracker(image,c.regions,target=target,options=options,detector=detector),c.confidence)
                      for c in candidates]
        else:
            trackers=[('background',(box,),ReferenceRegionTracker(image,(box,),options=options),1.)
                      for box in background_candidates(image,birds,existing)]
        source_size=image.size
    passed=[True]*len(trackers); quality=[v[3] for v in trackers]
    observed=[{} for _ in trackers]
    if method == 'subject_local':
        with decode_image(reference,decoder='auto') as image:
            for i,(name,regions,tracker,_) in enumerate(trackers):
                tracker.detector=lambda image,**kwargs:birds
                result=tracker.track(image,cancelled=cancelled)
                passed[i]=result.matched_count==len(regions)
                if not passed[i]:diagnostics.append(dict(file=reference.name,part=name,passed=False,reason=result.error))
    for sample_index,path in enumerate(samples[1:],1):
        check();progress(f'抽样预检 {sample_index}/{len(samples)-1}：{path.name}')
        with decode_image(path,decoder='auto') as moving:
            current_birds=detect(path,moving)
            current_parts=None
            if method == 'subject_local':
                try:
                    current_target=associate_target(target,current_birds)
                    current_parts=candidates_from_pose(infer(path,moving,current_target),current_target)
                except ValueError as exc:
                    diagnostics.append(dict(file=path.name,reason=str(exc)));passed=[False]*len(trackers);continue
            for i,(name,regions,tracker,confidence) in enumerate(trackers):
                check()
                if not passed[i]:
                    continue
                if moving.size != source_size:
                    passed[i]=False;diagnostics.append(dict(file=path.name,part=name,reason='源尺寸不同'));continue
                if method == 'subject_local':
                    tracker.detector=lambda image,**kwargs:current_birds
                result=tracker.track(moving,cancelled=cancelled)
                good=result.matched_count == len(regions)
                reason=result.error
                if good and current_parts is not None:
                    matching=[c for c in current_parts if c.part == name]
                    if not matching or not all(any(iou(b,r) > .1 for c in matching for r in c.regions) for b in result.boxes):
                        good=False;reason='跟踪位置与同名部位证据不一致或部位不可见'
                if good and name == 'background' and any(iou(b,c.box)>0 for b in result.boxes for c in current_birds):
                    good=False;reason='背景候选进入鸟体范围'
                if good:
                    score=min(result.scores or (1.,))
                    if result.observation and result.observation.region_metrics:
                        score=min(m[2]/max(1,m[0]) for m in result.observation.region_metrics)
                    quality[i]=min(quality[i],score)
                    observed[i][path]=(result.boxes[0],score)
                else:
                    passed[i]=False
                diagnostics.append(dict(file=path.name,part=name,regions=regions,passed=good,reason=reason))
    valid=[i for i,ok in enumerate(passed) if ok]
    valid.sort(key=lambda i:(-round(quality[i],2),{'torso':0,'head':1,'legs':2,'background':0}[trackers[i][0]]))
    chosen=[];chosen_indices=[];name=''
    from .auto_regions import _overlaps_with_margin
    for i in valid:
        name,regions,_,_=trackers[i]
        if method == 'subject_local':
            chosen=list(regions);break
        if len(chosen)+len(existing) >= target_count:
            break
        if not any(_overlaps_with_margin(regions[0],r,.01) for r in chosen):
            chosen.extend(regions);chosen_indices.append(i)
    if method == 'reference_region' and len(chosen_indices) > 1:
        from .region_consensus import resolve_tracking_consensus
        from .region_tracking_result import RegionTrackingResult
        # 单区可匹配不代表多区支持同一相机运动；只添加整个抽样集合一致的组合。
        while len(chosen_indices) > 1:
            compatible=True
            for path in samples[1:]:
                boxes,scores=zip(*(observed[i][path] for i in chosen_indices))
                result=resolve_tracking_consensus(tuple(chosen),RegionTrackingResult(boxes,scores=scores),
                    source_size,source_size,options=options)
                if result.matched_count != len(chosen_indices):
                    compatible=False;break
            if compatible:break
            chosen_indices.pop();chosen.pop()
    if method == 'reference_region' and existing and chosen:
        with decode_image(reference,decoder='auto') as image:
            group=ReferenceRegionTracker(image,(*existing,*chosen),options=options)
        for path in samples[1:]:
            check()
            with decode_image(path,decoder='auto') as moving:
                result=group.track(moving,cancelled=cancelled)
            if result.matched_count != len(group.regions):
                return Recommendation('manual_conflict',
                    f'{path.name}：人工区与推荐区未能共同通过预检；已保留原选区，请检查人工区。',
                    diagnostics=diagnostics,samples=tuple(p.name for p in samples))
    message=(f'抽样预检通过 {len(samples)}/{len(samples)} 张，推荐 {len(chosen)} 个选区；完整分析仍需逐张检查。'
             if chosen else '没有通过全部抽样帧的可靠选区；请手动选区、补关键帧，或改用背景稳定。')
    meta=dict(version=1,target=target,part=name,auto_regions=chosen,local_analysis=method=='subject_local',
              model=MODEL_ID if method=='subject_local' else '',experimental=experimental,resolved_part=name)
    return Recommendation('ready' if chosen else 'no_reliable_region',message,tuple(chosen),name,target,birds,
                          diagnostics,tuple(p.name for p in samples),meta)
