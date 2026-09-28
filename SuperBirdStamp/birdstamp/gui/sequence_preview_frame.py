from dataclasses import dataclass
from PyQt6.QtGui import QImage


@dataclass(slots=True)
class SequencePreviewFrame:
    path: object
    image: QImage
    source_size: tuple
    output_size: tuple
    crop_plan: tuple
    source_image: QImage | None = None
    alignment: object | None = None
    canvas_box: tuple = ()
