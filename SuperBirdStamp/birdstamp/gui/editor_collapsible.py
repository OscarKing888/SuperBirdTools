from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionTabWidgetFrame,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


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
