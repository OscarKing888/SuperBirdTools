"""Viewer-only metadata policy: headers and annotations, never ExifTool.

Run in the existing browser worker pool. Missing capture fields are valid, not
a reason to start another reader. Pixels, thumbnails and MakerNotes are not
decoded here; full EXIF/focus APIs remain available in app_common.
"""
from __future__ import annotations

import os
from pathlib import Path
import struct

from PIL import Image, IptcImagePlugin, PngImagePlugin

from app_common.image_formats import RAW_IMAGE_EXTENSIONS
from app_common.exif_io.fast_reader import _flatten_exifread_tags
from app_common.exif_io.writer import _xmp_rows_to_flat_dict
from app_common.exif_io.xmp_sidecar import parse_xmp_metadata
from app_common.file_browser._workers import MetadataLoader
from app_common.log import get_logger

_log = get_logger("superviewer.light_metadata")
_METADATA_LIMIT = 16 * 1024 * 1024
_HEIF_EXTENSIONS = {".heif", ".heic", ".hif"}
_EXIF_KEYS = {
    270: "IFD0:ImageDescription", 271: "IFD0:Make", 272: "IFD0:Model",
    274: "IFD0:Orientation", 18246: "XMP-xmp:Rating",
    33434: "ExifIFD:ExposureTime", 33437: "ExifIFD:FNumber",
    34855: "ExifIFD:ISO", 36867: "ExifIFD:DateTimeOriginal",
    37386: "ExifIFD:FocalLength", 37510: "ExifIFD:UserComment",
    40091: "IFD0:XPTitle", 40092: "IFD0:XPComment",
    40094: "IPTC:Keywords",
}


def _text(value, *, xp=False, user_comment=False) -> str:
    if xp and isinstance(value, (tuple, list)):
        value = bytes(value)
    if isinstance(value, bytes):
        if user_comment:
            from piexif.helper import UserComment
            try:
                return UserComment.load(value).rstrip("\0")
            except (ValueError, UnicodeError):
                value = value[8:] if value[:8] in (b"ASCII\0\0\0", b"UNICODE\0") else value
        if xp:
            return value.decode("utf-16-le", errors="replace").rstrip("\0")
        try:
            return value.decode("utf-8").rstrip("\0")
        except UnicodeError:
            return value.decode("latin-1").rstrip("\0")
    return str(value).rstrip("\0")


def _merge_xmp(rec: dict, packet) -> None:
    if not packet:
        return
    try:
        if len(packet) > _METADATA_LIMIT:
            raise ValueError("XMP packet exceeds metadata limit")
        rec.update(_xmp_rows_to_flat_dict(rec["SourceFile"], parse_xmp_metadata(packet)))
    except Exception as exc:
        _log.debug("[viewer.metadata] invalid embedded XMP path=%r err=%s", rec["SourceFile"], exc)


def _merge_exif(rec: dict, exif: Image.Exif) -> None:
    # Only standard IFDs; get_ifd(MakerNote) would perform proprietary parsing.
    fields = dict(exif)
    if 34665 in exif:
        fields.update(exif.get_ifd(34665))
    for tag, key in _EXIF_KEYS.items():
        value = fields.get(tag)
        if value is None:
            continue
        if tag in (33434, 33437, 37386):
            try:
                value = float(value)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        rec[key] = _text(value, xp=40091 <= tag <= 40095, user_comment=tag == 37510)
    packet = fields.get(700)
    if isinstance(packet, (tuple, list)):
        packet = bytes(packet)
    _merge_xmp(rec, packet)


def _merge_info(rec: dict, info: dict, *, read_exif=True) -> None:
    # Standard PNG text annotations and EXIF/XMP carried by JPEG/PNG/WebP/HEIF.
    for key, target in (("Description", "IFD0:ImageDescription"),
                        ("Comment", "ExifIFD:UserComment"),
                        ("Title", "IFD0:XPTitle"), ("Keywords", "IPTC:Keywords")):
        if key in info:
            rec[target] = _text(info[key])
    if read_exif and info.get("exif"):
        try:
            exif = Image.Exif()
            exif.load(info["exif"])
            _merge_exif(rec, exif)
        except Exception as exc:
            _log.debug("[viewer.metadata] invalid EXIF path=%r err=%s", rec["SourceFile"], exc)
    _merge_xmp(rec, info.get("xmp") or info.get("XML:com.adobe.xmp"))


def _read_png(path: str, rec: dict) -> None:
    # Pillow.getexif() can call load() for PNG. Walk chunks and seek past IDAT
    # instead, including metadata after the pixels. PngStream bounds compressed
    # text expansion; our limit also bounds raw metadata chunks.
    with open(path, "rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Invalid PNG header")
        parser = PngImagePlugin.PngStream(handle)
        try:
            while True:
                chunk, pos, length = parser.read()
                if chunk == b"IEND":
                    break
                if chunk in (b"IHDR", b"eXIf", b"iTXt", b"tEXt", b"zTXt"):
                    if length > _METADATA_LIMIT:
                        raise ValueError("PNG metadata exceeds limit")
                    data = parser.call(chunk, pos, length)
                    parser.crc(chunk, data)
                else:
                    handle.seek(length + 4, os.SEEK_CUR)
            rec["width"], rec["height"] = parser.im_size
            _merge_info(rec, parser.im_info)
        finally:
            parser.close()


def _read_raw(path: str, rec: dict) -> None:
    with open(path, "rb") as handle:
        signature = handle.read(4)
        handle.seek(0)
        if signature in (b"II*\0", b"MM\0*"):
            exif = Image.Exif()
            exif.load_from_fp(handle)
            _merge_exif(rec, exif)
        else:
            import exifread
            # RAF/other supported containers: skip MakerNotes and thumbnails.
            tags = exifread.process_file(handle, details=False, extract_thumbnail=False)
            rec.update(_flatten_exifread_tags(path, tags))
    # IFD0 and EXIF dimensions in RAW files can describe an embedded JPEG.
    # LibRaw.open_file reads source geometry without unpack()/postprocess().
    # Never use rawpy.imread here: it unpacks the entire RAW.
    try:
        import rawpy
        with rawpy.RawPy() as raw:
            raw.open_file(path)
            rec["width"], rec["height"] = int(raw.sizes.width), int(raw.sizes.height)
    except Exception as exc:
        _log.debug("[viewer.metadata] RAW source size unavailable path=%r err=%s", path, exc)
    # Do not pass preview dimensions through the info panel's aliases.
    rec.pop("ExifImageWidth", None)
    rec.pop("ExifImageHeight", None)


def _read_psd(path: str, rec: dict) -> None:
    from app_common.psd_composite import read_psd_composite_size
    size = read_psd_composite_size(path)
    if size is None:
        raise ValueError("Invalid PSD header")
    rec["width"], rec["height"] = size
    # Also works for 16-bit PSD, which Pillow cannot open. Skip color/layer/pixel
    # data and unrelated resources instead of materializing them in memory.
    with open(path, "rb") as handle:
        def uint32():
            return struct.unpack(">I", handle.read(4))[0]
        handle.seek(26)
        color_size = uint32()
        handle.seek(color_size, os.SEEK_CUR)
        resource_size = uint32()
        end = handle.tell() + resource_size
        while handle.tell() < end:
            if handle.read(4) != b"8BIM":
                raise ValueError("Invalid PSD resource")
            resource_id = struct.unpack(">H", handle.read(2))[0]
            name_size = handle.read(1)[0]
            handle.seek(name_size + (1 if name_size % 2 == 0 else 0), os.SEEK_CUR)
            length = uint32()
            next_resource = handle.tell() + length + length % 2
            if next_resource > end:
                raise ValueError("Truncated PSD resource")
            if resource_id in (1058, 1059, 1060) and length <= _METADATA_LIMIT:
                packet = handle.read(length)
                if resource_id == 1060:
                    _merge_xmp(rec, packet)
                else:
                    _merge_info(rec, {"exif": packet})
            handle.seek(next_resource)


def read_light_metadata(path: str) -> dict:
    """Read source headers without decoding pixels or invoking subprocesses.

    Unsupported/corrupt sources raise to the batch reader, which still loads
    sidecars. No negative cache is written for a failed source read.
    """
    path = os.path.normpath(path)
    rec = {"SourceFile": path}
    ext = Path(path).suffix.lower()
    if ext in RAW_IMAGE_EXTENSIONS:
        _read_raw(path, rec)
    elif ext == ".png":
        _read_png(path, rec)
    elif ext == ".psd":
        _read_psd(path, rec)
    elif ext in _HEIF_EXTENSIONS:
        from pillow_heif import open_heif
        header = open_heif(path)
        rec["width"], rec["height"] = header.size
        _merge_info(rec, header.info)
    else:
        with Image.open(path) as header:
            rec["width"], rec["height"] = header.size
            try:
                _merge_exif(rec, header.getexif())
            except Exception as exc:
                _log.debug("[viewer.metadata] invalid EXIF path=%r err=%s", path, exc)
            try:
                iptc = IptcImagePlugin.getiptcinfo(header) or {}
                for tag, key in (((2, 5), "IPTC:ObjectName"),
                                 ((2, 25), "IPTC:Keywords"),
                                 ((2, 120), "IPTC:Caption-Abstract")):
                    value = iptc.get(tag)
                    if value is not None:
                        rec[key] = [_text(v) for v in value] if isinstance(value, list) else _text(value)
            except Exception as exc:
                _log.debug("[viewer.metadata] invalid IPTC path=%r err=%s", path, exc)
            _merge_info(rec, header.info, read_exif=False)
    return rec


class ViewerMetadataLoader(MetadataLoader):
    """Reuse shared scheduling/sidecars, with a separate file-derived cache."""

    def _read_embedded_metadata(self, paths: list[str]) -> tuple[dict[str, dict], list[str]]:
        records = {}
        for path in paths:
            if self._stopped():
                break
            try:
                records[os.path.normpath(path)] = read_light_metadata(path)
            except Exception as exc:
                _log.debug("[viewer.metadata] source read failed path=%r err=%s", path, exc)
        return records, []  # Missing fields must never trigger ExifTool fallback.

    def _metadata_cache_db_path(self, path: str) -> str:
        shared_path = super()._metadata_cache_db_path(path)
        return os.path.join(os.path.dirname(shared_path), "viewer_light_v1.db")

    def _merge_sidecar_metadata(self, rec: dict, sidecar: dict) -> None:
        # Parse each layer independently: an explicit empty JSON comment/tag set
        # or zero rating must not fall through to older embedded aliases.
        parsed = super()._parse_rec(sidecar)
        names = {str(key).split(":")[-1].casefold() for key in sidecar}
        fields = rec.setdefault("_viewer_sidecar_fields", {})
        for field, aliases in (
            ("comment", {"comment", "description", "imagedescription", "usercomment", "xpcomment", "caption-abstract"}),
            ("tags", {"tags", "photo_tags", "subject", "subjects", "keywords"}),
            ("title", {"title", "xptitle", "objectname"}),
            ("rating", {"rating"}), ("pick", {"pick", "picklabel"}),
            ("color", {"label"}),
        ):
            if names & aliases:
                fields[field] = parsed[field]
        if "rating" in names and parsed["pick"] == -1:
            fields["pick"] = -1
        super()._merge_sidecar_metadata(rec, sidecar)

    def _parse_rec(self, rec: dict) -> dict:
        parsed = super()._parse_rec(rec)
        parsed.update(rec.get("_viewer_sidecar_fields", {}))
        for key in ("width", "height"):
            if rec.get(key):
                parsed[key] = rec[key]
        return parsed
