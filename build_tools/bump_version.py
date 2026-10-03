"""Prepare a SuperBirdTools release version for bump-version.sh / bump-version.bat.

This script validates the request and repository state, updates ``app_metadata.json`` and
prints a ``key=value`` plan on stdout. The entry scripts then commit, tag and push with plain
git commands. Messages go to stderr so stdout carries only the plan.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# 兼容直接执行本脚本；只依赖标准库，不需要 Qt 或第三方包。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_identity import normalize_build_number, normalize_version_prefix, version_prefix
from build_tools.set_build_version import apply_build_version, resolve_build_version

ENTRY_ENV = "SUPERBIRDTOOLS_BUMP_ENTRY"
METADATA_FILE = "app_metadata.json"
SUBMODULE = "app_common"


class BumpError(RuntimeError):
    pass


def _info(message: str) -> None:
    print(message, file=sys.stderr)


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise BumpError(f"git {args[0]} failed (exit {result.returncode}):\n{detail}")
    return result


def _git_out(root: Path, *args: str) -> str:
    return _git(root, *args).stdout.strip()


def _version_key(version: str) -> tuple[int, ...]:
    # hash 无大小顺序；同一主次版本允许从新提交继续发布。
    return tuple(int(part) for part in version_prefix(version).split("."))


def _read_metadata(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise BumpError(f"Invalid {METADATA_FILE}.")
    return data


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bump-version",
        description=(
            "Update app_metadata.json, then (from bump-version.sh / bump-version.bat) commit it, "
            "create the annotated tag vX.Y.HASH from the final HEAD, push app_common main, and push main with the tag."
        ),
    )
    parser.add_argument("version", help="MAJOR.MINOR, optionally prefixed with v; the 8-character commit hash is automatic.")
    parser.add_argument("--finalize", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--build-number",
        help="Positive integer macOS CFBundleVersion; defaults to the current value.",
    )
    parser.add_argument("--no-tag", action="store_true", help="Commit and push main without a tag.")
    parser.add_argument("--no-push", action="store_true", help="Commit and tag locally only.")
    parser.add_argument(
        "--no-commit", action="store_true", help="Only update app_metadata.json (no commit, tag or push)."
    )
    args = parser.parse_args(argv)
    try:
        args.version = normalize_version_prefix(args.version)
        if args.build_number is not None:
            args.build_number = normalize_build_number(args.build_number)
    except ValueError as exc:
        parser.error(str(exc))
    args.commit = not args.no_commit
    args.tag = args.commit and not args.no_tag
    args.push = args.commit and not args.no_push
    return args


def prepare(argv: list[str], root: Path) -> dict[str, object]:
    args = _parse_args(argv)
    version = args.version
    metadata_path = root / METADATA_FILE
    plan = {
        "version": version,
        "commit": False,
        "create_tag": False,
        "push": args.push,
        "push_tag": args.push and args.tag,
        "submodule": "",
    }

    if args.commit:
        if os.environ.get(ENTRY_ENV) != "1":
            raise BumpError(
                "Run ./bump-version.sh or bump-version.bat to commit, tag and push a version; "
                "running this script directly supports --no-commit only. No files were changed."
            )
        try:
            toplevel = Path(_git_out(root, "rev-parse", "--show-toplevel")).resolve()
        except (BumpError, OSError) as exc:
            raise BumpError(f"Committing a version requires the SuperBirdTools Git repository: {exc}") from exc
        if os.path.normcase(str(toplevel)) != os.path.normcase(str(root.resolve())):
            raise BumpError("The script must belong to this repository. No files were changed.")
        _git(root, "ls-files", "--error-unmatch", "--", METADATA_FILE)
        if _git_out(root, "status", "--porcelain", "--", METADATA_FILE):
            raise BumpError(f"{METADATA_FILE} already has uncommitted changes; commit or revert them first. No files were changed.")
        if args.push:
            # 发布推送到 origin/main，避免误推功能分支。
            if _git_out(root, "branch", "--show-current") != "main":
                raise BumpError("Pushing a release requires this checkout to be on main; use --no-push to commit and tag locally. No files were changed.")
            plan["submodule"] = _check_submodule(root)
        committed = _read_metadata(_git_out(root, "show", f"HEAD:{METADATA_FILE}"))
        if _version_key(version) < _version_key(str(committed.get("version", ""))):
            raise BumpError(f"The new version must not be lower than the committed version {committed.get('version')}. No files were changed.")

    current = _read_metadata(metadata_path.read_text(encoding="utf-8"))
    build_number = args.build_number or normalize_build_number(current.get("build_number", "1"))
    # 在任何写入之前验证 Git 可读；--no-commit 也不能伪造 hash。
    resolved = resolve_build_version(root, version)
    tag = f"v{resolved}"
    if args.finalize:
        if not args.commit or current["version"] != version:
            raise BumpError("Finalize requires the committed version prefix. No files were changed.")
        plan["version"] = resolved
        plan["create_tag"] = args.tag
        if args.tag and _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0:
            if (_git_out(root, "rev-parse", f"{tag}^{{commit}}") != _git_out(root, "rev-parse", "HEAD")
                    or _git_out(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag"):
                raise BumpError(f"Version tag {tag} already exists for a different commit or is not annotated; it will not be overwritten.")
            plan["create_tag"] = False
            _info(f"Reusing existing annotated tag {tag}.")
        return plan
    apply_build_version(root, version, build_number=build_number)
    _info(f"Updated {METADATA_FILE}: version {current.get('version')} -> {version}, build_number {build_number}.")
    if not args.commit:
        _info("Skipped commit, tag and push (--no-commit).")
        return plan
    plan["commit"] = bool(_git_out(root, "diff", "--name-only", "--", METADATA_FILE))
    if not plan["commit"]:
        _info("No version changes to commit.")
    plan["create_tag"] = args.tag
    return plan


def _check_submodule(root: Path) -> str:
    """CI checks out the app_common gitlink, so it must be on app_common main before pushing."""

    entry = _git_out(root, "ls-tree", "HEAD", SUBMODULE)
    if not entry:
        return ""
    gitlink = entry.split()[2]
    submodule = root / SUBMODULE
    if not (submodule / ".git").exists():
        raise BumpError(f"{SUBMODULE} is not checked out; run git submodule update --init {SUBMODULE}. No files were changed.")
    if _git(submodule, "merge-base", "--is-ancestor", gitlink, "refs/heads/main", check=False).returncode != 0:
        raise BumpError(
            f"The {SUBMODULE} commit {gitlink[:12]} recorded by main is not on {SUBMODULE} main; "
            f"commit or merge it there first. No files were changed."
        )
    return SUBMODULE


def _format_plan(plan: dict[str, object]) -> str:
    return "".join(
        f"{key}={int(value) if isinstance(value, bool) else value}\n" for key, value in plan.items()
    )


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        plan = prepare(sys.argv[1:] if argv is None else argv, root)
    except (BumpError, OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    sys.stdout.write(_format_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
