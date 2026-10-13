"""控制器任务槽：给保存后台 worker 的属性赋值时，自动发出运行状态变化信号。

用法（控制器须为 QObject，且在 ``super().__init__()`` 之后才给槽赋值）::

    class DenoiseController(QObject):
        busy_changed = pyqtSignal(bool)
        _worker = TaskSlot("busy_changed", "busy")

        @property
        def busy(self):
            return self._worker is not None

每次 ``self._worker = ...`` 后重新计算 ``busy``，只有值真正变化时才发 ``busy_changed``。
启动、完成、失败、关窗等所有路径都经过同一个赋值，因此不会漏发；同一个信号可由多个槽共同决定
（例如识鸟的 ``_worker`` 与 ``_adopt_worker``）。
"""
from __future__ import annotations


class TaskSlot:
    def __init__(self, signal_name: str, state_name: str) -> None:
        self._signal_name = signal_name
        self._state_name = state_name
        self._key = ""

    def __set_name__(self, owner, name: str) -> None:
        self._key = f"_task_slot_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.get(self._key)

    def __set__(self, obj, value) -> None:
        obj.__dict__[self._key] = value
        state = bool(getattr(obj, self._state_name))
        last_key = f"_task_state_{self._signal_name}"
        if obj.__dict__.get(last_key, False) != state:
            obj.__dict__[last_key] = state
            getattr(obj, self._signal_name).emit(state)
