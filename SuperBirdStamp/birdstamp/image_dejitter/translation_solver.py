"""孔径约束的联合平移求解：二维区给完整约束，单向边缘只给法向约束。

不做静默剔除：任一选区与其余证据冲突即整帧失败并点名；任一方向缺少约束也失败，
并说明哪个方向由哪些选区约束。
"""
from dataclasses import dataclass
import numpy as np

from .aperture import direction_label

MAX_POSTERIOR = .75      # × 最细参与区比例（源像素）
CONFLICT_Z = 3.
CONFLICT_ABS = 1.75      # × 该区比例（源像素）；旧两两阈值 3.5 分析像素的一半
CONTRIBUTION = .1
MIN_LK_INLIERS = 12


@dataclass(frozen=True, slots=True)
class Solve:
    ok: bool
    reason: str = ''
    displacement: tuple | None = None
    covariance: tuple = ()            # 2×2 展平，源像素²
    residuals: tuple = ()             # 每区 (编号, 绝对残差源像素, 归一化残差)
    constrained_by: tuple = ()        # ((方向, 后验标准差, (区号...)), ...)


def _names(indices):
    return '、'.join(str(i+1) for i in indices)


def _information(m):
    if m.kind == '1d':
        n = np.asarray(m.normal, float).reshape(2, 1)
        w = 1./max(m.covariance[0], 1e-12)
        return w*(n @ n.T), w*n[:, 0]*m.value[0]
    W = np.linalg.inv(np.asarray(m.covariance, float).reshape(2, 2)+np.eye(2)*1e-12)
    return W, W @ np.asarray(m.value, float)


def solve_translation(measurements, *, failed=(), max_std=None):
    """measurements：通过的测量；failed：本帧失配的区号（只用于说明缺失的方向）。

    max_std 为后验标准差上限（源像素）；默认按最细参与区比例 ×.75。链式累计时由调用方给出。
    """
    if not measurements:
        return Solve(False, '没有可靠的局部测量')
    parts = [_information(m) for m in measurements]
    A = sum(p[0] for p in parts)
    b = sum(p[1] for p in parts)
    values, vectors = np.linalg.eigh(A)
    two_d = [m.scale for m in measurements if m.kind == '2d']
    reference_scale = min(two_d or [m.scale for m in measurements])
    limit = MAX_POSTERIOR*reference_scale if max_std is None else max_std
    constrained = []
    for value, vector in zip(values, vectors.T):
        total = float(vector @ A @ vector)
        contributors = tuple(m.index for m, (W, _) in zip(measurements, parts)
                             if total > 0 and float(vector @ W @ vector) >= CONTRIBUTION*total)
        std = float(1/np.sqrt(value)) if value > 1e-12 else float('inf')
        constrained.append((direction_label(vector), std, contributors))
    weak = [c for c in constrained if not np.isfinite(c[1]) or c[1] > limit]
    if weak:
        missing = weak[0][0]
        others = [f'{label}方向由选区 {_names(regions)} 约束' for label, std, regions in constrained
                  if np.isfinite(std) and std <= limit and regions]
        reason = f'{missing}方向缺少约束'
        if others:
            reason += '：仅' + '；'.join(others)
        if failed:
            reason += f'；选区 {_names(failed)} 本帧失配'
        return Solve(False, reason, constrained_by=tuple((l, s if np.isfinite(s) else None, r) for l, s, r in constrained))
    covariance = np.linalg.inv(A)
    displacement = covariance @ b
    residuals, conflicts = [], []
    for m, (W, _) in zip(measurements, parts):
        if m.kind == '1d':
            r = m.value[0]-float(np.asarray(m.normal) @ displacement)
            absolute, z = abs(r), abs(r)/np.sqrt(max(m.covariance[0], 1e-12))
        else:
            r = np.asarray(m.value)-displacement
            absolute, z = float(np.linalg.norm(r)), float(np.sqrt(max(0., r @ W @ r)))
        residuals.append((m.index, float(absolute), float(z)))
        if z > CONFLICT_Z and absolute > CONFLICT_ABS*m.scale:
            conflicts.append((m.index, absolute))
    if conflicts:
        detail = '、'.join(f'{i+1}（残差 {a:.1f} 源像素）' for i, a in conflicts)
        return Solve(False, f'选区 {detail} 与其余选区运动冲突，可能有姿态变化；请修正匹配或设置新关键帧',
                     residuals=tuple(residuals), constrained_by=tuple(constrained))
    lk = [m for m in measurements if m.kind == '2d' and m.method == 'lk']
    if len(lk) == len(measurements) and sum(m.inliers for m in lk) < MIN_LK_INLIERS:
        return Solve(False, '共同平移证据不足', residuals=tuple(residuals), constrained_by=tuple(constrained))
    return Solve(True, '', tuple(map(float, displacement)), tuple(map(float, covariance.ravel())),
                 tuple(residuals), tuple(constrained))
