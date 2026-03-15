#!/usr/bin/env python3
"""
Diagnose report.db based directory loading flow without UI.

This script mirrors the non-Qt parts of:
1) find report root
2) load report rows
3) filter by selected subdirectory (recursive subtree)
4) build absolute file paths
5) build metadata dict from report rows
6) simulate the Python part of `_on_metadata_ready`
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app_common.log as app_log
import app_common.report_db as report_db_module
from app_common.report_db import (
    ReportDB,
    find_report_root,
)

# Keep CLI output focused on stage stats unless user configured otherwise.
logging.getLogger("report_db").setLevel(logging.WARNING)
app_log.LOG_LEVEL = "ERROR"
try:
    report_db_module._log.disabled = True  # type: ignore[attr-defined]
except Exception:
    pass


def _now() -> float:
    return time.perf_counter()


def _stat(stage: str, t0: float, **kwargs: Any) -> None:
    parts = [f"[STAT] {stage}", f"elapsed={_now() - t0:.3f}s"]
    for k, v in kwargs.items():
        parts.append(f"{k}={v!r}")
    print(" ".join(parts), flush=True)


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _is_same_or_child_path(parent: str, child: str) -> bool:
    try:
        parent_abs = os.path.normpath(os.path.abspath(parent))
        child_abs = os.path.normpath(os.path.abspath(child))
        if _path_key(parent_abs) == _path_key(child_abs):
            return True
        common = os.path.commonpath([parent_abs, child_abs])
        return _path_key(common) == _path_key(parent_abs)
    except Exception:
        return False


def _norm_rel_path_for_match(path_text: str) -> str:
    s = str(path_text or "").strip()
    if not s:
        return ""
    s = s.replace("/", os.sep).replace("\\", os.sep)
    s = os.path.normpath(s)
    while s.startswith("." + os.sep):
        s = s[2:]
    if s == ".":
        s = ""
    return os.path.normcase(s)


def _resolve_report_full_path(row: dict, report_root: str, fallback_dir: str) -> str | None:
    cp = row.get("current_path")
    if not cp or not str(cp).strip():
        return None

    cp_text = str(cp).strip()
    if os.path.isabs(cp_text):
        full_path = os.path.normpath(cp_text)
    else:
        base_dir = report_root or fallback_dir
        full_path = os.path.normpath(os.path.join(base_dir, cp_text))

    op = row.get("original_path")
    if op and str(op).strip():
        ext_orig = Path(str(op).strip()).suffix
        if ext_orig:
            full_path = str(Path(full_path).with_suffix(ext_orig))
    return full_path


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v)))
    except Exception:
        return default


def _build_basic_meta_from_row(row: dict[str, Any]) -> dict[str, Any]:
    title = (
        row.get("bird_species_cn")
        or row.get("title")
        or ""
    )
    focus = str(row.get("focus_status") or "").strip().upper()
    color = ""
    if row.get("is_flying") == 1:
        color = "Red"
    elif focus in ("BEST", "精焦"):
        color = "Green"
    return {
        "title": str(title).strip(),
        "color": color,
        "rating": max(0, min(5, _safe_int(row.get("rating"), 0))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose report.db directory load flow.")
    parser.add_argument("selected_dir", help="Selected directory path in UI.")
    parser.add_argument("--report-root", default="", help="Force report root directory.")
    parser.add_argument("--sample", type=int, default=10, help="Print first N matched files.")
    parser.add_argument("--check-file-exists", action="store_true", help="Check os.path.isfile for all matched files.")
    parser.add_argument("--progress-every", type=int, default=2000, help="Progress interval for long loops.")
    args = parser.parse_args()

    selected_dir = os.path.normpath(os.path.abspath(args.selected_dir))
    print(f"[INFO] selected_dir={selected_dir!r}", flush=True)

    t = _now()
    report_root = os.path.normpath(os.path.abspath(args.report_root)) if args.report_root else find_report_root(selected_dir)
    _stat("find_report_root", t, report_root=report_root)
    if not report_root:
        print("[ERROR] report root not found.", flush=True)
        return 2

    db_path = os.path.join(report_root, ".superpicky", ReportDB.DB_FILENAME)
    print(f"[INFO] db_path={db_path!r} exists={os.path.isfile(db_path)}", flush=True)
    if not os.path.isfile(db_path):
        print("[ERROR] report.db not found.", flush=True)
        return 2

    # Quick SQL count probe.
    t = _now()
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM photos")
        sql_total = cur.fetchone()[0]
    _stat("sqlite_count", t, sql_total=sql_total)

    t = _now()
    db = ReportDB.open_if_exists(report_root)
    _stat("reportdb_open", t, opened=bool(db))
    if not db:
        print("[ERROR] failed to open report.db via ReportDB.", flush=True)
        return 2

    try:
        t = _now()
        rows = db.get_all_photos()
        _stat("reportdb_get_all_photos", t, rows=len(rows))
    finally:
        db.close()

    t = _now()
    report_cache: dict[str, dict] = {}
    dup_stems = 0
    missing_stem = 0
    for row in rows:
        stem = row.get("filename")
        if stem is None:
            missing_stem += 1
            continue
        if stem in report_cache:
            dup_stems += 1
        report_cache[stem] = row
    _stat(
        "build_report_cache",
        t,
        unique_stems=len(report_cache),
        dup_stems=dup_stems,
        missing_stem=missing_stem,
    )

    selected_rel = ""
    if _is_same_or_child_path(report_root, selected_dir):
        try:
            selected_rel = os.path.relpath(selected_dir, report_root)
        except Exception:
            selected_rel = ""
    selected_rel_norm = _norm_rel_path_for_match(selected_rel)
    print(f"[INFO] selected_rel={selected_rel!r} selected_rel_norm={selected_rel_norm!r}", flush=True)

    t = _now()
    matched: list[tuple[str, dict]] = []
    skipped_by_prefix = 0
    skipped_no_path = 0
    skipped_outside = 0
    for idx, (stem, row) in enumerate(sorted(report_cache.items(), key=lambda kv: (kv[0].lower() if kv[0] else "")), 1):
        cp_text = str(row.get("current_path") or "").strip()
        if selected_rel_norm and cp_text and not os.path.isabs(cp_text):
            cp_norm = _norm_rel_path_for_match(cp_text)
            if cp_norm != selected_rel_norm and not cp_norm.startswith(selected_rel_norm + os.sep):
                skipped_by_prefix += 1
                continue
        full_path = _resolve_report_full_path(row, report_root, selected_dir)
        if not full_path:
            skipped_no_path += 1
            continue
        if not _is_same_or_child_path(selected_dir, full_path):
            skipped_outside += 1
            continue
        matched.append((full_path, row))
        if args.progress_every > 0 and idx % args.progress_every == 0:
            _stat("filter_progress", t, scanned=idx, matched=len(matched))
    _stat(
        "filter_rows_to_selected_scope",
        t,
        matched=len(matched),
        skipped_by_prefix=skipped_by_prefix,
        skipped_no_path=skipped_no_path,
        skipped_outside=skipped_outside,
    )

    if args.check_file_exists:
        t = _now()
        exists = 0
        for idx, (p, _) in enumerate(matched, 1):
            if os.path.isfile(p):
                exists += 1
            if args.progress_every > 0 and idx % args.progress_every == 0:
                _stat("exists_progress", t, checked=idx, exists=exists)
        _stat("check_file_exists", t, checked=len(matched), exists=exists, missing=max(0, len(matched) - exists))

    t = _now()
    meta_dict: dict[str, dict[str, Any]] = {}
    for idx, (path, row) in enumerate(matched, 1):
        meta_dict[os.path.normpath(path)] = _build_basic_meta_from_row(row)
        if args.progress_every > 0 and idx % args.progress_every == 0:
            _stat("meta_build_progress", t, built=idx)
    _stat("build_meta_dict_from_report", t, meta_entries=len(meta_dict))

    # Simulate Python-only part of _on_metadata_ready loop.
    t = _now()
    tree_item_map = {k: 1 for k in meta_dict.keys()}
    list_item_map = {k: 1 for k in meta_dict.keys()}
    tree_hits = 0
    list_hits = 0
    for idx, (norm_path, _meta) in enumerate(meta_dict.items(), 1):
        if norm_path in tree_item_map:
            tree_hits += 1
        if norm_path in list_item_map:
            list_hits += 1
        if args.progress_every > 0 and idx % args.progress_every == 0:
            _stat("simulate_apply_progress", t, processed=idx, tree_hits=tree_hits, list_hits=list_hits)
    sorted(tree_item_map.keys())
    _stat("simulate_on_metadata_ready_python_only", t, processed=len(meta_dict), tree_hits=tree_hits, list_hits=list_hits)

    if args.sample > 0:
        print(f"[INFO] sample first {min(args.sample, len(matched))} files:", flush=True)
        for p, _ in matched[: args.sample]:
            print(f"  {p}", flush=True)

    print("[INFO] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
