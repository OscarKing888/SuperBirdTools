"""Native pixel-edge coordinates shared by sequence analysis, previews and export."""
from dataclasses import dataclass
from math import atan2, cos, degrees, isfinite, sin

from .matching_options import MatchingOptions
from .region_consensus import select_translation

ALIGNMENT_MODE_KEY = 'dejitter_alignment_mode'
IDENTITY = (1., 0., 0., 0., 1., 0.)


def normalize_alignment_mode(value):
    # Missing fields in old workspaces/API callers retain integer translation.
    return 'rigid' if value == 'rigid' else 'translation'


def map_point(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return (a*x+b*y+c, d*x+e*y+f)


def inverse_matrix(matrix):
    a, b, c, d, e, f = matrix
    det = a*e-b*d
    if abs(det) < 1e-12:
        raise ValueError('无效的对齐变换')
    return (e/det, -b/det, (b*f-e*c)/det, -d/det, a/det, (d*c-a*f)/det)


def rectangle_points(box):
    l, t, r, b = box
    return ((l,t), (r,t), (r,b), (l,b))


@dataclass(frozen=True, slots=True)
class FrameAlignment:
    source_to_reference: tuple = IDENTITY
    measured_degrees: float | None = None
    applied_degrees: float = 0.
    status: str = 'translation'
    reason: str = ''
    region_indices: tuple = ()

    def __post_init__(self):
        m = self.source_to_reference
        if len(m) != 6 or not all(isfinite(float(v)) for v in m):
            raise ValueError('无效的对齐变换')
        a,b,_,d,e,_ = m
        if (abs(a*a+d*d-1) > 1e-6 or abs(b*b+e*e-1) > 1e-6
                or abs(a*b+d*e) > 1e-6 or abs(a*e-b*d-1) > 1e-6):
            raise ValueError('对齐变换必须仅包含平移与旋转')
        if (self.measured_degrees is not None and not isfinite(self.measured_degrees)) or not isfinite(self.applied_degrees):
            raise ValueError('无效的旋转角度')
        if self.status not in ('reference', 'translation', 'rigid', 'fallback'):
            raise ValueError('无效的对齐状态')
        if self.status == 'rigid' and self.measured_degrees is None:
            raise ValueError('缺少实测旋转角度')
        angle = degrees(atan2(d,a))
        if abs(angle-self.applied_degrees) > 1e-6:
            raise ValueError('应用角度与变换不一致')

    @property
    def reference_to_source(self):
        return inverse_matrix(self.source_to_reference)

    @property
    def rotated(self):
        return abs(self.source_to_reference[1]) > 1e-12

    def source_pixel_box(self, canvas_box):
        """Only translation has a rectangular source crop; never fabricate one for rotation."""
        if self.rotated:
            return None
        tx, ty = self.source_to_reference[2], self.source_to_reference[5]
        return tuple(round(v-(tx if i % 2 == 0 else ty)) for i,v in enumerate(canvas_box))

    def footprint(self, size, *, safe=False):
        margin = 2 if safe and self.rotated else 0
        w,h = size
        if w <= margin*2 or h <= margin*2:
            return ()
        return tuple(map_point(self.source_to_reference,p) for p in rectangle_points((margin,margin,w-margin,h-margin)))

    def output_polygon(self, box, source_size, canvas_box):
        if box is None:
            return ()
        w,h = source_size
        l,t,r,b = canvas_box
        points = (map_point(self.source_to_reference,(x*w,y*h)) for x,y in rectangle_points(box))
        return tuple(((x-l)/(r-l),(y-t)/(b-t)) for x,y in points)

    def source_crop_polygon(self, source_size, canvas_box):
        w,h = source_size
        return tuple((x/w,y/h) for x,y in
                     (map_point(self.reference_to_source,p) for p in rectangle_points(canvas_box)))

    def description(self):
        if self.status == 'reference':
            return '参考图：角度基准'
        if self.status == 'fallback':
            return '未纠正旋转，已退回平移：' + self.reason
        if self.status == 'rigid':
            return f'实测旋转 {self.measured_degrees:+.2f}° · 应用纠正 {self.applied_degrees:+.2f}°'
        return '仅平移'


def estimate_alignment(regions, result, size, reference_size, *, mode='translation', strength=100,
                       options=MatchingOptions(), is_reference=False):
    if is_reference:
        return FrameAlignment(measured_degrees=0., status='reference', region_indices=tuple(range(len(regions))))
    selected = select_translation(regions,result,size,reference_size,options=options)
    if selected is None:
        raise ValueError('没有可区分的可靠匹配，无法确定平移。')
    dx,dy,indices = selected
    blend = max(0,min(100,float(strength)))/100
    fallback = FrameAlignment((1.,0.,-round(dx*blend),0.,1.,-round(dy*blend)), region_indices=indices)
    if normalize_alignment_mode(mode) != 'rigid':
        return fallback
    reason = '可靠选区不足三个'
    if len(indices) >= 3:
        rw,rh = reference_size
        w,h = size
        p = [complex((regions[i][0]+regions[i][2])*rw/2,(regions[i][1]+regions[i][3])*rh/2) for i in indices]
        q = [complex((result.boxes[i][0]+result.boxes[i][2])*w/2,(result.boxes[i][1]+result.boxes[i][3])*h/2) for i in indices]
        pc,qc = sum(p)/len(p),sum(q)/len(q)
        covariance = sum((a-pc).conjugate()*(b-qc) for a,b in zip(p,q))
        span = max(abs(a-b) for a in p for b in p)
        reason = '选区分布过于集中'
        if span >= min(size)*.1 and abs(covariance) > 1e-8:
            rotation = covariance/abs(covariance)
            theta = atan2(rotation.imag,rotation.real)
            residual = max(abs(b-qc-rotation*(a-pc)) for a,b in zip(p,q))
            reason = '角度超过允许范围或几何误差过大'
            if abs(degrees(theta)) <= options.rotation_degrees+1e-8 and residual <= options.pixel_tolerance(size):
                angle = -blend*theta
                c,s = cos(angle),sin(angle)
                goal = qc+blend*(pc-qc)
                tx,ty = goal.real-c*qc.real+s*qc.imag,goal.imag-s*qc.real-c*qc.imag
                # A zero-angle result can retain the exact integer crop path.
                if abs(s) <= 1e-12:
                    tx,ty = round(tx),round(ty)
                return FrameAlignment((c,-s,tx,s,c,ty),degrees(theta),degrees(angle),'rigid','',indices)
    return FrameAlignment(fallback.source_to_reference,status='fallback',reason=reason,region_indices=indices)


def render_alignment(image, source_size, alignment, canvas_box, *, max_edge=None):
    """One affine sampling pass; integer translations retain Pillow crop's exact pixels."""
    from PIL import Image
    l,t,r,b = canvas_box
    ow,oh = r-l,b-t
    scale = min(1,max_edge/max(ow,oh)) if max_edge else 1
    output = (max(1,round(ow*scale)),max(1,round(oh*scale)))
    box = alignment.source_pixel_box(canvas_box)
    if box is not None and image.size == tuple(source_size) and output == (ow,oh):
        with image.crop(box) as cropped:
            return cropped.convert('RGB')
    a,bx,c,d,e,f = alignment.reference_to_source
    sx,sy = image.width/source_size[0],image.height/source_size[1]
    coefficients = (a*ow/output[0]*sx,bx*oh/output[1]*sx,(a*l+bx*t+c)*sx,
                    d*ow/output[0]*sy,e*oh/output[1]*sy,(d*l+e*t+f)*sy)
    return image.transform(output,Image.Transform.AFFINE,coefficients,
                           resample=Image.Resampling.BICUBIC,fillcolor=(0,0,0))
