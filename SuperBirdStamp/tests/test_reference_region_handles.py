"""真实画布事件验证参考区八手柄、暂存预览及单次提交。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication

from birdstamp.gui.edit_modes import EDIT_MODE_NONE, EDIT_MODE_CROP_ADJUST, EDIT_MODE_REFERENCE_REGION, ReferenceRegionEditMode
from birdstamp.gui.editor_preview_canvas import EditorPreviewCanvas

_APP = QApplication.instance() or QApplication([])
BOX = (.25, .25, .75, .75)


@pytest.fixture
def canvas():
    widget = EditorPreviewCanvas()
    widget.resize(600, 400)
    pixmap = QPixmap(600, 400)
    pixmap.fill(QColor('black'))
    widget.set_source_pixmap(pixmap)
    widget.set_reference_regions((BOX, (.8, .8, .95, .95)))
    widget.set_show_reference_regions(True)
    widget.set_edit_mode(EDIT_MODE_REFERENCE_REGION)
    widget.show()
    _APP.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    _APP.processEvents()


def send(canvas, kind, point, modifiers=Qt.KeyboardModifier.NoModifier):
    rect = canvas.display_rect()
    pos = QPointF(rect.left() + point[0] * rect.width(), rect.top() + point[1] * rect.height())
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
    event = QMouseEvent(kind, pos, canvas.mapToGlobal(pos), button, buttons, modifiers)
    _APP.sendEvent(canvas, event)


@pytest.mark.parametrize('handle,start,end,expected', [
    ('nw', (.25, .25), (.15, .10), (.15, .10, .75, .75)),
    ('n', (.50, .25), (.45, .10), (.25, .10, .75, .75)),
    ('ne', (.75, .25), (.85, .10), (.25, .10, .85, .75)),
    ('e', (.75, .50), (.85, .45), (.25, .25, .85, .75)),
    ('se', (.75, .75), (.85, .90), (.25, .25, .85, .90)),
    ('s', (.50, .75), (.45, .90), (.25, .25, .75, .90)),
    ('sw', (.25, .75), (.15, .90), (.15, .25, .75, .90)),
    ('w', (.25, .50), (.15, .45), (.15, .25, .75, .75)),
])
def test_all_eight_handles_keep_opposite_edge_and_other_regions(canvas, handle, start, end, expected):
    commits = []
    canvas.reference_region_changed.connect(commits.append)
    canvas.set_crop_ratio_constraint(1.0, False)  # 参考区缩放不继承正方形裁切约束。
    other = canvas.reference_regions()[1]
    send(canvas, QEvent.Type.MouseButtonPress, start)
    send(canvas, QEvent.Type.MouseMove, end)
    assert not commits
    assert canvas.reference_regions()[0] == BOX
    assert canvas.displayed_reference_regions()[0] == pytest.approx(expected)
    send(canvas, QEvent.Type.MouseButtonRelease, end)
    assert len(commits) == 1
    assert commits[0][0] == pytest.approx(expected)
    assert commits[0][1] == other


def test_resize_is_clamped_and_release_position_is_used(canvas):
    send(canvas, QEvent.Type.MouseButtonPress, (.25, .25))
    send(canvas, QEvent.Type.MouseButtonRelease, (-.3, -.4))
    assert canvas.reference_regions()[0] == (0, 0, .75, .75)
    send(canvas, QEvent.Type.MouseButtonPress, (0, .375))
    send(canvas, QEvent.Type.MouseButtonRelease, (1, .375))
    assert canvas.reference_regions()[0] == pytest.approx((.74, 0, .75, .75))


def test_mode_switch_cancels_drag_and_retains_outline_without_handles(canvas):
    commits = []
    canvas.reference_region_changed.connect(commits.append)
    send(canvas, QEvent.Type.MouseButtonPress, (.75, .5))
    send(canvas, QEvent.Type.MouseMove, (.9, .5))
    canvas.set_edit_mode(EDIT_MODE_NONE)
    send(canvas, QEvent.Type.MouseButtonRelease, (.9, .5))
    assert not commits
    assert canvas.reference_regions()[0] == BOX
    inactive = canvas.render_source_pixmap_with_overlays().toImage()
    canvas.set_edit_mode(EDIT_MODE_REFERENCE_REGION)
    active = canvas.render_source_pixmap_with_overlays().toImage()
    assert inactive.pixelColor(150, 100) != QColor('black')  # 选区边框仍在。
    assert inactive.pixelColor(147, 97) == QColor('black')
    assert active.pixelColor(147, 97) != QColor('black')  # 四角手柄仅在编辑模式显示。
    canvas.set_edit_mode(EDIT_MODE_CROP_ADJUST)
    assert canvas.reference_regions()[0] == BOX


def test_new_selection_and_shift_append_still_work(canvas):
    send(canvas, QEvent.Type.MouseButtonPress, (.05, .05), Qt.KeyboardModifier.ShiftModifier)
    send(canvas, QEvent.Type.MouseButtonRelease, (.15, .15), Qt.KeyboardModifier.ShiftModifier)
    assert len(canvas.reference_regions()) == 3
    assert canvas.reference_regions()[0] == BOX
    send(canvas, QEvent.Type.MouseButtonPress, (.4, .4))
    send(canvas, QEvent.Type.MouseButtonRelease, (.6, .6))
    assert canvas.reference_regions() == ((.4, .4, .6, .6),)


def test_source_clear_and_external_region_change_cancel_resize(canvas):
    commits = []
    canvas.reference_region_changed.connect(commits.append)
    send(canvas, QEvent.Type.MouseButtonPress, (.75, .5))
    canvas.set_reference_regions(((.1, .1, .6, .6),))
    send(canvas, QEvent.Type.MouseButtonRelease, (.9, .5))
    assert not commits
    assert canvas.reference_regions() == ((.1, .1, .6, .6),)
    send(canvas, QEvent.Type.MouseButtonPress, (.6, .35))
    canvas.set_source_pixmap(None)
    assert not canvas.reference_regions()
    assert canvas._edit_modes.active_mode()._resize_index is None


def test_handle_hit_testing_uses_zoomed_display_rect(canvas):
    canvas.set_reference_regions(((.4, .4, .6, .6),))
    canvas.set_display_scale_percent(200, preserve_view=True)
    send(canvas, QEvent.Type.MouseButtonPress, (.6, .5))
    send(canvas, QEvent.Type.MouseButtonRelease, (.64, .5))
    assert canvas.reference_regions()[0] == pytest.approx((.4, .4, .64, .6))
