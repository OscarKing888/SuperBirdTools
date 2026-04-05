from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FRAME_CACHE_MANIFEST_VERSION = 1
FRAME_CACHE_ROOT_NAME = "birdstamp_export_cache"
SOURCE_FRAME_BUCKET_KIND = "rendered_source_frames"
VIDEO_FRAME_BUCKET_KIND = "video_frames"
SOURCE_FRAME_CACHE_VERSION = 1
VIDEO_FRAME_CACHE_VERSION = 1


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_payload(value: Any) -> str:
    return hashlib.sha1(stable_json_dumps(value).encode("utf-8")).hexdigest()


def _parse_bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def normalized_path_text(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        resolved = path
    return os.path.normcase(str(resolved))


def path_signature(path: Path) -> str:
    normalized = normalized_path_text(path)
    try:
        stat = path.stat()
        return f"{normalized}:{stat.st_size}:{stat.st_mtime_ns}"
    except Exception:
        return normalized


def global_export_settings_from_settings(settings: dict[str, Any] | None) -> dict[str, bool]:
    raw = settings if isinstance(settings, dict) else {}
    return {
        "draw_banner": _parse_bool_value(raw.get("draw_banner"), True),
        "draw_text": _parse_bool_value(raw.get("draw_text"), True),
        "draw_focus": _parse_bool_value(raw.get("draw_focus"), False),
    }


def build_source_frame_bucket_key(*, global_export_settings: dict[str, Any]) -> str:
    payload = {
        "version": SOURCE_FRAME_CACHE_VERSION,
        "global_export_settings": global_export_settings_from_settings(global_export_settings),
    }
    return hash_payload(payload)


def build_source_frame_signature(*, render_settings: dict[str, Any]) -> str:
    payload = {
        "version": SOURCE_FRAME_CACHE_VERSION,
        "render_settings": render_settings if isinstance(render_settings, dict) else {},
    }
    return hash_payload(payload)


def build_video_frame_bucket_key(
    *,
    source_bucket_key: str,
    target_size: tuple[int, int],
    background_color: str,
) -> str:
    payload = {
        "version": VIDEO_FRAME_CACHE_VERSION,
        "source_bucket_key": str(source_bucket_key or "").strip(),
        "target_size": [int(target_size[0]), int(target_size[1])],
        "background_color": str(background_color or "").strip(),
    }
    return hash_payload(payload)


def build_video_frame_signature(
    *,
    source_frame_signature: str,
    target_size: tuple[int, int],
    background_color: str,
) -> str:
    payload = {
        "version": VIDEO_FRAME_CACHE_VERSION,
        "source_frame_signature": str(source_frame_signature or "").strip(),
        "target_size": [int(target_size[0]), int(target_size[1])],
        "background_color": str(background_color or "").strip(),
    }
    return hash_payload(payload)


@dataclass(slots=True)
class FrameCachePlan:
    cache_dir: Path
    frames_dir: Path
    manifest_path: Path
    bucket_kind: str
    bucket_key: str
    persistent: bool


def create_frame_cache_plan(
    output_path: Path,
    *,
    bucket_kind: str,
    bucket_key: str,
    persistent: bool,
) -> FrameCachePlan:
    parent_dir = output_path.parent.resolve(strict=False)
    parent_dir.mkdir(parents=True, exist_ok=True)
    normalized_kind = str(bucket_kind or "frames").strip() or "frames"
    normalized_key = str(bucket_key or "default").strip().lower() or "default"
    if persistent:
        cache_dir = parent_dir / FRAME_CACHE_ROOT_NAME / normalized_kind / normalized_key
    else:
        prefix = f"{normalized_kind}_{normalized_key[:12]}_"
        cache_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent_dir)))
    return FrameCachePlan(
        cache_dir=cache_dir,
        frames_dir=cache_dir / "frames",
        manifest_path=cache_dir / "manifest.json",
        bucket_kind=normalized_kind,
        bucket_key=normalized_key,
        persistent=bool(persistent),
    )


def empty_frame_manifest(plan: FrameCachePlan, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version": FRAME_CACHE_MANIFEST_VERSION,
        "bucket_kind": plan.bucket_kind,
        "bucket_key": plan.bucket_key,
        "metadata": dict(metadata or {}),
        "frames": {},
    }


def load_frame_manifest(plan: FrameCachePlan) -> dict[str, Any]:
    try:
        raw = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return empty_frame_manifest(plan)
    if not isinstance(raw, dict):
        return empty_frame_manifest(plan)
    if int(raw.get("version") or 0) != FRAME_CACHE_MANIFEST_VERSION:
        return empty_frame_manifest(plan)
    if str(raw.get("bucket_kind") or "").strip() != plan.bucket_kind:
        return empty_frame_manifest(plan)
    if str(raw.get("bucket_key") or "").strip() != plan.bucket_key:
        return empty_frame_manifest(plan)
    metadata = raw.get("metadata")
    frames = raw.get("frames")
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(frames, dict):
        frames = {}
    return {
        "version": FRAME_CACHE_MANIFEST_VERSION,
        "bucket_kind": plan.bucket_kind,
        "bucket_key": plan.bucket_key,
        "metadata": dict(metadata),
        "frames": dict(frames),
    }


def write_frame_manifest(
    plan: FrameCachePlan,
    manifest: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    payload = empty_frame_manifest(plan)
    if isinstance(manifest, dict):
        raw_metadata = manifest.get("metadata")
        raw_frames = manifest.get("frames")
        if isinstance(raw_metadata, dict):
            payload["metadata"] = dict(raw_metadata)
        if isinstance(raw_frames, dict):
            payload["frames"] = dict(raw_frames)
    if isinstance(metadata, dict) and metadata:
        payload["metadata"].update(metadata)
    plan.cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = plan.manifest_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, plan.manifest_path)


def frame_output_path(plan: FrameCachePlan, index: int, *, suffix: str = "png") -> Path:
    normalized_suffix = str(suffix or "png").strip().lower().lstrip(".") or "png"
    return plan.frames_dir / f"frame_{max(1, int(index)):06d}.{normalized_suffix}"


def reusable_frame_path(
    plan: FrameCachePlan,
    manifest: dict[str, Any],
    *,
    index: int,
    source_path: Path,
    source_signature: str,
    frame_signature: str,
) -> Path | None:
    frames = manifest.get("frames") if isinstance(manifest, dict) else None
    if not isinstance(frames, dict):
        return None
    record = frames.get(str(int(index)))
    if not isinstance(record, dict):
        return None
    if str(record.get("source_path") or "").strip() != normalized_path_text(source_path):
        return None
    if str(record.get("source_signature") or "").strip() != str(source_signature or "").strip():
        return None
    if str(record.get("frame_signature") or "").strip() != str(frame_signature or "").strip():
        return None
    relative_path = str(record.get("relative_path") or "").strip()
    if not relative_path:
        return None
    frame_path = plan.cache_dir / relative_path
    if not frame_path.is_file():
        return None
    return frame_path


def update_frame_manifest_record(
    plan: FrameCachePlan,
    manifest: dict[str, Any],
    *,
    index: int,
    source_path: Path,
    source_signature: str,
    frame_signature: str,
    frame_path: Path,
) -> None:
    frames = manifest.setdefault("frames", {})
    if not isinstance(frames, dict):
        frames = {}
        manifest["frames"] = frames
    frames[str(int(index))] = {
        "source_path": normalized_path_text(source_path),
        "source_signature": str(source_signature or "").strip(),
        "frame_signature": str(frame_signature or "").strip(),
        "relative_path": frame_path.resolve(strict=False).relative_to(plan.cache_dir.resolve(strict=False)).as_posix(),
    }


__all__ = [
    "FRAME_CACHE_MANIFEST_VERSION",
    "FRAME_CACHE_ROOT_NAME",
    "SOURCE_FRAME_BUCKET_KIND",
    "VIDEO_FRAME_BUCKET_KIND",
    "FrameCachePlan",
    "build_source_frame_bucket_key",
    "build_source_frame_signature",
    "build_video_frame_bucket_key",
    "build_video_frame_signature",
    "create_frame_cache_plan",
    "empty_frame_manifest",
    "frame_output_path",
    "global_export_settings_from_settings",
    "hash_payload",
    "load_frame_manifest",
    "normalized_path_text",
    "path_signature",
    "reusable_frame_path",
    "stable_json_dumps",
    "update_frame_manifest_record",
    "write_frame_manifest",
]
