from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget


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
