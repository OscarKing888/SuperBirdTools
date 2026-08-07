# -*- coding: utf-8 -*-
"""Reusable tag menu helpers for SuperViewer."""
from __future__ import annotations

from collections.abc import Callable, Iterable

try:
    from PyQt6.QtCore import QEvent, QObject, QPoint
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QEvent, QObject, QPoint

from .qt_compat import (
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QMenu,
    QTimer,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)


def _mouse_button_event_types() -> tuple:
    if hasattr(QEvent, "Type"):
        return (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        )
    return (
        QEvent.MouseButtonPress,
        QEvent.MouseButtonRelease,
        QEvent.MouseButtonDblClick,
    )


def _event_global_pos(event) -> QPoint:
    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    return event.globalPos()


def _root_context_menu(menu: QMenu) -> QMenu:
    root = menu
    parent = menu.parentWidget()
    while isinstance(parent, QMenu):
        root = parent
        parent = parent.parentWidget()
    return root


def _iter_visible_menus(root: QMenu):
    yield root
    for action in root.actions():
        child = action.menu() if hasattr(action, "menu") else None
        if child is not None and child.isVisible():
            yield from _iter_visible_menus(child)


def _global_pos_in_menus(root: QMenu, global_pos: QPoint) -> bool:
    for menu in _iter_visible_menus(root):
        local = menu.mapFromGlobal(global_pos)
        if menu.rect().contains(local):
            return True
    return False


class _KeepOpenMenuFilter(QObject):
    """Prevent QMenu from dismissing when clicking empty areas or widget actions."""

    _MOUSE_TYPES = None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if self._MOUSE_TYPES is None:
            type(self)._MOUSE_TYPES = _mouse_button_event_types()
        if event.type() not in self._MOUSE_TYPES:
            return False
        if not isinstance(obj, QMenu):
            return False
        action = obj.actionAt(event.pos())
        # Empty chrome / embedded widgets: keep menu open.
        # Plain QAction (e.g. "清除所有TAG") still goes through and can dismiss.
        if action is None or isinstance(action, QWidgetAction):
            return True
        return False


class _OutsideClickCloseFilter(QObject):
    """Close the whole context-menu tree when pressing outside any visible menu."""

    _PRESS_TYPE = None

    def __init__(self, root_menu: QMenu) -> None:
        super().__init__(root_menu)
        self._root_menu = root_menu

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if self._PRESS_TYPE is None:
            if hasattr(QEvent, "Type"):
                type(self)._PRESS_TYPE = QEvent.Type.MouseButtonPress
            else:
                type(self)._PRESS_TYPE = QEvent.MouseButtonPress
        if event.type() != self._PRESS_TYPE:
            return False
        root = self._root_menu
        if root is None or not root.isVisible():
            return False
        global_pos = _event_global_pos(event)
        if _global_pos_in_menus(root, global_pos):
            return False
        # Outside the entire right-click menu tree: dismiss everything.
        # Do not consume the event so the click can still select another image.
        root.close()
        return False


def _install_keep_open_guard(menu: QMenu) -> None:
    if getattr(menu, "_sv_keep_open_guard", None) is not None:
        return
    guard = _KeepOpenMenuFilter(menu)
    menu.installEventFilter(guard)
    menu._sv_keep_open_guard = guard  # type: ignore[attr-defined]


def _install_outside_close_guard(menu: QMenu) -> None:
    """Ensure clicks outside the menu tree close the root context menu."""
    root = _root_context_menu(menu)
    if getattr(root, "_sv_outside_close_guard", None) is not None:
        return
    app = QApplication.instance()
    if app is None:
        return
    guard = _OutsideClickCloseFilter(root)
    app.installEventFilter(guard)
    root._sv_outside_close_guard = guard  # type: ignore[attr-defined]

    def _cleanup() -> None:
        current = getattr(root, "_sv_outside_close_guard", None)
        if current is not guard:
            return
        app.removeEventFilter(guard)
        root._sv_outside_close_guard = None  # type: ignore[attr-defined]

    root.aboutToHide.connect(_cleanup)


def add_filterable_tag_actions(
    menu: QMenu,
    tags: Iterable[str],
    on_triggered: Callable[[str, bool], None],
    *,
    checkable: bool = False,
    checked_provider: Callable[[str], bool] | None = None,
    keep_open: bool = False,
    filter_placeholder: str = "过滤标签…",
    no_match_text: str = "没有匹配的标签",
) -> list:
    """Add tag actions to ``menu`` with a filter edit at the top.

    When ``keep_open`` and ``checkable`` are both True, tags are rendered inside a
    single embedded panel so clicks anywhere in the tag list (including empty
    padding) do not dismiss the parent ``QMenu``. Clicks outside the whole
    context-menu tree still close every open menu.
    """
    clean_tags = []
    seen = set()
    for tag in tags or []:
        clean = str(tag or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        clean_tags.append(clean)
    if not clean_tags:
        return []

    use_keep_open = bool(keep_open and checkable)
    if use_keep_open:
        return _add_keep_open_tag_panel(
            menu,
            clean_tags,
            on_triggered,
            checked_provider=checked_provider,
            filter_placeholder=filter_placeholder,
            no_match_text=no_match_text,
        )

    filter_edit = QLineEdit(menu)
    filter_edit.setPlaceholderText(filter_placeholder)
    filter_edit.setClearButtonEnabled(True)
    filter_edit.setStyleSheet("QLineEdit { padding: 5px 8px; min-width: 180px; }")
    filter_action = QWidgetAction(menu)
    filter_action.setDefaultWidget(filter_edit)
    menu.addAction(filter_action)
    menu.addSeparator()

    tag_actions = []
    for tag in clean_tags:
        action = menu.addAction(tag)
        if checkable:
            action.setCheckable(True)
            if checked_provider is not None:
                action.setChecked(bool(checked_provider(tag)))
        action.triggered.connect(
            lambda checked=False, t=tag: on_triggered(t, bool(checked))
        )
        tag_actions.append((tag, action))

    empty_match_action = menu.addAction(no_match_text)
    empty_match_action.setEnabled(False)
    empty_match_action.setVisible(False)

    def apply_filter(text: str) -> None:
        needle = str(text or "").strip().casefold()
        visible_count = 0
        for tag, action in tag_actions:
            visible = not needle or needle in tag.casefold()
            action.setVisible(visible)
            if visible:
                visible_count += 1
        empty_match_action.setVisible(visible_count == 0)

    filter_edit.textChanged.connect(apply_filter)
    QTimer.singleShot(0, filter_edit.setFocus)
    return [action for _tag, action in tag_actions]


def _add_keep_open_tag_panel(
    menu: QMenu,
    clean_tags: list[str],
    on_triggered: Callable[[str, bool], None],
    *,
    checked_provider: Callable[[str], bool] | None,
    filter_placeholder: str,
    no_match_text: str,
) -> list:
    """Build one full-width panel so the whole tag region absorbs clicks."""
    _install_keep_open_guard(menu)
    _install_outside_close_guard(menu)

    panel = QWidget(menu)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(2)

    filter_edit = QLineEdit(panel)
    filter_edit.setPlaceholderText(filter_placeholder)
    filter_edit.setClearButtonEnabled(True)
    filter_edit.setStyleSheet("QLineEdit { padding: 5px 8px; min-width: 180px; }")
    layout.addWidget(filter_edit)

    tag_rows: list[tuple[str, QCheckBox]] = []
    for tag in clean_tags:
        checkbox = QCheckBox(tag, panel)
        checkbox.setStyleSheet("QCheckBox { padding: 4px 8px; min-width: 180px; }")
        if checked_provider is not None:
            checkbox.setChecked(bool(checked_provider(tag)))
        checkbox.toggled.connect(
            lambda checked=False, t=tag: on_triggered(t, bool(checked))
        )
        layout.addWidget(checkbox)
        tag_rows.append((tag, checkbox))

    empty_label = QLabel(no_match_text, panel)
    empty_label.setEnabled(False)
    empty_label.setVisible(False)
    empty_label.setStyleSheet("QLabel { padding: 4px 8px; color: gray; }")
    layout.addWidget(empty_label)

    panel_action = QWidgetAction(menu)
    panel_action.setDefaultWidget(panel)
    menu.addAction(panel_action)

    def apply_filter(text: str) -> None:
        needle = str(text or "").strip().casefold()
        visible_count = 0
        for tag, checkbox in tag_rows:
            visible = not needle or needle in tag.casefold()
            checkbox.setVisible(visible)
            if visible:
                visible_count += 1
        empty_label.setVisible(visible_count == 0)

    filter_edit.textChanged.connect(apply_filter)
    QTimer.singleShot(0, filter_edit.setFocus)
    return [panel_action]


__all__ = [
    "add_filterable_tag_actions",
]
