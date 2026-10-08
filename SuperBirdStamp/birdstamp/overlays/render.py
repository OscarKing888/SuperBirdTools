"""统一布局场景：Pillow 导出、预览合成和画布命中使用同一几何。"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from PIL import Image, ImageColor, ImageDraw
from .model import document, number
from .assets import decode_asset


@dataclass
class Layer:
    item: dict
    pixels: Image.Image
    center: tuple[float, float]
    size: tuple[float, float]
    rotation: float = 0
    effective_scale: float = 1

    def corners(self):
        c, s = math.cos(math.radians(self.rotation)), math.sin(math.radians(self.rotation))
        w, h = self.size
        x, y = self.center
        return tuple((x+c*dx-s*dy, y+s*dx+c*dy) for dx, dy in
                     ((-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2)))

    def contains(self, point):
        a = math.radians(-self.rotation)
        dx, dy = point[0]-self.center[0], point[1]-self.center[1]
        x, y = dx*math.cos(a)-dy*math.sin(a), dx*math.sin(a)+dy*math.cos(a)
        return abs(x) <= self.size[0]/2 and abs(y) <= self.size[1]/2

    def manual_item(self, canvas_size):
        item = dict(self.item, layout_mode='manual', x=self.center[0]/canvas_size[0],
                    y=self.center[1]/canvas_size[1], rotation=self.rotation)
        if item['type'] in ('text', 'badge'):
            item['scale'] = self.effective_scale
        else:
            item.update(width=self.size[0]/canvas_size[0], height=self.size[1]/canvas_size[1], scale=1)
        return item


@dataclass
class Scene:
    size: tuple[int, int]
    layers: list[Layer]

    def close(self):
        for layer in self.layers:
            layer.pixels.close()


def _text_layer(t, field, size, photo_info, raw_metadata, text_scale):
    effects = t.normalize_text_effects(field)
    if field.get('text_mode') == 'literal':
        text = field.get('text', '')
    else:
        source = field['text_source']
        provider = t.build_template_context_provider(source['type'], source['key'], display_label=field['name'])
        text = t._resolve_template_field_text(provider, photo_info)
    if not text:
        return None
    factor = t._template_font_scale_for_canvas(*size) * text_scale * field['scale']
    font = t.load_font(t.template_font_path_from_type(field.get('font_type')), max(1, round(field['font_size']*factor)))
    with Image.new('RGB', (1,1)) as probe:
        text, box = t._measure_text_with_fallback(ImageDraw.Draw(probe), text, font=font)
    pixels, _ = t.styled_text_layer(text, box, font=font, color=field['color'], style=field['style'],
                                    effects=effects, scale=font.size/field['font_size'])
    return Layer(field, pixels, (field['x']*size[0], field['y']*size[1]), pixels.size,
                 field['rotation'], field['scale'])


def build_scene(payload, size, *, raw_metadata=None, metadata_context=None, photo_info=None,
                text_scale=1.0, draw_text=True, draw_images=True, draw_banner=True, platform_safe_area='off'):
    from birdstamp.gui import editor_template as t
    from birdstamp.render.text_scale import normalize_text_scale
    doc = document(payload)
    from .safe_area import safe_rect, layout_rect, arrange_free_layers, fit_background
    area = safe_rect(size, platform_safe_area)
    area = layout_rect(area) if area else None
    if area:
        # 安全布局仅测量实际输出的元素，隐藏类别不留下间距。
        enabled = {'badge': draw_text, 'text': draw_text, 'image': draw_images, 'background': draw_banner}
        for item in doc['overlays']:
            item['visible'] = item['visible'] and enabled[item['type']] and item['opacity'] > 0
    raw_metadata = raw_metadata or {}
    photo_info = t.ensure_photo_info(photo_info or raw_metadata.get('SourceFile') or '.', raw_metadata=raw_metadata)
    text_scale = normalize_text_scale(text_scale)
    layers = {}
    auto = [i for i in doc['overlays'] if i['type']=='text' and i['visible'] and i['layout_mode']=='auto']
    from .layout import indexes, arrange
    _, grouped = indexes(doc)
    auto = [i for i in auto if i['id'] not in grouped]
    commands = []
    # 复用旧排版的逐字号避让，避免老模板的自动位置和字号变化。
    if auto:
        fields = [dict(i, font_size=i['font_size']*i['scale']) for i in auto]
        with Image.new('RGB', (1,1)) as probe:
            t._render_legacy_template_overlay(probe, raw_metadata=raw_metadata, metadata_context=metadata_context or {},
                photo_info=photo_info, template_payload={'fields': fields}, layout_size=size,
                text_scale=text_scale, _capture=commands)
    try:
        for field, command in commands:
            text, x, y, color, font, style, rect, effects, effect_scale = command
            original = next(i for i in auto if i['id']==field['id'])
            with Image.new('RGB', (1,1)) as probe:
                text, box = t._measure_text_with_fallback(ImageDraw.Draw(probe), text, font=font)
            pixels, origin = t.styled_text_layer(text, box, font=font, color=color, style=style, effects=effects, scale=effect_scale)
            base = original['font_size'] * t._template_font_scale_for_canvas(*size) * text_scale
            layers[field['id']] = Layer(original, pixels, (x-origin[0]+pixels.width/2, y-origin[1]+pixels.height/2),
                                        pixels.size, original['rotation'], font.size/max(.001,base))
        for item in doc['overlays']:
            if not item['visible']:
                continue
            kind = item['type']
            if kind == 'text' and (item['layout_mode'] == 'manual' or item['id'] in grouped):
                layer = _text_layer(t, item, size, photo_info, raw_metadata, text_scale)
                if layer:
                    layers[item['id']] = layer
            elif kind == 'badge':
                from .badge import render_badge
                pixels = render_badge(t, item, size, photo_info, raw_metadata, text_scale)
                if pixels is not None:
                    w, h = pixels.size
                    if item['layout_mode']=='auto':
                        x,y = t._compute_template_text_position(canvas_width=size[0],canvas_height=size[1],
                            text_width=w,text_height=h,align_h=item['align_horizontal'],align_v=item['align_vertical'],
                            x_offset_pct=number(item['x_offset_pct'])/100,y_offset_pct=number(item['y_offset_pct'])/100)
                        center = (x+w/2,y+h/2)
                    else:
                        center = (item['x']*size[0],item['y']*size[1])
                    layers[item['id']] = Layer(item,pixels,center,(w,h),item['rotation'],item['scale'])
            elif kind == 'image':
                pixels = decode_asset(item.get('asset_id'), doc['overlay_assets'])
                if item['tint_enabled']:
                    # 仅替换图层副本的 RGB，保留透明边缘和素材缓存中的原色。
                    with pixels.getchannel('A') as alpha:
                        pixels.paste(item['tint_color'], (0, 0, pixels.width, pixels.height))
                        pixels.putalpha(alpha)
                w = item['width']*size[0]*item['scale']
                h = w*pixels.height/pixels.width
                if item['layout_mode']=='auto':
                    x,y = t._compute_template_text_position(canvas_width=size[0],canvas_height=size[1],
                        text_width=round(w),text_height=round(h),align_h=item['align_horizontal'],align_v=item['align_vertical'],
                        x_offset_pct=number(item['x_offset_pct'])/100,y_offset_pct=number(item['y_offset_pct'])/100)
                    center = (x+w/2,y+h/2)
                else:
                    center = (item['x']*size[0],item['y']*size[1])
                layers[item['id']] = Layer(item,pixels,center,(w,h),item['rotation'])
        arrange(doc, layers, size, rect=area)
        if area:
            free = [layer for key, layer in layers.items() if key not in grouped]
            layers.update(arrange_free_layers(free, area, size))
        text_layers = [v for v in layers.values() if v.item['type']=='text']
        for item in doc['overlays']:
            if item['type']!='background' or not item['visible']:
                continue
            gradient = item.get('banner_background_style') == 'gradient_bottom'
            if item['layout_mode']=='manual':
                w,h = item['width']*size[0]*item['scale'],item['height']*size[1]*item['scale']
                center = (item['x']*size[0],item['y']*size[1])
            else:
                if not text_layers:
                    continue
                boxes = [(min(p[0] for p in v.corners()),min(p[1] for p in v.corners()),
                          max(p[0] for p in v.corners()),max(p[1] for p in v.corners())) for v in text_layers]
                rect = (t._compute_template_bottom_gradient_rect(canvas_width=size[0],canvas_height=size[1],
                        height_pct=number(item.get('banner_gradient_height_pct'),30,10,100)) if gradient else
                        t._compute_template_banner_rect(text_boxes=boxes,canvas_width=size[0],canvas_height=size[1]))
                if rect is None:
                    continue
                l,top,r,b = rect
                w,h = r-l,b-top
                center = ((l+r)/2,(top+b)/2)
            if area and item['layout_mode'] != 'manual' and gradient:
                # 渐变跟随安全区底边，背景宽度独立收边，内容尺寸保持不变。
                w, h = area[2]-area[0], min(h, area[3]-area[1])
                center = ((area[0]+area[2])/2, area[3]-h/2)
            if gradient:
                height = max(2,round(h))
                top = ImageColor.getrgb(item.get('banner_gradient_top_color','#000000'))[:3]
                bottom = ImageColor.getrgb(item.get('banner_gradient_bottom_color','#000000'))[:3]
                top = (*top, round(number(item.get('banner_gradient_top_opacity_pct'),0,0,100)*2.55))
                bottom = (*bottom, round(number(item.get('banner_gradient_bottom_opacity_pct'),62,0,100)*2.55))
                pixels = Image.new('RGBA',(1,height))
                pixels.putdata([tuple(round(a+(b-a)*y/(height-1)) for a,b in zip(top,bottom)) for y in range(height)])
            else:
                color = t.template_banner_fill_color(item.get('banner_color'))
                if not color:
                    continue
                pixels = Image.new('RGBA',(1,1),color)
            layer = Layer(item,pixels,center,(w,h),item['rotation'])
            layers[item['id']] = fit_background(layer, area) if area else layer
        result = []
        for item in doc['overlays']:
            layer = layers.pop(item['id'],None)
            if layer is None:
                continue
            enabled = {'badge':draw_text,'text':draw_text,'image':draw_images,'background':draw_banner}[item['type']]
            if enabled:
                result.append(layer)
            else:
                layer.pixels.close()
        return Scene(size, result)
    except Exception:
        for layer in layers.values():
            layer.pixels.close()
        raise


def compose_scene(image, scene):
    """仅缩放局部图层；同一逻辑画幅映射到预览或最终输出。"""
    result = image.copy()
    sx,sy = image.width/scene.size[0], image.height/scene.size[1]
    from birdstamp.gui.editor_template import _composite_rgba_layer
    for layer in scene.layers:
        # 先在逻辑像素比例下旋转，随后映射至显示画布，避免非等比取整改变角度。
        scale = max(sx,sy)
        w,h = max(1,round(layer.size[0]*scale)),max(1,round(layer.size[1]*scale))
        pixels = layer.pixels.resize((w,h),Image.Resampling.LANCZOS)
        if layer.item['opacity'] < 100:
            alpha = pixels.getchannel('A').point(lambda v: round(v*layer.item['opacity']/100))
            pixels.putalpha(alpha)
            alpha.close()
        if layer.rotation:
            rotated = pixels.rotate(-layer.rotation,resample=Image.Resampling.BICUBIC,expand=True)
            pixels.close()
            pixels = rotated
        target = (max(1,round(pixels.width*sx/scale)), max(1,round(pixels.height*sy/scale)))
        if pixels.size != target:
            resized = pixels.resize(target,Image.Resampling.LANCZOS)
            pixels.close()
            pixels = resized
        _composite_rgba_layer(result,pixels,(round(layer.center[0]*sx-pixels.width/2),round(layer.center[1]*sy-pixels.height/2)))
        pixels.close()
    return result
