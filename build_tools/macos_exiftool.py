"""Collect and verify the self-contained ExifTool runtime in macOS bundles."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


RELATIVE_TOOL = Path("app_common/exif_io/exiftools_mac")
SYSTEM_PERL = "/usr/bin/perl"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _runtime_files(runtime: Path) -> list[Path]:
    """Reject links to the developer machine, including a linked library root."""
    files = []
    for path in sorted(runtime.rglob("*")):
        if not path.resolve().is_relative_to(runtime):
            raise RuntimeError(f"ExifTool runtime links outside its directory: {path} -> {path.resolve()}")
        if path.is_symlink() and not path.exists():
            raise RuntimeError(f"ExifTool runtime contains a broken link: {path}")
        if path.is_file():
            files.append(path)
    return files


def clean_environment() -> dict[str, str]:
    """Prevent developer-installed Perl libraries/tools from hiding missing files."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("PERL") and key not in {
            "EXIFTOOL_EXE", "EXIFTOOL_PATH", "PYTHONPATH", "PYTHONHOME",
        }
    }
    env["PATH"] = SYSTEM_PATH
    return env


def _version(path: Path, variable: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\$" + variable + r"\s*=\s*['\"]([0-9]+(?:\.[0-9]+)+)['\"]", text)
    if match is None:
        raise RuntimeError(f"ExifTool version not found in {path}")
    return match.group(1)


def _run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> str:
    try:
        result = subprocess.run(
            command, cwd=cwd, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ExifTool validation could not run {command[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"ExifTool validation failed (exit {result.returncode}): {detail}")
    return result.stdout.strip()


def check_exiftool(runtime: Path) -> str:
    """Check only this runtime, never a resolver's Homebrew/system fallback."""
    runtime = Path(runtime).resolve()
    executable = runtime / "exiftool"
    library = runtime / "lib/Image/ExifTool.pm"
    for required in (executable, library):
        if not required.is_file():
            raise RuntimeError(f"Incomplete macOS ExifTool runtime: missing {required}")
    _runtime_files(runtime)
    # Finder launches the bundled script directly; a successful `perl script`
    # check alone would incorrectly accept a script without execute permission.
    if not os.access(executable, os.X_OK):
        raise RuntimeError(f"macOS ExifTool entry is not executable: {executable}")
    version = _version(executable, "version")
    library_version = _version(library, "VERSION")
    if library_version != version:
        raise RuntimeError(
            f"ExifTool version mismatch in {runtime}: entry {version}, library {library_version}"
        )
    # -config must precede all other options; an empty value disables ~/.ExifTool_config.
    output = _run(
        [SYSTEM_PERL, str(executable), "-config", "", "-ver"],
        cwd=runtime, env=clean_environment(), timeout=30,
    )
    if output != version:
        raise RuntimeError(f"Unexpected ExifTool version from {executable}: {output!r} (expected {version})")
    return version


def collect_macos_exiftool(repo_root: Path) -> list[tuple[str, str]]:
    """Fail before Analysis if incomplete, then preserve the entire runtime tree."""
    source = (Path(repo_root) / RELATIVE_TOOL).resolve()
    check_exiftool(source)
    return [
        (str(path), (RELATIVE_TOOL / path.parent.relative_to(source)).as_posix())
        for path in _runtime_files(source)
    ]


def check_app(app: Path) -> str:
    """Validate the emitted bundle and its real metadata/export code headlessly."""
    app = Path(app).resolve()
    runtime = app / "Contents/Resources" / RELATIVE_TOOL
    if not runtime.resolve().is_relative_to(app):
        raise RuntimeError(f"ExifTool runtime links outside the app bundle: {runtime}")
    version = check_exiftool(runtime)
    executable = app / "Contents/MacOS" / app.stem
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"Packaged app executable missing or not executable: {executable}")
    env = clean_environment()
    env["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory(prefix="superbird-exiftool-check-") as temporary:
        # Isolate HOME and cwd as well as PATH; diagnostics must not use the user's
        # workspace, runtime settings, metadata config or shell-installed tools.
        env["HOME"] = temporary
        report_path = Path(temporary) / "exiftool-report.json"
        _run(
            [str(executable), "--check-exiftool", "--output", str(report_path)],
            cwd=Path(temporary), env=env, timeout=90,
        )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Packaged ExifTool diagnostic did not write a valid JSON report: {exc}") from exc
        if not isinstance(report, dict) or report.get("status") != "ok":
            raise RuntimeError(f"Packaged ExifTool diagnostic failed: {report}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--runtime", type=Path, help="Check one source or bundled ExifTool directory")
    group.add_argument("--app", type=Path, help="Check the emitted .app and its metadata/export diagnostics")
    args = parser.parse_args(argv)
    try:
        version = check_app(args.app) if args.app else check_exiftool(args.runtime)
    except RuntimeError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(f"[OK] Bundled ExifTool {version}: {args.app or args.runtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
