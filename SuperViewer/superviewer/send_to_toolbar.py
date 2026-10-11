"""「发送到」工具栏：每个已配置的外部应用一个按钮，使用该应用自己的系统图标。"""
from __future__ import annotations

import os
from typing import Callable

try:
    from PyQt6.QtWidgets import QToolBar
except ImportError:
    from PyQt5.QtWidgets import QToolBar

from app_common.send_to_app import get_external_apps
from app_common.send_to_app.icon import external_app_display_name, external_app_icon

from . import qt_compat
from .qt_compat import QAction, QSize

SEND_TO_TOOLBAR_OBJECT_NAME = "sendToToolBar"
_APP_SUFFIXES = (".app", ".exe")


def send_to_app_hover_name(app: dict) -> str:
    """按钮悬停显示的应用名：配置名去掉 .app/.exe 后缀，如 “Adobe Photoshop 2026”。"""
    name = external_app_display_name(app)
    # 配置名为空时 display_name 会回退到完整路径，悬停只保留文件名部分。
    if name == str(app.get("path") or "").strip():
        name = os.path.basename(name.rstrip("/\\")) or name
    stem, ext = os.path.splitext(name)
    return stem if stem and ext.lower() in _APP_SUFFIXES else name


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
) -> list[QAction]:
    """按外部应用列表重建按钮；列表为空时隐藏工具栏。返回新建的动作。"""
    for action in list(toolbar.actions()):
        toolbar.removeAction(action)
        action.deleteLater()
    if apps is None:
        apps = get_external_apps()
    actions: list[QAction] = []
    for app in apps:
        name = send_to_app_hover_name(app)
        action = QAction(external_app_icon(app), name, toolbar)
        action.setToolTip(name)
        action.setStatusTip(f"将选中的文件发送到 {name}")
        action.triggered.connect(lambda _checked=False, a=dict(app): send(a))
        toolbar.addAction(action)
        actions.append(action)
    toolbar.setVisible(bool(actions))
    return actions
