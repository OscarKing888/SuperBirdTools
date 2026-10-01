from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile

APPS = ("SuperViewer", "SuperBirdStamp", "SuperBirdUpdater")
INSTALLED_MANIFEST = "installed-update.json"
CONFIG_NAME = "update_config.json"
BLOCK_SIZE = 1024 * 1024


class UpdateError(Exception):
    """可以直接呈现给用户的更新错误。"""


class Cancelled(UpdateError):
    pass


def platform_id() -> str:
    return {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")


def architecture() -> str:
    name = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(name, name)


def component_names(target: str) -> tuple[str, ...]:
    return tuple(f"{app}.app" if target == "macos" else app for app in APPS)


def executable(root: Path, app: str, target: str | None = None) -> Path:
    if app not in APPS:
        raise UpdateError("未知应用")
    target = target or platform_id()
    if target == "macos":
        return root / f"{app}.app" / "Contents" / "MacOS" / app
    return root / app / (f"{app}.exe" if target == "windows" else app)


def suite_root(program: Path) -> Path:
    program = program.resolve()
    for parent in program.parents:
        if parent.name in component_names("macos"):
            return parent.parent
    return program.parent.parent


def hash_file(path: Path, cancel=None) -> dict:
    md5 = hashlib.md5(usedforsecurity=False)
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_SIZE):
            if cancel is not None and cancel.is_set():
                raise Cancelled("更新已取消")
            size += len(block)
            md5.update(block)
            sha.update(block)
    return {"size": size, "md5": md5.hexdigest(), "sha256": sha.hexdigest()}


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".update-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"无法读取 {path}: {exc}") from exc
