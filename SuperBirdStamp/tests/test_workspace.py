from __future__ import annotations

from pathlib import Path

from birdstamp.workspace import (
    WORKSPACE_APP_NAME,
    WORKSPACE_SCHEMA_VERSION,
    read_workspace_json,
    resolve_workspace_path,
    serialize_workspace_path,
    write_workspace_json,
)


def test_workspace_path_roundtrip_prefers_relative_path(tmp_path: Path) -> None:
    workspace_path = tmp_path / "项目" / "session.birdstamp-workspace.json"
    photo_path = tmp_path / "项目" / "素材" / "黑脸琵鹭.jpg"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(b"test")

    record = serialize_workspace_path(photo_path, workspace_path=workspace_path)

    assert record is not None
    assert record["relative_path"] == "素材/黑脸琵鹭.jpg"
    assert resolve_workspace_path(record, workspace_path=workspace_path) == photo_path.resolve(strict=False)


def test_workspace_path_resolution_falls_back_to_absolute_path(tmp_path: Path) -> None:
    workspace_path = tmp_path / "工作区" / "session.birdstamp-workspace.json"
    missing_relative = {
        "relative_path": "missing/素材.jpg",
    }
    absolute_photo = tmp_path / "绝对路径" / "素材.jpg"
    absolute_photo.parent.mkdir(parents=True, exist_ok=True)
    absolute_photo.write_bytes(b"image")
    missing_relative["absolute_path"] = str(absolute_photo.resolve(strict=False))

    resolved = resolve_workspace_path(missing_relative, workspace_path=workspace_path)

    assert resolved == absolute_photo.resolve(strict=False)


def test_workspace_plain_relative_path_resolves_from_workspace_dir(tmp_path: Path) -> None:
    workspace_path = tmp_path / "工作区" / "session.birdstamp-workspace.json"
    photo_path = tmp_path / "工作区" / "素材" / "白鹭.jpg"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(b"image")

    resolved = resolve_workspace_path("素材/白鹭.jpg", workspace_path=workspace_path)

    assert resolved == photo_path.resolve(strict=False)


def test_workspace_json_write_and_read_roundtrip(tmp_path: Path) -> None:
    workspace_path = tmp_path / "保存" / "示例.birdstamp-workspace.json"
    payload = {
        "report_databases": [],
        "photos": [],
        "editor_state": {
            "current_render_settings": {
                "template_name": "default",
            }
        },
    }

    written_path = write_workspace_json(workspace_path, payload)
    loaded = read_workspace_json(written_path)

    assert written_path == workspace_path.resolve(strict=False)
    assert loaded["app"] == WORKSPACE_APP_NAME
    assert loaded["workspace_version"] == WORKSPACE_SCHEMA_VERSION
    assert loaded["editor_state"]["current_render_settings"]["template_name"] == "default"
    assert loaded["saved_at"]
    assert workspace_path.read_text(encoding="utf-8").endswith("\n")


def test_workspace_json_write_refreshes_saved_at(tmp_path: Path) -> None:
    workspace_path = tmp_path / "保存" / "示例.birdstamp-workspace.json"
    stale_saved_at = "2000-01-01T00:00:00+00:00"

    write_workspace_json(workspace_path, {"saved_at": stale_saved_at, "photos": []})
    loaded = read_workspace_json(workspace_path)

    assert loaded["saved_at"] != stale_saved_at
