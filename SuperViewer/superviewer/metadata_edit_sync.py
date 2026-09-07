"""Synchronize successful EXIF-table sidecar edits with browser state."""
from __future__ import annotations

from app_common.exif_io.photo_meta import (
    PhotoMetaDataXMP,
    _is_xmp_description_key,
    _is_xmp_pick_key,
    _is_xmp_rating_key,
    _is_xmp_subject_key,
    _is_xmp_title_key,
    _xmp_sidecar_write_key,
)
from app_common.file_browser import _browser_core as display


_DISPLAY_FIELDS = {
    "xmp-exif:exposuretime": ("shutter", display._metadata_shutter_text),
    "xmp-exif:fnumber": ("aperture", display._metadata_aperture_text),
    "xmp-exif:photographicsensitivity": ("iso", display._metadata_iso_text),
    "xmp-exif:isospeedratings": ("iso", display._metadata_iso_text),
    "xmp-exif:focallength": ("focal_length", display._metadata_focal_length_text),
    "xmp-tiff:model": ("camera_model", display._metadata_camera_model_text),
    "xmp-aux:lensmodel": ("lens_model", display._metadata_lens_model_text),
    "xmp-aux:lens": ("lens_model", display._metadata_lens_model_text),
    "xmp-exif:datetimeoriginal": ("date_time_original", display._metadata_capture_time_text),
    "xmp-xmp:createdate": ("date_time_original", display._metadata_capture_time_text),
    "xmp-photoshop:city": ("sharpness", display._metadata_sharpness_text),
    "xmp-photoshop:state": ("aesthetic", display._metadata_aesthetic_text),
    "xmp-photoshop:country": ("focus_status", display._metadata_focus_status_text),
}


def sync_saved_xmp_edit(file_list, path: str, tag_key: str) -> None:
    """Read only the saved XML; never synchronously reread the source image."""
    key = _xmp_sidecar_write_key(tag_key)
    if _is_xmp_subject_key(key):
        # An EXIF-table edit bypasses tag commands. Refresh their cache and
        # generations, and discard inverses based on the previous subjects.
        file_list._update_photo_tag_cache_for_paths([path])
        file_list.clear_tag_history()
        file_list._refresh_metadata_state_for_paths([path])
        if file_list._photo_tag_lookup_needed_for_filters():
            file_list._apply_filter()
        return

    fresh = PhotoMetaDataXMP().read(path)
    updates = {
        key: next((value for name, value in fresh.items() if name.lower() == key.lower()), ""),
    }
    if _is_xmp_title_key(key):
        value = fresh.get("XMP-dc:Title", "")
        updates.update(title=value, bird_species_cn=value)
    elif _is_xmp_description_key(key):
        value = fresh.get("XMP-dc:Description", "")
        for alias in (
            "comment", "description", "Description", "XMP-dc:Description",
            "XMP-dc:description", "XMP:Description", "IFD0:ImageDescription",
            "EXIF:ImageDescription", "ExifIFD:UserComment", "EXIF:UserComment",
            "UserComment", "IFD0:XPComment", "IPTC:Caption-Abstract", "caption",
        ):
            updates[alias] = value
    elif _is_xmp_rating_key(key):
        updates["rating"] = fresh.get("rating", 0)
    elif _is_xmp_pick_key(key):
        updates["pick"] = fresh.get("pick", 0)
    elif key.lower() in _DISPLAY_FIELDS:
        field, formatter = _DISPLAY_FIELDS[key.lower()]
        updates[field] = formatter(fresh)
        # Mirror removed legacy property names in memory so they cannot
        # outrank the standard property just written to the sidecar.
        if field == "iso":
            updates["XMP-exif:PhotographicSensitivity"] = updates[key]
        elif field == "lens_model":
            updates["XMP-aux:LensModel"] = updates[key]
    else:
        # Preserve clears for arbitrary fields, even when XML omits the node.
        updates.setdefault(key, "")
    file_list.sync_metadata_edit_for_path(path, meta_updates=updates)
