"""Build and validate full release ZIPs while incremental updates are paused."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_identity import normalize_version
from build_tools.set_build_version import resolve_build_version
from SuperBirdUpdater.build import package_full_suite
from SuperBirdUpdater.common import APPS, UpdateError, executable
from SuperBirdUpdater.manifest import MAX_ASSET_SIZE

RELEASE_TARGETS = (("windows", "x86_64"), ("macos", "arm64"))


def validate_packages(directory: Path, version: str) -> None:
    version = normalize_version(version)
    actual = {p.name for p in directory.iterdir() if p.name != "SHA256SUMS.txt"}
    selected = []
    allowed = set()
    missing = set()
    for target, arch in RELEASE_TARGETS:
        suffixes = ("-cpu", "-cuda") if target == "windows" else ("",)
        candidates = {f"SuperBirdTools-{version}-{target}-{arch}{suffix}.zip" for suffix in suffixes}
        allowed.update(candidates)
        present = actual & candidates
        if not present:
            missing.add(f"{target}-{arch}")
        selected.extend((directory / name, target) for name in sorted(present))
    if missing or actual - allowed:
        raise UpdateError(f"Release packages mismatch: missing={missing}, extra={actual - allowed}")
    for package, target in selected:
        stem = package.stem
        if not package.is_file() or not 0 < package.stat().st_size <= MAX_ASSET_SIZE:
            raise UpdateError(f"Invalid release package size: {package.name}")
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            for app in APPS:
                required = executable(Path(stem), app, target).as_posix()
                if required not in names:
                    raise UpdateError(f"Missing executable in package: {required}")
            bad = archive.testzip()
            if bad:
                raise UpdateError(f"Corrupt package member: {bad}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--output", type=Path, default=Path("release"))
    parser.add_argument("--platform", choices=["windows", "macos"])
    parser.add_argument("--arch", choices=["x86_64", "arm64"])
    parser.add_argument("--version")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    version = resolve_build_version(repo, args.version or os.environ.get("SUPERBIRDTOOLS_BUILD_VERSION"))
    if args.verify:
        validate_packages(args.output, version)
    else:
        if (args.platform, args.arch) not in RELEASE_TARGETS:
            parser.error("--platform and --arch must select windows x86_64 or macos arm64")
        print(package_full_suite(args.dist, args.output, version, args.platform, args.arch, repo))


if __name__ == "__main__":
    main()
