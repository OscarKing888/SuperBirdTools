"""Horizontal carousel slicing: one sampled canvas, integer slices, atomic group export.

No Qt dependency. The separate canvas/export stages are shared by the editor and CLI;
they deliberately do not alter the ordinary photo/video pipeline or its crop state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
import tempfile
import time

from PIL import Image, ImageColor, ImageOps

from app_common.image_output_paths import portable_key
from birdstamp.image_pipeline import ImageProcContext, ImageProcExportStage, ImageProcOptionSpec, ImageProcStage


MAX_PIXELS = 100_000_000


class SplitCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel():
        raise SplitCancelled()


@dataclass(frozen=True)
class SplitOptions:
    count: int = 3
    width: int = 1080
    height: int = 1440
    fit: str = 'cover'
    background: str = '#FFFFFF'
    format: str = 'png'
    quality: int = 95

    def __post_init__(self):
        for name, low, high in (('count', 2, 20), ('width', 64, 8192), ('height', 64, 8192), ('quality', 1, 100)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{name} 必须在 {low}–{high} 之间')
        if self.count * self.width * self.height > MAX_PIXELS:
            raise ValueError('总画布超过 1 亿像素，请减少份数或单张尺寸')
        if self.fit not in ('cover', 'contain'):
            raise ValueError('构图方式必须是 cover 或 contain')
        if self.format not in ('jpeg', 'png'):
            raise ValueError('连屏切图支持 JPEG / PNG')
        try:
            ImageColor.getrgb(self.background)
        except (ValueError, TypeError) as exc:
            raise ValueError('背景颜色无效') from exc

    @property
    def size(self):
        return self.count * self.width, self.height

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('切分参数必须为对象')
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def normalized_crop(size, options, box=None):
    """Fit the total aspect inside the source, retaining the previous centre/area."""
    sw, sh = size
    if sw <= 0 or sh <= 0:
        raise ValueError('源图尺寸无效')
    ratio = options.size[0] / options.size[1] * sh / sw
    cx = cy = .5
    w, h = min(1., ratio), min(1., 1 / ratio)
    if box is not None:
        if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
            raise ValueError('裁剪框无效')
        l, t, r, b = map(float, box)
        if r <= l or b <= t:
            raise ValueError('裁剪框无效')
        cx, cy = (l + r) / 2, (t + b) / 2
        area = max(1e-8, (r - l) * (b - t))
        w, h = math.sqrt(area * ratio), math.sqrt(area / ratio)
        factor = min(1., 1 / w, 1 / h)
        w, h = w * factor, h * factor
    l, t = min(max(0., cx - w / 2), 1 - w), min(max(0., cy - h / 2), 1 - h)
    return l, t, l + w, t + h


def preview_size(options, edge=2400):
    """Keep the preview canvas divisible by count as well."""
    scale = min(1., edge / max(options.size))
    width = max(1, round(options.width * scale))
    return width * options.count, max(1, round(options.height * scale))


def render_canvas(image, options, crop=None, *, target_size=None, source_size=None):
    target = target_size or options.size
    if options.fit == 'cover':
        l, t, r, b = normalized_crop(source_size or image.size, options, crop)
        box = (max(0., l * image.width), max(0., t * image.height),
               min(image.width, r * image.width), min(image.height, b * image.height))
        result = image.resize(target, Image.Resampling.LANCZOS, box=box)
    else:
        result = Image.new('RGBA', target, options.background)
        with ImageOps.contain(image, target, Image.Resampling.LANCZOS) as contained:
            result.paste(contained, ((target[0] - contained.width) // 2, (target[1] - contained.height) // 2))
    if result.mode == 'RGB':
        return result
    try:
        # Flatten transparency once before slicing, for identical JPEG/PNG geometry.
        with result.convert('RGBA') as rgba:
            with Image.new('RGBA', target, options.background) as backdrop:
                backdrop.alpha_composite(rgba)
                return backdrop.convert('RGB')
    finally:
        result.close()


def slice_boxes(size, count):
    width, height = size
    if width % count:
        raise ValueError('总画布宽度必须能被份数整除')
    step = width // count
    return tuple((i * step, 0, (i + 1) * step, height) for i in range(count))


class SplitCanvasStage(ImageProcStage):
    stage_id = 'seamless_split_canvas'
    label = '连屏总画布'

    def parameter_options(self):
        return tuple(ImageProcOptionSpec(key=key, label=label, value_type=kind, default=default)
                     for key, label, kind, default in (
                         ('count', '切分份数', 'int', 3), ('width', '每张宽度', 'int', 1080),
                         ('height', '每张高度', 'int', 1440), ('fit', '构图方式', 'str', 'cover'),
                         ('background', '背景颜色', 'color', '#FFFFFF')))

    def process(self, context):
        options = SplitOptions.from_dict(context.settings)
        context.image = render_canvas(context.image, options, context.crop_box,
                                      target_size=context.precomputed.get('split_preview_size'),
                                      source_size=context.source_size)
        return context


class SplitExportStage(ImageProcExportStage):
    stage_id = 'seamless_split_export'
    export_kind = 'seamless_split'
    label = '连屏图片导出'

    def parameter_options(self):
        return (ImageProcOptionSpec(key='format', label='输出格式', value_type='str', default='png'),
                ImageProcOptionSpec(key='quality', label='JPEG 质量', value_type='int', default=95))

    def export(self, context, directory, *, page_index=None, cancel, progress):
        from birdstamp.export_metadata import copy_export_metadata
        options = SplitOptions.from_dict(context.settings)
        suffix = '.jpg' if options.format == 'jpeg' else '.png'
        boxes = slice_boxes(context.image.size, options.count)
        indices = range(options.count) if page_index is None else (page_index,)
        for done, index in enumerate(indices, 1):
            check_cancel(cancel)
            target = Path(directory) / f'{context.source_path.stem}_{index + 1:02d}{suffix}'
            with context.image.crop(boxes[index]) as tile:
                kwargs = dict(quality=options.quality, subsampling=0) if suffix == '.jpg' else {}
                # The caller owns this unpublished directory. Carousel outputs
                # deliberately omit XMP; keep native EXIF/ICC and strip embedded XMP.
                tile.save(target, format='JPEG' if suffix == '.jpg' else 'PNG', **kwargs)
                copy_export_metadata(context.source_path, target, tile)
            progress(done, len(indices))
        check_cancel(cancel)


def export_split(source, options, directory, crop=None, *, page_index=None,
                 cancel=lambda: False, progress=lambda done, total: None):
    """Publish all slices or one zero-based page in a new directory, without XMP."""
    from birdstamp.decoders.image_decoder import decode_image

    source, directory = Path(source), Path(directory)
    if page_index is not None and (type(page_index) is not int or not 0 <= page_index < options.count):
        raise ValueError('导出张数编号必须在 1 到切分份数之间')
    check_cancel(cancel)
    before = source.stat()
    directory.mkdir(parents=True, exist_ok=True)
    # Immediate ownership also covers decode/metadata/write failure and cancellation.
    with tempfile.TemporaryDirectory(prefix='.birdstamp-split-', dir=directory) as temporary:
        staging = Path(temporary) / 'complete'
        staging.mkdir()
        check_cancel(cancel)
        with decode_image(source) as image:
            context = ImageProcContext(image=image, source_path=source, settings=asdict(options), crop_box=crop)
            SplitCanvasStage().process(context)
            try:
                check_cancel(cancel)
                SplitExportStage().export(context, staging, page_index=page_index,
                                          cancel=cancel, progress=progress)
            finally:
                context.image.close()
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('导出期间原图发生变化，请重新导出')
        check_cancel(cancel)
        for number in range(1, 1_000_001):
            stem = f'{source.stem}_连屏' + (f'_{page_index + 1:02d}' if page_index is not None else '')
            stem += f'_{number}' if number > 1 else ''
            target = directory / stem
            # Serialize cooperating publishers; compare names portably on both OSes.
            lock_key = hashlib.sha256(portable_key(stem).encode('utf-8')).hexdigest()[:24]
            lock = directory / f'.birdstamp-split-{lock_key}.lock'
            try:
                with lock.open('x'):
                    pass
            except FileExistsError:
                continue
            try:
                if portable_key(stem) in {portable_key(p.name) for p in directory.iterdir()}:
                    continue
                check_cancel(cancel)
                # Windows indexers/virus scanners can briefly hold newly written files.
                for attempt in range(11):
                    check_cancel(cancel)
                    try:
                        staging.rename(target)
                        break
                    except PermissionError:
                        if attempt == 10:
                            raise
                        time.sleep(.05)
                return target
            finally:
                lock.unlink(missing_ok=True)
        raise FileExistsError('无法分配不重名的连屏目录')
