"""「照片处理」工具栏：识鸟 / 候选鸟名 / 手动指定鸟名 / 清晰度检测 / 批量降噪。

按钮只是右键菜单同名动作的快捷入口，任务仍由各控制器创建和管理；
作用对象是文件列表当前选中的照片（无多选时为当前照片）。
任务运行中按钮显示运行标记，点击弹出「查看进度 / 停止」，与右键菜单一致。
"""
from __future__ import annotations

from typing import Callable

try:
    from PyQt6.QtCore import QObject, QPoint, QTimer
    from PyQt6.QtWidgets import QMenu, QMessageBox, QToolBar, QToolButton
except ImportError:  # pragma: no cover
    from PyQt5.QtCore import QObject, QPoint, QTimer
    from PyQt5.QtWidgets import QMenu, QMessageBox, QToolBar, QToolButton

from . import qt_compat
from .file_context_menu import menu_icon
from .qt_compat import QAction, QSize

PROCESS_TOOLBAR_OBJECT_NAME = "processToolBar"
_REFRESH_INTERVAL_MS = 500


class _TaskButton:
    """一个工具栏动作及其所属控制器的运行状态。"""

    def __init__(self, action: QAction, kind: str, idle_tip: str, task_name: str, controller):
        self.action = action
        self.kind = kind
        self.idle_tip = idle_tip
        self.task_name = task_name
        self.controller = controller
        self.busy: bool | None = None


class ProcessToolbar(QObject):
    def __init__(self, window, file_list, bird_id, sharpness, denoise) -> None:
        super().__init__(window)
        self._window = window
        self._file_list = file_list
        self._bird_id = bird_id
        self._sharpness = sharpness
        self._denoise = denoise
        toolbar = self.toolbar = QToolBar("照片处理", window)
        toolbar.setObjectName(PROCESS_TOOLBAR_OBJECT_NAME)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        styles = getattr(qt_compat.Qt, "ToolButtonStyle", qt_compat.Qt)
        toolbar.setToolButtonStyle(styles.ToolButtonIconOnly)

        self.identify = self._add_task(
            "bird", "识别鸟种…", "识别选中照片的鸟种（SuperPicky）；右侧箭头可选逐只识别",
            "识鸟", bird_id, lambda paths: bird_id.start_for_paths(paths),
            menu_items=[("逐只识别…", lambda paths: bird_id.start_for_paths(paths, per_bird=True))],
        )
        self.candidates = self._add_task(
            "candidates", "选择候选鸟名…", "为选中照片从已保存的识别候选中选择鸟名",
            "识鸟", bird_id, lambda paths: bird_id.start_for_paths(paths, saved_candidates=True),
        )
        self.manual_name = self._add_action(
            "pencil", "手动指定鸟名…", "从鸟类名录中为选中照片手动指定鸟名", self._open_manual_name,
        )
        toolbar.addSeparator()
        self.sharpness = self._add_task(
            "focus", "鸟清晰度检测", "检测选中照片的鸟清晰度并写入 XMP；右侧箭头可查看计算过程",
            "鸟清晰度检测", sharpness, sharpness.start_for_paths,
            menu_items=[("查看清晰度计算过程…", lambda paths: sharpness.show_trace(paths[0]))],
            menu_while_busy=True,
        )
        self.denoise = self._add_task(
            "denoise", "批量降噪", "NAFNet RGB 降噪选中照片；RAW 先渲染，原图保持不变",
            "降噪", denoise, denoise.start_for_paths,
        )
        self._tasks = [self.identify, self.candidates, self.sharpness, self.denoise]

        # 控制器没有运行状态信号，任务也可能从右键菜单启动；只读几个布尔标志，代价可忽略。
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ── construction ─────────────────────────────────────────────────────
    def _add_action(self, kind: str, text: str, tip: str, callback: Callable[[], None]) -> QAction:
        action = QAction(menu_icon(kind), text, self.toolbar)
        action.setToolTip(text)
        action.setStatusTip(tip)
        action.triggered.connect(lambda _checked=False: callback())
        self.toolbar.addAction(action)
        return action

    def _add_task(self, kind, text, tip, task_name, controller, start, *, menu_items=(), menu_while_busy=False):
        action = QAction(menu_icon(kind), text, self.toolbar)
        task = _TaskButton(action, kind, tip, task_name, controller)
        action.triggered.connect(lambda _checked=False, t=task, s=start: self._run(t, s))
        if menu_items:
            menu = QMenu(self.toolbar)
            for label, callback in menu_items:
                item = menu.addAction(label)
                item.triggered.connect(lambda _checked=False, c=callback, t=task, w=menu_while_busy:
                                       self._run_menu_item(t, c, w))
            action.setMenu(menu)
        self.toolbar.addAction(action)
        if menu_items:
            button = self.toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                popup = getattr(QToolButton, "ToolButtonPopupMode", QToolButton)
                button.setPopupMode(popup.MenuButtonPopup)
        return task

    # ── actions ──────────────────────────────────────────────────────────
    def _selected_paths(self, title: str) -> list[str]:
        paths = self._file_list.get_selected_file_paths()
        if not paths:
            QMessageBox.information(self._window, title, "请先在文件列表中选择照片。")
        return paths

    def _run(self, task: _TaskButton, start: Callable[[list[str]], object]) -> None:
        if task.controller.busy:
            self._show_busy_menu(task)
            return
        paths = self._selected_paths(task.action.text().rstrip("…"))
        if paths:
            start(list(paths))
        self.refresh()

    def _run_menu_item(self, task: _TaskButton, callback, allowed_while_busy: bool) -> None:
        if task.controller.busy and not allowed_while_busy:
            self._show_busy_menu(task)
            return
        paths = self._selected_paths(task.action.text().rstrip("…"))
        if paths:
            callback(list(paths))
        self.refresh()

    def _open_manual_name(self) -> None:
        catalog = self._bird_id.catalog
        if catalog.applying:
            return
        paths = self._selected_paths("手动指定鸟名")
        if not paths or not self._file_list._file_writes_allowed("手动指定鸟名", warn=True):
            return
        catalog.open(list(paths))
        self.refresh()

    def busy_menu(self, task: _TaskButton) -> QMenu:
        menu = QMenu(self.toolbar)
        menu.addAction(f"查看{task.task_name}进度", task.controller.show_progress)
        menu.addAction(f"停止{task.task_name}", task.controller.stop)
        return menu

    def _show_busy_menu(self, task: _TaskButton) -> None:
        menu = self.busy_menu(task)
        button = self.toolbar.widgetForAction(task.action)
        anchor = button.mapToGlobal(QPoint(0, button.height())) if button is not None else None
        try:
            if anchor is not None:
                menu.exec(anchor)
        finally:
            menu.deleteLater()
        self.refresh()

    # ── state ────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        for task in self._tasks:
            busy = bool(task.controller.busy)
            if busy == task.busy:
                continue
            task.busy = busy
            task.action.setIcon(menu_icon(task.kind, busy=busy))
            if busy:
                tip = f"{task.task_name}进行中：点击查看进度或停止"
            else:
                tip = task.action.text()
            task.action.setToolTip(tip)
            task.action.setStatusTip(tip if busy else task.idle_tip)
        self.manual_name.setEnabled(not self._bird_id.catalog.applying)

    def request_shutdown(self) -> None:
        self._timer.stop()
        self.toolbar.setEnabled(False)
