# -*- coding: utf-8 -*-
"""SuperBirdStamp 性能探针薄封装（复用 app_common.perf_probe）。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app_common.log import get_logger
from app_common.perf_probe import elapsed_ms, perf_counter, perf_log, perf_probes_enabled

_log = get_logger("birdstamp.perf")

SLOW_PAINT_MS = 16.0


def enabled() -> bool:
    return perf_probes_enabled()


def plog(message: str, *args: Any) -> None:
    perf_log(_log, message, *args)


@contextmanager
def span(name: str, **fields: Any) -> Iterator[None]:
    if not enabled():
        yield
        return
    start = perf_counter()
    try:
        yield
    finally:
        field_text = " ".join(f"{key}={value}" for key, value in fields.items())
        if field_text:
            plog("%s %s elapsed_ms=%.1f", name, field_text, elapsed_ms(start))
        else:
            plog("%s elapsed_ms=%.1f", name, elapsed_ms(start))


class DragProbe:
    """聚合一次拖拽会话（press→release），release 时输出单行摘要。"""

    def __init__(self) -> None:
        self.mode = ""
        self.moves = 0
        self.handler_ms = 0.0
        self.paint_ms = 0.0
        self.callback_ms = 0.0
        self._active = False

    def begin(self, mode: str) -> None:
        if not enabled():
            return
        self.mode = str(mode or "")
        self.moves = 0
        self.handler_ms = 0.0
        self.paint_ms = 0.0
        self.callback_ms = 0.0
        self._active = True

    def end(self) -> None:
        if not self._active:
            return
        self._active = False
        plog(
            "drag_summary mode=%s moves=%s handler_ms=%.1f paint_ms=%.1f callback_ms=%.1f",
            self.mode,
            self.moves,
            self.handler_ms,
            self.paint_ms,
            self.callback_ms,
        )

    def add_move_handler(self, elapsed: float) -> None:
        if not self._active:
            return
        self.moves += 1
        self.handler_ms += float(elapsed)

    def add_paint(self, elapsed: float) -> None:
        if not self._active:
            return
        self.paint_ms += float(elapsed)
        if float(elapsed) >= SLOW_PAINT_MS:
            plog("slow_paint mode=%s elapsed_ms=%.1f", self.mode, float(elapsed))

    def add_callback(self, elapsed: float) -> None:
        if not self._active:
            return
        self.callback_ms += float(elapsed)


__all__ = [
    "DragProbe",
    "SLOW_PAINT_MS",
    "enabled",
    "plog",
    "span",
]
