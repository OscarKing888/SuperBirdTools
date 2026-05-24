#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnose where a file's rating/pick state is read from.

Current SuperViewer file-browser metadata flow is sidecar + embedded EXIF/XMP;
report.db is shown only as a legacy comparison and is opened read-only.

Usage:
  .venv/bin/python3 SuperViewer/scripts_dev/rating_source_diag.py
  .venv/bin/python3 SuperViewer/scripts_dev/rating_source_diag.py "/path/to/file.HIF" --expect-rating 3 --expect-source embedded
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app_common.log as app_log  # noqa: E402
from app_common.exif_io.photo_meta import (  # noqa: E402
    PhotoMetaDataEXIFEmbeded,
    PhotoMetaDataProxy,
    PhotoMetaDataXMP,
)
from app_common.exif_io.writer import read_batch_metadata  # noqa: E402

DEFAULT_SAMPLE_PATH = (
    "/Users/oscar/Pictures/2026-1-1-深圳湾/新建文件夹/3星_优选/"
    "亚历山大鹦鹉/burst_001/DSC03012.HIF"
)

logging.getLogger("exif_io").setLevel(logging.WARNING)
logging.getLogger("report_db").setLevel(logging.WARNING)
app_log.LOG_LEVEL = "ERROR"


def _normalise_rating(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0, min(5, int(float(text))))
    except Exception:
        return None


def _normalise_pick(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return 0
    if text in {"true", "yes"}:
        return 1
    if text in {"false", "no"}:
        return 0
    if text == "reject":
        return -1
    try:
        return max(-1, min(1, int(float(text))))
    except Exception:
        return None


def _first_present(meta: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in meta and meta.get(key) not in (None, ""):
            return meta.get(key)
    return None


def _extract_rating_pick(meta: dict[str, Any]) -> dict[str, Any]:
    rating_raw = _first_present(
        meta,
        (
            "rating",
            "XMP-xmp:Rating",
            "XMP:Rating",
            "XMP-xmp:rating",
            "Rating",
        ),
    )
    pick_raw = _first_present(
        meta,
        (
            "pick",
            "XMP-xmpDM:pick",
            "XMP-xmpDM:Pick",
            "XMP-xmp:Pick",
            "XMP-xmp:PickLabel",
            "XMP:Pick",
            "XMP:PickLabel",
            "Pick",
        ),
    )
    return {
        "rating": _normalise_rating(rating_raw),
        "pick": _normalise_pick(pick_raw),
        "rating_raw": rating_raw,
        "pick_raw": pick_raw,
    }


def _find_report_db_path(start_dir: str) -> str:
    current = os.path.normpath(start_dir)
    last = ""
    while current and current != last:
        db_path = os.path.join(current, ".superpicky", "report.db")
        if os.path.isfile(db_path):
            return os.path.normpath(db_path)
        last = current
        parent = os.path.dirname(current)
        if not parent or parent == current:
            break
        current = parent
    return ""


def _read_legacy_report_row(file_path: str) -> dict[str, Any]:
    db_path = _find_report_db_path(os.path.dirname(file_path))
    if not db_path:
        return {"db_path": "", "row": None}
    stem = Path(file_path).stem
    try:
        uri = Path(db_path).as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "select filename,rating,pick,current_path,original_path,temp_jpeg_path "
                "from photos where filename=?",
                (stem,),
            ).fetchone()
            if row is None:
                row = con.execute(
                    "select filename,rating,pick,current_path,original_path,temp_jpeg_path "
                    "from photos where current_path like ? or original_path like ? limit 1",
                    (f"%{stem}%", f"%{stem}%"),
                ).fetchone()
            return {"db_path": db_path, "row": dict(row) if row is not None else None}
        finally:
            con.close()
    except Exception as exc:
        return {"db_path": db_path, "row": None, "error": str(exc)}


def _source_name(*, sidecar_rating: int | None, embedded_rating: int | None, proxy_rating: int | None) -> str:
    if proxy_rating is None:
        return "none"
    if sidecar_rating is not None and proxy_rating == sidecar_rating:
        return "sidecar"
    if embedded_rating is not None and proxy_rating == embedded_rating:
        return "embedded"
    return "merged"


def diagnose(file_path: str) -> dict[str, Any]:
    path = os.path.normpath(os.path.abspath(file_path))
    xmp_reader = PhotoMetaDataXMP()
    default_sidecar_path = str(xmp_reader.sidecar_path_for(path))

    sidecar_meta = xmp_reader.read(path)
    embedded_meta = PhotoMetaDataEXIFEmbeded().read(path)
    batch_meta = read_batch_metadata([path], use_cache=False).get(path, {})
    proxy_meta = PhotoMetaDataProxy().read(path)
    legacy_report = _read_legacy_report_row(path)

    sidecar = _extract_rating_pick(sidecar_meta)
    embedded = _extract_rating_pick(embedded_meta)
    batch = _extract_rating_pick(batch_meta)
    proxy = _extract_rating_pick(proxy_meta)
    report_row = legacy_report.get("row")
    report = _extract_rating_pick(report_row or {}) if isinstance(report_row, dict) else {
        "rating": None,
        "pick": None,
        "rating_raw": None,
        "pick_raw": None,
    }

    source = _source_name(
        sidecar_rating=sidecar["rating"],
        embedded_rating=embedded["rating"],
        proxy_rating=proxy["rating"],
    )

    return {
        "path": path,
        "exists": os.path.isfile(path),
        "default_sidecar_path": default_sidecar_path,
        "sidecar_exists": os.path.isfile(default_sidecar_path),
        "sidecar": sidecar,
        "embedded": embedded,
        "read_batch_metadata": batch,
        "proxy": proxy,
        "current_source": source,
        "legacy_report_db": {
            "db_path": legacy_report.get("db_path", ""),
            "row": report_row,
            "rating": report["rating"],
            "pick": report["pick"],
            "error": legacy_report.get("error", ""),
            "note": "legacy comparison only; current PhotoMetaDataProxy/FileListPanel does not read report.db",
        },
    }


def _print_human(result: dict[str, Any]) -> None:
    print(f"[FILE] {result['path']}")
    print(f"  exists: {result['exists']}")
    print(f"  default sidecar: {result['default_sidecar_path']}")
    print(f"  sidecar exists: {result['sidecar_exists']}")
    print()
    for label, key in (
        ("sidecar XMP", "sidecar"),
        ("embedded EXIF/XMP", "embedded"),
        ("read_batch_metadata", "read_batch_metadata"),
        ("PhotoMetaDataProxy", "proxy"),
    ):
        item = result[key]
        print(
            f"[{label}] rating={item['rating']!r} pick={item['pick']!r} "
            f"raw_rating={item['rating_raw']!r} raw_pick={item['pick_raw']!r}"
        )
    print()
    legacy = result["legacy_report_db"]
    print(f"[legacy report.db] path={legacy['db_path']!r}")
    print(f"  rating={legacy['rating']!r} pick={legacy['pick']!r}")
    print(f"  row={legacy['row']!r}")
    if legacy.get("error"):
        print(f"  error={legacy['error']}")
    print(f"  note={legacy['note']}")
    print()
    print(f"[RESULT] current_source={result['current_source']!r} rating={result['proxy']['rating']!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose rating/pick metadata source for one image.")
    parser.add_argument("file_path", nargs="?", default=DEFAULT_SAMPLE_PATH, help="Image file path to diagnose.")
    parser.add_argument("--expect-rating", type=int, default=None, help="Fail if current proxy rating differs.")
    parser.add_argument(
        "--expect-source",
        choices=("sidecar", "embedded", "merged", "none"),
        default=None,
        help="Fail if current source differs.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    result = diagnose(args.file_path)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)

    if args.expect_rating is not None and result["proxy"]["rating"] != args.expect_rating:
        print(
            f"[FAIL] expected rating={args.expect_rating!r}, got={result['proxy']['rating']!r}",
            file=sys.stderr,
        )
        return 1
    if args.expect_source is not None and result["current_source"] != args.expect_source:
        print(
            f"[FAIL] expected source={args.expect_source!r}, got={result['current_source']!r}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
