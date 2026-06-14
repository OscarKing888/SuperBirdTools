"""Concrete and abstract image processing pipeline stages."""
from __future__ import annotations

from typing import Any

from .image_proc_stage import ImageProcStage

__all__ = [
    "ImageProcFocusOverlayStage",
    "ImageProcResizeLimitStage",
    "ImageProcStage",
    "ImageProcTemplateCropStage",
    "ImageProcTemplateOverlayStage",
]

_LAZY_EXPORTS = {
    "ImageProcFocusOverlayStage": ".image_proc_focus_overlay_stage",
    "ImageProcResizeLimitStage": ".image_proc_resize_limit_stage",
    "ImageProcTemplateCropStage": ".image_proc_template_crop_stage",
    "ImageProcTemplateOverlayStage": ".image_proc_template_overlay_stage",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path, __name__)
    return getattr(module, name)
