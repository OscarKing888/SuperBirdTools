"""Image processing pipeline: one public class per module; import from here.

Module layout:
- ``image_proc_crop_plan`` — ``ImageProcCropPlan`` type alias
- ``image_proc_option_choice`` — ``ImageProcOptionChoice``
- ``image_proc_option_spec`` — ``ImageProcOptionSpec``
- ``image_proc_stage_descriptor`` — ``ImageProcStageDescriptor``
- ``image_proc_context`` — ``ImageProcContext`` shared processing state
- ``image_proc_stage`` — ``ImageProcStage`` ABC and concrete stage implementations
- ``image_proc_export_stage`` — ``ImageProcExportStage`` terminal export marker
- ``image_proc_pipeline`` — ``ImageProcPipeline`` stage orchestration

External code should import from ``birdstamp.image_pipeline`` (this package).
Concrete stages are re-exported lazily to avoid import cycles with ``export_stage``.
"""
from __future__ import annotations

from typing import Any

from .image_proc_context import ImageProcContext
from .image_proc_crop_plan import ImageProcCropPlan
from .image_proc_export_stage import ImageProcExportStage
from .image_proc_option_choice import ImageProcOptionChoice
from .image_proc_option_spec import ImageProcOptionSpec
from .image_proc_pipeline import ImageProcPipeline
from .image_proc_stage.image_proc_stage import ImageProcStage
from .image_proc_stage_descriptor import ImageProcStageDescriptor

__all__ = [
    "ImageProcContext",
    "ImageProcCropPlan",
    "ImageProcExportStage",
    "ImageProcFocusOverlayStage",
    "ImageProcOptionChoice",
    "ImageProcOptionSpec",
    "ImageProcPipeline",
    "ImageProcResizeLimitStage",
    "ImageProcStage",
    "ImageProcStageDescriptor",
    "ImageProcTemplateCropStage",
    "ImageProcTemplateOverlayStage",
]

_LAZY_EXPORTS = {
    "ImageProcFocusOverlayStage": ".image_proc_stage",
    "ImageProcResizeLimitStage": ".image_proc_stage",
    "ImageProcTemplateCropStage": ".image_proc_stage",
    "ImageProcTemplateOverlayStage": ".image_proc_stage",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path, __name__)
    return getattr(module, name)
