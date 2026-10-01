"""局部平移配准后端：NCC 唯一峰初始化、ECC 精修及双向/分块核验。

只在已有部位区域内取纹理，不扩到邻近背景，不放宽 LK 的点数门槛。
"""
import numpy as np
from .subject_local_tracker import SubjectLocalTracker


def _check(cancelled):
    if cancelled():
        raise InterruptedError('已取消局部配准')


def _locate(reference, moving, rect, *, radius=64, cancelled=lambda: False):
    import cv2
    _check(cancelled)
    x,y,z,b = map(int, rect)
    template = reference[y:b,x:z]
    if min(template.shape, default=0) < 12 or template.std() < 3:
        raise ValueError('局部纹理或面积不足')
    gy,gx = np.gradient(template.astype(np.float32))
    eig = np.linalg.eigvalsh([[np.mean(gx*gx),np.mean(gx*gy)],
                            [np.mean(gx*gy),np.mean(gy*gy)]])
    if eig[0] < .03*max(eig[1],1e-6):
        raise ValueError('局部主要是单向边缘，无法确定二维位移')
    h,w = moving.shape
    left,top,right,bottom = max(0,x-radius),max(0,y-radius),min(w,z+radius),min(h,b+radius)
    search = moving[top:bottom,left:right]
    if search.shape[0] < b-y or search.shape[1] < z-x:
        raise ValueError('局部有效覆盖不足')
    heat = cv2.matchTemplate(search,template,cv2.TM_CCOEFF_NORMED)
    _,peak,_,position = cv2.minMaxLoc(heat)
    px,py = position
    exclusion = max(3,round(min(template.shape)*.15))
    rivals = heat.copy()
    rivals[max(0,py-exclusion):py+exclusion+1,max(0,px-exclusion):px+exclusion+1] = -1
    if not np.isfinite(heat).all() or peak < .86 or peak-float(rivals.max()) < .06:
        raise ValueError('相关匹配质量不足或存在重复纹理')
    mx,my = left+px,top+py
    patch = moving[my:my+b-y,mx:mx+z-x]
    _check(cancelled)
    warp = np.eye(2,3,dtype=np.float32)
    try:
        score,warp = cv2.findTransformECC(template,patch,warp,cv2.MOTION_TRANSLATION,(3,50,.0001),None,3)
    except cv2.error as exc:
        raise ValueError('平移 ECC 未收敛') from exc
    delta = np.array((mx-x,my-y),float)+warp[:,2]
    if not np.isfinite(delta).all() or not np.isfinite(score) or np.linalg.norm(warp[:,2]) > 3:
        raise ValueError('平移 ECC 与相关峰不一致')
    # ECC 的迭代返回值含边界采样影响；在真实有效重叠内重新核验最终相关度。
    aligned=cv2.warpAffine(patch,warp,(z-x,b-y),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP)
    pad=max(2,int(np.ceil(np.abs(warp[:,2]).max()))+1)
    valid_template=template[pad:-pad,pad:-pad]
    if min(valid_template.shape)<6 or valid_template.size<template.size*.5:
        raise ValueError('精修后有效覆盖不足')
    score=float(cv2.computeECC(valid_template,aligned[pad:-pad,pad:-pad]))
    if not np.isfinite(score) or score<.88:
        raise ValueError('精修后局部相关质量不足')
    if x+delta[0] < 1 or y+delta[1] < 1 or z+delta[0] >= w-1 or b+delta[1] >= h-1:
        raise ValueError('局部有效覆盖不足')
    return delta,min(peak,score)


def register_region(reference, moving, rect, *, cancelled=lambda: False):
    """返回真实子块对应点，不能把 ECC 残差冒充关键点内点数量。"""
    import cv2
    delta,quality = _locate(reference,moving,rect,cancelled=cancelled)
    x,y,z,b = rect
    # 反向从实际当前图取模板，独立全窗口搜索，避免只验证同一个初始化。
    moved = np.rint(np.array(rect)+np.tile(delta,2)).astype(int)
    reverse,_ = _locate(moving,reference,moved,cancelled=cancelled)
    fb = float(np.linalg.norm(delta+reverse))
    if fb > .75:
        raise ValueError('正反向配准不一致')
    points=[]
    for top,bottom in ((y,(y+b)//2),((y+b)//2,b)):
        for left,right in ((x,(x+z)//2),((x+z)//2,z)):
            _check(cancelled)
            patch=reference[top:bottom,left:right]
            if min(patch.shape) < 6 or patch.std() < 3:continue
            dx,dy=np.rint(delta).astype(int)
            sx,sy=left+dx-3,top+dy-3
            ex,ey=right+dx+3,bottom+dy+3
            if sx < 0 or sy < 0 or ex > moving.shape[1] or ey > moving.shape[0]:continue
            heat=cv2.matchTemplate(moving[sy:ey,sx:ex],patch,cv2.TM_CCOEFF_NORMED)
            _,score,_,(px,py)=cv2.minMaxLoc(heat)
            local=np.array((sx+px-left,sy+py-top),float)
            if score < .8 or np.linalg.norm(local-delta) > 1.75:continue
            center=np.array(((left+right)/2,(top+bottom)/2))
            points.append((*center,*(center+local),fb))
    if len(points) < 3:
        raise ValueError('局部子块运动不一致或覆盖不足')
    return delta,quality,points


# LK 失败后的独立配准已在 region_measurement 中按区调用；保留名称兼容旧导入。
LocalRegistrationTracker = SubjectLocalTracker
