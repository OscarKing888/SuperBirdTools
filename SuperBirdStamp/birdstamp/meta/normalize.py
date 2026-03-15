from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from birdstamp.models import NormalizedMetadata


def _normalize_lookup(raw: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for key, value in raw.items():
        k = str(key).strip().lower()
        if not k:
            continue
        lookup.setdefault(k, value)
        if ":" in k:
            lookup.setdefault(k.split(":")[-1], value)
    return lookup


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for codec in ("utf-8", "utf-16le", "latin1"):
            try:
                decoded = value.decode(codec, errors="ignore")
                value = decoded
                break
            except Exception:
                continue
    if isinstance(value, (list, tuple)):
        text_items = [str(v).strip() for v in value if str(v).strip()]
        value = " ".join(text_items)
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def _pick(lookup: dict[str, Any], candidates: list[str]) -> Any | None:
    for key in candidates:
        value = lookup.get(key.lower())
        if value in (None, "", " "):
            continue
        return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except Exception:
        pass
    text = _clean_text(value)
    if not text:
        return None
    frac_match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*/\s*([-+]?\d+(?:\.\d+)?)", text)
    if frac_match:
        try:
            num = float(frac_match.group(1))
            den = float(frac_match.group(2))
        except ValueError:
            return None
        if den == 0:
            return None
        return num / den
    match = re.search(r"[-+]?\d+(\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    numeric = _to_float(value)
    if numeric is None:
        return None
    return int(round(numeric))


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    normalized = text.replace("T", " ").strip()
    if "." in normalized:
        normalized = normalized.split(".", 1)[0]
    patterns = [
        "%Y:%m:%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]
    for pattern in patterns:
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _parse_exposure_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if seconds > 0 else None

    text = _clean_text(value)
    if not text:
        return None
    text = text.lower().replace("sec", "").replace("s", "").strip()
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            numerator = float(left.strip())
            denominator = float(right.strip())
        except ValueError:
            return None
        if denominator == 0:
            return None
        seconds = numerator / denominator
        return seconds if seconds > 0 else None
    try:
        seconds = float(text)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _format_aperture(value: float | None) -> str | None:
    if value is None:
        return None
    return f"f/{value:g}"


def _format_shutter(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 1:
        denominator = round(1 / seconds)
        if denominator > 0:
            return f"1/{denominator}s"
    return f"{seconds:g}s"


def _format_focal(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:g}mm"


def format_settings_line(meta: NormalizedMetadata, show_eq_focal: bool = True) -> str | None:
    parts: list[str] = []
    aperture = _format_aperture(meta.aperture)
    shutter = _format_shutter(meta.shutter_s)
    focal = _format_focal(meta.focal_mm)
    if aperture:
        parts.append(aperture)
    if shutter:
        parts.append(shutter)
    if meta.iso is not None:
        parts.append(f"ISO{meta.iso}")
    if focal:
        if show_eq_focal and meta.focal35_mm:
            parts.append(f"{focal} ({meta.focal35_mm:g}mm eq)")
        else:
            parts.append(focal)
    return "  ".join(parts) if parts else None


def _dedupe_join(parts: list[str | None]) -> str | None:
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if not part:
            continue
        part = part.strip()
        if not part:
            continue
        lowered = part.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(part)
    if not ordered:
        return None
    return ", ".join(ordered)


def _parse_bird_from_filename(stem: str, bird_regex: str) -> str | None:
    try:
        pattern = re.compile(bird_regex)
    except re.error:
        return None
    match = pattern.search(stem)
    if not match:
        return None
    if "bird" in match.groupdict():
        return _clean_text(match.group("bird"))
    if match.groups():
        return _clean_text(match.group(1))
    return _clean_text(match.group(0))


def normalize_metadata(
    source: Path,
    raw_metadata: dict[str, Any],
    *,
    bird_arg: str | None,
    bird_priority: list[str],
    bird_regex: str,
    time_format: str = "%Y-%m-%d %H:%M",
) -> NormalizedMetadata:
    lookup = _normalize_lookup(raw_metadata)

    dt_value = _pick(
        lookup,
        [
            "DateTimeOriginal",
            "CreateDate",
            "DateTimeCreated",
            "DateCreated",
            "MediaCreateDate",
        ],
    )
    capture_dt = _parse_datetime(dt_value)
    if capture_dt is None:
        try:
            capture_dt = datetime.fromtimestamp(source.stat().st_ctime)
        except Exception:
            capture_dt = None
    capture_text = capture_dt.strftime(time_format) if capture_dt else None

    gps_lat = _to_float(_pick(lookup, ["GPSLatitude", "Composite:GPSLatitude"]))
    gps_lon = _to_float(_pick(lookup, ["GPSLongitude", "Composite:GPSLongitude"]))
    gps_text = None
    if gps_lat is not None and gps_lon is not None:
        gps_text = f"{gps_lat:.5f}, {gps_lon:.5f}"

    location = _dedupe_join(
        [
            _clean_text(_pick(lookup, ["SubLocation", "Location", "Sublocation"])),
            _clean_text(_pick(lookup, ["City"])),
            _clean_text(_pick(lookup, ["State", "Province-State"])),
            _clean_text(_pick(lookup, ["Country", "Country-PrimaryLocationName"])),
        ]
    )
    if not location and gps_text:
        location = gps_text

    make = _clean_text(_pick(lookup, ["Make"]))
    model = _clean_text(_pick(lookup, ["Model", "CameraModelName"]))
    camera = None
    if make and model and model.lower().startswith(make.lower()):
        camera = model
    elif make and model:
        camera = f"{make} {model}"
    else:
        camera = make or model

    lens = _clean_text(
        _pick(
            lookup,
            [
                "LensModel",
                "LensID",
                "Lens",
                "LensType",
                "XMP:Lens",
            ],
        )
    )

    aperture = _to_float(_pick(lookup, ["FNumber", "Aperture", "ApertureValue"]))
    shutter_s = _parse_exposure_seconds(_pick(lookup, ["ExposureTime", "ShutterSpeed"]))
    iso = _to_int(_pick(lookup, ["ISO", "PhotographicSensitivity", "ISOSpeedRatings"]))
    focal_mm = _to_float(_pick(lookup, ["FocalLength"]))
    focal35_mm = _to_float(_pick(lookup, ["FocalLengthIn35mmFormat", "FocalLength35efl"]))

    meta_bird = _clean_text(
        _pick(
            lookup,
            [
                "ImageDescription",
                "XPTitle",
                "Title",
                "ObjectName",
                "Headline",
                "Caption-Abstract",
                "Description",
            ],
        )
    )
    file_bird = _parse_bird_from_filename(source.stem, bird_regex)

    bird = None
    for source_name in bird_priority:
        item = source_name.strip().lower()
        if item == "arg" and bird_arg:
            bird = _clean_text(bird_arg)
        elif item == "meta" and meta_bird:
            bird = meta_bird
        elif item == "filename" and file_bird:
            bird = file_bird
        if bird:
            break

    metadata = NormalizedMetadata(
        source=source,
        stem=source.stem,
        bird=bird,
        capture_dt=capture_dt,
        capture_text=capture_text,
        location=location,
        gps_text=gps_text,
        camera=camera,
        lens=lens,
        aperture=aperture,
        shutter_s=shutter_s,
        iso=iso,
        focal_mm=focal_mm,
        focal35_mm=focal35_mm,
        raw=raw_metadata,
    )
    metadata.settings_text = format_settings_line(metadata, show_eq_focal=False)
    return metadata
