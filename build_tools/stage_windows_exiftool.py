"""Keep a complete, directly executable ExifTool beside each Windows app."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess


RELATIVE_TOOL = Path("app_common/exif_io/exiftools_win")
APPS = ("SuperViewer", "SuperBirdStamp")


def check_exiftool(directory: Path) -> str:
    executable = directory / "exiftool.exe"
    try:
        result = subprocess.run(
            [str(executable), "-ver"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ExifTool cannot start: {executable}: {exc}") from exc
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"ExifTool cannot start: {executable}: {result.stderr}")
    return result.stdout.strip()


def stage_exiftool(source: Path, dist: Path) -> None:
    source, dist = source.resolve(), dist.resolve()
    version = check_exiftool(source)
    # Validate the entire output layout before copying anything. MERGE still
    # shares Python/Qt/Torch; only this external executable gets a private tree.
    for app in APPS:
        if not (dist / app / f"{app}.exe").is_file():
            raise FileNotFoundError(f"Missing built application: {dist / app}")
    for app in APPS:
        target = dist / app / "_internal" / RELATIVE_TOOL
        shutil.copytree(source, target, dirs_exist_ok=True)
        if check_exiftool(target) != version:
            raise RuntimeError(f"ExifTool version mismatch: {target}")
        print(f"[OK] {app}: ExifTool {version}: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path)
    args = parser.parse_args()
    stage_exiftool(Path(__file__).resolve().parents[1] / RELATIVE_TOOL, args.dist)


if __name__ == "__main__":
    main()
