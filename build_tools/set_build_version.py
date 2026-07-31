from __future__ import annotations

import argparse
import re
from pathlib import Path


_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
)
_SEMVER_RE = re.compile(
    rf"""
    (?P<major>0|[1-9]\d*)
    \.
    (?P<minor>0|[1-9]\d*)
    \.
    (?P<patch>0|[1-9]\d*)
    (?:-{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*)?
    (?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?
    """,
    re.VERBOSE,
)

_PACKAGE_VERSION_RE = re.compile(
    r'^(\s*__version__\s*=\s*["\'])([^"\']*)(["\']\s*)$',
    re.MULTILINE,
)
_MAC_SHORT_VERSION_RE = re.compile(
    r'("CFBundleShortVersionString"\s*:\s*")([^"]*)(")',
)
_MAC_BUILD_VERSION_RE = re.compile(
    r'("CFBundleVersion"\s*:\s*")([^"]*)(")',
)


def normalize_version(value: str) -> str:
    """Return a validated SemVer value without an optional leading ``v``."""

    normalized = str(value or "").strip()
    if normalized[:1].lower() == "v":
        normalized = normalized[1:]
    if _SEMVER_RE.fullmatch(normalized) is None:
        raise ValueError(
            "version must use SemVer, for example 1.2.3 or 1.2.3-rc.1"
        )
    return normalized


def normalize_build_number(value: str | int) -> str:
    """Return an Apple-compatible, positive integer bundle build number."""

    normalized = str(value).strip()
    if re.fullmatch(r"[1-9]\d*", normalized) is None:
        raise ValueError("build number must be a positive integer")
    return normalized


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_text(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(value)


def _replace_single_value(
    source: str,
    path: Path,
    pattern: re.Pattern[str],
    value: str,
) -> str:
    updated, count = pattern.subn(
        lambda match: f"{match.group(1)}{value}{match.group(3)}",
        source,
    )
    if count != 1:
        raise RuntimeError(
            f"expected exactly one version field in {path}, found {count}"
        )
    return updated


def apply_build_version(
    repo_root: Path,
    version: str,
    *,
    build_number: str | int = "1",
) -> tuple[Path, ...]:
    """Synchronize the build version fields consumed by both packaged apps."""

    root = Path(repo_root).resolve()
    normalized_version = normalize_version(version)
    normalized_build_number = normalize_build_number(build_number)
    plist_version = normalized_version.split("-", 1)[0].split("+", 1)[0]

    targets = (
        (
            root / "SuperViewer" / "superviewer" / "__init__.py",
            _PACKAGE_VERSION_RE,
            normalized_version,
        ),
        (
            root / "SuperBirdStamp" / "birdstamp" / "__init__.py",
            _PACKAGE_VERSION_RE,
            normalized_version,
        ),
        (
            root / "SuperBirdStamp" / "BirdStamp_mac.spec",
            _MAC_SHORT_VERSION_RE,
            plist_version,
        ),
        (
            root / "SuperBirdStamp" / "BirdStamp_mac.spec",
            _MAC_BUILD_VERSION_RE,
            normalized_build_number,
        ),
        (
            root / "SuperBirdStamp" / "BirdStamp_mac_console.spec",
            _MAC_SHORT_VERSION_RE,
            plist_version,
        ),
        (
            root / "SuperBirdStamp" / "BirdStamp_mac_console.spec",
            _MAC_BUILD_VERSION_RE,
            normalized_build_number,
        ),
    )

    missing = sorted({str(path) for path, _, _ in targets if not path.is_file()})
    if missing:
        raise FileNotFoundError("missing version source(s): " + ", ".join(missing))

    updates: dict[Path, str] = {}
    for path, pattern, replacement in targets:
        source = updates.get(path)
        if source is None:
            source = _read_text(path)
        updates[path] = _replace_single_value(
            source,
            path,
            pattern,
            replacement,
        )

    for path, updated in updates.items():
        _write_text(path, updated)
    return tuple(updates)


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
