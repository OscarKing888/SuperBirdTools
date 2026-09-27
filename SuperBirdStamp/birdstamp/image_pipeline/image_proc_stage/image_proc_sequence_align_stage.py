from __future__ import annotations

from birdstamp.image_dejitter.sequence_geometry import aligned_crop_plan
from ..image_proc_context import ImageProcContext
from ..image_proc_option_spec import ImageProcOptionSpec
from .image_proc_stage import ImageProcStage


class ImageProcSequenceAlignStage(ImageProcStage):
    """独立去抖动管线：使用整组对齐后的原图像素框，不读取模板裁切。"""

    stage_id = 'sequence_align'
    label = '参考区对齐与公共裁切'
    description = '默认取对齐画面交集；可补边保留完整范围，供二次裁切。'

    def parameter_options(self):
        return (ImageProcOptionSpec(key='dejitter_reference_strength', label='补偿强度',
                                    value_type='int', default=100),
                ImageProcOptionSpec(key='dejitter_pad_to_union', label='补边保留完整画面',
                                    value_type='bool', default=False))

    def process(self, context: ImageProcContext) -> ImageProcContext:
        left, top, right, bottom = context.precomputed['sequence_crop_pixels']
        width, height = context.image.size
        if not context.settings.get('dejitter_pad_to_union', False) and not (
                0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError('去抖动裁切范围已失效，请重新分析。')
        context.crop_plan = aligned_crop_plan((width,height), (left,top,right,bottom))
        context.crop_box, context.outer_pad = context.crop_plan
        # 整数像素复制；PIL crop 的图外区域补黑，原有画面不缩放。
        with context.image.crop((left,top,right,bottom)) as cropped:
            context.image = cropped.convert('RGB')
        return context
