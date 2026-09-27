"""将预览画布按键交给当前文件视图，复用列表快捷键与快速浏览节拍。"""
from .qt_compat import QApplication, Qt

try:
    from PyQt6.QtCore import QEvent, QObject
    from PyQt6.QtGui import QKeyEvent, QKeySequence
except ImportError:
    from PyQt5.QtCore import QEvent, QObject
    from PyQt5.QtGui import QKeyEvent, QKeySequence

_EVENTS = getattr(QEvent, 'Type', QEvent)
_KEYS = getattr(Qt, 'Key', Qt)
_CONTEXT = getattr(Qt, 'ShortcutContext', Qt)
_MATCH = getattr(QKeySequence, 'SequenceMatch', QKeySequence)


class PreviewKeyRouter(QObject):
    def __init__(self, canvas, file_list, parent):
        super().__init__(parent)
        self.canvas = canvas
        self.file_list = file_list
        self._forwarding = False
        canvas.setFocusPolicy(getattr(Qt, 'FocusPolicy', Qt).StrongFocus)
        canvas.installEventFilter(self)

    def _local_shortcut(self, event):
        # Copy/Cut/Paste 的作用域是文件列表子控件；转用已有快捷键对象，不复制其操作实现。
        if hasattr(event, 'keyCombination'):
            sequence = QKeySequence(event.keyCombination())
        else:
            sequence = QKeySequence(int(event.modifiers()) | event.key())
        for shortcut in self.file_list._file_action_shortcuts:
            if (shortcut.isEnabled() and shortcut.context() == _CONTEXT.WidgetWithChildrenShortcut
                    and shortcut.key().matches(sequence) == _MATCH.ExactMatch):
                return shortcut
        return None

    def eventFilter(self, watched, event):
        if watched is not self.canvas:
            return False
        kind = event.type()
        if kind in (_EVENTS.FocusOut, _EVENTS.WindowDeactivate, _EVENTS.Hide):
            self.file_list.stop_key_navigation_playback(commit=False)
            return False
        if not self.file_list.isEnabled():
            return False
        if self._forwarding or kind not in (_EVENTS.KeyPress, _EVENTS.KeyRelease, _EVENTS.ShortcutOverride):
            return False
        # Tab 仍用于正常焦点移动；路由不安装到输入框、工具栏或视频控件。
        if event.key() in (_KEYS.Key_Tab, _KEYS.Key_Backtab):
            return False
        shortcut = self._local_shortcut(event)
        if kind == _EVENTS.ShortcutOverride:
            if shortcut is not None:
                event.accept()
                return True
            return False
        if shortcut is not None:
            if kind == _EVENTS.KeyPress and (not event.isAutoRepeat() or shortcut.autoRepeat()):
                shortcut.activated.emit()
            return True
        view = (self.file_list._list_widget if self.file_list._view_mode == self.file_list._MODE_THUMB
                else self.file_list._tree_widget)
        forwarded = QKeyEvent(kind, event.key(), event.modifiers(), event.nativeScanCode(),
                              event.nativeVirtualKey(), event.nativeModifiers(), event.text(),
                              event.isAutoRepeat(), event.count())
        # 不移动焦点；完整保留物理/自动重复 press/release，列表仍是唯一节拍与选择状态所有者。
        self._forwarding = True
        try:
            QApplication.sendEvent(view, forwarded)
        finally:
            self._forwarding = False
        return True
