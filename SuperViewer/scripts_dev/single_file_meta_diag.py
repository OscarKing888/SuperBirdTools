#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single-file metadata source diagnostics.

Usage:
  python scripts/single_file_meta_diag.py "F:\\path\\to\\file.ARW"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app_common.log as app_log  # noqa: E402
import app_common.report_db as report_db_module  # noqa: E402
from app_common.report_db import ReportDB, find_report_root, report_row_to_exiftool_style  # noqa: E402

logging.getLogger("report_db").setLevel(logging.WARNING)
app_log.LOG_LEVEL = "ERROR"
try:
    report_db_module._log.disabled = True  # type: ignore[attr-defined]
except Exception:
    pass


def _resolve_report_full_path(row: dict[str, Any], report_root: str) -> str | None:
    cp = row.get("current_path")
    if not cp or not str(cp).strip():
        return None
    cp_text = str(cp).strip()
    if os.path.isabs(cp_text):
        full_path = os.path.normpath(cp_text)
    else:
        full_path = os.path.normpath(os.path.join(report_root, cp_text))
    op = row.get("original_path")
    if op and str(op).strip():
        ext = Path(str(op).strip()).suffix
        if ext:
            full_path = str(Path(full_path).with_suffix(ext))
    return full_path


def _safe_parse_browser_meta(flat: dict[str, Any]) -> dict[str, Any]:
    title = (
        flat.get("XMP-dc:Title")
        or flat.get("XMP-dc:title")
        or flat.get("IFD0:XPTitle")
        or flat.get("IPTC:ObjectName")
        or ""
    )
    color = flat.get("XMP-xmp:Label") or ""
    try:
        rating = int(float(str(flat.get("XMP-xmp:Rating") or 0)))
    except Exception:
        rating = 0
    pick_raw = (
        flat.get("XMP-xmpDM:pick")
        or flat.get("XMP-xmpDM:Pick")
        or flat.get("XMP-xmp:Pick")
        or flat.get("XMP-xmp:PickLabel")
        or flat.get("XMP:Pick")
        or flat.get("XMP:PickLabel")
        or ""
    )
    try:
        s = str(pick_raw).strip().lower()
        if s in ("-1", "reject"):
            pick = -1
        elif s in ("1", "true", "yes"):
            pick = 1
        else:
            pick = 0
    except Exception:
        pick = 0
    city = flat.get("XMP:City") or flat.get("XMP-photoshop:City") or ""
    state = flat.get("XMP:State") or flat.get("XMP-photoshop:State") or ""
    country = (
        flat.get("XMP:Country")
        or flat.get("XMP-photoshop:Country")
        or flat.get("XMP-photoshop:Country-PrimaryLocationName")
        or ""
    )
    return {
        "title": str(title).strip(),
        "color": str(color).strip(),
        "rating": rating,
        "pick": pick,
        "city": str(city).strip(),
        "state": str(state).strip(),
        "country": str(country).strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose single-file metadata source.")
    parser.add_argument("file_path", help="Absolute file path shown in file list.")
    parser.add_argument(
        "--with-read-batch",
        action="store_true",
        help="Try read_batch_metadata for this path (requires runtime deps).",
    )
    args = parser.parse_args()

    file_path = os.path.normpath(os.path.abspath(args.file_path))
    stem = Path(file_path).stem
    parent_dir = os.path.dirname(file_path)

    print(f"[INFO] file_path={file_path!r}")
    print(f"[INFO] stem={stem!r}")

    report_root = find_report_root(parent_dir)
    print(f"[INFO] report_root={report_root!r}")
    if not report_root:
        print("[ERROR] report_root not found")
        return 2

    db = ReportDB.open_if_exists(report_root)
    if not db:
        print("[ERROR] report.db not found/open failed")
        return 2

    try:
        row = db.get_photo(stem)
    finally:
        db.close()

    if not row:
        print("[ERROR] row not found by stem in report.db")
        return 3

    print("[INFO] row found in report.db")
    print(f"  filename={row.get('filename')!r}")
    print(f"  current_path={row.get('current_path')!r}")
    print(f"  original_path={row.get('original_path')!r}")
    print(f"  rating={row.get('rating')!r} focus_status={row.get('focus_status')!r}")
    print(
        f"  title={row.get('title')!r} adj_sharpness={row.get('adj_sharpness')!r} "
        f"adj_topiq={row.get('adj_topiq')!r}"
    )

    resolved = _resolve_report_full_path(row, report_root)
    print(f"[INFO] resolved_path_from_report={resolved!r}")
    print(f"[INFO] exists(file_path)={os.path.isfile(file_path)} exists(resolved)={os.path.isfile(resolved or '')}")

    cp = str(row.get("current_path") or "").strip()
    op = str(row.get("original_path") or "").strip()
    cp_ext = Path(cp).suffix.lower() if cp else ""
    op_ext = Path(op).suffix.lower() if op else ""
    print(f"[CHECK] current_path_ext={cp_ext!r} original_path_ext={op_ext!r}")
    if cp_ext == ".xmp" and op_ext and op_ext != ".xmp":
        print("[CHECK] xmp->original ext replacement: EXPECTED and APPLIED")

    flat = report_row_to_exiftool_style(row, file_path)
    parsed = _safe_parse_browser_meta(flat)
    print("[INFO] parsed_from_db_row:", parsed)

    if args.with_read_batch:
        try:
            from app_common.exif_io import read_batch_metadata  # local import; optional runtime deps
        except Exception as e:
            print(f"[WARN] read_batch_metadata unavailable: {e}")
            return 0

        batch = read_batch_metadata([file_path])
        rec = batch.get(os.path.normpath(file_path), {})
        print(f"[INFO] read_batch keys={len(rec)} has_record={bool(rec)}")
        if rec:
            parsed_batch = _safe_parse_browser_meta(rec)
            print("[INFO] parsed_from_read_batch:", parsed_batch)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
