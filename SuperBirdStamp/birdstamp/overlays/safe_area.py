"""全屏发布的叠加安全区；无 Qt，几何与图层变换供预览和导出共用。"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite


def normalize_platform(value):
    from .safe_area_options import current_options
    value = str(value or 'off').strip()
    labels = current_options()['labels']
    return value if value in labels else value.lower() if value.lower() in labels else 'off'


def normalize_options(raw, *, strict=False):
    """规范化任意命名组；平台名及默认边距仅来自配置，不在算法里固定。"""
    if strict and (not isinstance(raw, dict) or not isinstance(raw.get('labels'), dict)
                   or not isinstance(raw.get('presets'), dict)):
        raise ValueError('安全区配置必须包含组名 labels 和边距 presets。')
    raw = raw if isinstance(raw, dict) else {}
    labels = raw.get('labels') if isinstance(raw.get('labels'), dict) else {}
    presets = raw.get('presets') if isinstance(raw.get('presets'), dict) else {}
    result = {'labels': {'off': '关闭（非全屏）'}, 'presets': {}}
    names = {'off', result['labels']['off'].casefold()}
    for key, settings in presets.items():
        key = str(key).strip()
        name = str(labels.get(key) or '').strip()
        try:
            if not key or key == 'off' or not name or name.casefold() in names:
                raise ValueError('组名不能为空或重复，且不能使用保留标识 off。')
            margins = {}
            for orientation in ('portrait', 'landscape'):
                values = tuple(float(v) for v in settings[orientation])
                if (len(values) != 4 or not all(isfinite(v) and 0 <= v < 1 for v in values)
                        or values[0]+values[2] >= 1 or values[1]+values[3] >= 1):
                    raise ValueError('边距需在 0%～100% 之间，左右及上下之和必须小于 100%。')
                margins[orientation] = values
        except (TypeError, ValueError, KeyError) as exc:
            if strict:
                raise ValueError(f'安全区「{name or key}」配置无效：{exc}') from exc
            continue
        names.add(name.casefold())
        result['labels'][key] = name
        result['presets'][key] = margins
    return result


def safe_rect(size, platform, *, presets=None):
    """返回逻辑画幅内的 l/t/r/b；按实际宽高选方向，任意比例和分辨率均适用。"""
    platform = str(platform or 'off').strip()
    width, height = size
    if platform == 'off' or min(width, height) <= 0:
        return None
    if presets is None:
        from .safe_area_options import current_options
        presets = current_options()['presets']
    if platform not in presets:
        return None
    orientation = 'landscape' if width >= height else 'portrait'
    left, top, right, bottom = presets[platform][orientation]
    return left * width, top * height, (1-right) * width, (1-bottom) * height


def layout_rect(rect, size=None):
    """留一个逻辑像素吸收旋转和合成取整，不改变任何图层大小。"""
    left, top, right, bottom = rect
    inset = min(1., (right-left)/4, (bottom-top)/4)
    # 0% 的边保持画幅边界，不暗中追加左右（或上下）留边。
    return (left+(inset if left != 0 else 0), top+(inset if top != 0 else 0),
            right-(inset if size is None or right != size[0] else 0),
            bottom-(inset if size is None or bottom != size[1] else 0))


def clamp_start(start, extent, low, high, anchor=.5):
    # 内容本身放不下时保留尺寸，按固定边溢出；用户仍可用统一缩放调节。
    if extent > high-low:
        return low + (high-low-extent)*anchor
    return min(max(start, low), high-extent)


def fit_layers(layers, rect, *, anchor=(.5, .5)):
    """仅整体平移一个布局块；绝不调整字号、图像大小或 effective_scale。"""
    from .layout import bounds
    visible = [layer for layer in layers if layer.item.get('opacity', 100) > 0]
    if rect is None or not visible:
        return layers
    left, top, right, bottom = bounds(visible)
    sl, st, sr, sb = rect
    dx = clamp_start(left, right-left, sl, sr, anchor[0])-left
    dy = clamp_start(top, bottom-top, st, sb, anchor[1])-top
    return [replace(layer, center=(layer.center[0]+dx, layer.center[1]+dy)) for layer in layers]


def arrange_free_layers(layers, rect, size):
    """无显式 Layout 的相邻元素保持为一块，避免旧模板多行文字挤到同一底边。"""
    from .layout import bounds
    blocks = []
    distance = min(size)*.03
    for layer in layers:
        box = bounds([layer])
        touching = []
        for block in blocks:
            # 用真实成员边界连接，背景不参加，远处编号不会牵动底部信息块。
            if any(max(box[0]-b[2], b[0]-box[2], 0) <= distance and
                   max(box[1]-b[3], b[1]-box[3], 0) <= distance
                   for b in (bounds([member]) for member in block)):
                touching.append(block)
        merged = [layer]
        for block in touching:
            merged.extend(block)
            blocks.remove(block)
        blocks.append(merged)
    result = {}
    for block in blocks:
        box = bounds(block)
        anchor = tuple(0 if (box[i]+box[i+2])/2 < size[i]/3 else
                       1 if (box[i]+box[i+2])/2 > size[i]*2/3 else .5 for i in (0, 1))
        for layer in fit_layers(block, rect, anchor=anchor):
            result[layer.item['id']] = layer
    return result


def fit_background(layer, rect):
    """背景是可伸缩容器，独立收边，不让满幅渐变触发内容缩小。"""
    from .layout import bounds
    left, top, right, bottom = bounds([layer])
    width, height = rect[2]-rect[0], rect[3]-rect[1]
    if layer.rotation:
        scale = min(1., width/max(.001, right-left), height/max(.001, bottom-top))
        size = tuple(value*scale for value in layer.size)
    else:
        size = (min(layer.size[0], width), min(layer.size[1], height))
    return fit_layers([replace(layer, size=size)], rect)[0]


def guide_box(size, platform, crop=None):
    """将输出安全框映射回保留裁切外画布的预览归一化坐标。"""
    rect = safe_rect(size, platform)
    if rect is None:
        return None
    left, top, right, bottom = crop or (0., 0., 1., 1.)
    return (left + rect[0]/size[0]*(right-left), top + rect[1]/size[1]*(bottom-top),
            left + rect[2]/size[0]*(right-left), top + rect[3]/size[1]*(bottom-top))
