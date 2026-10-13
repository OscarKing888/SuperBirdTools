# -*- coding: utf-8 -*-
"""照片过滤栏的多选菜单与紧凑换行布局，状态由宿主列表持有。"""
try:
    from PyQt6.QtCore import QPoint, QRect, QSize, Qt
    from PyQt6.QtWidgets import QLayout, QMenu, QToolButton
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QPoint, QRect, QSize, Qt
    from PyQt5.QtWidgets import QLayout, QMenu, QToolButton


class FilterMenu(QMenu):
    """普通动作照常关闭；勾选动作支持连续鼠标和键盘多选。"""

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.pos())
        if (event.button() == getattr(Qt, "MouseButton", Qt).LeftButton
                and action is not None and action.isEnabled() and action.isCheckable()):
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        keys = getattr(Qt, "Key", Qt)
        action = self.activeAction()
        if (event.key() in (keys.Key_Space, keys.Key_Return, keys.Key_Enter)
                and action is not None and action.isEnabled() and action.isCheckable()):
            action.trigger()
            event.accept()
            return
        super().keyPressEvent(event)


class FilterDropdown(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoRaise(False)
        self.setCheckable(True)
        self.setFocusPolicy(getattr(Qt, "FocusPolicy", Qt).StrongFocus)
        self.setPopupMode(getattr(QToolButton, "ToolButtonPopupMode", QToolButton).InstantPopup)
        self.setMenu(FilterMenu(self))


class FilterFlowLayout(QLayout):
    """按实际字体尺寸换行；只移动现有控件，不重建菜单或过滤状态。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < self.count() else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < self.count() else None

    def expandingDirections(self):
        return getattr(Qt, "Orientation", Qt).Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect, *, apply):
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self._items:
            size = item.sizeHint()
            size.setWidth(min(size.width(), max(0, rect.width())))
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y, row_height = rect.x(), y + row_height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y()
