"""Pure revision/completeness policy for queued editor metadata snapshots."""
from __future__ import annotations
import os


def cached_path_key(path):
    """Look up an already-resolved editor identity without touching its volume."""
    from app_common.exif_io.metadata_identity import cached_path
    return os.path.abspath(cached_path(path)).casefold()


def photo_info_from_snapshot(path, metadata, existing=None):
    """Update list/selection data without rediscovering a sidecar on the GUI."""
    from birdstamp.gui import template_context as context
    known = isinstance(existing, context.PhotoInfo)
    base = (context.preview_photo_info(existing) if known else
            context.EditorPhotoInfo(path=path, metadata_is_snapshot=True))
    result = context.ensure_editor_photo_info(base, raw_metadata=metadata)
    # Keep the row's ordinary export policy; only this update is snapshot-only.
    result.metadata_is_snapshot = existing.metadata_is_snapshot if known else False
    return result


def metadata_revision(record):
    if not isinstance(record, dict):
        return None
    revision = record.get("_metadata_revision")
    if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
        return revision
    return None


def complete_metadata(record):
    if not isinstance(record, dict) or not record:
        return False
    if (record.get("_metadata_complete") is False or record.get("_metadata_read_ok") is False
            or record.get("_metadata_refresh_failed") is True):
        return False
    if record.get("_metadata_read_ok") is True:
        return True
    if any(key == "Error" or str(key).endswith(":Error") for key in record):
        return False
    return any(key != "SourceFile" and not str(key).startswith("_metadata_") for key in record)


def accepts_metadata(record, known_records=(), *, current_revision=0):
    """Reject late generations and placeholders without doing any source I/O."""
    if not isinstance(record, dict):
        return False
    known = [item for item in known_records if isinstance(item, dict)]
    revisions = [value for item in known if (value := metadata_revision(item)) is not None]
    incoming = metadata_revision(record)
    if current_revision > 0:
        revisions.append(current_revision)
    if revisions and (incoming is None or incoming < max(revisions)):
        return False
    if not complete_metadata(record) and any(complete_metadata(item) for item in known):
        known_revisions = [metadata_revision(item) for item in known]
        latest = max((value for value in known_revisions if value is not None), default=-1)
        # New accepted edits can arrive before a complete source read. Their
        # fields must win; an equal-generation failure cannot erase a full row.
        if incoming is None or incoming <= latest:
            return False
    return True


def metadata_candidate(record, known_records=(), *, current_revision=0):
    known = [item for item in known_records if isinstance(item, dict)]
    if not accepts_metadata(record, known, current_revision=current_revision):
        return None
    if complete_metadata(record):
        return dict(record)
    merged = {}
    for item in sorted(known, key=lambda value: metadata_revision(value)
                       if metadata_revision(value) is not None else -1):
        merged.update(item)
    merged.update(record)
    merged["_metadata_complete"] = False
    return merged
