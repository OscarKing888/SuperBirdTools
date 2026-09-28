from __future__ import annotations

from birdstamp.image_dejitter.sequence_geometry import aligned_crop_plan
from birdstamp.image_dejitter.matching_options import ROTATION_RANGE, TOLERANCE_RANGE
from birdstamp.image_dejitter.rigid_alignment import render_alignment
from ..image_proc_context import ImageProcContext
from ..image_proc_option_spec import ImageProcOptionSpec
from ..image_proc_option_choice import ImageProcOptionChoice
from .image_proc_stage import ImageProcStage


class ImageProcSequenceAlignStage(ImageProcStage):
    """独立去抖动管线：合并刚性变换与整组画布，一次采样，不读取模板裁切。"""

    stage_id = 'sequence_align'
    label = '参考区对齐与公共裁切'
    description = '默认取对齐画面交集；可补边保留完整范围，供二次裁切。'

    def parameter_options(self):
        return (ImageProcOptionSpec(key='dejitter_alignment_mode',label='对齐方式',value_type='choice',default='rigid',
                                    choices=(ImageProcOptionChoice('平移＋旋转','rigid'),ImageProcOptionChoice('仅平移','translation'))),
                ImageProcOptionSpec(key='dejitter_reference_strength', label='补偿强度',
                                    value_type='int', default=100),
                ImageProcOptionSpec(key='dejitter_pad_to_union', label='补边保留完整画面',
                                    value_type='bool', default=False),
                ImageProcOptionSpec(key='dejitter_match_mode', label='匹配设置', value_type='choice', default='auto',
                                    choices=(ImageProcOptionChoice('自动（推荐）', 'auto'),
                                             ImageProcOptionChoice('高级自定义', 'custom'))),
                ImageProcOptionSpec(key='dejitter_match_rotation_deg', label='允许的轻微旋转（度）',
                                    value_type='float', default=2.0, minimum=ROTATION_RANGE[0],
                                    maximum=ROTATION_RANGE[1], step=.1,
                                    description='仅高级自定义生效；限制可接受的旋转，是否应用纠正由对齐方式决定。'),
                ImageProcOptionSpec(key='dejitter_match_tolerance_pct', label='位置容差（短边百分比）',
                                    value_type='float', default=.3, minimum=TOLERANCE_RANGE[0],
                                    maximum=TOLERANCE_RANGE[1], step=.05,
                                    description='仅高级自定义生效；至少 1 像素，调大可能增加错配。'))

    def process(self, context: ImageProcContext) -> ImageProcContext:
        alignment = context.precomputed.get('sequence_alignment')
        if alignment is not None:
            canvas = context.precomputed['sequence_canvas_box']
            box = alignment.source_pixel_box(canvas)
            context.crop_plan = aligned_crop_plan(context.image.size,box) if box else ((0.,0.,1.,1.),(0,0,0,0))
            context.crop_box,context.outer_pad = context.crop_plan
            context.image = render_alignment(context.image,context.image.size,alignment,canvas)
            return context
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
