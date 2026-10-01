"""在可信部位支持框内部选择多尺度纹理区，不用鸟框比例伪造解剖部位。"""
from dataclasses import replace
import numpy as np
from PIL import Image


def part_region_variants(image, candidates):
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
    return tuple(result)
