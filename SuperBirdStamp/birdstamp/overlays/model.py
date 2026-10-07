"""叠加层文档和旧模板适配。读取旧格式不会写回磁盘。"""
from __future__ import annotations

from copy import deepcopy
import math
import uuid
from PIL import ImageColor

VERSION = 1


def number(value, default=0.0, low=-10000.0, high=10000.0):
    try:
        value = float(value)
        return max(low, min(high, value)) if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def flag(value, default=True):
    return default if value is None else str(value).lower() in ('true', '1', 'yes', 'on')


def normalize_item(raw, index=0):
    from birdstamp.gui.editor_template import normalize_template_field
    kind = raw.get('type', 'text')
    if kind not in ('text', 'badge', 'image', 'background'):
        raise ValueError(f'不支持的叠加层类型: {kind}')
    item = deepcopy(raw)
    if kind == 'image':
        try:
            rgb = ImageColor.getrgb(str(raw.get('tint_color') or '#FFFFFF'))[:3]
        except ValueError:
            rgb = (255, 255, 255)
        item.update(tint_enabled=flag(raw.get('tint_enabled'), False),
                    tint_color='#{:02X}{:02X}{:02X}'.format(*rgb))
    if kind == 'background':
        from birdstamp.gui import editor_template as t
        item.update(banner_background_style=t._normalize_banner_background_style(raw.get('banner_background_style')),
                    banner_color=t.normalize_template_banner_color(raw.get('banner_color')),
                    banner_gradient_height_pct=t._normalize_banner_gradient_height_pct(raw.get('banner_gradient_height_pct')),
                    banner_gradient_top_opacity_pct=t._normalize_banner_gradient_top_opacity_pct(raw.get('banner_gradient_top_opacity_pct')),
                    banner_gradient_bottom_opacity_pct=t._normalize_banner_gradient_bottom_opacity_pct(raw.get('banner_gradient_bottom_opacity_pct')),
                    banner_gradient_top_color=t._normalize_banner_gradient_color(raw.get('banner_gradient_top_color'),'#000000'),
                    banner_gradient_bottom_color=t._normalize_banner_gradient_color(raw.get('banner_gradient_bottom_color'),'#000000'))
    if kind in ('text', 'badge'):
        item.update(normalize_template_field(raw, index))
        item['text'] = str(raw.get('text', ''))
        item['text_mode'] = 'literal' if raw.get('text_mode') == 'literal' else 'metadata'
    if kind == 'badge':
        from .badge import normalize_badge
        item.update(normalize_badge(raw))
    item.update(id=str(raw.get('id') or f'legacy-{index}'), type=kind,
                name=str(raw.get('name') or {'text': '文本', 'badge': '圆角 Badge', 'image': '图像', 'background': '背景'}[kind]),
                visible=flag(raw.get('visible')), locked=flag(raw.get('locked'), False),
                layout_mode='manual' if raw.get('layout_mode') == 'manual' else 'auto',
                x=number(raw.get('x'), .5), y=number(raw.get('y'), .5),
                scale=number(raw.get('scale'), 1, .001, 100),
                rotation=number(raw.get('rotation'), 0) % 360,
                opacity=number(raw.get('opacity'), 100, 0, 100),
                width=number(raw.get('width'), .3, .001, 10),
                height=number(raw.get('height'), .25, .001, 10))
    item.setdefault('align_horizontal', 'center')
    item.setdefault('align_vertical', 'center')
    item.setdefault('x_offset_pct', 0)
    item.setdefault('y_offset_pct', 0)
    return item


def document(payload):
    """返回独立叠加文档；旧 id 由序号稳定产生，不在每次渲染生成随机 id。"""
    if 'overlays' in payload:
        if payload.get('overlay_version', VERSION) != VERSION:
            raise ValueError('不支持的叠加层版本')
        raw_items = payload['overlays']
        if not isinstance(raw_items, list):
            raise ValueError('overlays 必须为列表')
    else:
        background = {key: deepcopy(value) for key, value in payload.items() if key.startswith('banner_')}
        background.update(id='legacy-background', type='background', name='Banner 背景',
                          locked=True, visible=flag(payload.get('draw_banner_background')))
        raw_items = [background] + [dict(item, type='text', id=f'legacy-text-{i}')
                                   for i, item in enumerate(payload.get('fields', [])) if isinstance(item, dict)]
    items, ids = [], set()
    for index, raw in enumerate(raw_items):
        item = normalize_item(raw, index)
        if item['id'] in ids:
            raise ValueError(f'叠加层 ID 重复: {item["id"]}')
        ids.add(item['id'])
        items.append(item)
    assets = payload.get('overlay_assets') or {}
    references = {i.get('asset_id') for i in items if i['type']=='image'}
    from .layout import normalize_layouts
    result = dict(overlay_version=VERSION, overlays=items,
                  overlay_assets={k:deepcopy(v) for k,v in assets.items() if k in references})
    layouts = normalize_layouts(payload.get('overlay_layouts'), items)
    if layouts:
        result['overlay_layouts'] = layouts
    return result


def with_document(payload, doc):
    result = deepcopy(payload)
    # 新文档只保留一份叠加数据，裁切和其它模板设置不变。
    for key in list(result):
        if key == 'fields' or key == 'draw_banner_background' or key.startswith('banner_'):
            result.pop(key)
    result.pop('overlay_layouts', None)
    result.update(document(doc))
    return result


def effective_payload(payload, override):
    return with_document(payload, override) if isinstance(override, dict) else payload


def new_item(kind='text', *, metadata=False):
    from birdstamp.gui.editor_options import TEXT_EFFECT_DEFAULTS, OVERLAY_NEW_TEXT_SIZE, OVERLAY_IMAGE_WIDTH_PCT
    if kind == 'badge':
        from birdstamp.gui.editor_options import BADGE_DEFAULTS
        return normalize_item(dict(TEXT_EFFECT_DEFAULTS, **BADGE_DEFAULTS,
            id=uuid.uuid4().hex, type=kind, name='圆角 Badge',
            text_mode='metadata', text='自定义徽章',
            text_source={'type': 'auto', 'key': 'gbif_rarity_100'},
            font_size=OVERLAY_NEW_TEXT_SIZE, layout_mode='manual'))
    return normalize_item(dict(TEXT_EFFECT_DEFAULTS, id=uuid.uuid4().hex, type=kind,
                               name='元数据文本' if metadata else {'text': '自定义文本', 'image': '图像', 'background': '背景'}[kind],
                               text_mode='metadata' if metadata else 'literal', text='自定义文本',
                               text_source={'type': 'auto', 'key': '{bird}'}, font_size=OVERLAY_NEW_TEXT_SIZE,
                               width=OVERLAY_IMAGE_WIDTH_PCT/100,
                               layout_mode='auto' if metadata else 'manual'))


def clone_override(value):
    return document(value) if isinstance(value, dict) else None


def pack_assets(payload):
    """工作区全局去重；内存快照仍自包含，导出线程不依赖窗口素材库。"""
    result = deepcopy(payload)
    assets = {}
    def walk(value):
        if isinstance(value, dict):
            own = value.pop('overlay_assets', None)
            if own:
                assets.update(own)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(result)
    if assets:
        result['overlay_assets'] = assets
    return result


def unpack_assets(payload):
    result = deepcopy(payload)
    assets = result.get('overlay_assets') or {}
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('overlays'), list):
                refs = {i.get('asset_id') for i in value['overlays'] if isinstance(i, dict)}
                value['overlay_assets'] = {k: v for k, v in assets.items() if k in refs} | value.get('overlay_assets', {})
            for key, child in list(value.items()):
                if key != 'overlay_assets':
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(result)
    return result
