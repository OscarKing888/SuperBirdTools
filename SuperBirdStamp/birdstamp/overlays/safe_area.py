"""全屏发布的叠加安全区；无 Qt，几何与图层变换供预览和导出共用。"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite

PLATFORMS = ('off', 'xiaohongshu', 'bilibili', 'douyin')
LABELS = ('关闭（非全屏）', '小红书', 'B站', '抖音')
# 项目保守预设，不是平台保证。顺序为左、上、右、下占画幅比例。
# 可通过 editor_options.json 调整；横屏与竖屏分别预留工具栏/互动区。
DEFAULT_PRESETS = {
    'xiaohongshu': {'portrait': (.06, .12, .16, .22), 'landscape': (.06, .08, .10, .16)},
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


def fit_layers(layers, rect):
    """可见图层作为一个整体，先必要缩小，再以最小平移装入安全区。"""
    visible = [layer for layer in layers if layer.item.get('opacity', 100) > 0]
    if rect is None or not visible:
        return layers
    points = [point for layer in visible for point in layer.corners()]
    left, top = min(p[0] for p in points), min(p[1] for p in points)
    right, bottom = max(p[0] for p in points), max(p[1] for p in points)
    sl, st, sr, sb = rect
    # 留一个逻辑像素吸收旋转扩展和合成取整，避免边缘溢出。
    inset = min(1., (sr-sl)/4, (sb-st)/4)
    sl, st, sr, sb = sl+inset, st+inset, sr-inset, sb-inset
    scale = min(1., (sr-sl)/max(.001, right-left), (sb-st)/max(.001, bottom-top))
    cx, cy = (left+right)/2, (top+bottom)/2
    left, right = cx+(left-cx)*scale, cx+(right-cx)*scale
    top, bottom = cy+(top-cy)*scale, cy+(bottom-cy)*scale
    dx = min(max(0., sl-left), sr-right)
    dy = min(max(0., st-top), sb-bottom)
    return [replace(layer, center=(cx+(layer.center[0]-cx)*scale+dx, cy+(layer.center[1]-cy)*scale+dy),
                    size=(layer.size[0]*scale, layer.size[1]*scale),
                    effective_scale=layer.effective_scale*scale) for layer in layers]


def guide_box(size, platform, crop=None):
    """将输出安全框映射回保留裁切外画布的预览归一化坐标。"""
    rect = safe_rect(size, platform)
    if rect is None:
        return None
    left, top, right, bottom = crop or (0., 0., 1., 1.)
    return (left + rect[0]/size[0]*(right-left), top + rect[1]/size[1]*(bottom-top),
            left + rect[2]/size[0]*(right-left), top + rect[3]/size[1]*(bottom-top))
