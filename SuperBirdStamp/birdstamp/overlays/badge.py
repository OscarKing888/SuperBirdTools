"""圆角徽章的数据解析、共享稀有度配色及 Pillow 绘制（无 Qt）。"""
from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
import sys

from PIL import Image, ImageColor, ImageDraw
from app_common.bird_rarity import (
    RARITY_FIELD, RARITY_SOURCE_FIELD, RARITY_COMPAT_FIELDS,
    normalize_rarity_options, rarity_level, rarity_metadata,
)
from .model import number


def _color(value, default):
    try:
        rgb = ImageColor.getrgb(str(value or default))[:3]
        return '#{:02X}{:02X}{:02X}'.format(*rgb)
    except ValueError:
        return default


def normalize_badge(raw):
    raw = raw if isinstance(raw, dict) else {}
    return dict(
        badge_color_mode='custom' if raw.get('badge_color_mode') == 'custom' else 'auto',
        badge_background=_color(raw.get('badge_background'), '#64748B'),
        color=_color(raw.get('color'), '#FFFFFF'),
        badge_text_format='raw' if raw.get('badge_text_format') == 'raw' else 'auto',
        badge_padding_x=number(raw.get('badge_padding_x'), .5, 0, 2),
        badge_padding_y=number(raw.get('badge_padding_y'), .25, 0, 2),
        badge_radius=number(raw.get('badge_radius'), .35, 0, .5),
    )


def is_rarity_badge(item):
    if item.get('text_mode') != 'metadata':
        return False
    key = str(item.get('text_source', {}).get('key', '')).strip().strip('{}')
    return key in (RARITY_FIELD, 'report.' + RARITY_FIELD, 'XMP-superpicky:' + RARITY_FIELD,
                   RARITY_COMPAT_FIELDS[RARITY_FIELD], 'XMP-Iptc4xmpExt:Event')


def palette_paths(*, frozen=None, executable=None, source_root=None):
    """只查找当前安装旁的 Viewer 配置，不扫描磁盘或导入 Viewer/Qt。"""
    from app_common.superviewer_user_options import get_user_options_path, USER_OPTIONS_FILENAME
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if not frozen:
        root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[3]
        paths = [root / 'SuperViewer' / USER_OPTIONS_FILENAME]
    else:
        exe = Path(executable or sys.executable)
        bundle = next((p for p in exe.parents if p.suffix.lower() == '.app'), None)
        if bundle:
            paths = [bundle.parent / 'SuperViewer.app' / 'Contents' / 'MacOS' / USER_OPTIONS_FILENAME]
        else:
            # Windows merged onedir / 独立目录及同目录可执行文件布局。
            paths = [exe.parent.parent / 'SuperViewer' / USER_OPTIONS_FILENAME,
                     exe.parent / 'SuperViewer' / USER_OPTIONS_FILENAME,
                     exe.parent / USER_OPTIONS_FILENAME]
    paths.append(Path(get_user_options_path()))
    return list(dict.fromkeys(paths))


@lru_cache(maxsize=8)
def _read_palette(path, mtime_ns, size):
    # 时间戳/大小参与 key；每次绘制只做 stat，文件变化才重新解析 UTF-8。
    try:
        return normalize_rarity_options(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning('读取稀有度徽章配色失败 %s: %s', path, exc)
        return None


def load_badge_palette():
    for path in palette_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        palette = _read_palette(str(path), stat.st_mtime_ns, stat.st_size)
        if palette is not None:
            return dict(palette)
    from app_common.superviewer_user_options import get_runtime_user_options
    return normalize_rarity_options(get_runtime_user_options())


def badge_content(t, item, photo_info, raw_metadata, palette):
    """普通字段沿用 provider 优先级；稀有度只对明确选中的字段做等级映射。"""
    if item['text_mode'] == 'literal':
        text = item['text']
    else:
        source = item['text_source']
        provider = t.build_template_context_provider(source['type'], source['key'], display_label=item['name'])
        text = t._resolve_template_field_text(provider, photo_info)
    background, foreground = item['badge_background'], item['color']
    if is_rarity_badge(item):
        score = text
        source = item['text_source']
        # 自动 GBIF 字段复用 Viewer 的兼容 XMP 和失效标记，避免旧鸟种等级回填。
        if source['type'] == 'auto' and source['key'].strip().strip('{}') == RARITY_FIELD:
            from birdstamp.gui.template_context import _metadata_with_xmp_priority
            metadata = _metadata_with_xmp_priority(photo_info)
            keys = (RARITY_FIELD, 'report.' + RARITY_FIELD, 'XMP-superpicky:' + RARITY_FIELD,
                    RARITY_SOURCE_FIELD, 'XMP-superpicky:' + RARITY_SOURCE_FIELD,
                    RARITY_COMPAT_FIELDS[RARITY_FIELD], 'XMP-Iptc4xmpExt:Event')
            if any(key in metadata for key in keys):
                score = rarity_metadata(metadata)[0]
                text = f'{score:g}' if score is not None else '未知'
        prefix = f'rarity_badge_{rarity_level(score)}_'
        if item['badge_text_format'] == 'auto':
            text = palette[prefix + 'text']
        if item['badge_color_mode'] == 'auto':
            background, foreground = palette[prefix + 'background'], palette[prefix + 'foreground']
    return text, background, foreground


def render_badge(t, item, size, photo_info, raw_metadata, text_scale):
    text, background, foreground = badge_content(t, item, photo_info, raw_metadata, load_badge_palette())
    if not text:
        return None
    factor = t._template_font_scale_for_canvas(*size) * text_scale * item['scale']
    font = t.load_font(t.template_font_path_from_type(item.get('font_type')),
                       max(1, round(item['font_size'] * factor)))
    with Image.new('RGB', (1, 1)) as probe:
        text, box = t._measure_text_with_fallback(ImageDraw.Draw(probe), text, font=font)
    pixels, _ = t.styled_text_layer(text, box, font=font, color=foreground, style=item['style'],
        effects=t.normalize_text_effects(item), scale=font.size / item['font_size'])
    try:
        px = round(font.size * item['badge_padding_x'])
        py = round(font.size * item['badge_padding_y'])
        width, height = pixels.width + 2*px, pixels.height + 2*py
        # 局部蒙版超采样，缩略预览和原尺寸导出均有平滑圆角。
        antialias = 3
        with Image.new('L', (width*antialias, height*antialias)) as mask:
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width-1, mask.height-1),
                radius=min(height*item['badge_radius'], width/2)*antialias, fill=255)
            with mask.resize((width, height), Image.Resampling.LANCZOS) as alpha:
                result = Image.new('RGBA', (width, height), background)
                result.putalpha(alpha)
        result.alpha_composite(pixels, (px, py))
        return result
    finally:
        pixels.close()
