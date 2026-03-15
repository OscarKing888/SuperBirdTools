from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class NormalizedMetadata:
    source: Path
    stem: str
    bird: str | None = None
    capture_dt: datetime | None = None
    capture_text: str | None = None
    location: str | None = None
    gps_text: str | None = None
    camera: str | None = None
    lens: str | None = None
    aperture: float | None = None
    shutter_s: float | None = None
    iso: int | None = None
    focal_mm: float | None = None
    focal35_mm: float | None = None
    settings_text: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "stem": self.stem,
            "bird": self.bird,
            "capture_dt": self.capture_dt.isoformat() if self.capture_dt else None,
            "capture_text": self.capture_text,
            "location": self.location,
            "gps_text": self.gps_text,
            "camera": self.camera,
            "lens": self.lens,
            "aperture": self.aperture,
            "shutter_s": self.shutter_s,
            "iso": self.iso,
            "focal_mm": self.focal_mm,
            "focal35_mm": self.focal35_mm,
            "settings_text": self.settings_text,
        }
