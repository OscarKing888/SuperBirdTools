"""只读图库的元数据诊断；两次独立运行可比较首次构建和跨进程 SQLite 命中。

从仓库根用共享 .venv 执行；必须指定独立 --cache-dir，所有性能缓存仅写入那里。
计时不包含缩略图/GUI；不清空 OS 文件缓存，也不自动删除已有缓存。
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0, help="0 = 全目录")
    args = parser.parse_args()
    root = args.directory.resolve()
    if not root.is_dir():
        parser.error("照片目录不存在")
    cache_dir = args.cache_dir.resolve()
    if cache_dir == root or root in cache_dir.parents:
        parser.error("诊断缓存目录必须位于图库之外")
    os.environ["SUPERBIRD_METADATA_CACHE_DIR"] = str(cache_dir)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from PyQt6.QtCore import QCoreApplication, Qt
    from app_common.file_browser import _workers as workers, _browser_core as core
    from app_common.file_browser._metadata_memory import MetadataMemoryCache
    from app_common.file_browser._work_pool import BrowserWorkPool
    from app_common.exif_io import metadata_sqlite, meta_disk_cache, xmp_sidecar
    from app_common.exif_io.exiftool_runner import close_exiftool_process

    app = QCoreApplication.instance() or QCoreApplication([])
    workers._meta_disk_cache_db_path_for_file = lambda *a: str(cache_dir / "legacy-exif.sqlite3")
    counts = Counter()
    lock = threading.Lock()

    def instrument(module, name, label):
        original = getattr(module, name)

        def wrapped(*a, **kw):
            with lock:
                counts[label] += 1
            return original(*a, **kw)

        setattr(module, name, wrapped)

    instrument(xmp_sidecar, "_build_xmp_dir_index", "directory_scans")
    instrument(xmp_sidecar, "read_xmp_file", "xml_reads")
    instrument(workers, "read_raw_embedded_focus_metadata", "raw_focus_reads")
    instrument(workers, "probe_video", "video_probes")
    scan_results = []
    scan = workers.DirectoryScanWorker(str(root), True, report_root=str(root), use_report_db=True, include_videos=True)
    scan.scan_finished.connect(lambda *values: scan_results.append(values), Qt.ConnectionType.DirectConnection)
    started = time.perf_counter()
    scan.run()
    scan_seconds = time.perf_counter() - started
    if not scan_results:
        raise RuntimeError("目录扫描失败")
    _, paths, _, _, rows = scan_results[0]
    if args.limit > 0:
        paths = paths[:args.limit]
    stamps = {p: metadata_sqlite.file_stamp(p) for p in paths}
    pool = BrowserWorkPool(max(2, args.workers), min(3, max(2, args.workers)))
    pool.set_thumbnail_mode(False)
    loader = workers.MetadataLoader(paths, object(), metadata_tags=core._SUPERBIRDSTAMP_BROWSER_METADATA_TAGS,
                                    report_rows_by_path=rows, selected_dir=str(root), work_pool=pool,
                                    memory_cache=MetadataMemoryCache())
    processed, first, last_print = 0, None, 0.0
    totals = Counter()
    started = time.perf_counter()

    def collect(batch):
        nonlocal processed, first, last_print
        elapsed = time.perf_counter() - started
        processed += len(batch)
        if first is None:
            first = elapsed
        for rec in batch.values():
            totals["title"] += bool(rec.get("title"))
            totals["focus_box"] += rec.get("focus_box") is not None
            totals["video_info"] += bool(rec.get("video_info"))
        if elapsed - last_print >= 10:
            print(json.dumps({"progress": processed, "seconds": round(elapsed, 2)}), flush=True)
            last_print = elapsed

    loader.metadata_batch_ready.connect(collect, Qt.ConnectionType.DirectConnection)
    try:
        loader.run()
        seconds = time.perf_counter() - started
        flushed = metadata_sqlite.get_cache().flush(timeout=30)
        print(json.dumps({"files": len(paths), "processed": processed, "scan_seconds": scan_seconds,
                          "metadata_seconds": seconds, "first_batch_seconds": first, "cache_flushed": flushed,
                          "operations": counts, "fields": totals,
                          "source_files_unchanged": all(metadata_sqlite.file_stamp(p) == stamp for p, stamp in stamps.items()),
                          "cache_path": str(metadata_sqlite.get_cache().path)}, ensure_ascii=False), flush=True)
    finally:
        pool.shutdown()
        metadata_sqlite.close_cache()
        meta_disk_cache.close_all()
        close_exiftool_process()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
