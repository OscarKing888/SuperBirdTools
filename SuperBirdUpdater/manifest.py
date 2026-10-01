from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import unicodedata

from .common import APPS, UpdateError, component_names, executable, hash_file, read_json

MAX_ASSET_SIZE = 2 * 1024**3 - 1
MAX_MANIFEST_BYTES = 32 * 1024**2
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
_HASH = re.compile(r"[0-9a-f]+")


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise UpdateError("清单路径无效")
    parts = value.split("/")
    if any(p in {"", ".", ".."} or p.endswith((" ", ".")) or _RESERVED.match(p)
           or any(ord(c) < 32 or c in '\\:<>"|?*' for c in p) for p in parts):
        raise UpdateError(f"不安全或跨平台不兼容的路径: {value}")
    return value


def checked_path(root: Path, value: str) -> Path:
    path = root.joinpath(*relative_path(value).split("/"))
    # 不跟随已有父目录链接，防止更新写到安装目录之外。
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise UpdateError(f"更新路径经过符号链接: {parent}")
    return path


def mutable_path(value: str) -> bool:
    p = PurePosixPath(value)
    # macOS 已签名 bundle 内的是发布默认值，用户运行时覆盖写在 MacOS/ 或用户目录。
    if p.parts[0].endswith(".app") and len(p.parts) > 2 and p.parts[1:3] in {
            ("Contents", "Resources"), ("Contents", "Frameworks")}:
        return False
    return (p.name in {"super_viewer.cfg", "tags.cfg", "SuperViewerUser.cfg", "config.yaml"}
            or "templates" in p.parts)


def excluded_path(value: str) -> bool:
    p = PurePosixPath(value)
    parts = tuple(part.casefold() for part in p.parts)
    config_cache = any(parts[i:i+2] == ("config", "cache") for i in range(len(parts) - 1))
    return (p.name in {"editor_autosave.birdstamp-workspace.json", "editor_export_state.json",
                       ".DS_Store", "last_selected_directory.txt", ".last_folder.txt", "SuperViewerUser.cfg"}
            or config_cache or p.name.startswith("._") or p.suffix == ".log"
            or any(part in {"__pycache__", "logs", ".superpicky", ".cache"} for part in p.parts))


def _integer(value, field: str, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise UpdateError(f"清单 {field} 无效")
    return value


def _hash(value, length: int) -> None:
    if not isinstance(value, str) or len(value) != length or not _HASH.fullmatch(value):
        raise UpdateError("清单哈希无效")


def validate(data: dict) -> dict:
    try:
        if type(data["schema"]) is not int or data["schema"] != 1 or data["platform"] not in {"macos", "windows", "linux"}:
            raise UpdateError("不支持的更新清单")
        if data["arch"] not in {"arm64", "x86_64", "universal2"}:
            raise UpdateError("不支持的 CPU 架构")
        _hash(data["commit"], 40)
        _hash(data["app_common_commit"], 40)
        if not isinstance(data["version"], str) or not 7 <= len(data["version"]) <= 40 or not data["commit"].startswith(data["version"]):
            raise UpdateError("短版本号与 commit 不匹配")
        _integer(data["revision"], "revision")
        assets = data["assets"]
        if not isinstance(assets, dict) or len(assets) > 990:
            raise UpdateError("更新附件数量无效")
        for name, asset in assets.items():
            if "/" in relative_path(name):
                raise UpdateError("附件名不能包含目录")
            _integer(asset["size"], "asset.size", MAX_ASSET_SIZE)
            _hash(asset["sha256"], 64)
        entries = data["files"]
        if not isinstance(entries, list) or len(entries) > 100000:
            raise UpdateError("文件列表无效")
        seen, kinds = set(), {}
        roots = component_names(data["platform"])
        for entry in entries:
            name = relative_path(entry["path"])
            if name.split("/")[0] not in roots or excluded_path(name):
                raise UpdateError(f"不允许更新此路径: {name}")
            normalized = unicodedata.normalize("NFC", name).casefold()
            if normalized in seen:
                raise UpdateError(f"文件名冲突: {name}")
            seen.add(normalized)
            kind = entry["kind"]
            kinds[name] = kind
            if kind not in {"file", "symlink", "directory"}:
                raise UpdateError("未知文件类型")
            _integer(entry["mode"], "mode", 0o777)
            if kind == "symlink":
                target = entry["target"]
                if not isinstance(target, str) or not target or "\\" in target or ":" in target or target.startswith("/"):
                    raise UpdateError(f"非法符号链接: {name}")
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                relative_path(resolved)
                if resolved.split("/")[0] not in roots:
                    raise UpdateError(f"符号链接越界: {name}")
            elif kind == "file":
                _integer(entry["size"], "size")
                _hash(entry["md5"], 32)
                _hash(entry["sha256"], 64)
                offset = _integer(entry["offset"], "offset")
                length = _integer(entry["compressed_size"], "compressed_size")
                if entry["compression"] not in {0, 8} or offset + length > assets[entry["asset"]]["size"]:
                    raise UpdateError(f"归档位置无效: {name}")
        for name in kinds:
            for parent in PurePosixPath(name).parents:
                if str(parent) != "." and kinds.get(str(parent)) != "directory":
                    raise UpdateError(f"文件父路径不是清单目录: {name}")
        if not all(kinds.get(root) == "directory" for root in roots):
            raise UpdateError("清单缺少套件组件")
        for app in APPS:
            name = executable(Path("."), app, data["platform"]).as_posix()
            if kinds.get(name) != "file":
                raise UpdateError(f"清单缺少应用入口: {name}")
        # 拒绝链接环以及通过另一条链接间接逃逸。
        links = {e["path"]: e["target"] for e in entries if e["kind"] == "symlink"}
        for name in links:
            current, visited = name, set()
            for _ in range(len(links) + 1):
                parts = current.split("/")
                prefix = next(("/".join(parts[:i]) for i in range(1, len(parts) + 1)
                               if "/".join(parts[:i]) in links), None)
                if prefix is None:
                    break
                if prefix in visited:
                    raise UpdateError(f"符号链接形成环: {name}")
                visited.add(prefix)
                current = posixpath.normpath(posixpath.join(posixpath.dirname(prefix), links[prefix],
                                                           *parts[len(prefix.split('/')):]))
                relative_path(current)
                if current.split("/")[0] not in roots:
                    raise UpdateError(f"符号链接越界: {name}")
        return data
    except (KeyError, TypeError, AttributeError) as exc:
        raise UpdateError(f"更新清单结构错误: {exc}") from exc


def load(path: Path) -> dict:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise UpdateError("更新清单过大")
    return validate(read_json(path))


def newer(current: dict, candidate: dict) -> bool:
    if (current["platform"], current["arch"]) != (candidate["platform"], candidate["arch"]):
        raise UpdateError("更新平台或架构不匹配")
    if current["commit"] == candidate["commit"]:
        return False
    if current["revision"] == candidate["revision"]:
        raise UpdateError("版本顺序相同但 commit 不同；请使用同一主线的发布版本")
    return candidate["revision"] > current["revision"]


def matches(root: Path, entry: dict, cancel=None) -> bool:
    path = checked_path(root, entry["path"])
    try:
        if entry["kind"] == "symlink":
            return path.is_symlink() and os.readlink(path) == entry["target"]
        if path.is_symlink():
            return False
        if entry["kind"] == "directory":
            return path.is_dir()
        if not path.is_file() or path.stat().st_size != entry["size"]:
            return False
        if os.name != "nt" and (path.stat().st_mode & 0o777) != entry["mode"]:
            return False
        hashes = hash_file(path, cancel)
        return hashes["md5"] == entry["md5"] and hashes["sha256"] == entry["sha256"]
    except FileNotFoundError:
        return False
