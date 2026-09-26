from __future__ import annotations

from ..image_proc_context import ImageProcContext
from ..image_proc_option_spec import ImageProcOptionSpec
from .image_proc_stage import ImageProcStage


class ImageProcSequenceAlignStage(ImageProcStage):
    """独立去抖动管线：使用整组求交后的原图像素框，不读取模板裁切。"""

    stage_id = 'sequence_align'
    label = '参考区对齐与公共裁切'
    description = '对齐参考区，保留整组共同覆盖的最大矩形。'

    def parameter_options(self):
        return (ImageProcOptionSpec(key='dejitter_reference_strength', label='补偿强度',
                                    value_type='int', default=100),)

    def process(self, context: ImageProcContext) -> ImageProcContext:
        left, top, right, bottom = context.precomputed['sequence_crop_pixels']
        width, height = context.image.size
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError('去抖动裁切范围已失效，请重新分析。')
        context.image = context.image.crop((left, top, right, bottom)).convert('RGB')
        context.crop_box = (left / width, top / height, right / width, bottom / height)
        context.outer_pad = (0, 0, 0, 0)
        context.crop_plan = (context.crop_box, context.outer_pad)
        return context
