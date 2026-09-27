"""原图整数像素边界统一用于去抖动导出、小图预览和辅助层映射。"""
from PIL import Image


def aligned_crop_plan(source_size, pixel_box):
    """补边模式允许画布越界；辅助层仍遵循先补边、再裁切的坐标约定。"""
    width, height = source_size
    left, top, right, bottom = pixel_box
    if right <= left or bottom <= top:
        raise ValueError('去抖动画幅范围无效，请重新分析。')
    pt, pb = max(0, -top), max(0, bottom-height)
    pl, pr = max(0, -left), max(0, right-width)
    pw, ph = width+pl+pr, height+pt+pb
    return ((left+pl)/pw, (top+pt)/ph, (right+pl)/pw, (bottom+pt)/ph), (pt,pb,pl,pr)


def source_normalized_crop(source_size, pixel_box):
    width, height = source_size
    return tuple(value/(width if i % 2 == 0 else height) for i,value in enumerate(pixel_box))


def render_aligned_thumbnail(image, source_size, pixel_box, max_edge):
    """在最终画布上采样小图；图外补黑，并集扩大时仍遵守快速预览预算。"""
    width, height = source_size
    left, top, right, bottom = pixel_box
    output_width, output_height = right-left, bottom-top
    scale = min(1, max_edge/max(output_width, output_height))
    size = (max(1, round(output_width*scale)), max(1, round(output_height*scale)))
    return image.transform(size, Image.Transform.AFFINE,
        (output_width/size[0]*image.width/width, 0, left*image.width/width,
         0, output_height/size[1]*image.height/height, top*image.height/height),
        resample=Image.Resampling.BILINEAR, fillcolor=(0,0,0))
