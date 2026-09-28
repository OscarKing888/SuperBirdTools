"""用已选中的实测区域拟合运动，供诊断及局部补查复用。"""
from dataclasses import dataclass
from math import atan2, degrees


@dataclass(frozen=True, slots=True)
class RegionMotion:
    rotation: complex
    source_center: complex
    target_center: complex

    @property
    def angle_degrees(self):
        return degrees(atan2(self.rotation.imag, self.rotation.real))

    def displacement(self, point):
        return self.target_center + self.rotation * (point - self.source_center) - point


def fit_region_motion(regions, offsets, reference_size, size, *, options):
    """至少三个分散区域且所有残差通过，才允许用转动预测其它位置。"""
    if len(offsets) < 3 or options.rotation_degrees <= 0:
        return None
    rw, rh = reference_size
    source = [complex((regions[i][0] + regions[i][2]) * rw / 2,
                      (regions[i][1] + regions[i][3]) * rh / 2) for i in offsets]
    target = [p + complex(*delta) for p, delta in zip(source, offsets.values())]
    if max(abs(a - b) for a in source for b in source) < min(size) * .1:
        return None
    center, mapped = sum(source) / len(source), sum(target) / len(target)
    covariance = sum((p - center).conjugate() * (q - mapped) for p, q in zip(source, target))
    if abs(covariance) < 1e-8:
        return None
    motion = RegionMotion(covariance / abs(covariance), center, mapped)
    if abs(motion.angle_degrees) > options.rotation_degrees + 1e-8:
        return None
    if any(abs(q - p - motion.displacement(p)) > options.pixel_tolerance(size)
           for p, q in zip(source, target)):
        return None
    return motion
