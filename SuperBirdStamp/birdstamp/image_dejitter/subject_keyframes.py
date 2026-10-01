"""失配段的逐帧链式关键帧：只在真实图像上重新配准，误差上限内才发布。

第一阶段（并行）每张都直接对原参考测量；仍能直接测量的选区（如电线法向）保持
锚定原参考，不累计漂移。只有直接失配的选区改为对“最近可靠帧”的模板测量，
质量下降时才换关键帧，因此累计误差随换帧次数而非帧数增长。

两端都有可靠锚点时分别前向/反向行走，两条链在误差范围内一致才融合发布；
只有单侧锚点（如末尾）时，链的后验标准差和链数都受上限约束，超限即明确拒绝，
保留人工关键帧入口。每次只持有一张源图和若干小模板。
"""
from dataclasses import replace
import numpy as np

from birdstamp.decoders.image_decoder import decode_image
from birdstamp.gui.editor_utils import path_key
from .region_measurement import Measurement, measure

MAX_CHAIN_LINKS = 12
CHAIN_STD = 1.          # × 最细选区比例（源像素）：单侧链的后验标准差上限
BRIDGE_TOLERANCE = 1.5  # × 最细选区比例：双向链最小容差
LINK_FACTOR = 1.5       # 每次链式测量的噪声放大，吸收相关误差
REKEY_LK = .6           # LK 内点率低于此值即换关键帧
REKEY_NCC = .9          # 相关类测量质量低于此值即换关键帧
MAX_BRIDGE_FRAMES = MAX_CHAIN_LINKS  # 兼容旧名


def _matrix(values):
    values = tuple(values or ())
    return np.asarray(values, float).reshape(2, 2) if len(values) == 4 else np.zeros((2, 2))


def _std(cov):
    return float(np.sqrt(max(0., np.linalg.eigvalsh(cov).max())))


def _inflate(m, template):
    """链式测量噪声 ×LINK_FACTOR；模板帧自身的后验协方差原样累加。"""
    if not m.ok:
        return m
    base = _matrix(template.base_cov)
    if m.kind == '1d':
        n = np.asarray(m.normal)
        prior = float(n @ base @ n)
        return replace(m, covariance=(prior+LINK_FACTOR**2*(m.covariance[0]-prior),))
    cov = _matrix(m.covariance)
    return replace(m, covariance=tuple((base+LINK_FACTOR**2*(cov-base)).ravel()))


def _needs_rekey(m):
    return m.quality < (REKEY_LK if m.method == 'lk' else REKEY_NCC)


def _direct(result):
    """第一阶段对原参考的逐区测量；缺失（如身份失败）时全部视为失配。"""
    obs = result.observation if result is not None else None
    rows = obs.constraints if obs is not None else ()
    return {m.index: m for m in map(Measurement.from_row, rows) if m.ok}


def chain_keyframe_segments(jobs, tracker, tracking, *, cancelled, progress=lambda text: None):
    if not hasattr(tracker, 'templates') or not hasattr(tracker, 'geometry'):
        return tracking
    output = dict(tracking)
    paths = [job.path for job in jobs]
    keys = [path_key(p) for p in paths]
    count = len(tracker.regions)
    scale = float(min(tracker.geometry.scales))

    def check():
        if cancelled():
            raise InterruptedError('已取消分段关键帧核验')

    def reliable(index):
        result = tracking.get(keys[index])
        obs = result.observation if result is not None else None
        return (result is not None and result.matched_count == count
                and (obs is None or obs.displacement is not None))

    def anchor_state(index):
        obs = tracking[keys[index]].observation
        disp = np.asarray(obs.displacement if obs is not None and obs.displacement is not None else (0., 0.), float)
        cov = _matrix(obs.covariance) if obs is not None else np.zeros((2, 2))
        return disp, cov

    def prior_for(index, previous_index, last):
        here, there = (tracker.motion_prior(paths[i]) for i in (index, previous_index))
        return last + (np.asarray(here)-np.asarray(there) if here is not None and there is not None else 0.)

    def walk(indices, anchor, regions, direction):
        """返回 ({帧: (结果, 位移, 协方差, 链数)}, (失败帧, 原因))。"""
        disp, cov = anchor_state(anchor)
        anchor_obs = tracking[keys[anchor]].observation
        base_links = 0 if anchor_obs is None or anchor_obs.status == 'reference' else 1
        templates, fresh = {}, {}
        check()
        with decode_image(paths[anchor], decoder='auto') as image:
            for r in regions:
                try:
                    templates[r] = tracker.template_at(r, image, base=disp, base_cov=tuple(cov.ravel()),
                                                       links=base_links, source=str(paths[anchor]))
                except ValueError as exc:
                    return {}, (indices[0], f'锚点 {paths[anchor].name} 上无法建立关键帧：{exc}')
        recovered, previous, last = {}, anchor, disp
        for index in indices:
            check()
            identity = tracker.identity_error(paths[index])
            if identity:
                # 目标鸟身份中断时不跨越该帧串接，避免链到另一只鸟。
                return recovered, (index, identity)
            direct = _direct(tracking.get(keys[index]))
            prior = prior_for(index, previous, last)
            try:
                with decode_image(paths[index], decoder='auto') as image:
                    if image.size != tracker.reference_size:
                        return recovered, (index, '源尺寸或方向不同')
                    measurements, used = [], {}
                    for r in range(count):
                        if r in direct:
                            measurements.append(direct[r])
                            continue
                        if r not in templates:
                            return recovered, (index, f'选区 {r+1} 没有可用关键帧')
                        m = _inflate(measure(templates[r], image, prior=prior, cancelled=cancelled), templates[r])
                        if not m.ok and fresh.get(r) is not None and fresh[r] is not templates[r]:
                            retry = _inflate(measure(fresh[r], image, prior=prior, cancelled=cancelled), fresh[r])
                            if retry.ok:
                                templates[r], m = fresh[r], retry
                        measurements.append(m)
                        used[r] = m
                    links = max([m.links for m in used.values()] or [0])
                    result = tracker.solve_result(measurements, status='keyframe_chain', max_std=1e9)
                    obs = result.observation
                    if obs.displacement is None:
                        return recovered, (index, result.error)
                    disp, cov = np.asarray(obs.displacement), _matrix(obs.covariance)
                    recovered[index] = (result, disp, cov, links)
                    # 为下一张准备“上一张”模板；质量下降时立即换关键帧。
                    for r in regions:
                        try:
                            fresh[r] = tracker.template_at(r, image, base=disp, base_cov=tuple(cov.ravel()),
                                                           links=links+1, source=str(paths[index]))
                        except ValueError:
                            fresh[r] = None
                        if r in used and _needs_rekey(used[r]) and fresh[r] is not None:
                            templates[r] = fresh[r]
            except (ValueError, OSError) as exc:
                return recovered, (index, str(exc))
            previous, last = index, disp
        return recovered, None

    def publish(index, result, disp, cov, links, how, anchors):
        original = tracking[keys[index]]
        w, h = tracker.reference_size
        boxes = tuple((l+disp[0]/w, t+disp[1]/h, r+disp[0]/w, b+disp[1]/h) for l, t, r, b in tracker.regions)
        status = 'keyframe_bridge' if how == 'both' else 'keyframe_chain'
        reason = '双向链式关键帧核验通过' if how == 'both' else f'单侧链式关键帧（{links} 段）'
        observation = replace(result.observation, status=status, reason=reason, displacement=tuple(map(float, disp)),
                              covariance=tuple(map(float, cov.ravel())), chain=(int(links), _std(cov), how),
                              keyframe_paths=tuple(str(paths[a]) for a in anchors))
        return replace(result, boxes=boxes, signature=original.signature, error='', reasons=('',)*count,
                       observation=observation)

    def reject(index, reason):
        result = output[keys[index]]
        hint = '请在此帧拖动全部选区修正，建立人工关键帧'
        message = (result.error+'；' if result.error else '')+reason+'；'+hint
        return replace(result, error=message,
                       observation=replace(result.observation, reason=message) if result.observation else None)

    index = 0
    while index < len(paths):
        check()
        if reliable(index):
            index += 1
            continue
        start = index
        while index < len(paths) and not reliable(index):
            index += 1
        end = index
        # 不跨读取失败/未采样照片；那些帧没有第一阶段观测。
        if any(k not in tracking for k in keys[start:end]):
            continue
        regions = sorted({r for i in range(start, end) for r in range(count)} - set.intersection(
            *(set(_direct(tracking.get(keys[i]))) for i in range(start, end))))
        progress(f'链式核验局部关键帧：{paths[start].name} — {paths[end-1].name}')
        forward, fail_f = walk(list(range(start, end)), start-1, regions, 'forward') if start > 0 else ({}, None)
        reverse, fail_r = walk(list(range(end-1, start-1, -1)), end, regions, 'reverse') if end < len(paths) else ({}, None)
        for i in range(start, end):
            f, r = forward.get(i), reverse.get(i)
            if f and r:
                cf, cr = f[2], r[2]
                tolerance = max(BRIDGE_TOLERANCE*scale, 3*_std(cf+cr))
                gap = float(np.linalg.norm(f[1]-r[1]))
                if gap > tolerance:
                    output[keys[i]] = reject(i, f'前向与反向链式结果相差 {gap:.1f} 源像素，超过容差 {tolerance:.1f}')
                    continue
                wf, wr = np.linalg.inv(cf+np.eye(2)*1e-9), np.linalg.inv(cr+np.eye(2)*1e-9)
                cov = np.linalg.inv(wf+wr)
                disp = cov @ (wf @ f[1]+wr @ r[1])
                output[keys[i]] = publish(i, f[0], disp, cov, max(f[3], r[3]), 'both', (start-1, end))
                continue
            one = f or r
            if one is None:
                failure = [x for x in (fail_f, fail_r) if x]
                detail = '；'.join(dict.fromkeys(reason for at, reason in failure if reason)) or '没有可靠的链式锚点'
                if failure:
                    output[keys[i]] = reject(i, f'链式关键帧未能连接（{detail}）')
                continue
            result, disp, cov, links = one
            std = _std(cov)
            if std > CHAIN_STD*scale or links > MAX_CHAIN_LINKS:
                output[keys[i]] = reject(i, f'链式累计误差超限（σ={std:.1f} 源像素，{links} 段）')
                continue
            anchor = start-1 if f else end
            output[keys[i]] = publish(i, result, disp, cov, links, 'forward' if f else 'reverse', (anchor,))
    return output


def recover_keyframe_segments(jobs, tracker, tracking, *, cancelled, progress=lambda text: None):
    """旧名称：现由链式关键帧实现。"""
    return chain_keyframe_segments(jobs, tracker, tracking, cancelled=cancelled, progress=progress)
