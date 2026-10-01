"""在可信部位支持框内部选择多尺度纹理区，不用鸟框比例伪造解剖部位。

小鸟站在电线/枝条上时，另给出“部位 + 邻近单向边缘”组合：边缘只约束法向，
与鸟体二维证据联合求解；边缘只取鸟框旁 0.5 倍范围内，避免远处相机滚转。
"""
from dataclasses import replace
import numpy as np
from PIL import Image

SMALL_BIRD_PX = 500     # 鸟框短边（源像素）小于此值时才建议加入邻近边缘


def _texture(image, box):
    from .aperture import classify_texture
    pixels = tuple(round(v*s) for v, s in zip(box, (*image.size, *image.size)))
    scale = max(1., max(pixels[2]-pixels[0], pixels[3]-pixels[1])/256)
    size = (max(1, round((pixels[2]-pixels[0])/scale)), max(1, round((pixels[3]-pixels[1])/scale)))
    with image.resize(size, Image.Resampling.LANCZOS, box=pixels) as small, small.convert('L') as gray:
        return classify_texture(np.array(gray))


def nearby_edges(image, target):
    """鸟框左右两侧各找一个最强的单向边缘框（电线/枝条），在远离判定允许的范围内。"""
    l, t, r, b = target
    bw, bh = r-l, b-t
    found = []
    for side in (-1, 1):
        x0 = max(0., l-.45*bw) if side < 0 else r+.02*bw
        x1 = l-.02*bw if side < 0 else min(1., r+.45*bw)
        if x1-x0 < .2*bw:
            continue
        best = None
        for k in range(7):
            cy = t+bh*(.2+.1*k)
            box = (x0, max(0., cy-.18*bh), x1, min(1., cy+.18*bh))
            if (box[2]-box[0])*image.width < 24 or (box[3]-box[1])*image.height < 24:
                continue
            texture = _texture(image, box)
            if texture.kind == '1d' and (best is None or texture.strength > best[0]):
                best = (texture.strength, box)
        if best:
            found.append(best[1])
    return tuple(found)


def part_region_variants(image, candidates, target=None):
    result=[]
    for candidate in candidates:
        result.append(replace(candidate,variant='完整部位'))
        # 双腿分别候选；联合候选仍由匹配器验证共同运动。
        if candidate.part == 'legs' and len(candidate.regions)>1:
            result.extend(replace(candidate,regions=(r,),variant=f'可见腿 {i+1}',support_ids=(i,))
                          for i,r in zip(candidate.support_ids,candidate.regions))
        # 只取单个部位框内的子区，禁止扩入翅膀、电线或另一部位。
        if len(candidate.regions)!=1:continue
        l,t,r,b=candidate.regions[0]
        proposals=[]
        for scale,fx,fy in ((.85,.5,.5),(.7,0,0),(.7,1,0),(.7,0,1),(.7,1,1)):
            bw,bh=(r-l)*scale,(b-t)*scale
            x,y=l+(r-l-bw)*fx,t+(b-t-bh)*fy
            box=(x,y,x+bw,y+bh)
            pixels=tuple(round(v*s) for v,s in zip(box,(*image.size,*image.size)))
            if min(pixels[2]-pixels[0],pixels[3]-pixels[1])<24:continue
            with image.crop(pixels) as crop, crop.convert('L') as gray:
                gray.thumbnail((128,128),Image.Resampling.LANCZOS)
                gy,gx=np.gradient(np.array(gray,dtype=np.float32))
                eig=np.linalg.eigvalsh([[np.mean(gx*gx),np.mean(gx*gy)],
                                       [np.mean(gx*gy),np.mean(gy*gy)]])
                if eig[0]<.03*max(eig[1],1e-6):continue
                proposals.append((float(eig[0]),box,scale))
        for i,(_,box,scale) in enumerate(sorted(proposals,reverse=True)[:3]):
            result.append(replace(candidate,regions=(box,),variant=f'纹理子区 {i+1}（{scale:.0%}）'))
    torso = next((c for c in candidates if c.part == 'torso' and len(c.regions) == 1), None)
    small = target is not None and min((target[2]-target[0])*image.width,(target[3]-target[1])*image.height) < SMALL_BIRD_PX
    if small and torso is not None:
        # 小鸟的解剖部位只有几十像素、姿态一变就失配；整只鸟的纹理更稳。
        # 仍以躯干身份核验（躯干关键点须落在框内），不改成背景稳定。
        l,t,r,b = target
        dx,dy = (r-l)*.05,(b-t)*.05
        whole = replace(torso,regions=((l+dx,t+dy,r-dx,b-dy),),variant='整鸟')
        result.append(whole)
        edges = nearby_edges(image, target)
        for base in (whole, torso):
            if edges:
                result.append(replace(base,regions=(*base.regions,*edges),edges=len(edges),
                                      variant=f'{base.variant or "完整部位"}＋邻近边缘 {len(edges)} 段'))
    return tuple(result)
