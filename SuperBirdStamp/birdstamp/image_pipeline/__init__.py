"""Image processing pipeline: one public class per module; import from here.

Module layout:
- ``image_proc_crop_plan`` — ``ImageProcCropPlan`` type alias
- ``image_proc_option_choice`` — ``ImageProcOptionChoice``
- ``image_proc_option_spec`` — ``ImageProcOptionSpec``
- ``image_proc_stage_descriptor`` — ``ImageProcStageDescriptor``
- ``image_proc_context`` — ``ImageProcContext`` shared processing state
- ``image_proc_stage`` — ``ImageProcStage`` abstract base for pipeline steps
- ``image_proc_export_stage`` — ``ImageProcExportStage`` terminal export marker
- ``image_proc_pipeline`` — ``ImageProcPipeline`` stage orchestration

External code should import from ``birdstamp.image_pipeline`` (this package).
"""
from __future__ import annotations

from .image_proc_context import ImageProcContext
from .image_proc_crop_plan import ImageProcCropPlan
from .image_proc_export_stage import ImageProcExportStage
from .image_proc_option_choice import ImageProcOptionChoice
from .image_proc_option_spec import ImageProcOptionSpec
from .image_proc_pipeline import ImageProcPipeline
from .image_proc_stage import ImageProcStage
from .image_proc_stage_descriptor import ImageProcStageDescriptor

__all__ = [
    "ImageProcContext",
    "ImageProcCropPlan",
    "ImageProcExportStage",
    "ImageProcOptionChoice",
    "ImageProcOptionSpec",
    "ImageProcPipeline",
    "ImageProcStage",
    "ImageProcStageDescriptor",
]
