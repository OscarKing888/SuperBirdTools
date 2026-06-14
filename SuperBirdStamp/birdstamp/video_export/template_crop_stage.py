from __future__ import annotations

from typing import Any, Mapping

from birdstamp.image_pipeline import ImageProcContext, ImageProcExportStage, ImageProcOptionChoice, ImageProcOptionSpec, ImageProcStage

from . import core
from .constants import (
    EXPORT_STAGE_GIF_ID,
    EXPORT_STAGE_PNG_ID,
    EXPORT_STAGE_VIDEO_ID,
    STAGE_FOCUS_OVERLAY_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ID,
    STAGE_RESIZE_LIMIT_ENABLED_KEY,
    STAGE_RESIZE_LIMIT_ID,
    STAGE_TEMPLATE_CROP_ENABLED_KEY,
    STAGE_TEMPLATE_CROP_ID,
    STAGE_TEMPLATE_OVERLAY_ENABLED_KEY,
    STAGE_TEMPLATE_OVERLAY_ID,
    _CENTER_MODE_BIRD,
    _CENTER_MODE_CUSTOM,
    _CENTER_MODE_FOCUS,
    _CENTER_MODE_IMAGE,
    _DEFAULT_TEMPLATE_CENTER_MODE,
    _DEFAULT_TEMPLATE_MAX_LONG_EDGE,
)

class TemplateCropStage(ImageProcStage):
    stage_id = STAGE_TEMPLATE_CROP_ID
    label = "模板裁切"
    description = "根据模板比例、中心模式、照片级裁切框与留边设置生成裁切后的图像。"
    enabled_option_key = None
    enabled_by_default = True

    def parameter_options(self) -> tuple[ImageProcOptionSpec, ...]:
        return (
            ImageProcOptionSpec(
                key="template_name",
                label="模板",
                value_type="template",
                default="default",
                description="用于裁切参数、模板字段和照片级重载的当前模板。",
            ),
            ImageProcOptionSpec(
                key="ratio",
                label="裁切比例",
                value_type="ratio",
                default=None,
                description="模板裁切比例；原比例、不裁切和自由比例由现有 ratio 选项表达。",
            ),
            ImageProcOptionSpec(
                key="center_mode",
                label="中心模式",
                value_type="choice",
                default=_DEFAULT_TEMPLATE_CENTER_MODE,
                choices=(
                    ImageProcOptionChoice("图像中心", _CENTER_MODE_IMAGE),
                    ImageProcOptionChoice("焦点中心", _CENTER_MODE_FOCUS),
                    ImageProcOptionChoice("鸟体中心", _CENTER_MODE_BIRD),
                    ImageProcOptionChoice("自定义", _CENTER_MODE_CUSTOM),
                ),
            ),
            ImageProcOptionSpec(key="crop_padding_top", label="上留边", value_type="int", default=0),
            ImageProcOptionSpec(key="crop_padding_bottom", label="下留边", value_type="int", default=0),
            ImageProcOptionSpec(key="crop_padding_left", label="左留边", value_type="int", default=0),
            ImageProcOptionSpec(key="crop_padding_right", label="右留边", value_type="int", default=0),
            ImageProcOptionSpec(key="crop_padding_fill", label="留边颜色", value_type="color", default="#FFFFFF"),
        )

    def process(self, context: ImageProcContext) -> ImageProcContext:
        settings = core._clone_render_settings(context.settings)
        raw_metadata = dict(context.raw_metadata or {})
        precomputed_crop_plan = core._normalize_precomputed_crop_plan(context.crop_plan)
        if precomputed_crop_plan is None:
            precomputed_crop_plan = core._normalize_precomputed_crop_plan(context.precomputed.get("crop_plan"))

        if precomputed_crop_plan is None:
            crop_box, outer_pad = core._compute_crop_plan_for_image(
                path=context.source_path,
                image=context.image,
                raw_metadata=raw_metadata,
                settings=settings,
                bird_box_cache=core._context_bird_box_cache(context),
                bird_box_lock=context.bird_box_lock,
            )
        else:
            crop_box, outer_pad = precomputed_crop_plan

        context.crop_plan = (crop_box, outer_pad)
        context.crop_box = crop_box
        context.outer_pad = outer_pad
        context.precomputed["crop_plan"] = (crop_box, outer_pad)

        top, bottom, left, right = outer_pad
        image = context.image
        if top or bottom or left or right:
            fill = str(settings.get("crop_padding_fill") or "#FFFFFF").strip() or "#FFFFFF"
            image = core._pad_image(image, top=top, bottom=bottom, left=left, right=right, fill=fill)
        context.image = core._crop_image_by_normalized_box(image, crop_box)
        return context

