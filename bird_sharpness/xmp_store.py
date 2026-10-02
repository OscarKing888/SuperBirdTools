"""Persist analysis results to same-stem XMP sidecars (never into the original file)."""

from __future__ import annotations

from typing import Dict, Optional

from app_common import bird_sharpness_fields as fields
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.writer import invalidate_metadata_cache

from .analyzer import BirdSharpnessResult


def write_result(path: str, result: BirdSharpnessResult, *, writer: Optional[PhotoMetaDataXMP] = None) -> bool:
    """Write the result's sidecar fields; returns False on failure or for error results."""
    assignments = result.to_xmp_fields()
    if not assignments:
        return False
    writer = writer or PhotoMetaDataXMP()
    ok = writer.write(path, assignments)
    if ok:
        invalidate_metadata_cache([path])
    return ok


def browser_meta_updates(result: BirdSharpnessResult) -> Dict[str, object]:
    """Keys to merge into a file browser's in-memory metadata after a successful write."""
    updates: Dict[str, object] = {}
    for key, value in result.to_xmp_fields().items():
        updates[key] = value
        group, _, name = key.partition(":")
        if group == fields.XMP_GROUP:
            updates[name] = value
    if result.score is not None:
        sharp = "%06.2f" % float(result.score)
        updates.update({"XMP:City": sharp, "city": sharp, "sharpness": sharp})
    return updates
