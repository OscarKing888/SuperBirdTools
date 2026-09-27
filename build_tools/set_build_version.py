from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# 兼容直接执行本脚本（CI --check-only 不需要 Qt 或第三方包）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_identity import normalize_build_number, normalize_version


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_text(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(value)


def apply_build_version(
    repo_root: Path,
    version: str,
    *,
    build_number: str | int = "1",
) -> tuple[Path, ...]:
    """只更新统一配置，应用和各平台 spec 均直接读取它。"""
    normalized_version = normalize_version(version)
    normalized_build_number = normalize_build_number(build_number)
    path = Path(repo_root).resolve() / "app_metadata.json"
    source = _read_text(path)
    data = json.loads(source)
    if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
        raise ValueError(f"Invalid app metadata: {path}")
    data["version"] = normalized_version
    data["build_number"] = normalized_build_number
    newline = "\r\n" if "\r\n" in source else "\n"
    updated = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").replace("\n", newline)
    _write_text(path, updated)
    return (path,)


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize SuperBirdTools package version fields."
    )
    parser.add_argument("version", help="SemVer value, optionally prefixed with v.")
    parser.add_argument(
        "--build-number",
        default="1",
        help="Positive integer used as the macOS CFBundleVersion.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_default_repo_root(),
        help="Repository root containing SuperViewer and SuperBirdStamp.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate and print the normalized version without changing files.",
    )
    args = parser.parse_args()

    try:
        version = normalize_version(args.version)
        if args.check_only:
            print(version)
            return 0
        changed = apply_build_version(
            args.repo_root,
            version,
            build_number=args.build_number,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    print(f"build version: {version}")
    for path in changed:
        print(f"updated: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
