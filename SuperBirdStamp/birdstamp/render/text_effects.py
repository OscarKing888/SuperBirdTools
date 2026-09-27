"""模板文字效果的规范参数和 Pillow 图层；预览、CLI 与导出共用。"""
from __future__ import annotations

import math
from PIL import Image, ImageColor, ImageDraw, ImageFilter

DEFAULT_TEXT_EFFECTS = {
    'stroke_enabled': False, 'stroke_color': '#000000', 'stroke_width': 2.0,
    'shadow_enabled': False, 'shadow_color': '#000000', 'shadow_opacity': 70.0,
    'shadow_offset_x': 3.0, 'shadow_offset_y': 3.0, 'shadow_blur': 3.0,
}
TEXT_EFFECT_RANGES = {
    'stroke_width': (0.0, 20.0), 'shadow_opacity': (0.0, 100.0),
    'shadow_offset_x': (-100.0, 100.0), 'shadow_offset_y': (-100.0, 100.0),
    'shadow_blur': (0.0, 50.0),
}


def normalize_text_effects(data):
    result = dict(DEFAULT_TEXT_EFFECTS)
    for key, default in result.items():
        value = data.get(key, default)
        if key.endswith('_enabled'):
            result[key] = str(value).lower() in {'true', '1', 'yes', 'on'}
        elif key.endswith('_color'):
            try:
                rgb = ImageColor.getrgb(str(value))
                result[key] = '#%02X%02X%02X' % rgb[:3]
            except (ValueError, TypeError):
                pass
        else:
            try:
                number = float(value)
                if math.isfinite(number):
                    low, high = TEXT_EFFECT_RANGES[key]
                    result[key] = max(low, min(high, number))
            except (ValueError, TypeError):
                pass
    return result


def effect_geometry(effects, scale):
    stroke = max(1, round(effects['stroke_width'] * scale)) if effects['stroke_enabled'] and effects['stroke_width'] else 0
    shadow = effects['shadow_enabled'] and effects['shadow_opacity'] > 0
    dx = round(effects['shadow_offset_x'] * scale) if shadow else 0
    dy = round(effects['shadow_offset_y'] * scale) if shadow else 0
    blur = effects['shadow_blur'] * scale if shadow else 0
    spread = math.ceil(blur * 3)
    margins = (stroke + max(0, spread-dx), stroke + max(0, spread-dy),
               stroke + max(0, spread+dx), stroke + max(0, spread+dy))
    return stroke, dx, dy, blur, margins


def styled_text_layer(text, box, *, font, color, style, effects, scale):
    """返回局部透明图层及文字原点；只处理字形周围像素，不建立全画幅蒙版。"""
    left, top, right, bottom = box
    stroke, dx, dy, blur, margins = effect_geometry(effects, scale)
    ml, mt, mr, mb = margins
    layer = Image.new('RGBA', (max(1, right-left)+10+ml+mr, max(1, bottom-top)+10+mt+mb))
    draw = ImageDraw.Draw(layer)
    offsets = [(0, 0), (1, 0), (0, 1)] if style in {'bold', 'bold_italic'} else [(0, 0)]
    # 先完整绘制所有描边，再绘制正文，避免粗体的第二笔覆盖前一笔正文。
    for ox, oy in offsets:
        draw.text((5+ml-left+ox, 5+mt-top+oy), text, font=font, fill=color,
                  stroke_width=stroke, stroke_fill=effects['stroke_color'])
    if stroke:
        for ox, oy in offsets:
            draw.text((5+ml-left+ox, 5+mt-top+oy), text, font=font, fill=color)
    if style in {'italic', 'bold_italic'}:
        shear = -0.28
        transformed = layer.transform((max(1, round(layer.width+abs(shear)*layer.height)), layer.height),
                                      Image.Transform.AFFINE, (1, shear, 0, 0, 1, 0),
                                      resample=Image.Resampling.BICUBIC)
        layer.close()
        layer = transformed
    if effects['shadow_enabled'] and effects['shadow_opacity'] > 0:
        alpha = layer.getchannel('A')
        if blur:
            blurred = alpha.filter(ImageFilter.GaussianBlur(blur))
            alpha.close()
            alpha = blurred
        opacity = effects['shadow_opacity'] / 100
        mask = alpha.point([round(i*opacity) for i in range(256)])
        alpha.close()
        shadow = Image.new('RGBA', layer.size, effects['shadow_color'])
        shadow.putalpha(mask)
        mask.close()
        combined = Image.new('RGBA', layer.size)
        combined.alpha_composite(shadow, (dx, dy))
        combined.alpha_composite(layer)
        shadow.close()
        layer.close()
        layer = combined
    return layer, (5+ml, 5+mt)
