"""GUI/CLI 共用推荐与抽样预检；候选、语义识别及实际匹配分别留诊断。"""
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
from PIL import Image
from .bird_candidates import detect_bird_candidates, iou
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
class CandidateProposal:
    part: str
    regions: tuple
    confidence: float
    status: str = 'pending'
    passed_frames: int = 0
    total_frames: int = 0
    diagnostics: tuple = ()
    variant: str = ''


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
    candidates: tuple = ()


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


LEG_BOX_EXPAND = .25
PRECHECK_CHAIN_FRAMES = 12


def _inside(box, region, expand=LEG_BOX_EXPAND):
    cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
    w,h=(region[2]-region[0])*expand,(region[3]-region[1])*expand
    return region[0]-w <= cx <= region[2]+w and region[1]-h <= cy <= region[3]+h


PART_KEYPOINTS = {'head':tuple(range(7)),'torso':(7,8,13,14,15,20)}
LEG_KEYPOINTS = ((16,18),(17,19))


def semantic_check(candidate, boxes, current_parts, pose=None):
    """跟踪位置须有当前帧同名部位证据；返回 (是否一致, 是否左右腿置换)。

    证据二选一：当前帧同名部位框包含跟踪中心，或当前帧该部位的可靠关键点落在
    跟踪框（外扩 25%）内——单个关键点的翻转一致性失败不应让整条腿“不可见”。
    邻近边缘框不是解剖部位，不参与核验。腿部允许整组一致的左右置换，但不允许
    两条腿各自挑最近的一条。
    """
    anatomical = boxes[:len(boxes)-candidate.edges] if candidate.edges else boxes
    matching=[c for c in current_parts if c.part == candidate.part]
    def keypoints(ids):
        if pose is None:return ()
        return tuple(pose['points'][k] for k in ids if pose['reliable'][k])
    def hit(box, ids):
        return any(_inside((x,y,x,y),box) for x,y in keypoints(ids))
    supports=candidate.support_ids
    if not supports:
        ids=PART_KEYPOINTS.get(candidate.part,())
        return all(any(_inside(box,r) or iou(box,r)>.1 for c in matching for r in c.regions) or hit(box,ids)
                   for box in anatomical),False
    for swap in (False,True):
        def target_id(identity):return 1-identity if swap else identity
        def leg_ok(identity, box):
            tid=target_id(identity)
            return (any(tid in c.support_ids and _inside(box,c.regions[c.support_ids.index(tid)]) for c in matching)
                    or hit(box,LEG_KEYPOINTS[tid]))
        if all(leg_ok(identity,box) for identity,box in zip(supports,anatomical)):
            return True,swap
    return False,False


def evidence_score(result):
    """越高越好：1/(1+后验标准差/最细选区比例)；缺少协方差时退回内点率。"""
    obs=result.observation
    if obs is not None and obs.covariance and obs.region_scales:
        std=float(np.sqrt(max(0.,np.linalg.eigvalsh(np.asarray(obs.covariance).reshape(2,2)).max())))
        return 1/(1+std/min(obs.region_scales))
    if obs is not None and obs.region_metrics:
        return min(m[2]/max(1,m[0]) for m in obs.region_metrics)
    return min(result.scores or (1.,))


def precheck_chain(paths, reference, samples, trackers, candidates, results_by, parts_by_path, passed, quality,
                   diagnostics, cancelled):
    """参考后连续的抽样帧（+1、+2…）若固定参考失配，用与完整分析相同的链式关键帧核验。

    只在连续抽样内进行，解码不超过 PRECHECK_CHAIN_FRAMES；远处抽样不做链式，失败如实保留。
    """
    from types import SimpleNamespace
    from .region_tracking_result import RegionTrackingResult
    from .subject_keyframes import chain_keyframe_segments
    from birdstamp.gui.editor_utils import path_key
    ordered=list(dict.fromkeys(paths))
    if reference not in ordered:return
    start=ordered.index(reference)
    run=[reference]
    for path in ordered[start+1:start+PRECHECK_CHAIN_FRAMES]:
        if path not in samples:break
        run.append(path)
    if len(run) < 3:return
    for i,(name,regions,tracker,_) in enumerate(trackers):
        if tracker is None or passed[i]:continue
        failing=[p for p in run[1:] if p in results_by[i] and results_by[i][p].matched_count != len(regions)]
        if not failing or any(p not in results_by[i] for p in run[1:]):continue
        tracking={path_key(reference):RegionTrackingResult(tuple(regions))}
        tracking.update({path_key(p):results_by[i][p] for p in run[1:]})
        try:
            output=chain_keyframe_segments([SimpleNamespace(path=p) for p in run],tracker,tracking,cancelled=cancelled)
        except (ValueError,OSError):
            continue
        for p in failing:
            result=output[path_key(p)]
            ok=result.matched_count == len(regions)
            swapped=False
            if ok and p in parts_by_path:
                ok,swapped=semantic_check(candidates[i],result.boxes,*parts_by_path[p])
            for d in diagnostics:
                if d.get('candidate_id') == i and d.get('file') == p.name:
                    d.update(passed=ok,stage='chain' if ok else d.get('stage'),swapped=swapped,
                             reason='' if ok else d.get('reason',''))
            if ok:
                quality[i]=min(quality[i],evidence_score(result))
        passed[i]=all(d.get('passed') for d in diagnostics if d.get('candidate_id') == i and d.get('file') != reference.name) \
                  and all(p in results_by[i] for p in samples[1:])


def recommend_regions(reference, paths, *, method='reference_region', existing=(), target=None,
                      part='auto', target_count=9, experimental=False, options=MatchingOptions(),
                      cancelled=lambda:False, progress=lambda text:None,
                      detector=detect_bird_candidates, pose_predictor=None, candidate_callback=lambda result:None):
    from birdstamp.decoders.image_decoder import decode_image
    from .reference_region_tracker import ReferenceRegionTracker
    from .local_crop_tracker import LocalCropSubjectTracker
    from .bird_parts.pose import candidates_from_pose
    from .bird_parts.model_store import MODEL_ID
    reference = Path(reference)
    paths = tuple(Path(p) for p in paths)
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
            candidates=[c for c in candidates_from_pose(pose,target,image.size) if part == 'auto' or c.part == part]
            from .part_region_variants import part_region_variants
            candidates=part_region_variants(image,candidates,target)
            proposals=tuple(CandidateProposal(c.part,c.regions,c.confidence,total_frames=len(samples),variant=c.variant) for c in candidates)
            draft=Recommendation('prechecking','已识别到部位候选，正在预检；候选尚不能用于导出。',
                target=target,birds=birds,candidates=proposals,
                metadata=dict(version=1,target=target,part=part,local_analysis=True,experimental=experimental,model=MODEL_ID))
            check();candidate_callback(draft)
            if existing:
                # 手工局部可能属于另一运动；不把未知语义的旧框自动混入。
                draft.status='manual_conflict'
                draft.message='保留了人工选区；候选仅供查看。请先处理人工区冲突，再采用候选。'
                return draft
            if not candidates:
                draft.status='no_reliable_region'
                draft.message='已检测到目标鸟，但没有可靠的指定部位关键点；请手动选区。'
                return draft
            trackers=[]
            for i,c in enumerate(candidates):
                try:
                    tracker=LocalCropSubjectTracker(image,c.regions,target=target,options=options,detector=detector)
                except ValueError as exc:
                    # 纹理/孔径不合格的候选单独记失败原因，不中断其他候选。
                    tracker=None
                    diagnostics.append(dict(file=reference.name,part=c.part,candidate_id=i,passed=False,reason=str(exc),stage='texture'))
                trackers.append((c.part,c.regions,tracker,c.confidence))
        else:
            trackers=[('background',(box,),ReferenceRegionTracker(image,(box,),options=options),1.)
                      for box in background_candidates(image,birds,existing)]
        source_size=image.size
    passed=[v[2] is not None for v in trackers]; quality=[v[3] for v in trackers]
    observed=[{} for _ in trackers]
    results_by=[{} for _ in trackers]; parts_by_path={}; identity_break=None
    trajectory=None
    if method == 'subject_local':
        from .target_trajectory import build_target_trajectory
        trajectory=build_target_trajectory(reference,paths,target,cancelled=cancelled,progress=progress,detector=detector)
        with decode_image(reference,decoder='auto') as image:
            for i,(name,regions,tracker,_) in enumerate(trackers):
                if tracker is None:continue
                result=tracker.track(image,cancelled=cancelled,target_box=target)
                passed[i]=result.matched_count==len(regions)
                diagnostics.append(dict(file=reference.name,part=name,candidate_id=i,passed=passed[i],reason=result.error,stage='tracking'))
    for sample_index,path in enumerate(samples[1:],1):
        check();progress(f'抽样预检 {sample_index}/{len(samples)-1}：{path.name}')
        with decode_image(path,decoder='auto') as moving:
            current_birds=detect(path,moving)
            current_parts=None
            if method == 'subject_local':
                try:
                    frame=trajectory.frame(path)
                    if frame.box is None:raise ValueError(frame.error)
                    current_target=frame.box
                    current_pose=infer(path,moving,current_target)
                    current_parts=candidates_from_pose(current_pose,current_target,moving.size)
                    parts_by_path[path]=(current_parts,current_pose)
                except ValueError as exc:
                    stage='identity' if frame.box is None else 'parts'
                    diagnostics.extend(dict(file=path.name,part=name,candidate_id=i,passed=False,reason=str(exc),stage=stage)
                                       for i,(name,_,tracker,_) in enumerate(trackers) if tracker is not None)
                    passed=[False]*len(trackers)
                    if stage == 'identity':
                        # 身份中断后不再检查后续抽样（不跨越失败帧重选鸟）；中断前的逐帧结果保留。
                        identity_break=path.name
                        break
                    continue
            for i,(name,regions,tracker,confidence) in enumerate(trackers):
                check()
                if tracker is None or (not passed[i] and method != 'subject_local'):
                    continue
                if moving.size != source_size:
                    passed[i]=False;diagnostics.append(dict(file=path.name,part=name,candidate_id=i,reason='源尺寸不同'));continue
                if method == 'subject_local':
                    result=tracker.track(moving,cancelled=cancelled,target_box=current_target)
                else:
                    result=tracker.track(moving,cancelled=cancelled)
                good=result.matched_count == len(regions)
                reason=result.error
                swapped=False
                if good and current_parts is not None:
                    semantic_ok,swapped=semantic_check(candidates[i],result.boxes,current_parts,current_pose)
                    if not semantic_ok:
                        good=False;reason='跟踪位置与同名部位证据不一致或部位不可见'
                results_by[i][path]=result
                if good and name == 'background' and any(iou(b,c.box)>0 for b in result.boxes for c in current_birds):
                    good=False;reason='背景候选进入鸟体范围'
                if good:
                    score=evidence_score(result)
                    quality[i]=min(quality[i],score)
                    observed[i][path]=(result.boxes[0],score)
                else:
                    passed[i]=False
                diagnostics.append(dict(file=path.name,part=name,candidate_id=i,regions=regions,passed=good,reason=reason,
                                        stage='parts' if reason.startswith('跟踪位置') else 'tracking',swapped=swapped))
    if method == 'subject_local' and identity_break is None:
        precheck_chain(paths,reference,samples,trackers,candidates,results_by,parts_by_path,passed,quality,diagnostics,cancelled)
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
    if not chosen and method=='subject_local':
        message=f'已识别到目标鸟，保留 {len(candidates)} 个部位候选；预检未全部通过，可在下方查看并采用为人工待修正区。'
    if identity_break and method=='subject_local':
        message=f'目标鸟身份在 {identity_break} 中断，未检查其后的抽样；'+message
    meta=dict(version=1,target=target,part=name,auto_regions=chosen,local_analysis=method=='subject_local',
              model=MODEL_ID if method=='subject_local' else '',experimental=experimental,resolved_part=name)
    proposals=tuple(CandidateProposal(c.part,c.regions,c.confidence,'passed' if passed[i] else 'failed',
        sum(bool(d.get('passed')) for d in diagnostics if d.get('candidate_id')==i),len(samples),
        tuple(d for d in diagnostics if d.get('candidate_id')==i),variant=c.variant) for i,c in enumerate(candidates)) if method=='subject_local' else ()
    status='ready' if chosen else ('identity_break' if identity_break and method=='subject_local' else 'no_reliable_region')
    return Recommendation(status,message,tuple(chosen),name,target,birds,
                          diagnostics,tuple(p.name for p in samples),meta,proposals)
