from __future__ import annotations

from pathlib import Path
from typing import Iterable

from birdstamp.constants import SUPPORTED_EXTENSIONS


def _normalize_extensions(extensions: Iterable[str] | None) -> set[str]:
    if not extensions:
        return set(SUPPORTED_EXTENSIONS)
    normalized: set[str] = set()
    for ext in extensions:
        if not ext:
            continue
        ext = ext.lower()
        normalized.add(ext if ext.startswith(".") else f".{ext}")
    return normalized


def discover_inputs(
    input_path: Path,
    recursive: bool = False,
    extensions: Iterable[str] | None = None,
) -> list[Path]:
    exts = _normalize_extensions(extensions)
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in exts else []
    if not input_path.exists():
        return []
    if recursive:
        files = [p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    else:
        files = [p for p in input_path.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files)

