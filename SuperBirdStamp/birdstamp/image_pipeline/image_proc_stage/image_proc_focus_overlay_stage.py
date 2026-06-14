from __future__ import annotations

from typing import Any, Mapping

from ..image_proc_context import ImageProcContext
from ..image_proc_option_spec import ImageProcOptionSpec
from birdstamp.export_stage.constants import (
    STAGE_FOCUS_OVERLAY_ENABLED_KEY,
    STAGE_FOCUS_OVERLAY_ID,
)

from ._export_stage_core import export_stage_core
from .image_proc_stage import ImageProcStage


class ImageProcFocusOverlayStage(ImageProcStage):
    stage_id = STAGE_FOCUS_OVERLAY_ID
    label = "焦点框"
    description = "根据原图元数据和裁切计划绘制对焦框。"
    enabled_option_key = STAGE_FOCUS_OVERLAY_ENABLED_KEY
    enabled_by_default = True

    def parameter_options(self) -> tuple[ImageProcOptionSpec, ...]:
        return (
            ImageProcOptionSpec(key="draw_focus", label="焦点", value_type="bool", default=False),
        )

    def is_enabled(self, settings: Mapping[str, Any]) -> bool:
        core = export_stage_core()
        if not core._resolve_stage_enabled(
            settings,
            stage_id=self.stage_id,
            enabled_key=str(self.enabled_option_key or ""),
            default=self.enabled_by_default,
        ):
            return False
        return core._parse_bool_value(settings.get("draw_focus"), False)

    def process(self, context: ImageProcContext) -> ImageProcContext:
        if not self.is_enabled(context.settings):
            return context
        core = export_stage_core()
        raw_metadata = dict(context.raw_metadata or {})
        source_width, source_height = context.source_size or (context.image.width, context.image.height)
        focus_box = core._resolve_focus_box_after_processing(
            raw_metadata,
            source_width=source_width,
            source_height=source_height,
            crop_box=context.crop_box,
            outer_pad=context.outer_pad,
            apply_ratio_crop=True,
            camera_type=core._resolve_focus_camera_type_from_metadata(raw_metadata),
        )
        if focus_box is not None:
            context.image = core._draw_focus_box_overlay(context.image, focus_box)
        return context
