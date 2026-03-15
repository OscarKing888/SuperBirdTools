#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

EXCLUDED_PARTS = {"_CodeSignature", "__pycache__"}
DEFAULT_MIN_SIZE = 64 * 1024


def _should_skip(path: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in path.parts):
        return True
    suffix = path.suffix.lower()
    if suffix in {".plist", ".json"}:
        return True
    return False


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if _should_skip(path):
            continue
        yield path


def dedupe(roots: list[Path], min_size: int) -> tuple[int, int]:
    seen: dict[tuple[int, str], Path] = {}
    linked_files = 0
    saved_bytes = 0

    for root in roots:
        for path in _iter_files(root):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size < min_size:
                continue
            key = (stat.st_size, _hash_file(path))
            existing = seen.get(key)
            if existing is None:
                seen[key] = path
                continue
            try:
                existing_stat = existing.stat()
            except OSError:
                seen[key] = path
                continue
            if (stat.st_dev, stat.st_ino) == (existing_stat.st_dev, existing_stat.st_ino):
                continue
            path.unlink()
            os.link(existing, path)
            linked_files += 1
            saved_bytes += stat.st_size
    return linked_files, saved_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description="Hardlink identical bundle files to reduce aggregate on-disk size.")
    parser.add_argument("roots", nargs="+", help="Bundle directories to deduplicate together.")
    parser.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE, help="Minimum file size in bytes.")
    args = parser.parse_args()

    roots = [Path(item).resolve() for item in args.roots]
    missing = [str(root) for root in roots if not root.exists()]
    if missing:
        raise SystemExit(f"Missing paths: {', '.join(missing)}")

    linked_files, saved_bytes = dedupe(roots, max(1, int(args.min_size)))
    print(f"[dedupe] hardlinked_files={linked_files} saved_bytes={saved_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

