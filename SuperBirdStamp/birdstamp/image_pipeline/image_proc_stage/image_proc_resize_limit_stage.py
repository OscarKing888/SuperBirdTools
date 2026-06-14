from __future__ import annotations

from typing import Any, Mapping

from ..image_proc_context import ImageProcContext
from ..image_proc_option_spec import ImageProcOptionSpec
from birdstamp.export_stage.constants import (
    STAGE_RESIZE_LIMIT_ENABLED_KEY,
    STAGE_RESIZE_LIMIT_ID,
    _DEFAULT_TEMPLATE_MAX_LONG_EDGE,
)

from ._export_stage_core import export_stage_core
from .image_proc_stage import ImageProcStage


class ImageProcResizeLimitStage(ImageProcStage):
    stage_id = STAGE_RESIZE_LIMIT_ID
    label = "尺寸限制"
    description = "在裁切后按最长边限制缩放图像。"
    enabled_option_key = STAGE_RESIZE_LIMIT_ENABLED_KEY
    enabled_by_default = True

    def parameter_options(self) -> tuple[ImageProcOptionSpec, ...]:
        return (
            ImageProcOptionSpec(
                key="max_long_edge",
                label="最长边",
                value_type="int",
                default=_DEFAULT_TEMPLATE_MAX_LONG_EDGE,
                minimum=0,
            ),
        )

    def process(self, context: ImageProcContext) -> ImageProcContext:
        core = export_stage_core()
        settings = core._clone_render_settings(context.settings)
        context.image = core._resize_fit(context.image, max(0, int(settings.get("max_long_edge") or 0)))
        return context

    def is_enabled(self, settings: Mapping[str, Any]) -> bool:
        core = export_stage_core()
        return core._resolve_stage_enabled(
            settings,
            stage_id=self.stage_id,
            enabled_key=str(self.enabled_option_key or ""),
            default=self.enabled_by_default,
        )
