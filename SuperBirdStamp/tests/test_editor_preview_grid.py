import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas


def test_editor_composition_grid_stays_inside_crop_and_exports() -> None:
    _app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(120, 120)
    pixmap.fill(QColor(0, 0, 0))
    preview = EditorPreviewCanvas()
    preview.set_source_pixmap(pixmap, log_performance=False)
    preview.set_crop_effect_box((0.25, 0.25, 0.75, 0.75))
    preview.set_composition_grid_mode("thirds")

    rendered = preview.render_source_pixmap_with_overlays()

    assert rendered is not None and not rendered.isNull()
    image = rendered.toImage()
    outside = image.pixelColor(20, 60)
    inside_grid = image.pixelColor(50, 60)
    assert (outside.red(), outside.green(), outside.blue()) == (0, 0, 0)
    assert inside_grid.red() > 0 or inside_grid.green() > 0 or inside_grid.blue() > 0
    with tempfile.TemporaryDirectory() as temp_dir:
        target = os.path.join(temp_dir, "editor-grid.png")
        assert preview.save_source_pixmap_with_overlays(target, "PNG")
        assert os.path.isfile(target)
    preview.close()
