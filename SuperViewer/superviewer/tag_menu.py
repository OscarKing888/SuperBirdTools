# -*- coding: utf-8 -*-
"""Reusable tag menu helpers for SuperViewer."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from .qt_compat import QLineEdit, QMenu, QTimer, QWidgetAction


def add_filterable_tag_actions(
    menu: QMenu,
    tags: Iterable[str],
    on_triggered: Callable[[str, bool], None],
    *,
    checkable: bool = False,
    checked_provider: Callable[[str], bool] | None = None,
    filter_placeholder: str = "过滤标签…",
    no_match_text: str = "没有匹配的标签",
) -> list:
    """Add tag actions to ``menu`` with a filter edit at the top."""
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


__all__ = [
    "add_filterable_tag_actions",
]
