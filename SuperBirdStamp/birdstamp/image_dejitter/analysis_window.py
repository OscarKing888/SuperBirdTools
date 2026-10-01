"""按选区尺寸确定分析比例与窗口；所有像素阈值都作用于这一规范尺度。

旧实现把整图压到长边 2048（或鸟框裁片到 1024），同一组阈值在不同照片/路径上
代表 1–5.5 倍不等的源像素。这里改为每个选区按自身几何平均边长换算到约
CANONICAL_GEOM_PX 分析像素，并在源图上直接带预滤波地采样窗口，避免下采样混叠。
"""
from dataclasses import dataclass
import math
import numpy as np
from PIL import Image

CANONICAL_GEOM_PX = 128     # 选区几何平均边长对应的分析像素
MOTION_FRACTION = .04       # 搜索余量占源图长边比例（旧值 100/2048≈.049）
MAX_WINDOW_SIDE = 4096      # 单窗口分析像素上限，超出则整体改用更粗比例


@dataclass(frozen=True, slots=True)
class AnalysisWindow:
    origin: tuple   # 源像素 (x, y)，可为小数
    scale: float    # 源像素 / 分析像素，双轴相同
    size: tuple     # 分析像素 (w, h)

    @property
    def extent(self):
        return (self.size[0]*self.scale, self.size[1]*self.scale)

    def placed(self, source_size, shift=(0., 0.)):
        """平移后夹回源图内；不补黑边，实际原点写回窗口。"""
        ew, eh = self.extent
        x = min(max(0., self.origin[0]+shift[0]), max(0., source_size[0]-ew))
        y = min(max(0., self.origin[1]+shift[1]), max(0., source_size[1]-eh))
        return AnalysisWindow((float(x), float(y)), self.scale, self.size)

    def gray(self, image):
        """LANCZOS 带预滤波采样，返回 uint8 灰度数组。"""
        x, y = self.origin
        ew, eh = self.extent
        box = (x, y, min(float(image.width), x+ew), min(float(image.height), y+eh))
        with image.resize(self.size, Image.Resampling.LANCZOS, box=box) as small:
            with small.convert('L') as gray:
                return np.array(gray)

    def to_source(self, xy):
        return np.asarray(xy, dtype=float)*self.scale + np.asarray(self.origin, dtype=float)

    def to_analysis(self, xy):
        return (np.asarray(xy, dtype=float) - np.asarray(self.origin, dtype=float))/self.scale


def region_scale(source_size, box):
    w, h = (box[2]-box[0])*source_size[0], (box[3]-box[1])*source_size[1]
    return max(1., math.sqrt(max(w*h, 1.))/CANONICAL_GEOM_PX)


def motion_margin(source_size):
    return MOTION_FRACTION*max(source_size)


def region_window(source_size, box, scale, margin):
    """选区外扩运动余量后的窗口；过大时提高比例而不是截断搜索范围。"""
    W, H = source_size
    l, t = max(0., box[0]*W-margin), max(0., box[1]*H-margin)
    r, b = min(float(W), box[2]*W+margin), min(float(H), box[3]*H+margin)
    scale = max(scale, (r-l)/MAX_WINDOW_SIDE, (b-t)/MAX_WINDOW_SIDE)
    size = (max(1, int((r-l)/scale)), max(1, int((b-t)/scale)))
    return AnalysisWindow((float(l), float(t)), float(scale), size)


def window_rect(window, source_size, box):
    """选区在窗口分析坐标中的整数矩形。"""
    W, H = source_size
    a = window.to_analysis((box[0]*W, box[1]*H))
    z = window.to_analysis((box[2]*W, box[3]*H))
    x0, y0 = max(0, int(round(a[0]))), max(0, int(round(a[1])))
    x1, y1 = min(window.size[0], int(round(z[0]))), min(window.size[1], int(round(z[1])))
    return (x0, y0, x1, y1)


def pyramid_levels(margin, scale):
    return int(min(5, max(0, math.ceil(math.log2(max(1., margin/scale/12.))))))
