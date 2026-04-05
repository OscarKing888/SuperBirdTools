from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

WORKSPACE_SCHEMA_VERSION = 1
WORKSPACE_FILE_EXTENSION = ".birdstamp-workspace.json"
WORKSPACE_APP_NAME = "SuperBirdStamp"


class WorkspaceFormatError(ValueError):
    """工作区 JSON 格式错误。"""


def _normalize_path(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve(strict=False)
    except Exception:
        return None


def _workspace_dir(workspace_path: Path | str | None) -> Path | None:
    normalized = _normalize_path(workspace_path)
    if normalized is None:
        return None
    return normalized.parent


def _relative_path_text(path: Path, base_dir: Path | None) -> str | None:
    if base_dir is None:
        return None
    try:
        relative_text = os.path.relpath(str(path), str(base_dir))
    except Exception:
        return None
    relative = Path(relative_text)
    return relative.as_posix()


def serialize_workspace_path(
    value: Path | str | None,
    *,
    workspace_path: Path | str | None = None,
) -> dict[str, str] | None:
    normalized = _normalize_path(value)
    if normalized is None:
        return None

    record: dict[str, str] = {
        "absolute_path": str(normalized),
    }
    relative_text = _relative_path_text(normalized, _workspace_dir(workspace_path))
    if relative_text:
        record["relative_path"] = relative_text
    return record


def resolve_workspace_path(
    raw: Any,
    *,
    workspace_path: Path | str | None = None,
) -> Path | None:
    if isinstance(raw, (str, Path)):
        return _normalize_path(raw)
    if not isinstance(raw, dict):
        return None

    workspace_dir = _workspace_dir(workspace_path)
    relative_text = str(raw.get("relative_path") or "").strip()
    absolute_text = str(raw.get("absolute_path") or "").strip()

    relative_candidate: Path | None = None
    if relative_text and workspace_dir is not None:
        try:
            relative_candidate = (workspace_dir / Path(relative_text)).expanduser().resolve(strict=False)
        except Exception:
            relative_candidate = None

    absolute_candidate = _normalize_path(absolute_text) if absolute_text else None

    if relative_candidate is not None:
        try:
            if relative_candidate.exists():
                return relative_candidate
        except Exception:
            pass
    if absolute_candidate is not None:
        try:
            if absolute_candidate.exists():
                return absolute_candidate
        except Exception:
            pass
    if relative_candidate is not None:
        return relative_candidate
    return absolute_candidate


def read_workspace_json(path: Path | str) -> dict[str, Any]:
    workspace_path = _normalize_path(path)
    if workspace_path is None:
        raise WorkspaceFormatError("工作区路径无效。")

    try:
        text = workspace_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise WorkspaceFormatError(f"读取工作区失败: {exc}") from exc

    try:
        raw = json.loads(text)
    except Exception as exc:
        raise WorkspaceFormatError(f"工作区 JSON 解析失败: {exc}") from exc

    if not isinstance(raw, dict):
        raise WorkspaceFormatError("工作区根节点必须为对象。")

    app_name = str(raw.get("app") or "").strip()
    if app_name and app_name != WORKSPACE_APP_NAME:
        raise WorkspaceFormatError(f"该文件不是 {WORKSPACE_APP_NAME} 工作区。")

    try:
        version = int(raw.get("workspace_version") or WORKSPACE_SCHEMA_VERSION)
    except Exception as exc:
        raise WorkspaceFormatError(f"工作区版本无效: {exc}") from exc
    if version != WORKSPACE_SCHEMA_VERSION:
        raise WorkspaceFormatError(
            f"不支持的工作区版本: {version}，当前仅支持 {WORKSPACE_SCHEMA_VERSION}。"
        )
    return raw


def write_workspace_json(path: Path | str, payload: dict[str, Any]) -> Path:
    workspace_path = _normalize_path(path)
    if workspace_path is None:
        raise WorkspaceFormatError("工作区路径无效。")
    if not isinstance(payload, dict):
        raise WorkspaceFormatError("工作区内容必须为对象。")

    document = dict(payload)
    document["app"] = WORKSPACE_APP_NAME
    document["workspace_version"] = WORKSPACE_SCHEMA_VERSION
    document["saved_at"] = str(document.get("saved_at") or datetime.now().astimezone().isoformat())

    try:
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        raise WorkspaceFormatError(f"写入工作区失败: {exc}") from exc
    return workspace_path
