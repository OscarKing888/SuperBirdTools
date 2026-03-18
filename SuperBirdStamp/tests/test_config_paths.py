from __future__ import annotations

from pathlib import Path

from birdstamp import config


def _write_json(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_resolve_bundled_path_prefers_existing_internal_resource_over_empty_meipass(
    tmp_path,
    monkeypatch,
) -> None:
    meipass_dir = tmp_path / "_MEI123456"
    meipass_dir.mkdir(parents=True, exist_ok=True)

    executable_dir = tmp_path / "dist" / "SuperBirdStamp"
    internal_dir = executable_dir / "_internal"
    default_template = internal_dir / "config" / "templates" / "default.json"
    _write_json(default_template)
    _write_json(internal_dir / "config" / "editor_options.json")

    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "_MEIPASS", str(meipass_dir), raising=False)
    monkeypatch.setattr(config.sys, "executable", str(executable_dir / "SuperBirdStamp.exe"), raising=False)
    monkeypatch.setattr(config.sys, "platform", "win32", raising=False)

    assert config.get_app_resource_dir() == internal_dir.resolve(strict=False)
    assert config.resolve_bundled_path("config", "templates", "default.json") == default_template.resolve(
        strict=False
    )


def test_resolve_bundled_path_keeps_meipass_when_resource_exists(tmp_path, monkeypatch) -> None:
    meipass_dir = tmp_path / "_MEI654321"
    default_template = meipass_dir / "config" / "templates" / "default.json"
    _write_json(default_template)
    _write_json(meipass_dir / "config" / "editor_options.json")

    executable_dir = tmp_path / "dist" / "SuperBirdStamp"
    executable_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "_MEIPASS", str(meipass_dir), raising=False)
    monkeypatch.setattr(config.sys, "executable", str(executable_dir / "SuperBirdStamp.exe"), raising=False)
    monkeypatch.setattr(config.sys, "platform", "win32", raising=False)

    assert config.get_app_resource_dir() == meipass_dir.resolve(strict=False)
    assert config.resolve_bundled_path("config", "templates", "default.json") == default_template.resolve(
        strict=False
    )
