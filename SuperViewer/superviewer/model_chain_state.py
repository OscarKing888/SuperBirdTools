# -*- coding: utf-8 -*-
"""Saved model chain (trace window → right-hand model windows), restored next time.

One small JSON file per user, next to the downloaded models
(``…/SuperBirdTools/model_chain.json``): the toolbar's 「自动传给下一窗口」 and, per
window, its model, its input when the user picked one, and the detector parameters.
SAM's drawn boxes / points are not saved (they belong to one photo). Everything read
back is validated; a missing or broken file is an empty chain. Qt-free.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

STATE_FILENAME = "model_chain.json"
STATE_VERSION = 1
MAX_STAGES = 12
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\.pt$")
_INPUTS = ("previous", "trace", "image")
_CHOICES = {"scope": ("full", "view"), "use": ("crop", "mask")}
_INTS = {"margin": (30, 0, 200), "imgsz": (640, 320, 2048), "min_conf": (10, 1, 95)}
_BOOLS = {"birds_only": True, "lift": True}


def default_path() -> Path:
    from bird_sharpness import model_catalog

    return model_catalog.user_model_dir().parent / STATE_FILENAME


def _stage(data) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    model = data.get("model")
    if not isinstance(model, str) or not (model == "auto" or _MODEL_RE.match(model)):
        return None
    stage = {"model": model, "input": data.get("input") if data.get("input") in _INPUTS else None}
    for key, allowed in _CHOICES.items():
        stage[key] = data.get(key) if data.get(key) in allowed else allowed[0]
    for key, (default, low, high) in _INTS.items():
        value = data.get(key, default)
        stage[key] = max(low, min(high, value)) if isinstance(value, int) and not isinstance(value, bool) else default
    for key, default in _BOOLS.items():
        value = data.get(key, default)
        stage[key] = value if isinstance(value, bool) else default
    return stage


def normalize(data) -> dict:
    """``{"version", "auto", "stages": [...]}`` with every value checked."""
    source = data if isinstance(data, dict) else {}
    stages = [s for s in (_stage(d) for d in (source.get("stages") or [])
                          if isinstance(source.get("stages"), list)) if s is not None]
    auto = source.get("auto", True)
    return {"version": STATE_VERSION, "auto": auto if isinstance(auto, bool) else True,
            "stages": stages[:MAX_STAGES]}


class ModelChainStore:
    """Reads / writes the saved chain (atomic replace, UTF-8)."""

    def __init__(self, path=None) -> None:
        self.path = Path(path) if path is not None else default_path()

    def load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return normalize(json.load(f))
        except (OSError, ValueError):
            return normalize(None)

    def save(self, state) -> dict:
        state = normalize(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".model_chain.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return state
