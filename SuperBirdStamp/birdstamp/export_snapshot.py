"""Worker-only capture shared by image, retained-frame and CLI exports."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
from pathlib import Path

from app_common.exif_io import extract_metadata_with_xmp_priority
from app_common.exif_io.export_xmp import read_export_xmp
from app_common.exif_io.metadata_store import get_store
from app_common.exif_io.reader import full_metadata_signatures
from app_common.exif_io.sidecar_lock import xmp_sidecar_write_lock
from birdstamp.gui import template_context


@dataclass(frozen=True, slots=True)
class ExportMetadataSnapshot:
    raw_metadata: dict
    xmp: bytes
    revision: int
    xmp_digest: str


def capture_export_metadata(path: Path, *, mode="auto") -> ExportMetadataSnapshot:
    """Capture one committed version, including edits still waiting for XMP.

    The file lock protects sidecar publication. SQLite edits remain free to
    commit, so check the resource revision on both sides and retry a bounded
    number of times. Never publish pixels with a different metadata version.
    """
    path = Path(path).resolve(strict=False)
    if not path.is_file():
        raise FileNotFoundError(f"无法读取原图：{path}")
    store = get_store()
    store.prepare_paths([str(path)])
    profile = "full-pillow" if mode.lower() == "off" else "full"
    for _ in range(8):
        with xmp_sidecar_write_lock(str(path)):
            source_before = full_metadata_signatures([str(path)], profile)
            before = store.revisions([str(path)]).get(str(path), 0)
            raw = extract_metadata_with_xmp_priority(path, mode=mode)
            if raw.get("_metadata_complete") is False or raw.get("_metadata_read_ok") is False:
                raise RuntimeError(f"无法完整读取导出元数据：{path}")
            xmp = read_export_xmp(path)
            source_after = full_metadata_signatures([str(path)], profile)
            validated = store.read_many([str(path)], profile=profile, signatures=source_after)
            after = store.revisions([str(path)]).get(str(path), 0)
            if before == after and source_before == source_after:
                # A fallback to an older full row after a reader failure is fine
                # for browsing, but cannot certify this export against current XMP.
                if str(path) not in validated:
                    raise RuntimeError(f"无法校验导出元数据：{path}")
                raw = validated[str(path)]
                clean = {key: value for key, value in raw.items()
                         if not str(key).startswith("_metadata_")}
                stat = path.stat()
                clean.setdefault("File:FileCreateDate", datetime.fromtimestamp(
                    getattr(stat, "st_birthtime", None) or stat.st_ctime).isoformat())
                clean.setdefault("File:FileModifyDate", datetime.fromtimestamp(stat.st_mtime).isoformat())
                clean.setdefault("File:FileSize", stat.st_size)
                return ExportMetadataSnapshot(deepcopy(clean), xmp, after,
                                              hashlib.sha256(xmp).hexdigest())
    raise RuntimeError(f"元数据持续变化，无法固定导出版本，请重试：{path}")


def export_photo_info(path, raw_metadata, photo_info=None):
    """Freeze explicit provider inputs as well as the automatic template map."""
    base = photo_info or template_context.PhotoInfo(path=Path(path))
    row = template_context.get_report_db_row_for_path(Path(path))
    sidecar = {key: value for key, value in raw_metadata.items() if key.startswith("XMP")}
    template_context._overlay_template_metadata_aliases(sidecar, prefer_xmp=True)
    return replace(base, raw_metadata=deepcopy(raw_metadata), metadata_is_snapshot=True,
                   _snapshot_contexts={}, report_row_is_snapshot=True,
                   report_row_snapshot=deepcopy(row), sidecar_metadata_snapshot=sidecar)


def prepare_export_job(job):
    """Mutate a worker-owned job once, before its cache signature or rendering."""
    if job.export_metadata is None:
        snapshot = capture_export_metadata(job.path)
        info = export_photo_info(job.path, snapshot.raw_metadata, job.photo_info)
        context = template_context.build_template_context(info)
        job.raw_metadata = deepcopy(snapshot.raw_metadata)
        job.metadata_context = context
        job.photo_info = info
        job.export_metadata = snapshot
    return job


def prepare_export_jobs(jobs, *, progress_callback=None, cancel_event=None, refresh=False):
    for index, job in enumerate(jobs, 1):
        if cancel_event is not None and cancel_event.is_set():
            from birdstamp.export_stage.video_export_cancelled_error import VideoExportCancelledError
            raise VideoExportCancelledError("导出已取消。")
        if refresh:
            job.export_metadata = None
        prepare_export_job(job)
        if progress_callback is not None:
            progress_callback(index, len(jobs))
    return jobs
