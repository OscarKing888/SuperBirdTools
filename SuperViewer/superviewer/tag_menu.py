# -*- coding: utf-8 -*-
"""Reusable tag menu helpers for SuperViewer."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

try:
    from PyQt6.QtCore import QEvent, QObject, QPoint
except ImportError:  # pragma: no cover - PyQt5 fallback
    from PyQt5.QtCore import QEvent, QObject, QPoint

from .photo_tags import TagTreeNode, iter_tag_tree_leaves
from .qt_compat import (
    QApplication,
    QCheckBox,
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


def _nodes_from_flat_tags(tags: Iterable[str] | None) -> list[TagTreeNode]:
    nodes: list[TagTreeNode] = []
    seen: set[str] = set()
    for tag in tags or []:
        clean = str(tag or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        nodes.append(TagTreeNode(name=clean))
    return nodes


@dataclass
class _LeafEntry:
    tag: str
    set_visible: Callable[[bool], None]
    action: object | None = None


@dataclass
class _GroupEntry:
    name: str
    menu_action: object
    children: list["_MenuEntry"] = field(default_factory=list)


_MenuEntry = _LeafEntry | _GroupEntry


def _apply_entry_filter(entry: _MenuEntry, needle: str) -> bool:
    """Show/hide entry for *needle*; return whether anything visible remains."""
    if isinstance(entry, _LeafEntry):
        visible = not needle or needle in entry.tag.casefold()
        entry.set_visible(visible)
        return visible
    visible_children = 0
    for child in entry.children:
        if _apply_entry_filter(child, needle):
            visible_children += 1
    visible = visible_children > 0
    try:
        entry.menu_action.setVisible(visible)
    except Exception:
        pass
    return visible


def add_filterable_tag_actions(
    menu: QMenu,
    tags: Iterable[str] | None = None,
    on_triggered: Callable[[str, bool], None] | None = None,
    *,
    tree: Iterable[TagTreeNode] | None = None,
    checkable: bool = False,
    checked_provider: Callable[[str], bool] | None = None,
    keep_open: bool = False,
    leaf_filter: Callable[[str], bool] | None = None,
    filter_placeholder: str = "过滤标签…",
    no_match_text: str = "没有匹配的标签",
) -> list:
    """Add tag actions to ``menu`` with a filter edit at the top.

    Pass ``tree`` for nested group/leaf menus. When ``tree`` is omitted, ``tags``
    is treated as a flat leaf list (backward compatible).

    When ``keep_open`` and ``checkable`` are both True, leaf tags use embedded
    checkbox widgets so clicks do not dismiss the parent ``QMenu``. Clicks
    outside the whole context-menu tree still close every open menu.
    """
    if on_triggered is None:
        raise TypeError("on_triggered is required")

    if tree is not None:
        nodes = list(tree)
    else:
        nodes = _nodes_from_flat_tags(tags)

    if leaf_filter is not None:
        nodes = _filter_tree_leaves(nodes, leaf_filter)

    if not iter_tag_tree_leaves(nodes):
        return []

    use_keep_open = bool(keep_open and checkable)
    if use_keep_open:
        _install_keep_open_guard(menu)
        _install_outside_close_guard(menu)

    filter_edit = QLineEdit(menu)
    filter_edit.setPlaceholderText(filter_placeholder)
    filter_edit.setClearButtonEnabled(True)
    filter_edit.setStyleSheet("QLineEdit { padding: 5px 8px; min-width: 180px; }")
    filter_action = QWidgetAction(menu)
    filter_action.setDefaultWidget(filter_edit)
    menu.addAction(filter_action)
    menu.addSeparator()

    entries, leaf_actions = _populate_tag_tree_menu(
        menu,
        nodes,
        on_triggered,
        checkable=checkable,
        checked_provider=checked_provider,
        keep_open=use_keep_open,
    )

    empty_match_action = menu.addAction(no_match_text)
    empty_match_action.setEnabled(False)
    empty_match_action.setVisible(False)

    def apply_filter(text: str) -> None:
        needle = str(text or "").strip().casefold()
        visible_count = 0
        for entry in entries:
            if _apply_entry_filter(entry, needle):
                visible_count += 1
        empty_match_action.setVisible(visible_count == 0)

    filter_edit.textChanged.connect(apply_filter)
    QTimer.singleShot(0, filter_edit.setFocus)
    return leaf_actions


def _filter_tree_leaves(
    nodes: list[TagTreeNode],
    leaf_filter: Callable[[str], bool],
) -> list[TagTreeNode]:
    """Return a pruned copy keeping groups that still contain matching leaves."""
    result: list[TagTreeNode] = []
    for node in nodes:
        if node.is_leaf:
            if leaf_filter(node.name):
                result.append(TagTreeNode(name=node.name))
            continue
        children = _filter_tree_leaves(list(node.children), leaf_filter)
        if children:
            result.append(TagTreeNode(name=node.name, children=children))
    return result


def _populate_tag_tree_menu(
    menu: QMenu,
    nodes: list[TagTreeNode],
    on_triggered: Callable[[str, bool], None],
    *,
    checkable: bool,
    checked_provider: Callable[[str], bool] | None,
    keep_open: bool,
) -> tuple[list[_MenuEntry], list]:
    entries: list[_MenuEntry] = []
    leaf_actions: list = []

    if keep_open:
        _install_keep_open_guard(menu)
        _install_outside_close_guard(menu)

    # Collect consecutive leaves into one panel when keep_open so padding clicks
    # stay inside a QWidgetAction; preserve group/leaf order from the config.
    pending_leaves: list[TagTreeNode] = []

    def flush_leaves() -> None:
        nonlocal pending_leaves
        if not pending_leaves:
            return
        if keep_open:
            panel_entries, actions = _add_keep_open_leaf_panel(
                menu,
                [node.name for node in pending_leaves],
                on_triggered,
                checked_provider=checked_provider,
            )
            entries.extend(panel_entries)
            leaf_actions.extend(actions)
        else:
            for leaf in pending_leaves:
                action = menu.addAction(leaf.name)
                if checkable:
                    action.setCheckable(True)
                    if checked_provider is not None:
                        action.setChecked(bool(checked_provider(leaf.name)))
                action.triggered.connect(
                    lambda checked=False, t=leaf.name: on_triggered(t, bool(checked))
                )
                entries.append(
                    _LeafEntry(
                        tag=leaf.name,
                        set_visible=action.setVisible,
                        action=action,
                    )
                )
                leaf_actions.append(action)
        pending_leaves = []

    for node in nodes:
        if node.is_group:
            flush_leaves()
            submenu = menu.addMenu(node.name)
            child_entries, child_actions = _populate_tag_tree_menu(
                submenu,
                list(node.children),
                on_triggered,
                checkable=checkable,
                checked_provider=checked_provider,
                keep_open=keep_open,
            )
            entries.append(
                _GroupEntry(
                    name=node.name,
                    menu_action=submenu.menuAction(),
                    children=child_entries,
                )
            )
            leaf_actions.extend(child_actions)
        else:
            pending_leaves.append(node)
    flush_leaves()
    return entries, leaf_actions


def _add_keep_open_leaf_panel(
    menu: QMenu,
    tags: list[str],
    on_triggered: Callable[[str, bool], None],
    *,
    checked_provider: Callable[[str], bool] | None,
) -> tuple[list[_LeafEntry], list]:
    """Build one full-width panel so the whole leaf region absorbs clicks."""
    panel = QWidget(menu)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(2)

    leaf_entries: list[_LeafEntry] = []
    for tag in tags:
        checkbox = QCheckBox(tag, panel)
        checkbox.setStyleSheet("QCheckBox { padding: 4px 8px; min-width: 180px; }")
        if checked_provider is not None:
            checkbox.setChecked(bool(checked_provider(tag)))
        checkbox.toggled.connect(
            lambda checked=False, t=tag: on_triggered(t, bool(checked))
        )
        layout.addWidget(checkbox)
        leaf_entries.append(
            _LeafEntry(tag=tag, set_visible=checkbox.setVisible, action=checkbox)
        )

    panel_action = QWidgetAction(menu)
    panel_action.setDefaultWidget(panel)
    menu.addAction(panel_action)

    # Hide the whole panel when every leaf is filtered out.
    def set_panel_visible_from_children() -> None:
        any_visible = any(
            entry.action.isVisible()  # type: ignore[union-attr]
            for entry in leaf_entries
            if entry.action is not None
        )
        panel_action.setVisible(any_visible)

    wrapped: list[_LeafEntry] = []
    for entry in leaf_entries:
        checkbox = entry.action

        def _make_setter(cb, refresh=set_panel_visible_from_children):
            def _set(visible: bool) -> None:
                cb.setVisible(visible)
                refresh()

            return _set

        wrapped.append(
            _LeafEntry(
                tag=entry.tag,
                set_visible=_make_setter(checkbox),
                action=checkbox,
            )
        )

    return wrapped, [panel_action]


__all__ = [
    "add_filterable_tag_actions",
]
