"""短失败段的双向关键帧连接；不跨身份丢失，不无限累计漂移。"""
from dataclasses import replace
import numpy as np
from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui.editor_utils import path_key

MAX_BRIDGE_FRAMES = 4


def recover_keyframe_segments(jobs, tracker, tracking, *, cancelled, progress=lambda text: None):
    """两端必须直接锁定到原参考；两条独立链同意后才发布全段。

自动片段是本次图像观测，不写成人工框，也不缓存为固定参考直接匹配。
每次仅持有一张源图与局部模板。长段、末尾无锚点保留人工修正入口。
"""
    from .local_crop_tracker import LocalCropSubjectTracker
    if not isinstance(tracker,LocalCropSubjectTracker) or tracker.target_trajectory is None:
        return tracking
    output=dict(tracking)
    paths=[job.path for job in jobs]
    keys=[path_key(p) for p in paths]
    def check():
        if cancelled():raise InterruptedError('已取消分段关键帧核验')
    def reliable(index):
        result=tracking.get(keys[index])
        return result is not None and result.matched_count==len(tracker.regions)
    def delta(result):
        return np.array(result.observation.displacement if result.observation else (0.,0.))
    def walk(indices, anchor):
        previous=tracking[keys[anchor]]
        offset=delta(previous)
        keyframe=None
        with decode_image(paths[anchor],decoder='auto') as image:
            target=tracker.target_trajectory.frame(paths[anchor]).box
            if target is None:return None
            keyframe=tracker.at_keyframe(image,previous.boxes,target)
        recovered={}
        for index in indices:
            check()
            frame=tracker.target_trajectory.frame(paths[index])
            if frame.box is None:return None
            with decode_image(paths[index],decoder='auto') as image:
                result=keyframe.track(image,cancelled=cancelled,target_box=frame.box)
                if result.matched_count!=len(tracker.regions):return None
                observed=delta(result)
                total=observed+offset
                w,h=tracker.reference_size
                boxes=tuple((l+total[0]/w,t+total[1]/h,r+total[0]/w,b+total[1]/h)
                            for l,t,r,b in tracker.regions)
                # DEBUG 的参考端也恢复到最初虚拟锚点，不能混用局部关键帧坐标。
                points=tuple((p[0],p[1],p[2]-offset[0],p[3]-offset[1],*p[4:]) for p in result.observation.points)
                result=replace(result,boxes=boxes,predicted_boxes=tracker.regions,
                    signature=frame.signature,observation=replace(result.observation,
                        displacement=tuple(total),points=points,
                        reference_origin=tuple(np.array(result.observation.reference_origin)-offset)))
                recovered[index]=result
                keyframe=tracker.at_keyframe(image,boxes,frame.box)
                offset=total
        return recovered
    index=0
    while index<len(paths):
        check()
        if reliable(index):index+=1;continue
        start=index
        while index<len(paths) and not reliable(index):index+=1
        end=index
        # 不跨读取失败、不跨未采样照片、不以新恢复帧充当下段的全局锚点。
        if any(k not in tracking for k in keys[start:end]):continue
        if start==0 or end==len(paths) or end-start>MAX_BRIDGE_FRAMES:
            key=keys[start]
            result=output[key]
            hint='失配段缺少可靠的双向锚点或超过 4 帧；请在此帧拖动全部选区修正，建立人工关键帧'
            reason=result.error+'；'+hint
            output[key]=replace(result,error=reason,observation=replace(result.observation,reason=reason)
                                if result.observation else None)
            continue
        progress(f'双向核验局部关键帧：{paths[start].name} — {paths[end-1].name}')
        try:
            forward=walk(range(start,end),start-1)
            reverse=walk(range(end-1,start-1,-1),end)
        except InterruptedError:
            raise
        except (ValueError,OSError):
            continue  # 裁片容纳不了同尺度选区时仍需要人工分段。
        if forward is None or reverse is None:continue
        tolerance=1.5*float(max(tracker.source_per_analysis))
        if any(np.linalg.norm(delta(forward[i])-delta(reverse[i]))>tolerance for i in range(start,end)):
            continue
        for i in range(start,end):
            result=forward[i]
            output[keys[i]]=replace(result,observation=replace(result.observation,status='keyframe_bridge',
                reason='短段已通过双向关键帧回查',keyframe_paths=(str(paths[start-1]),str(paths[end]))))
    return output
