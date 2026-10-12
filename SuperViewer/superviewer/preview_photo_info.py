"""Capture information formatting for the existing preview status label."""
from __future__ import annotations

from app_common.file_browser._browser_core import (
    _metadata_aperture_text,
    _metadata_camera_model_text,
    _metadata_focal_length_text,
    _metadata_iso_text,
    _metadata_lens_model_text,
    _metadata_shutter_text,
)


def photo_capture_info_fields(metadata: dict | None) -> tuple[tuple[str, str], ...]:
    """Reuse the information tab's source/XMP/report formatting, without I/O."""
    return tuple((name, formatter(metadata) or "—") for name, formatter in (
        ("快门", _metadata_shutter_text),
        ("光圈", _metadata_aperture_text),
        ("ISO", _metadata_iso_text),
        ("焦距", _metadata_focal_length_text),
        ("镜头型号", _metadata_lens_model_text),
        ("机器型号", _metadata_camera_model_text),
    ))


def photo_capture_info_text(metadata: dict | None) -> str:
    return " | ".join(f"{name}：{value}" for name, value in photo_capture_info_fields(metadata))
