"""拼图的无窗口布局、渲染与原子导出；GUI 和 CLI 使用同一入口。"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import tempfile
from typing import Callable, Sequence

from PIL import Image, ImageColor, ImageOps

PRESETS = {
    'vertical': ('纵向长图', 1, 0),
    'horizontal': ('横向拼图', 0, 1),
    'grid4': ('4 宫格 · 2 × 2', 2, 2),
    'grid6': ('6 宫格 · 3 × 2', 3, 2),
    'grid9': ('9 宫格 · 3 × 3', 3, 3),
    'grid16': ('16 宫格 · 4 × 4', 4, 4),
}
MAX_IMAGES = 64
MAX_PIXELS = 64_000_000
MAX_EDGE = 32768


class CollageCancelled(Exception):
    """用户取消；不发布不完整的输出。"""


@dataclass(frozen=True)
class CollageOptions:
    preset: str = 'vertical'
    cell_size: int = 800
    gap: int = 12
    margin: int = 12
    background: str = '#FFFFFF'
    fit: str = 'contain'

    def __post_init__(self):
        if self.preset not in PRESETS:
            raise ValueError('请选择有效的拼图排版')
        for name, low, high in (('cell_size', 64, 4096), ('gap', 0, 256), ('margin', 0, 256)):
            value = getattr(self, name)
            if not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f'{name} 必须在 {low}–{high} 之间')
        if self.fit not in ('contain', 'cover'):
            raise ValueError('请选择完整显示或居中裁满')
        ImageColor.getrgb(self.background)


@dataclass(frozen=True)
class CollagePlan:
    size: tuple[int, int]
    boxes: tuple[tuple[int, int, int, int], ...]


def plan_collage(sizes: Sequence[tuple[int, int]], options: CollageOptions) -> CollagePlan:
    count = len(sizes)
    if not 1 <= count <= MAX_IMAGES:
        raise ValueError(f'请添加 1–{MAX_IMAGES} 张照片')
    if any(w <= 0 or h <= 0 for w, h in sizes):
        raise ValueError('照片尺寸无效')
    _, cols, rows = PRESETS[options.preset]
    if cols and rows and count > cols * rows:
        raise ValueError(f'此排版最多容纳 {cols * rows} 张，当前有 {count} 张；请移除多余照片或更换排版')
    side, gap, margin = options.cell_size, options.gap, options.margin
    boxes = []
    if options.preset in ('vertical', 'horizontal'):
        vertical = options.preset == 'vertical'
        offset = margin
        for width, height in sizes:
            w, h = (side, max(1, round(side * height / width))) if vertical else (max(1, round(side * width / height)), side)
            x, y = (margin, offset) if vertical else (offset, margin)
            boxes.append((x, y, x + w, y + h))
            offset += (h if vertical else w) + gap
        size = (side + 2 * margin, offset - gap + margin) if vertical else (offset - gap + margin, side + 2 * margin)
    else:
        size = (cols * side + (cols - 1) * gap + 2 * margin, rows * side + (rows - 1) * gap + 2 * margin)
        for index in range(count):
            x = margin + (index % cols) * (side + gap)
            y = margin + (index // cols) * (side + gap)
            boxes.append((x, y, x + side, y + side))
    if max(size) > MAX_EDGE or size[0] * size[1] > MAX_PIXELS:
        raise ValueError('拼图过大（最多 6400 万像素，单边 32768 px），请减小单格尺寸或照片数量')
    return CollagePlan(size, tuple(boxes))


def _check_cancel(cancel):
    if cancel():
        raise CollageCancelled()


def render_collage(paths: Sequence[Path], options: CollageOptions, *, preview_edge: int | None = None,
                   cancel: Callable[[], bool] = lambda: False,
                   progress: Callable[[int, int], None] = lambda done, total: None):
    """返回 (图像, 原生布局)。逐张解码，预览按最终画幅缩小，输出只持有一张源图。"""
    from birdstamp.decoders.image_decoder import decode_image, decode_image_for_preview
    if not 1 <= len(paths) <= MAX_IMAGES:
        raise ValueError(f'请添加 1–{MAX_IMAGES} 张照片')
    _, cols, rows = PRESETS[options.preset]
    if cols and rows and len(paths) > cols * rows:
        plan_collage([(1, 1)] * len(paths), options)
    sizes, alpha_flags = [], []
    for path in paths:
        _check_cancel(cancel)
        with decode_image_for_preview(Path(path), max_long_edge=64) as probe:
            size = probe.info.get('birdstamp_source_properties', {}).get('size', probe.size)
            sizes.append(tuple(size))
            alpha_flags.append(probe.info.get('birdstamp_source_properties', {}).get('has_alpha', False))
    plan = plan_collage(sizes, options)
    scale = min(1.0, max(1, preview_edge) / max(plan.size)) if preview_edge is not None else 1.0
    size = tuple(max(1, round(v * scale)) for v in plan.size)
    canvas = Image.new('RGB', size, options.background)
    try:
        for index, (path, box, source_size, has_alpha) in enumerate(zip(paths, plan.boxes, sizes, alpha_flags)):
            _check_cancel(cancel)
            x, y, right, bottom = (round(v * scale) for v in box)
            target = (max(1, right - x), max(1, bottom - y))
            limit = None
            if preview_edge is not None:
                factor = max(target[0] / source_size[0], target[1] / source_size[1])
                limit = min(4096, max(64, math.ceil(max(source_size) * factor)))
            if has_alpha:
                # 照片解码器统一输出 RGB；拼图素材需将透明区留给用户选择的背景。
                with Image.open(path) as original:
                    if limit is not None:
                        original.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                    ImageOps.exif_transpose(original, in_place=True)
                    source = original.convert('RGBA')
            elif limit is None:
                source = decode_image(Path(path))
            else:
                source = decode_image_for_preview(Path(path), max_long_edge=limit)
            try:
                method = ImageOps.fit if options.fit == 'cover' and cols and rows else ImageOps.contain
                tile = method(source, target, method=Image.Resampling.LANCZOS)
                try:
                    position = (x + (target[0] - tile.width) // 2, y + (target[1] - tile.height) // 2)
                    if 'A' in tile.getbands():
                        with tile.getchannel('A') as alpha:
                            canvas.paste(tile, position, alpha)
                    else:
                        canvas.paste(tile, position)
                finally:
                    tile.close()
            finally:
                source.close()
            progress(index + 1, len(paths))
        _check_cancel(cancel)
        return canvas, plan
    except BaseException:
        canvas.close()
        raise


def export_collage(paths: Sequence[Path], options: CollageOptions, output: Path, *, overlays=None,
                   cancel=lambda: False, progress=lambda done, total: None) -> Path:
    """同目录暂存后替换目标，失败/取消保留已有文件，禁止覆盖任何输入照片。"""
    output = Path(output)
    if output.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
        raise ValueError('拼图支持导出 PNG 或 JPEG')
    for path in paths:
        if output.resolve() == Path(path).resolve() or (output.exists() and os.path.samefile(path, output)):
            raise ValueError('不能将拼图覆盖到输入照片，请另选输出文件')
    result, _ = render_collage(paths, options, cancel=cancel, progress=progress)
    temporary = None
    try:
        if overlays:
            from birdstamp.overlays.render import build_scene, compose_scene
            scene = build_scene(overlays, result.size)
            try:
                composed = compose_scene(result, scene)
            finally:
                scene.close()
            result.close()
            result = composed
        _check_cancel(cancel)
        with tempfile.NamedTemporaryFile(prefix='.birdstamp-collage-', suffix=output.suffix,
                                         dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
        if output.suffix.lower() in ('.jpg', '.jpeg'):
            with result.convert('RGB') as rgb:
                rgb.save(temporary, format='JPEG', quality=95, subsampling=0)
        else:
            result.save(temporary, format='PNG')
        _check_cancel(cancel)
        os.replace(temporary, output)
        return output
    finally:
        result.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
