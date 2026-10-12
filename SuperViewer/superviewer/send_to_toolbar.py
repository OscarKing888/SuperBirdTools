"""「发送到」工具栏：每个已配置的外部应用一个按钮，使用该应用自己的系统图标。"""
from __future__ import annotations

from typing import Callable

try:
    from PyQt6.QtWidgets import QToolBar
except ImportError:
    from PyQt5.QtWidgets import QToolBar

from app_common.send_to_app import get_external_apps
from app_common.send_to_app.icon import external_app_display_name, external_app_icon

from . import qt_compat
from .file_context_menu import menu_icon
from .qt_compat import QAction, QSize

SEND_TO_TOOLBAR_OBJECT_NAME = "sendToToolBar"
SETTINGS_ACTION_TEXT = "外部应用设置…"


def create_send_to_toolbar(window) -> QToolBar:
    """创建固定的「发送到」工具栏；按钮由 rebuild_send_to_toolbar() 按配置填充。"""
    toolbar = QToolBar("发送到", window)
    toolbar.setObjectName(SEND_TO_TOOLBAR_OBJECT_NAME)
    toolbar.setMovable(False)
    toolbar.setIconSize(QSize(20, 20))
    styles = getattr(qt_compat.Qt, "ToolButtonStyle", qt_compat.Qt)
    toolbar.setToolButtonStyle(styles.ToolButtonIconOnly)
    return toolbar


def rebuild_send_to_toolbar(
    toolbar: QToolBar,
    send: Callable[[dict], None],
    apps: list[dict] | None = None,
    *,
    open_settings: Callable[[], None] | None = None,
) -> list[QAction]:
    """按外部应用列表重建按钮，末尾放「外部应用设置」齿轮。返回新建的应用动作。

    提供 open_settings 时工具栏始终显示（未配置应用时只有齿轮），否则无应用即隐藏。
    """
    for action in list(toolbar.actions()):
        toolbar.removeAction(action)
        action.deleteLater()
    if apps is None:
        apps = get_external_apps()
    actions: list[QAction] = []
    for app in apps:
        name = external_app_display_name(app)
        action = QAction(external_app_icon(app), name, toolbar)
        action.setToolTip(name)
        action.setStatusTip(f"将选中的文件发送到 {name}")
        action.triggered.connect(lambda _checked=False, a=dict(app): send(a))
        toolbar.addAction(action)
        actions.append(action)
    if open_settings is not None:
        if actions:
            toolbar.addSeparator()
        gear = QAction(menu_icon("gear"), SETTINGS_ACTION_TEXT, toolbar)
        gear.setToolTip(SETTINGS_ACTION_TEXT)
        gear.setStatusTip("添加、删除或调整「发送到」外部应用")
        gear.triggered.connect(lambda _checked=False: open_settings())
        toolbar.addAction(gear)
    toolbar.setVisible(bool(actions) or open_settings is not None)
    return actions
