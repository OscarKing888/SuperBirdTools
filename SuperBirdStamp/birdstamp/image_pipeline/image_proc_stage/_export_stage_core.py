from __future__ import annotations

from functools import lru_cache
from types import ModuleType


@lru_cache(maxsize=1)
def export_stage_core() -> ModuleType:
    from birdstamp.export_stage import core as export_core

    return export_core
