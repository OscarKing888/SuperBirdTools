from __future__ import annotations

from pathlib import Path

import pytest

from birdstamp import config


def _write_json(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize(
    ("system", "env", "relative_root"),
    [
        ("Darwin", {}, "home/Library/Application Support"),
        ("Windows", {"APPDATA": "roaming", "LOCALAPPDATA": "local"}, "roaming"),
        ("Windows", {"LOCALAPPDATA": "local"}, "local"),
        ("Windows", {}, "home/AppData/Roaming"),
        ("Linux", {"XDG_CONFIG_HOME": "xdg"}, "xdg"),
        ("Linux", {}, "home/.config"),
    ],
)
def test_user_config_location_is_independent_of_build(
    tmp_path, monkeypatch, frozen, system, env, relative_root,
):
    monkeypatch.setattr(config.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(config.platform, "system", lambda: system)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(config, "get_app_dir", lambda: tmp_path / "application")
    for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(tmp_path / value))

    expected = tmp_path / relative_root / "BirdStamp"
    assert config.get_user_data_dir() == expected
    assert config.get_config_path() == expected / "Config" / "config.yaml"


def test_source_and_frozen_share_existing_config_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    app_dir = tmp_path / "source"
    monkeypatch.setattr(config, "get_app_dir", lambda: app_dir)
    old_config = app_dir / "Config" / "config.yaml"
    old_config.parent.mkdir(parents=True)
    old_config.write_text("template: 旧源码模板\n", encoding="utf-8")
    target = tmp_path / "home/Library/Application Support/BirdStamp/Config/config.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("template: 我的用户模板\n", encoding="utf-8")
    original = target.read_bytes()

    for frozen in (False, True):
        monkeypatch.setattr(config.sys, "frozen", frozen, raising=False)
        assert config.load_config()["template"] == "我的用户模板"
        assert config.write_default_config() == target
        assert target.read_bytes() == original
    assert old_config.read_text(encoding="utf-8") == "template: 旧源码模板\n"


def test_source_seeds_templates_from_resources_without_overwriting_user_files(tmp_path, monkeypatch):
    from birdstamp.gui import editor_template

    monkeypatch.setattr(config.sys, "frozen", False, raising=False)
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    app_dir = tmp_path / "source"
    monkeypatch.setattr(config, "get_app_dir", lambda: app_dir)
    seeds = app_dir / "config" / "templates"
    _write_json(seeds / "default.json")
    _write_json(seeds / "中文模板.json")
    target = editor_template.template_directory()
    target.mkdir(parents=True)
    existing = target / "default.json"
    existing.write_text('{"name": "我的模板"}', encoding="utf-8")
    original = existing.read_bytes()

    assert target == tmp_path / "user" / "Config" / "templates"
    assert editor_template._copy_missing_seed_templates(target) == 1
    assert (target / "中文模板.json").read_bytes() == (seeds / "中文模板.json").read_bytes()
    assert existing.read_bytes() == original
    assert editor_template._copy_missing_seed_templates(target) == 0


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
