"""Persist analysis results to same-stem XMP sidecars (never into the original file)."""

from __future__ import annotations

from typing import Dict, Optional

from app_common import bird_sharpness_fields as fields
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.writer import invalidate_same_stem_metadata_cache

from .analyzer import BirdSharpnessResult


def write_result(path: str, result: BirdSharpnessResult, *, writer: Optional[PhotoMetaDataXMP] = None) -> bool:
    """Write the result's sidecar fields; returns False on failure or for error results."""
    assignments = result.to_xmp_fields()
    if not assignments:
        return False
    writer = writer or PhotoMetaDataXMP()
    ok = writer.write(path, assignments)
    if ok:
        # RAW+JPEG of one shot share this sidecar: drop the sibling's cached record too.
        invalidate_same_stem_metadata_cache([path])
    return ok


def browser_meta_updates(result: BirdSharpnessResult) -> Dict[str, object]:
    """Keys to merge into a file browser's in-memory metadata after a successful write."""
    updates: Dict[str, object] = {}
    for key, value in result.to_xmp_fields().items():
        updates[key] = value
        group, _, name = key.partition(":")
        if group == fields.XMP_GROUP:
            updates[name] = value
    return updates


def persisted_sharpness_updates(path: str, *, reader: Optional[PhotoMetaDataXMP] = None) -> Dict[str, object]:
    """Worker-only cache patch from saved report sharpness, never from analysis scores.

    Empty values deliberately clear a legacy detection score from the browser
    when no original report value could be recovered. A read error propagates
    so the caller can preserve its cache rather than inventing missing values.
    """
    rec = (reader or PhotoMetaDataXMP()).read(path)
    if not rec:
        raise OSError(f"无法读回已保存的 XMP: {path}")

    def value(*keys):
        for key in keys:
            found = rec.get(key)
            if found is not None and str(found).strip():
                return found
        return ""

    sharp = value("XMP-photoshop:City", "XMP:City")
    archived = value("XMP-superpicky:adj_sharpness", "adj_sharpness")
    city_archive = value("XMP-superpicky:city", "city")
    updates = {"XMP-photoshop:City": sharp, "XMP:City": sharp, "city": sharp, "sharpness": sharp,
               "XMP-superpicky:adj_sharpness": archived, "adj_sharpness": archived, "report.adj_sharpness": archived,
               "XMP-superpicky:city": city_archive, "report.city": city_archive}
    # The shared reader expands explicit City clears to all display fallbacks.
    for key in ("IPTC:City", "head_sharp", "report.head_sharp", "XMP-superpicky:head_sharp"):
        if rec.get(key) == "":
            updates[key] = ""
    return updates


def already_analyzed(path: str, version: str, *, reader: Optional[PhotoMetaDataXMP] = None) -> bool:
    """True when the sidecar already holds a verdict written by algorithm ``version``."""
    try:
        rec = (reader or PhotoMetaDataXMP()).read(path)
    except Exception:
        return False
    return bool(str(rec.get(fields.FIELD_VERDICT) or "").strip()) and str(rec.get(fields.FIELD_VERSION) or "") == version
