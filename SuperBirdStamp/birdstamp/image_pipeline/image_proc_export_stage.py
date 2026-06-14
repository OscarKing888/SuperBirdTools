from __future__ import annotations

from .image_proc_context import ImageProcContext
from .image_proc_stage import ImageProcStage


class ImageProcExportStage(ImageProcStage):
    """Marker base for terminal export stages.

    Export stages describe the final output target. The editor owns file dialogs
    and long-running export workers, so the default ``process`` is intentionally
    a no-op for the image context.
    """

    is_export_stage = True
    export_kind = "export"

    def process(self, context: ImageProcContext) -> ImageProcContext:
        return context
