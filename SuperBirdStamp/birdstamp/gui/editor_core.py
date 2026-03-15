# Core editor algorithms: focus extraction, crop math, bird detection.
# No Qt/GUI dependencies; safe for CLI use.
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageOps
from app_common.focus_calc import (
    CameraFocusType,
    extract_focus_box as _extract_focus_box_by_camera_type,
    extract_focus_box_for_display as _extract_focus_box_for_display_by_camera_type,
    get_focus_point as _get_focus_point_by_camera_type,
    get_focus_point_for_display as _get_focus_point_for_display_by_camera_type,
    resolve_focus_camera_type as _resolve_focus_camera_type,
    resolve_focus_camera_type_from_metadata as _resolve_focus_camera_type_from_metadata,
)
from birdstamp.config import resolve_bundled_path

try:
    from birdstamp.gui.editor_options import RATIO_FREE
except ImportError:
    RATIO_FREE = "free"  # fallback when GUI not available (e.g. CLI)

# Center mode constants (used by CLI and GUI)
CENTER_MODE_IMAGE = "image"
CENTER_MODE_FOCUS = "focus"
CENTER_MODE_BIRD = "bird"
CENTER_MODE_CUSTOM = "custom"
CENTER_MODE_OPTIONS = (CENTER_MODE_IMAGE, CENTER_MODE_FOCUS, CENTER_MODE_BIRD, CENTER_MODE_CUSTOM)

DEFAULT_CROP_PADDING_PX = 128
DEFAULT_FOCUS_BOX_SHORT_EDGE_RATIO = 0.12

_BIRD_MODEL_CANDIDATES = ("yolo11n.pt", "yolo11s.pt", "yolov8n.pt")
_BIRD_CLASS_NAME = "bird"
_COCO_FALLBACK_BIRD_CLASS_ID = 14
_BIRD_DETECT_CONFIDENCE = 0.25
_BIRD_DETECTOR_ERROR_MESSAGE = ""

_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RDF_DESC_TAG = f"{{{_RDF_NS}}}Description"
_RDF_LI_TAG = f"{{{_RDF_NS}}}li"
_RDF_RESOURCE_ATTR = f"{{{_RDF_NS}}}resource"
_XML_LANG_ATTR = "{http://www.w3.org/XML/1998/namespace}lang"
_XMP_NS_TO_PREFIX = {
    "http://purl.org/dc/elements/1.1/": "XMP-dc",
    "http://ns.adobe.com/photoshop/1.0/": "XMP-photoshop",
    "http://ns.adobe.com/xap/1.0/": "XMP",
    "http://ns.adobe.com/xmp/1.0/DynamicMedia/": "XMP-xmpDM",
}
_XMP_SIDECAR_SUFFIX_CANDIDATES = (".xmp", ".XMP", ".Xmp")
_XMP_DERIVED_EXPORT_DIR_NAMES = {"dxo", "dxo pureraw", "pureraw", "exports", "export"}
_XMP_DERIVED_STEM_SPLIT_MARKERS = ("-DxO_", "_DxO_")


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        for codec in ("utf-8", "utf-16le", "latin1"):
            try:
                value = value.decode(codec, errors="ignore")
                break
            except Exception:
                continue
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if str(v).strip()]
        value = " ".join(items)
    text = str(value).replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def normalize_lookup(raw: dict[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for key, value in raw.items():
        key_text = str(key).strip().lower()
        if not key_text:
            continue
        lookup.setdefault(key_text, value)
        if ":" in key_text:
            lookup.setdefault(key_text.split(":")[-1], value)
    return lookup


def _split_xml_tag(tag: str) -> tuple[str, str]:
    if not isinstance(tag, str):
        return ("", "")
    if tag.startswith("{") and "}" in tag:
        uri, local = tag[1:].split("}", 1)
        return (uri, local)
    return ("", tag)


def find_sidecar_xmp_path(source_path: Path) -> Path | None:
    stem_text = str(source_path.stem or "").strip()
    if not stem_text:
        return None

    stem_candidates: list[str] = [stem_text]
    for marker in _XMP_DERIVED_STEM_SPLIT_MARKERS:
        pos = stem_text.find(marker)
        if pos <= 0:
            continue
        base = stem_text[:pos].rstrip(" _-")
        if base and base not in stem_candidates:
            stem_candidates.append(base)

    dir_candidates: list[Path] = [source_path.parent]
    stem_changed = any(candidate != stem_text for candidate in stem_candidates)
    parent_name = str(source_path.parent.name or "").strip().lower()
    if stem_changed or parent_name in _XMP_DERIVED_EXPORT_DIR_NAMES:
        upper = source_path.parent.parent
        if upper != source_path.parent and upper not in dir_candidates:
            dir_candidates.append(upper)

    for dir_path in dir_candidates:
        for stem in stem_candidates:
            for suffix in _XMP_SIDECAR_SUFFIX_CANDIDATES:
                candidate = dir_path / f"{stem}{suffix}"
                try:
                    if candidate.exists() and candidate.is_file():
                        return candidate
                except Exception:
                    continue
            target_lower = f"{stem.lower()}.xmp"
            try:
                for sibling in dir_path.iterdir():
                    if not sibling.is_file():
                        continue
                    if sibling.suffix.lower() != ".xmp":
                        continue
                    if sibling.name.lower() == target_lower:
                        return sibling
            except Exception:
                continue
    return None


def _extract_xmp_property_value(node: ET.Element) -> Any | None:
    li_nodes = node.findall(f".//{_RDF_LI_TAG}")
    if li_nodes:
        default_text: str | None = None
        values: list[str] = []
        for li in li_nodes:
            text = clean_text(li.text)
            if not text:
                continue
            values.append(text)
            lang = str(li.attrib.get(_XML_LANG_ATTR) or "").strip().lower()
            if lang == "x-default" and default_text is None:
                default_text = text
        if default_text:
            return default_text
        if values:
            return values[0] if len(values) == 1 else values
    resource_text = clean_text(node.attrib.get(_RDF_RESOURCE_ATTR))
    if resource_text:
        return resource_text
    direct_text = clean_text(node.text)
    if direct_text:
        return direct_text
    all_text = clean_text(" ".join(part for part in node.itertext() if isinstance(part, str)))
    if all_text:
        return all_text
    return None


def load_sidecar_xmp_metadata(source_path: Path) -> dict[str, Any]:
    xmp_path = find_sidecar_xmp_path(source_path)
    if xmp_path is None:
        return {}
    try:
        payload = xmp_path.read_bytes()
    except Exception:
        return {}
    try:
        root = ET.fromstring(payload)
    except Exception:
        try:
            root = ET.fromstring(payload.decode("utf-8", errors="ignore"))
        except Exception:
            return {}
    parsed: dict[str, Any] = {}
    for desc in root.findall(f".//{_RDF_DESC_TAG}"):
        for child in list(desc):
            if not isinstance(child.tag, str):
                continue
            namespace_uri, local_name = _split_xml_tag(child.tag)
            local = str(local_name or "").strip()
            if not local:
                continue
            value = _extract_xmp_property_value(child)
            if value is None:
                continue
            prefix = _XMP_NS_TO_PREFIX.get(namespace_uri, "XMP")
            parsed[f"{prefix}:{local}"] = value
            if namespace_uri == "http://purl.org/dc/elements/1.1/" and local.lower() == "title":
                parsed.setdefault("XMP:Title", value)
                parsed.setdefault("Title", value)
            if namespace_uri == "http://purl.org/dc/elements/1.1/" and local.lower() == "description":
                parsed.setdefault("XMP:Description", value)
    if parsed:
        parsed["XMP:SidecarFile"] = str(xmp_path)
    return parsed


def _extract_numbers(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        numbers: list[float] = []
        for item in value:
            numbers.extend(_extract_numbers(item))
        return numbers
    text = str(value)
    tokens = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    result: list[float] = []
    for token in tokens:
        try:
            result.append(float(token))
        except ValueError:
            continue
    return result


def _is_dimension_like(value: float, size: int) -> bool:
    if size <= 0:
        return False
    if value <= 1.0:
        return False
    size_f = float(size)
    return abs(value - size_f) <= 3.0 or abs(value - (size_f + 1.0)) <= 3.0


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize_focus_coordinate(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    if x > 1.0 or y > 1.0:
        if width > 0 and height > 0:
            return (clamp01(x / float(width)), clamp01(y / float(height)))
    return (clamp01(x), clamp01(y))


def _decode_focus_numbers_layout(
    numbers: list[float], width: int, height: int
) -> tuple[float, float, float | None, float | None] | None:
    if len(numbers) < 2:
        return None
    if len(numbers) >= 4 and _is_dimension_like(numbers[0], width) and _is_dimension_like(numbers[1], height):
        center_x = numbers[2]
        center_y = numbers[3]
        span_start = 4
    else:
        center_x = numbers[0]
        center_y = numbers[1]
        span_start = 2
    span_x: float | None = None
    span_y: float | None = None
    if len(numbers) >= span_start + 2:
        span_x = numbers[span_start]
        span_y = numbers[span_start + 1]
    elif len(numbers) >= span_start + 1:
        span_x = numbers[span_start]
        span_y = numbers[span_start]
    return (center_x, center_y, span_x, span_y)


def _extract_focus_frame_size(value: Any) -> tuple[float, float] | None:
    numbers = _extract_numbers(value)
    if len(numbers) < 2:
        return None
    width = numbers[0]
    height = numbers[1]
    if width <= 0 or height <= 0:
        return None
    return (float(width), float(height))


def get_focus_point(
    raw: dict[str, Any],
    width: int,
    height: int,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float] | None:
    """Return normalized (x,y) focus point from metadata, or None."""
    return _get_focus_point_by_camera_type(raw, width, height, camera_type=camera_type)


def get_focus_point_for_display(
    raw: dict[str, Any],
    width: int,
    height: int,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float] | None:
    """Resolve a preview-ready focus point using metadata size + Orientation mapping."""
    return _get_focus_point_for_display_by_camera_type(raw, width, height, camera_type=camera_type)


def _extract_focus_point_impl(raw: dict[str, Any], width: int, height: int) -> tuple[float, float] | None:
    if width <= 0 or height <= 0:
        return None
    lookup = normalize_lookup(raw)
    key_pairs = [
        ("composite:focusx", "composite:focusy"),
        ("focusx", "focusy"),
        ("regioninfo:regionsregionlistregionareax", "regioninfo:regionsregionlistregionareay"),
        ("regionareax", "regionareay"),
    ]
    for x_key, y_key in key_pairs:
        if x_key in lookup and y_key in lookup:
            xs = _extract_numbers(lookup[x_key])
            ys = _extract_numbers(lookup[y_key])
            if xs and ys:
                x, y = xs[0], ys[0]
                if x > 1.0 or y > 1.0:
                    return (max(0.0, min(1.0, x / width)), max(0.0, min(1.0, y / height)))
                return (max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))
    for key in ("subjectarea", "subjectlocation", "focuslocation", "focuslocation2", "afpoint"):
        if key not in lookup:
            continue
        nums = _extract_numbers(lookup[key])
        decoded = _decode_focus_numbers_layout(nums, width, height)
        if decoded is None:
            continue
        x, y, _span_x, _span_y = decoded
        return _normalize_focus_coordinate(x, y, width, height)
    return None


def _extract_focus_point(
    raw: dict[str, Any],
    width: int,
    height: int,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float] | None:
    """Alias for backward compatibility in editor_core internals."""
    return _get_focus_point_by_camera_type(raw, width, height, camera_type=camera_type)


def resolve_focus_camera_type(camera_model: Any, *, camera_make: Any = None) -> CameraFocusType:
    """Public alias for app_common focus camera-type resolver."""
    return _resolve_focus_camera_type(camera_model, camera_make=camera_make)


def resolve_focus_camera_type_from_metadata(raw: dict[str, Any]) -> CameraFocusType:
    """Public alias for app_common metadata -> focus camera-type resolver."""
    return _resolve_focus_camera_type_from_metadata(raw)


def _normalize_focus_span(value: float | None, full_size: int, fallback: float) -> float:
    if full_size <= 0:
        return max(0.01, min(1.0, fallback))
    if value is None or value <= 0:
        return max(0.01, min(1.0, fallback))
    span = float(value)
    if span > 1.0:
        span = span / float(full_size)
    return max(0.01, min(1.0, span))


def _focus_box_from_center(center_x: float, center_y: float, span_x: float, span_y: float) -> tuple[float, float, float, float]:
    cx = clamp01(center_x)
    cy = clamp01(center_y)
    sx = max(0.01, min(1.0, span_x))
    sy = max(0.01, min(1.0, span_y))
    half_x = sx * 0.5
    half_y = sy * 0.5
    left = cx - half_x
    right = cx + half_x
    top = cy - half_y
    bottom = cy + half_y
    if left < 0.0:
        right = min(1.0, right - left)
        left = 0.0
    if right > 1.0:
        left = max(0.0, left - (right - 1.0))
        right = 1.0
    if top < 0.0:
        bottom = min(1.0, bottom - top)
        top = 0.0
    if bottom > 1.0:
        top = max(0.0, top - (bottom - 1.0))
        bottom = 1.0
    return (left, top, right, bottom)


def _focus_box_from_numbers(
    numbers: list[float],
    width: int,
    height: int,
    fallback_span_px: tuple[float, float] | None = None,
) -> tuple[float, float, float, float] | None:
    if width <= 0 or height <= 0:
        return None
    decoded = _decode_focus_numbers_layout(numbers, width, height)
    if decoded is None:
        return None
    x, y, span_x_raw, span_y_raw = decoded
    center_x, center_y = _normalize_focus_coordinate(x, y, width, height)
    default_side_px = max(24.0, min(width, height) * DEFAULT_FOCUS_BOX_SHORT_EDGE_RATIO)
    if fallback_span_px is not None and fallback_span_px[0] > 0 and fallback_span_px[1] > 0:
        fallback_span_x = fallback_span_px[0] / float(width)
        fallback_span_y = fallback_span_px[1] / float(height)
    else:
        fallback_span_x = default_side_px / float(width)
        fallback_span_y = default_side_px / float(height)
    span_x = _normalize_focus_span(span_x_raw, width, fallback_span_x)
    span_y = _normalize_focus_span(span_y_raw, height, fallback_span_y)
    return _focus_box_from_center(center_x, center_y, span_x, span_y)


def extract_focus_box(
    raw: dict[str, Any],
    width: int,
    height: int,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float, float, float] | None:
    return _extract_focus_box_by_camera_type(raw, width, height, camera_type=camera_type)


def extract_focus_box_for_display(
    raw: dict[str, Any],
    width: int,
    height: int,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float, float, float] | None:
    """Resolve a preview-ready focus box using metadata size + Orientation mapping."""
    return _extract_focus_box_for_display_by_camera_type(raw, width, height, camera_type=camera_type)


def transform_focus_box_after_crop(
    focus_box: tuple[float, float, float, float],
    *,
    source_width: int,
    source_height: int,
    ratio: float | None,
    anchor: tuple[float, float],
) -> tuple[float, float, float, float] | None:
    if source_width <= 0 or source_height <= 0:
        return None
    left = focus_box[0] * source_width
    top = focus_box[1] * source_height
    right = focus_box[2] * source_width
    bottom = focus_box[3] * source_height
    width_ref = float(source_width)
    height_ref = float(source_height)
    if ratio is not None and ratio > 0:
        current_ratio = source_width / float(source_height)
        if abs(current_ratio - ratio) >= 0.0001:
            anchor_x = clamp01(anchor[0])
            anchor_y = clamp01(anchor[1])
            if current_ratio > ratio:
                new_width = max(1, int(round(source_height * ratio)))
                center_x = int(round(anchor_x * source_width))
                crop_left = max(0, min(source_width - new_width, center_x - (new_width // 2)))
                left -= crop_left
                right -= crop_left
                width_ref = float(new_width)
                height_ref = float(source_height)
            else:
                new_height = max(1, int(round(source_width / ratio)))
                center_y = int(round(anchor_y * source_height))
                crop_top = max(0, min(source_height - new_height, center_y - (new_height // 2)))
                top -= crop_top
                bottom -= crop_top
                width_ref = float(source_width)
                height_ref = float(new_height)
    left_n = left / width_ref
    right_n = right / width_ref
    top_n = top / height_ref
    bottom_n = bottom / height_ref
    if right_n <= 0.0 or left_n >= 1.0 or bottom_n <= 0.0 or top_n >= 1.0:
        return None
    left_n = clamp01(left_n)
    right_n = clamp01(right_n)
    top_n = clamp01(top_n)
    bottom_n = clamp01(bottom_n)
    if right_n <= left_n or bottom_n <= top_n:
        return None
    return (left_n, top_n, right_n, bottom_n)


def normalize_unit_box(box: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
    if box is None:
        return None
    try:
        left = clamp01(float(box[0]))
        top = clamp01(float(box[1]))
        right = clamp01(float(box[2]))
        bottom = clamp01(float(box[3]))
    except Exception:
        return None
    if right < left:
        left, right = right, left
    if bottom < top:
        top, bottom = bottom, top
    if right - left <= 0.0001 or bottom - top <= 0.0001:
        return None
    return (left, top, right, bottom)


def normalized_box_to_pixel_box(
    box: tuple[float, float, float, float] | None,
    width: int,
    height: int,
    *,
    fallback_full: bool = False,
) -> tuple[int, int, int, int] | None:
    if width <= 0 or height <= 0:
        return None
    normalized = normalize_unit_box(box)
    if normalized is None:
        if not fallback_full:
            return None
        normalized = (0.0, 0.0, 1.0, 1.0)
    left = int(round(normalized[0] * width))
    top = int(round(normalized[1] * height))
    right = int(round(normalized[2] * width))
    bottom = int(round(normalized[3] * height))
    left = max(0, min(width - 1, left))
    top = max(0, min(height - 1, top))
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    return (left, top, right, bottom)


def compute_crop_output_size(
    source_width: int,
    source_height: int,
    crop_box: tuple[float, float, float, float] | None,
    outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple[int, int] | None:
    """Return crop output size in pixels after applying outer pad and crop box."""
    if source_width <= 0 or source_height <= 0:
        return None
    top, bottom, left, right = outer_pad
    padded_width = source_width + max(0, int(left)) + max(0, int(right))
    padded_height = source_height + max(0, int(top)) + max(0, int(bottom))
    if padded_width <= 0 or padded_height <= 0:
        return None
    crop_px = normalized_box_to_pixel_box(
        crop_box,
        padded_width,
        padded_height,
        fallback_full=True,
    )
    if crop_px is None:
        return None
    crop_left, crop_top, crop_right, crop_bottom = crop_px
    return (
        max(1, crop_right - crop_left),
        max(1, crop_bottom - crop_top),
    )


def transform_source_box_after_crop_padding(
    source_box: tuple[float, float, float, float] | None,
    *,
    crop_box: tuple[float, float, float, float] | None,
    source_width: int,
    source_height: int,
    pt: int,
    pb: int,
    pl: int,
    pr: int,
) -> tuple[float, float, float, float] | None:
    source_px = normalized_box_to_pixel_box(source_box, source_width, source_height)
    if source_px is None:
        return None
    crop_px = normalized_box_to_pixel_box(crop_box, source_width, source_height, fallback_full=True)
    if crop_px is None:
        return None
    crop_left, crop_top, crop_right, crop_bottom = crop_px
    crop_w = crop_right - crop_left
    crop_h = crop_bottom - crop_top
    if crop_w <= 0 or crop_h <= 0:
        return None
    pad_top = max(0, int(pt))
    pad_bottom = max(0, int(pb))
    pad_left = max(0, int(pl))
    pad_right = max(0, int(pr))
    padded_w = crop_w + pad_left + pad_right
    padded_h = crop_h + pad_top + pad_bottom
    if padded_w <= 0 or padded_h <= 0:
        return None
    src_left, src_top, src_right, src_bottom = source_px
    clipped_left = max(crop_left, min(crop_right, src_left))
    clipped_top = max(crop_top, min(crop_bottom, src_top))
    clipped_right = max(crop_left, min(crop_right, src_right))
    clipped_bottom = max(crop_top, min(crop_bottom, src_bottom))
    if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
        return None
    mapped_left = (pad_left + (clipped_left - crop_left)) / float(padded_w)
    mapped_top = (pad_top + (clipped_top - crop_top)) / float(padded_h)
    mapped_right = (pad_left + (clipped_right - crop_left)) / float(padded_w)
    mapped_bottom = (pad_top + (clipped_bottom - crop_top)) / float(padded_h)
    left_n = clamp01(mapped_left)
    top_n = clamp01(mapped_top)
    right_n = clamp01(mapped_right)
    bottom_n = clamp01(mapped_bottom)
    if right_n <= left_n or bottom_n <= top_n:
        return None
    return (left_n, top_n, right_n, bottom_n)


def resolve_focus_box_after_processing(
    raw_metadata: dict[str, Any],
    *,
    source_width: int,
    source_height: int,
    crop_box: tuple[float, float, float, float] | None,
    outer_pad: tuple[int, int, int, int] = (0, 0, 0, 0),
    apply_ratio_crop: bool = True,
    camera_type: CameraFocusType | str | None = None,
) -> tuple[float, float, float, float] | None:
    if source_width <= 0 or source_height <= 0:
        return None
    focus_box = extract_focus_box_for_display(
        raw_metadata,
        source_width,
        source_height,
        camera_type=camera_type,
    )
    if focus_box is None:
        return None
    top, bottom, left, right = outer_pad
    return transform_source_box_after_crop_padding(
        focus_box,
        crop_box=crop_box if apply_ratio_crop else None,
        source_width=source_width,
        source_height=source_height,
        pt=top,
        pb=bottom,
        pl=left,
        pr=right,
    )


def draw_focus_box_overlay(
    image: Image.Image,
    focus_box: tuple[float, float, float, float] | None,
) -> Image.Image:
    focus_outer_black_width = 1
    focus_green_width = 4
    focus_inner_black_width = 1

    def _draw_box_ring(
        draw: ImageDraw.ImageDraw,
        *,
        left_px: int,
        top_px: int,
        right_px: int,
        bottom_px: int,
        thickness: int,
        fill: str,
    ) -> tuple[int, int, int, int]:
        if thickness <= 0:
            return (left_px, top_px, right_px, bottom_px)
        width_px = right_px - left_px
        height_px = bottom_px - top_px
        ring = min(int(thickness), max(0, width_px // 2), max(0, height_px // 2))
        if ring <= 0:
            return (left_px, top_px, right_px, bottom_px)

        top_band_bottom = top_px + ring - 1
        bottom_band_top = bottom_px - ring
        draw.rectangle((left_px, top_px, right_px - 1, top_band_bottom), fill=fill)
        draw.rectangle((left_px, bottom_band_top, right_px - 1, bottom_px - 1), fill=fill)

        inner_top = top_px + ring
        inner_bottom = bottom_px - ring
        if inner_bottom > inner_top:
            left_band_right = left_px + ring - 1
            right_band_left = right_px - ring
            draw.rectangle((left_px, inner_top, left_band_right, inner_bottom - 1), fill=fill)
            draw.rectangle((right_band_left, inner_top, right_px - 1, inner_bottom - 1), fill=fill)
        return (left_px + ring, top_px + ring, right_px - ring, bottom_px - ring)

    if focus_box is None:
        return image
    width, height = image.size
    focus_px = normalized_box_to_pixel_box(focus_box, width, height)
    if focus_px is None:
        return image
    left, top, right, bottom = focus_px
    if right - left < 2 or bottom - top < 2:
        return image

    draw = ImageDraw.Draw(image)
    ring_left, ring_top, ring_right, ring_bottom = _draw_box_ring(
        draw,
        left_px=left,
        top_px=top,
        right_px=right,
        bottom_px=bottom,
        thickness=focus_outer_black_width,
        fill="#000000",
    )
    ring_left, ring_top, ring_right, ring_bottom = _draw_box_ring(
        draw,
        left_px=ring_left,
        top_px=ring_top,
        right_px=ring_right,
        bottom_px=ring_bottom,
        thickness=focus_green_width,
        fill="#2EFF55",
    )
    _draw_box_ring(
        draw,
        left_px=ring_left,
        top_px=ring_top,
        right_px=ring_right,
        bottom_px=ring_bottom,
        thickness=focus_inner_black_width,
        fill="#000000",
    )
    return image


def resize_fit(image: Image.Image, max_long_edge: int) -> Image.Image:
    if max_long_edge <= 0:
        return image
    width, height = image.size
    long_edge = max(width, height)
    if long_edge <= max_long_edge:
        return image
    scale = max_long_edge / float(long_edge)
    new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def pad_image(
    image: Image.Image,
    top: int,
    bottom: int,
    left: int,
    right: int,
    fill: str = "#FFFFFF",
) -> Image.Image:
    if top <= 0 and bottom <= 0 and left <= 0 and right <= 0:
        return image
    top = max(0, top)
    bottom = max(0, bottom)
    left = max(0, left)
    right = max(0, right)
    rgb = ImageColor.getrgb(fill)
    if image.mode == "RGBA":
        fill_color: tuple[int, ...] = (*rgb, 255)
    elif image.mode == "L":
        fill_color = (int(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]),)
    else:
        fill_color = rgb
    return ImageOps.expand(image, border=(left, top, right, bottom), fill=fill_color)


def parse_ratio_value(value: Any) -> float | None | str:
    """Return ratio as float, None (original aspect), or RATIO_FREE (no aspect lock)."""
    if value is None:
        return None
    if value is RATIO_FREE or (isinstance(value, str) and str(value).strip().lower() == "free"):
        return RATIO_FREE
    try:
        ratio = float(value)
    except Exception:
        return None
    if ratio <= 0:
        return None
    return ratio


def is_ratio_free(ratio: Any) -> bool:
    """True when ratio is the free-aspect sentinel (no constraint on 9-grid crop)."""
    return ratio is RATIO_FREE or ratio == RATIO_FREE


def parse_bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_padding_value(value: Any, default: int = DEFAULT_CROP_PADDING_PX) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(-9999, min(9999, parsed))


def expand_unit_box_to_unclamped_pixels(
    box: tuple[float, float, float, float] | None,
    *,
    width: int,
    height: int,
    top: int,
    bottom: int,
    left: int,
    right: int,
) -> tuple[float, float, float, float] | None:
    normalized = normalize_unit_box(box)
    if normalized is None or width <= 0 or height <= 0:
        return None
    left_px = normalized[0] * width - int(left)
    top_px = normalized[1] * height - int(top)
    right_px = normalized[2] * width + int(right)
    bottom_px = normalized[3] * height + int(bottom)
    if right_px <= left_px:
        center_x = ((normalized[0] + normalized[2]) * 0.5) * width
        left_px = center_x - 0.5
        right_px = center_x + 0.5
    if bottom_px <= top_px:
        center_y = ((normalized[1] + normalized[3]) * 0.5) * height
        top_px = center_y - 0.5
        bottom_px = center_y + 0.5
    return (left_px, top_px, right_px, bottom_px)


def normalize_center_mode(value: Any) -> str:
    text = str(value or CENTER_MODE_IMAGE).strip().lower()
    if text not in CENTER_MODE_OPTIONS:
        return CENTER_MODE_IMAGE
    return text


def box_center(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)


def solve_axis_crop_start(
    *,
    full_size: int,
    crop_size: int,
    anchor_center: float,
    keep_start: float | None = None,
    keep_end: float | None = None,
) -> int:
    if full_size <= 0 or crop_size >= full_size:
        return 0
    max_start = full_size - crop_size
    target_center = clamp01(anchor_center) * float(full_size)
    start = int(round(target_center - (crop_size * 0.5)))
    start = max(0, min(max_start, start))
    if keep_start is None or keep_end is None:
        return start
    low = min(keep_start, keep_end)
    high = max(keep_start, keep_end)
    feasible_min = max(0, int(math.ceil(high - crop_size)))
    feasible_max = min(max_start, int(math.floor(low)))
    if feasible_min <= feasible_max:
        return max(feasible_min, min(feasible_max, start))
    keep_center = (low + high) * 0.5
    centered = int(round(keep_center - (crop_size * 0.5)))
    return max(0, min(max_start, centered))


def compute_ratio_crop_box(
    *,
    width: int,
    height: int,
    ratio: float | None,
    anchor: tuple[float, float] = (0.5, 0.5),
    keep_box: tuple[float, float, float, float] | None = None,
) -> tuple[float, float, float, float]:
    if width <= 0 or height <= 0 or ratio is None or ratio <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    current = width / float(height)
    if abs(current - ratio) < 0.0001:
        return (0.0, 0.0, 1.0, 1.0)
    keep = normalize_unit_box(keep_box)
    anchor_x = clamp01(anchor[0])
    anchor_y = clamp01(anchor[1])
    if current > ratio:
        crop_w = max(1, min(width, int(round(height * ratio))))
        left = solve_axis_crop_start(
            full_size=width,
            crop_size=crop_w,
            anchor_center=anchor_x,
            keep_start=(keep[0] * width) if keep else None,
            keep_end=(keep[2] * width) if keep else None,
        )
        right = left + crop_w
        return (
            clamp01(left / float(width)),
            0.0,
            clamp01(right / float(width)),
            1.0,
        )
    crop_h = max(1, min(height, int(round(width / ratio))))
    top = solve_axis_crop_start(
        full_size=height,
        crop_size=crop_h,
        anchor_center=anchor_y,
        keep_start=(keep[1] * height) if keep else None,
        keep_end=(keep[3] * height) if keep else None,
    )
    bottom = top + crop_h
    return (
        0.0,
        clamp01(top / float(height)),
        1.0,
        clamp01(bottom / float(height)),
    )


def crop_box_has_effect(crop_box: tuple[float, float, float, float] | None) -> bool:
    normalized = normalize_unit_box(crop_box)
    if normalized is None:
        return False
    eps = 0.0005
    return (
        normalized[0] > eps
        or normalized[1] > eps
        or normalized[2] < (1.0 - eps)
        or normalized[3] < (1.0 - eps)
    )


def constrain_box_to_ratio(
    box: tuple[float, float, float, float],
    ratio: float | None,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    """Return a normalized box with the same center but crop aspect in pixels = ratio, clamped to [0,1].
    In normalized space (r-l)/(b-t) must equal ratio*height/width so that (r-l)*width/((b-t)*height)=ratio.
    When ratio is None, use image aspect. When ratio is RATIO_FREE, return box unchanged.
    """
    if is_ratio_free(ratio) or width <= 0 or height <= 0:
        return normalize_unit_box(box) or box
    # Pixel aspect R = (r-l)*W / ((b-t)*H) => (r-l)/(b-t) = R*H/W in normalized space.
    pixel_ratio = float(ratio) if ratio is not None and ratio > 0 else width / float(height)
    target_ratio = pixel_ratio * height / float(width) if width > 0 else pixel_ratio
    l, t, r, b = box[0], box[1], box[2], box[3]
    cx = (l + r) * 0.5
    cy = (t + b) * 0.5
    w = max(r - l, 0.0001)
    h = max(b - t, 0.0001)
    if w / h > target_ratio:
        new_h = w / target_ratio
        new_w = w
    else:
        new_w = h * target_ratio
        new_h = h
    new_l = cx - new_w * 0.5
    new_r = cx + new_w * 0.5
    new_t = cy - new_h * 0.5
    new_b = cy + new_h * 0.5
    if new_l < 0.0:
        new_l, new_r = 0.0, new_w
    if new_r > 1.0:
        new_r, new_l = 1.0, 1.0 - new_w
    if new_t < 0.0:
        new_t, new_b = 0.0, new_h
    if new_b > 1.0:
        new_b, new_t = 1.0, 1.0 - new_h
    return (clamp01(new_l), clamp01(new_t), clamp01(new_r), clamp01(new_b))


def crop_image_by_normalized_box(
    image: Image.Image,
    crop_box: tuple[float, float, float, float] | None,
) -> Image.Image:
    width, height = image.size
    crop_px = normalized_box_to_pixel_box(crop_box, width, height)
    if crop_px is None:
        return image
    left, top, right, bottom = crop_px
    if left <= 0 and top <= 0 and right >= width and bottom >= height:
        return image
    return image.crop((left, top, right, bottom))


def crop_to_ratio_with_anchor(image: Image.Image, ratio: float, anchor: tuple[float, float]) -> Image.Image:
    crop_box = compute_ratio_crop_box(
        width=image.width,
        height=image.height,
        ratio=ratio,
        anchor=anchor,
        keep_box=None,
    )
    return crop_image_by_normalized_box(image, crop_box)


def _resolve_bird_class_ids(names: Any) -> set[int]:
    if names is None:
        return {_COCO_FALLBACK_BIRD_CLASS_ID}
    if isinstance(names, dict):
        ids: set[int] = set()
        for k, v in names.items():
            try:
                key_int = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, str) and _BIRD_CLASS_NAME in v.lower():
                ids.add(key_int)
        if ids:
            return ids
        return {_COCO_FALLBACK_BIRD_CLASS_ID}
    if isinstance(names, (list, tuple)):
        for i, item in enumerate(names):
            if isinstance(item, str) and _BIRD_CLASS_NAME in item.lower():
                return {i}
    return {_COCO_FALLBACK_BIRD_CLASS_ID}


def _short_error_text(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    if len(text) > 120:
        return text[:117] + "..."
    return text


def _best_bird_box_from_result(result: Any, bird_class_ids: set[int]) -> tuple[float, float, float, float] | None:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return None
    xyxy = getattr(boxes, "xyxy", None)
    cls = getattr(boxes, "cls", None)
    conf = getattr(boxes, "conf", None)
    if xyxy is None:
        return None
    try:
        xyxy_arr = xyxy.cpu().numpy()
    except Exception:
        return None
    if xyxy_arr.size == 0:
        return None
    cls_arr = None
    if cls is not None:
        try:
            cls_arr = cls.cpu().numpy()
        except Exception:
            pass
    conf_arr = None
    if conf is not None:
        try:
            conf_arr = conf.cpu().numpy()
        except Exception:
            pass
    best: tuple[float, float, float, float, float] | None = None
    for idx in range(xyxy_arr.shape[0]):
        row = xyxy_arr[idx]
        if row.size < 4:
            continue
        if cls_arr is not None and idx < cls_arr.size:
            c = int(cls_arr.flat[idx])
            if c not in bird_class_ids:
                continue
        x1, y1, x2, y2 = float(row[0]), float(row[1]), float(row[2]), float(row[3])
        area = (x2 - x1) * (y2 - y1)
        score = conf_arr.flat[idx] if conf_arr is not None and idx < conf_arr.size else 1.0
        combined = area * score
        if best is None or combined > best[4]:
            best = (x1, y1, x2, y2, combined)
    if best is None:
        return None
    return (best[0], best[1], best[2], best[3])


def _normalize_xyxy_box(
    box: tuple[float, float, float, float], width: int, height: int
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = box[0], box[1], box[2], box[3]
    if width <= 0 or height <= 0:
        return (clamp01(x1), clamp01(y1), clamp01(x2), clamp01(y2))
    return (
        clamp01(x1 / width),
        clamp01(y1 / height),
        clamp01(x2 / width),
        clamp01(y2 / height),
    )


@lru_cache(maxsize=1)
def _load_torch_module() -> Any | None:
    global _BIRD_DETECTOR_ERROR_MESSAGE
    try:
        import torch as torch_module
    except Exception as exc:
        text = _short_error_text(exc)
        if "numpy" in text.lower():
            _BIRD_DETECTOR_ERROR_MESSAGE = "Torch/NumPy version incompatible (try numpy<2 or matching versions)"
        else:
            _BIRD_DETECTOR_ERROR_MESSAGE = f"Failed to load torch: {text}"
        return None
    return torch_module


@lru_cache(maxsize=1)
def _load_yolo_class() -> Any | None:
    global _BIRD_DETECTOR_ERROR_MESSAGE
    try:
        from ultralytics import YOLO as yolo_class
    except Exception as exc:
        text = _short_error_text(exc)
        if "numpy" in text.lower():
            _BIRD_DETECTOR_ERROR_MESSAGE = "Torch/NumPy version incompatible (try numpy<2 or matching versions)"
        else:
            _BIRD_DETECTOR_ERROR_MESSAGE = f"ultralytics not installed or failed: {text}"
        return None
    return yolo_class


def _preferred_bird_detect_device() -> str | int:
    torch_module = _load_torch_module()
    if torch_module is None:
        return "cpu"
    try:
        if torch_module.cuda.is_available():
            return 0
    except Exception:
        pass
    try:
        backends = getattr(torch_module, "backends", None)
        mps = getattr(backends, "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@lru_cache(maxsize=1)
def _load_bird_detector() -> tuple[Any, set[int]] | None:
    global _BIRD_DETECTOR_ERROR_MESSAGE
    _BIRD_DETECTOR_ERROR_MESSAGE = ""
    yolo_class = _load_yolo_class()
    if yolo_class is None:
        if not _BIRD_DETECTOR_ERROR_MESSAGE:
            _BIRD_DETECTOR_ERROR_MESSAGE = "ultralytics not installed (pip install ultralytics)"
        return None
    last_error = ""
    for model_name in _BIRD_MODEL_CANDIDATES:
        model_path = resolve_bundled_path("models", model_name)
        if not model_path.is_file():
            last_error = f"{model_name}: model file not found ({model_path})"
            continue
        try:
            model = yolo_class(str(model_path))
        except Exception as exc:
            last_error = f"{model_name}: {_short_error_text(exc)}"
            continue
        bird_class_ids = _resolve_bird_class_ids(getattr(model, "names", None))
        if not bird_class_ids:
            last_error = f"{model_name}: no bird class"
            continue
        return (model, bird_class_ids)
    _BIRD_DETECTOR_ERROR_MESSAGE = last_error or "Bird detection model failed to load"
    return None


def get_bird_detector_error_message() -> str:
    return _BIRD_DETECTOR_ERROR_MESSAGE


def preload_bird_detector() -> None:
    """Preload bird detector (for GUI)."""
    _load_bird_detector()


def detect_primary_bird_box(image: Image.Image) -> tuple[float, float, float, float] | None:
    global _BIRD_DETECTOR_ERROR_MESSAGE
    detector = _load_bird_detector()
    if detector is None:
        return None
    _BIRD_DETECTOR_ERROR_MESSAGE = ""
    model, bird_class_ids = detector
    source = image if image.mode == "RGB" else image.convert("RGB")
    detect_device = _preferred_bird_detect_device()
    predict_kwargs = {
        "source": source,
        "conf": _BIRD_DETECT_CONFIDENCE,
        "verbose": False,
    }
    try:
        results = model.predict(device=detect_device, **predict_kwargs)
    except Exception as primary_exc:
        primary_text = _short_error_text(primary_exc)
        if detect_device == "cpu":
            if "Numpy is not available" in primary_text:
                _BIRD_DETECTOR_ERROR_MESSAGE = "Torch/NumPy version incompatible (try numpy<2 or matching versions)"
            else:
                _BIRD_DETECTOR_ERROR_MESSAGE = f"Bird detection inference failed: {primary_text}"
            return None
        try:
            results = model.predict(device="cpu", **predict_kwargs)
        except Exception as fallback_exc:
            fallback_text = _short_error_text(fallback_exc)
            if "Numpy is not available" in fallback_text:
                _BIRD_DETECTOR_ERROR_MESSAGE = "Torch/NumPy version incompatible (try numpy<2 or matching versions)"
            else:
                _BIRD_DETECTOR_ERROR_MESSAGE = f"Bird detection failed: {primary_text}; CPU fallback: {fallback_text}"
            return None
    if not results:
        return None
    best_box = _best_bird_box_from_result(results[0], bird_class_ids)
    if best_box is None:
        return None
    return _normalize_xyxy_box(best_box, source.width, source.height)


def _crop_plan_from_override(
    width: int,
    height: int,
    crop_box: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float, float], tuple[int, int, int, int]]:
    """From a normalized crop box (may extend outside 0-1), compute padded-image box and outer_pad."""
    import math as _math
    l, t, r, b = crop_box[0], crop_box[1], crop_box[2], crop_box[3]
    pad_l = max(0, int(_math.ceil(-l * width)))
    pad_r = max(0, int(_math.ceil(r * width - width)))
    pad_t = max(0, int(_math.ceil(-t * height)))
    pad_b = max(0, int(_math.ceil(b * height - height)))
    pw = width + pad_l + pad_r
    ph = height + pad_t + pad_b
    if pw <= 0 or ph <= 0:
        return ((0.0, 0.0, 1.0, 1.0), (0, 0, 0, 0))
    x1 = pad_l + l * width
    y1 = pad_t + t * height
    x2 = pad_l + r * width
    y2 = pad_t + b * height
    box_norm = (
        x1 / pw,
        y1 / ph,
        x2 / pw,
        y2 / ph,
    )
    return (box_norm, (pad_t, pad_b, pad_l, pad_r))


def compute_crop_plan(
    image: Image.Image,
    raw_metadata: dict[str, Any],
    *,
    ratio: float | None | str,
    center_mode: str,
    camera_type: CameraFocusType | str | None = None,
    inner_top: int = 0,
    inner_bottom: int = 0,
    inner_left: int = 0,
    inner_right: int = 0,
    crop_box_override: tuple[float, float, float, float] | None = None,
) -> tuple[tuple[float, float, float, float] | None, tuple[int, int, int, int]]:
    """Compute (crop_box, outer_pad) using the same logic as the main editor's pipeline.

    Returns the normalised crop box (0-1 coordinates) and the outer padding
    (top, bottom, left, right) in pixels that must be added to the image *before*
    applying the crop. Matches ``_BirdStampCropCalculatorMixin._compute_crop_plan_for_image``.
    When crop_box_override is provided and effective, it is used and outer_pad is derived from it.
    When ratio is RATIO_FREE and no override, returns (None, (0,0,0,0)).
    """
    w, h = image.size
    if crop_box_override is not None and crop_box_has_effect(crop_box_override):
        box_norm, outer_pad = _crop_plan_from_override(w, h, crop_box_override)
        return (box_norm, outer_pad)
    if is_ratio_free(ratio) or ratio is None:
        return (None, (0, 0, 0, 0))
    center_mode = normalize_center_mode(center_mode)
    anchor: tuple[float, float] = (0.5, 0.5)
    keep_box: tuple[float, float, float, float] | None = None

    # Resolve anchor and keep_box
    focus_point = get_focus_point_for_display(raw_metadata, w, h, camera_type=camera_type)
    bird_box: tuple[float, float, float, float] | None = None
    try:
        bird_box = detect_primary_bird_box(image)
    except Exception:
        pass

    if center_mode == CENTER_MODE_FOCUS:
        if focus_point is not None:
            anchor = focus_point
        elif bird_box is not None:
            anchor = box_center(bird_box)
    elif center_mode == CENTER_MODE_BIRD:
        if bird_box is not None:
            anchor = box_center(bird_box)
            keep_box = bird_box
        elif focus_point is not None:
            anchor = focus_point

    # Auto bird crop with asymmetric inner padding
    if keep_box is not None:
        expanded_px = expand_unit_box_to_unclamped_pixels(
            keep_box, width=w, height=h,
            top=inner_top, bottom=inner_bottom, left=inner_left, right=inner_right,
        )
        if expanded_px is not None:
            import math as _math
            kl, kt, kr, kb = expanded_px
            kw = max(1.0, kr - kl)
            kh = max(1.0, kb - kt)
            cx = (kl + kr) * 0.5
            cy = (kt + kb) * 0.5
            cw = kw
            ch = cw / ratio
            if ch < kh:
                ch = kh
                cw = ch * ratio
            cl = cx - cw * 0.5
            ct = cy - ch * 0.5
            cr = cl + cw
            cb = ct + ch
            outer_l = max(0, int(_math.ceil(-cl)))
            outer_t = max(0, int(_math.ceil(-ct)))
            outer_r = max(0, int(_math.ceil(cr - w)))
            outer_b = max(0, int(_math.ceil(cb - h)))
            pw = w + outer_l + outer_r
            ph = h + outer_t + outer_b
            if pw > 0 and ph > 0:
                crop_box = normalize_unit_box((
                    (cl + outer_l) / float(pw),
                    (ct + outer_t) / float(ph),
                    (cr + outer_l) / float(pw),
                    (cb + outer_t) / float(ph),
                ))
                if crop_box is not None:
                    return (crop_box, (outer_t, outer_b, outer_l, outer_r))

    # Fallback: simple ratio crop from anchor
    crop_box = compute_ratio_crop_box(
        width=w, height=h, ratio=ratio, anchor=anchor, keep_box=None,
    )
    if not crop_box_has_effect(crop_box):
        return (None, (0, 0, 0, 0))
    return (crop_box, (0, 0, 0, 0))


def apply_full_crop(
    image: Image.Image,
    raw_metadata: dict[str, Any],
    *,
    ratio: float | None,
    center_mode: str,
    camera_type: CameraFocusType | str | None = None,
    inner_top: int = 0,
    inner_bottom: int = 0,
    inner_left: int = 0,
    inner_right: int = 0,
    max_long_edge: int = 0,
    fill_color: str = "#FFFFFF",
) -> Image.Image:
    """Apply the full main-editor crop pipeline to an image and return the result.

    Supports asymmetric inner padding (``inner_*``) for the bird-crop algorithm,
    unlike ``apply_editor_crop`` which only has a uniform ``crop_padding_px``.
    Suitable for template dialog preview and any standalone render use case.
    """
    crop_box, outer_pad = compute_crop_plan(
        image, raw_metadata,
        ratio=ratio,
        center_mode=center_mode,
        camera_type=camera_type,
        inner_top=inner_top,
        inner_bottom=inner_bottom,
        inner_left=inner_left,
        inner_right=inner_right,
    )
    pt, pb, pl, pr = outer_pad
    if pt or pb or pl or pr:
        image = pad_image(image, top=pt, bottom=pb, left=pl, right=pr, fill=fill_color)
    if crop_box is not None:
        image = crop_image_by_normalized_box(image, crop_box)
    if max_long_edge > 0:
        image = resize_fit(image, max_long_edge)
    return image


def apply_editor_crop(
    image: Image.Image,
    *,
    source_path: Path,
    raw_metadata: dict[str, Any],
    ratio: float | None,
    center_mode: str,
    camera_type: CameraFocusType | str | None = None,
    crop_padding_px: int = DEFAULT_CROP_PADDING_PX,
    max_long_edge: int = 0,
    fill_color: str = "#FFFFFF",
) -> Image.Image:
    """Apply editor-style crop (focus/bird/image center) for CLI or batch use."""
    w, h = image.width, image.height
    if w <= 0 or h <= 0:
        return image
    center_mode = normalize_center_mode(center_mode)
    anchor: tuple[float, float] = (0.5, 0.5)
    keep_box: tuple[float, float, float, float] | None = None

    if center_mode == CENTER_MODE_FOCUS:
        focus_box = extract_focus_box_for_display(raw_metadata, w, h, camera_type=camera_type)
        if focus_box is not None:
            if ratio is not None and ratio > 0:
                focus_box = transform_focus_box_after_crop(
                    focus_box,
                    source_width=w,
                    source_height=h,
                    ratio=ratio,
                    anchor=box_center(focus_box),
                )
            if focus_box is not None:
                keep_box = focus_box
                anchor = box_center(focus_box)
    elif center_mode == CENTER_MODE_BIRD:
        bird_box = detect_primary_bird_box(image)
        if bird_box is not None:
            keep_box = bird_box
            anchor = box_center(bird_box)

    crop_box = compute_ratio_crop_box(
        width=w,
        height=h,
        ratio=ratio,
        anchor=anchor,
        keep_box=keep_box,
    )
    if not crop_box_has_effect(crop_box):
        out = image
    else:
        out = crop_image_by_normalized_box(image, crop_box)
    if crop_padding_px > 0 and crop_box_has_effect(crop_box):
        out = pad_image(
            out,
            crop_padding_px,
            crop_padding_px,
            crop_padding_px,
            crop_padding_px,
            fill=fill_color,
        )
    if max_long_edge > 0:
        out = resize_fit(out, max_long_edge)
    return out
