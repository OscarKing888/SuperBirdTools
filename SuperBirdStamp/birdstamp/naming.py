from __future__ import annotations

import re
from pathlib import Path

from birdstamp.models import NormalizedMetadata

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')


def sanitize_token(value: str | None, fallback: str = "NA") -> str:
    text = (value or "").strip()
    if not text:
        text = fallback
    text = INVALID_FILENAME_CHARS.sub("_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip(" ._")
    return text or fallback


def sanitize_filename(value: str, fallback: str = "output") -> str:
    text = INVALID_FILENAME_CHARS.sub("_", value).strip()
    text = text.replace("/", "_").replace("\\", "_")
    text = text.strip(" .")
    return text or fallback


def build_output_name(
    name_template: str,
    source: Path,
    meta: NormalizedMetadata,
    extension: str,
    template_name: str = "banner",
) -> str:
    ext = extension.lower().lstrip(".")
    capture = meta.capture_dt.strftime("%Y%m%d_%H%M") if meta.capture_dt else "unknown_date"
    values = {
        "stem": sanitize_token(source.stem, fallback="image"),
        "date": sanitize_token(capture),
        "camera": sanitize_token(meta.camera),
        "lens": sanitize_token(meta.lens),
        "bird": sanitize_token(meta.bird),
        "location": sanitize_token(meta.location),
        "template": sanitize_token(template_name, fallback="banner"),
        "ext": ext,
    }
    try:
        rendered = name_template.format(**values)
    except KeyError as exc:
        missing = str(exc).strip("'")
        raise ValueError(f"name template contains unknown key: {missing}") from exc

    rendered = sanitize_filename(rendered, fallback=f"{source.stem}__banner.{ext}")
    if not Path(rendered).suffix:
        rendered = f"{rendered}.{ext}"
    return rendered

