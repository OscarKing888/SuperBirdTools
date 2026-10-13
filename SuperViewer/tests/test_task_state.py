from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from SuperViewer.superviewer.task_state import TaskSlot

_APP = QApplication.instance() or QApplication([])


class _Controller(QObject):
    busy_changed = pyqtSignal(bool)
    _worker = TaskSlot("busy_changed", "busy")
    _adopt_worker = TaskSlot("busy_changed", "busy")

    def __init__(self) -> None:
        super().__init__()
        self.events: list[bool] = []
        self.busy_changed.connect(self.events.append)
        self._worker = self._adopt_worker = None

    @property
    def busy(self) -> bool:
        return self._worker is not None or self._adopt_worker is not None


def test_slot_emits_only_when_combined_state_changes() -> None:
    controller = _Controller()
    assert controller.events == [] and controller._worker is None
    worker, adopt = object(), object()
    controller._worker = worker
    assert controller._worker is worker and controller.events == [True]
    controller._adopt_worker = adopt  # 仍在忙，不重复通知
    controller._worker = None
    assert controller.events == [True]
    controller._adopt_worker = None
    assert controller.events == [True, False]
    controller._worker = None
    assert controller.events == [True, False]


def test_slots_are_per_instance() -> None:
    first, second = _Controller(), _Controller()
    first._worker = object()
    assert first.busy and not second.busy
    assert second.events == []
    assert isinstance(_Controller._worker, TaskSlot)
