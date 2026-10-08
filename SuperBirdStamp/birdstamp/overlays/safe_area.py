"""全屏发布的叠加安全区；无 Qt，几何与图层变换供预览和导出共用。"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite

PLATFORMS = ('off', 'xiaohongshu', 'bilibili', 'douyin')
LABELS = ('关闭（非全屏）', '小红书', 'B站', '抖音')
# 项目保守预设，不是平台保证。顺序为左、上、右、下占画幅比例。
# 可通过 editor_options.json 调整；横屏与竖屏分别预留工具栏/互动区。
DEFAULT_PRESETS = {
    'xiaohongshu': {'portrait': (.03, .12, .03, .21), 'landscape': (.03, .08, .03, .20)},
    'bilibili': {'portrait': (.06, .10, .16, .20), 'landscape': (.05, .12, .05, .14)},
    'douyin': {'portrait': (.06, .12, .18, .24), 'landscape': (.06, .10, .12, .18)},
}


def normalize_platform(value):
    value = str(value or 'off').strip().lower()
    return value if value in PLATFORMS else 'off'


def normalize_options(raw):
    raw = raw if isinstance(raw, dict) else {}
    labels = raw.get('labels') if isinstance(raw.get('labels'), dict) else {}
    presets = raw.get('presets') if isinstance(raw.get('presets'), dict) else {}
    result = {'labels': {key: str(labels.get(key) or label) for key, label in zip(PLATFORMS, LABELS)},
              'presets': {}}
    for platform, defaults in DEFAULT_PRESETS.items():
        settings = presets.get(platform)
        settings = settings if isinstance(settings, dict) else {}
        result['presets'][platform] = {}
        for orientation, fallback in defaults.items():
            try:
                values = tuple(float(v) for v in settings.get(orientation, fallback))
                valid = (len(values) == 4 and all(isfinite(v) and 0 <= v < .45 for v in values)
                         and values[0] + values[2] < .8 and values[1] + values[3] < .8)
            except (TypeError, ValueError):
                valid = False
            result['presets'][platform][orientation] = values if valid else fallback
    return result


def safe_rect(size, platform, *, presets=None):
    """返回逻辑画幅内的 l/t/r/b；按实际宽高选方向，任意比例和分辨率均适用。"""
    platform = normalize_platform(platform)
    width, height = size
    if platform == 'off' or min(width, height) <= 0:
        return None
    if presets is None:
        from birdstamp.gui.editor_options import PLATFORM_SAFE_AREA
        presets = PLATFORM_SAFE_AREA['presets']
    orientation = 'landscape' if width >= height else 'portrait'
    left, top, right, bottom = presets[platform][orientation]
    return left * width, top * height, (1-right) * width, (1-bottom) * height


def layout_rect(rect):
    """留一个逻辑像素吸收旋转和合成取整，不改变任何图层大小。"""
    left, top, right, bottom = rect
    inset = min(1., (right-left)/4, (bottom-top)/4)
    return left+inset, top+inset, right-inset, bottom-inset


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
