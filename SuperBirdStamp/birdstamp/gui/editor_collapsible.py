from __future__ import annotations

import os
from typing import Callable

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionTabWidgetFrame,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app_common.log import get_logger


class CurrentPageTabWidget(QTabWidget):
    """高度只跟随当前页的 QTabWidget，避免被最高的隐藏页撑开。

    QTabWidget/QStackedLayout 默认取所有页 sizeHint 的最大值；改为只按当前页计算，
    当前页内容展开/收起时整个分组高度才会随之增减。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self._on_current_changed)

    def tabInserted(self, index: int) -> None:  # type: ignore[override]
        super().tabInserted(index)
        self._on_current_changed(self.currentIndex())

    def _on_current_changed(self, index: int) -> None:
        for i in range(self.count()):
            page = self.widget(i)
            if page is None:
                continue
            policy = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            page.setSizePolicy(policy, policy)
        self.updateGeometry()
        refresh_layout_chain(self)
        schedule_layout_dump(self.widget(index), f"tab changed -> {index}")

    def _current_page_height(self, full_height: int, page_height: Callable[[QWidget], int]) -> int:
        current = self.currentWidget()
        if current is None:
            return full_height
        tallest = 0
        for i in range(self.count()):
            page = self.widget(i)
            if page is not None and self.isTabVisible(i):
                tallest = max(tallest, page_height(page))
        return full_height - tallest + page_height(current)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        full = super().sizeHint()
        return QSize(full.width(), self._current_page_height(full.height(), lambda w: w.sizeHint().height()))

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        full = super().minimumSizeHint()
        return QSize(
            full.width(),
            self._current_page_height(full.height(), lambda w: w.minimumSizeHint().height()),
        )

    def heightForWidth(self, width: int) -> int:  # type: ignore[override]
        # 含自动换行内容时父布局走 heightForWidth，QStackedLayout 同样取所有页最大值。
        full = super().heightForWidth(width)
        current = self.currentWidget()
        stack = current.parentWidget() if current is not None else None
        if full < 0 or current is None or stack is None:
            return full
        opt = QStyleOptionTabWidgetFrame()
        self.initStyleOption(opt)
        padding = self.style().sizeFromContents(QStyle.ContentsType.CT_TabWidget, opt, QSize(0, 0), self)
        page_width = width - padding.width()
        if self.tabPosition() in (QTabWidget.TabPosition.West, QTabWidget.TabPosition.East):
            page_width -= self.tabBar().sizeHint().width()
        page_width = max(0, page_width)
        if current.hasHeightForWidth():
            current_height = current.heightForWidth(page_width)
        else:
            current_height = current.sizeHint().height()
        current_height = max(current_height, current.minimumSizeHint().height())
        return full - stack.heightForWidth(page_width) + current_height


_LAYOUT_DEBUG = os.environ.get("BIRDSTAMP_LAYOUT_DEBUG", "").strip() not in {"", "0", "false", "no"}
_layout_log = get_logger("layout_debug")


def _describe_widget(widget: QWidget) -> str:
    name = type(widget).__name__
    title = ""
    if isinstance(widget, QGroupBox):
        title = widget.title()
    elif isinstance(widget, QToolButton):
        title = widget.text()
    label = widget.objectName() or title
    return f"{name}({label})" if label else name


def _hfw_text(widget: QWidget) -> str:
    if not widget.hasHeightForWidth():
        return "-"
    return str(widget.heightForWidth(widget.width()))


def dump_layout_chain(widget: QWidget | None, reason: str) -> None:
    """BIRDSTAMP_LAYOUT_DEBUG=1 时记录 widget 到左侧滚动区的各层尺寸，用于排查布局空白。

    每层输出 geometry / sizeHint / heightForWidth 及其 layout 子项；只读，不触发重排。
    实际高度超出自身 heightForWidth（无则 sizeHint）时标记 TALLER，即多余空间落在该层。
    """
    if not _LAYOUT_DEBUG or widget is None:
        return
    _layout_log.info("---- layout dump: %s ----", reason)
    current: QWidget | None = widget
    depth = 0
    while current is not None:
        geometry = current.geometry()
        hint = current.sizeHint()
        hfw = _hfw_text(current)
        layout = current.layout()
        items: list[str] = []
        if layout is not None:
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item is None:
                    continue
                child = item.widget()
                if child is not None and child.isHidden():
                    continue
                item_rect = item.geometry()
                item_name = _describe_widget(child) if child is not None else type(item).__name__
                item_hfw = item.heightForWidth(item_rect.width()) if item.hasHeightForWidth() else "-"
                items.append(
                    f"{item_name} y={item_rect.y()} h={item_rect.height()} "
                    f"hint={item.sizeHint().height()} hfw={item_hfw}"
                )
        expected = int(hfw) if hfw != "-" else hint.height()
        taller = " TALLER" if 0 <= expected < geometry.height() - 2 and not isinstance(current, QScrollArea) else ""
        _layout_log.info(
            "%s%s geo=(%d,%d %dx%d) hint=%dx%d min=%d hfw=%s policy=%s%s",
            "  " * depth,
            _describe_widget(current),
            geometry.x(), geometry.y(), geometry.width(), geometry.height(),
            hint.width(), hint.height(),
            current.minimumSizeHint().height(),
            hfw,
            current.sizePolicy().verticalPolicy().name,
            taller,
        )
        for line in items:
            _layout_log.info("%s  - %s", "  " * depth, line)
        if isinstance(current, QScrollArea):
            break
        current = current.parentWidget()
        depth += 1


def schedule_layout_dump(widget: QWidget | None, reason: str) -> None:
    """布局稳定后（下一轮事件循环及 300ms 后）各记录一次。"""
    if not _LAYOUT_DEBUG or widget is None:
        return

    def _dump(suffix: str) -> None:
        try:
            dump_layout_chain(widget, f"{reason} {suffix}")
        except RuntimeError:
            pass  # 定时器触发前控件已销毁（如关闭了模板管理对话框）

    QTimer.singleShot(0, lambda: _dump("+0ms"))
    QTimer.singleShot(300, lambda: _dump("+300ms"))


def refresh_layout_chain(widget: QWidget | None) -> None:
    """Relayout nested widgets inside the left scroll panel without resizing the window."""
    if widget is None:
        return

    scroll_area: QScrollArea | None = None
    chain: list[QWidget] = []
    current: QWidget | None = widget
    while current is not None:
        chain.append(current)
        if isinstance(current, QScrollArea):
            scroll_area = current
            break
        current = current.parentWidget()

    for node in chain:
        if isinstance(node, CollapsibleSection):
            node.refresh_section_layout()
        layout = node.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        node.updateGeometry()

    if scroll_area is None:
        return
    inner = scroll_area.widget()
    if inner is None or inner in chain:
        return
    layout = inner.layout()
    if layout is not None:
        layout.invalidate()
        layout.activate()
    inner.updateGeometry()


class CollapsibleSection(QWidget):
    """可折叠的分组容器。"""

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._content_widget: QWidget | None = None
        self._expanded = bool(expanded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header_button = QToolButton(self)
        self.header_button.setObjectName("CollapsibleHeaderButton")
        self.header_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header_button.setArrowType(Qt.ArrowType.DownArrow if self._expanded else Qt.ArrowType.RightArrow)
        self.header_button.setText(str(title or "").strip())
        self.header_button.setCheckable(True)
        self.header_button.setChecked(self._expanded)
        self.header_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header_button.clicked.connect(self.set_expanded)
        root.addWidget(self.header_button)

        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("CollapsibleContentFrame")
        self.content_layout = QVBoxLayout(self.content_frame)
        self.content_layout.setContentsMargins(0, 8, 0, 0)
        self.content_layout.setSpacing(0)
        self.content_frame.setVisible(self._expanded)
        root.addWidget(self.content_frame)
        self._apply_expanded_size_policy(self._expanded)

    def _apply_expanded_size_policy(self, expanded: bool) -> None:
        if expanded:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def set_content_widget(self, widget: QWidget) -> None:
        if self._content_widget is widget:
            return
        if self._content_widget is not None:
            self.content_layout.removeWidget(self._content_widget)
            self._content_widget.setParent(None)
        self._content_widget = widget
        self.content_layout.addWidget(widget)

    def refresh_section_layout(self) -> None:
        content = self._content_widget
        if content is not None:
            content.updateGeometry()
            content_layout = content.layout()
            if content_layout is not None:
                content_layout.invalidate()
                content_layout.activate()
        self.content_frame.updateGeometry()
        self.content_layout.invalidate()
        self.content_layout.activate()
        self.updateGeometry()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        state = bool(expanded)
        if self._expanded == state:
            self.header_button.setChecked(state)
            self.header_button.setArrowType(Qt.ArrowType.DownArrow if state else Qt.ArrowType.RightArrow)
            self.content_frame.setVisible(state)
            self._apply_expanded_size_policy(state)
            self.refresh_section_layout()
            return
        self._expanded = state
        self.header_button.blockSignals(True)
        self.header_button.setChecked(state)
        self.header_button.blockSignals(False)
        self.header_button.setArrowType(Qt.ArrowType.DownArrow if state else Qt.ArrowType.RightArrow)
        self.content_frame.setVisible(state)
        self._apply_expanded_size_policy(state)
        self.refresh_section_layout()
        self.toggled.emit(state)
