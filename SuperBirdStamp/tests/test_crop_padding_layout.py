"""留边反复展开时，滚动面板必须容纳完整控件，不能压扁分边输入。"""
import time

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFormLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from birdstamp import config
from birdstamp.gui.editor_collapsible import CollapsibleSection, CurrentPageTabWidget
from birdstamp.gui.editor_crop_padding_widget import CropPaddingEditorWidget
from birdstamp.gui.editor_utils import configure_form_layout


_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate):
    deadline = time.monotonic() + 2
    while True:
        _APP.processEvents()
        if predicate():
            return
        assert time.monotonic() < deadline, "留边布局未恢复到完整高度"


@pytest.mark.parametrize("wrapping_form", [False, True])
def test_padding_repeated_toggle_keeps_all_controls_inside_scroll_content(
    tmp_path, monkeypatch, wrapping_form,
):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.resize(600, 180)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    section = CollapsibleSection("导出")
    tabs = CurrentPageTabWidget()
    section.set_content_widget(tabs)
    content_layout.addWidget(section)
    content_layout.addStretch()
    scroll.setWidget(content)

    page = QWidget()
    form = QFormLayout(page)
    if wrapping_form:
        configure_form_layout(form)
    padding = CropPaddingEditorWidget()
    padding.set_values(top=1443, bottom=1443, left=1443, right=1443, fill="#123456")
    form.addRow("留边", padding)
    tall_page = QWidget()
    tall_layout = QVBoxLayout(tall_page)
    tall_label = QLabel("去抖动设置")
    tall_label.setMinimumHeight(900)
    tall_layout.addWidget(tall_label)
    tabs.addTab(page, "导出设置")
    tabs.addTab(tall_page, "去抖动")
    changes = []
    padding.changed.connect(lambda: changes.append(padding.get_values()))
    values = padding.get_values()

    try:
        scroll.show()
        _wait_until(lambda: padding.isVisible() and padding.height() == padding.sizeHint().height())
        collapsed_height = content.height()
        # 隐藏页的高度不能让当前页的最小高度变小，甚至变成负数。
        assert tabs.minimumSizeHint().height() >= page.minimumSizeHint().height()
        assert scroll.verticalScrollBar().maximum() == 0

        for cycle in range(12):
            QTest.mouseClick(padding._details_toggle, Qt.MouseButton.LeftButton)
            _wait_until(lambda: padding.height() >= padding.minimumSizeHint().height())
            assert padding.details_expanded()
            assert padding._details_toggle.arrowType() == Qt.ArrowType.DownArrow
            assert padding._details_widget.isVisible()
            assert scroll.verticalScrollBar().maximum() > 0
            for control in (padding.top_spin, padding.bottom_spin, padding.left_spin,
                            padding.right_spin, padding.fill_editor):
                assert control.height() >= control.minimumSizeHint().height()
                # 检查真实几何；仅 isVisible() 无法发现被父控件裁掉的输入框。
                ancestor = control.parentWidget()
                while ancestor is not scroll.widget().parentWidget():
                    assert ancestor.rect().contains(control.mapTo(ancestor, control.rect().topLeft()))
                    assert ancestor.rect().contains(control.mapTo(ancestor, control.rect().bottomRight()))
                    ancestor = ancestor.parentWidget()

            if cycle % 2:
                tabs.setCurrentIndex(1)
                _wait_until(lambda: tall_page.isVisible())
                tabs.setCurrentIndex(0)
            else:
                section.set_expanded(False)
                section.set_expanded(True)
            _wait_until(lambda: padding.isVisible() and padding.height() >= padding.minimumSizeHint().height())

            QTest.mouseClick(padding._details_toggle, Qt.MouseButton.LeftButton)
            _wait_until(lambda: content.height() == collapsed_height)
            assert not padding.details_expanded()
            assert padding._details_toggle.arrowType() == Qt.ArrowType.RightArrow
            assert padding._details_widget.isHidden()
            assert padding.height() == padding.sizeHint().height()
            assert scroll.verticalScrollBar().maximum() == 0
            assert padding.get_values() == values
        assert not changes
    finally:
        scroll.close()
        scroll.deleteLater()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
