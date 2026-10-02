"""FPS 下拉框：预置项、手动输入与范围约束。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from birdstamp.gui.editor_fps_combo import FpsComboBox
from birdstamp.gui.editor_gif_panel import GifExportPanel


_APP = QApplication.instance() or QApplication([])


def test_presets_are_integer_values_within_range() -> None:
    combo = FpsComboBox(1, 30, 8, presets=[5, 12, 23.976, 24, 29.97, 30, 60])
    assert [combo.itemText(i) for i in range(combo.count())] == ["5", "12", "24", "30"]
    assert combo.value() == 8 and combo.currentText() == "8"


def test_preset_selection_typing_and_clamping_emit_value() -> None:
    combo = FpsComboBox(1, 30, 8, presets=[12, 24])
    seen: list[int] = []
    combo.valueChanged.connect(seen.append)

    combo.activated.emit(combo.findData(24))
    assert combo.value() == 24

    combo.lineEdit().clear()
    QTest.keyClicks(combo.lineEdit(), "15")
    assert combo.value() == 15
    combo.lineEdit().editingFinished.emit()
    assert combo.currentText() == "15"

    combo.lineEdit().setText("99")
    combo.lineEdit().editingFinished.emit()
    assert combo.value() == 30 and combo.currentText() == "30"

    QTest.keyClick(combo, Qt.Key.Key_Down)
    assert combo.value() == 29
    combo.setValue(0)
    assert combo.value() == 1 and combo.currentText() == "1"
    assert seen == [24, 1, 15, 30, 29, 1]


def test_gif_panel_offers_fps_presets_and_restores_state() -> None:
    panel = GifExportPanel()
    try:
        combo = panel.fps_combo
        assert combo.count() > 1
        panel.set_state(fps=12.4)
        assert combo.currentText() == "12"
        assert panel.current_request().fps == 12.0
    finally:
        panel.deleteLater()
