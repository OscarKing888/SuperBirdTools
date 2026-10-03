from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# 兼容直接执行本脚本（CI --check-only 不需要 Qt 或第三方包）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_identity import git_version, normalize_build_number, normalize_version, normalize_version_prefix, version_prefix


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_text(path: Path, value: str) -> None:
    # 内容未变时保留时间戳，让 PyInstaller 的增量缓存继续有效。
    if path.exists() and _read_text(path) == value:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(value)


def apply_build_version(
    repo_root: Path,
    version: str,
    *,
    build_number: str | int | None = None,
    output: Path | None = None,
) -> tuple[Path, ...]:
    """只更新统一配置，应用和各平台 spec 均直接读取它。"""
    normalized_version = (normalize_version_prefix(version) if version.count(".") == 1
                          else normalize_version(version))
    path = Path(repo_root).resolve() / "app_metadata.json"
    source = _read_text(path)
    data = json.loads(source)
    if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
        raise ValueError(f"Invalid app metadata: {path}")
    data["version"] = normalized_version
    data["build_number"] = normalize_build_number(build_number if build_number is not None else data["build_number"])
    newline = "\r\n" if "\r\n" in source else "\n"
    updated = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").replace("\n", newline)
    destination = Path(output) if output is not None else path
    _write_text(destination, updated)
    return (destination,)


def resolve_build_version(repo_root: Path, value: str | None = None, *, release_tag: bool = False) -> str:
    """本地默认使用配置前两段；CI 的完整版本/Tag 必须与实际 HEAD 对应。"""
    if value is None:
        if release_tag:
            raise ValueError("release tag is required")
        data = json.loads((repo_root / "app_metadata.json").read_text(encoding="utf-8"))
        return git_version(version_prefix(data["version"]), repo_root)
    value = value.strip()
    if release_tag and not value.startswith("v"):
        raise ValueError("release tag must use vMAJOR.MINOR.HASH")
    raw = value.removeprefix("v")
    prefix = version_prefix(raw)
    expected = git_version(prefix, repo_root)
    if (raw != prefix and raw != expected) or (release_tag and raw != expected):
        raise ValueError(f"version must match the current Git commit: {expected}")
    if release_tag:
        data = json.loads((repo_root / "app_metadata.json").read_text(encoding="utf-8"))
        if version_prefix(data["version"]) != prefix:
            raise ValueError("release tag prefix does not match app_metadata.json")
    return expected


def prepare_build_metadata(repo_root: Path, *, version: str | None = None,
                           build_number: str | int | None = None) -> Path:
    """所有 spec 共用同一份生成配置，源码配置不写入自身提交的 hash。"""
    repo_root = Path(repo_root).resolve()
    version = resolve_build_version(repo_root, version or os.environ.get("SUPERBIRDTOOLS_BUILD_VERSION"))
    build_number = build_number if build_number is not None else os.environ.get("SUPERBIRDTOOLS_BUILD_NUMBER")
    build_root = Path(os.environ.get("SUPERBIRDTOOLS_BUILD_ROOT", str(repo_root / "build")))
    if not build_root.is_absolute():
        build_root = repo_root / build_root
    output = build_root / "version" / "app_metadata.json"
    apply_build_version(repo_root, version, build_number=build_number, output=output)
    return output


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize SuperBirdTools package version fields."
    )
    parser.add_argument("version", nargs="?", help="MAJOR.MINOR, or a full MAJOR.MINOR.HASH matching HEAD; defaults to app_metadata.json.")
    parser.add_argument("--release-tag", action="store_true", help="Require vMAJOR.MINOR.HASH matching HEAD and the configured prefix.")
    parser.add_argument(
        "--build-number",
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
        version = resolve_build_version(args.repo_root, args.version or os.environ.get("SUPERBIRDTOOLS_BUILD_VERSION"), release_tag=args.release_tag)
        if args.build_number is not None:
            normalize_build_number(args.build_number)
        if args.check_only:
            print(version)
            return 0
        output = prepare_build_metadata(
            args.repo_root,
            version=version,
            build_number=args.build_number,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    print(f"build version: {version}")
    print(f"generated: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
