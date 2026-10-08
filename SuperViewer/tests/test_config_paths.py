import json
from pathlib import Path

import pytest

from app_common import superviewer_user_options as options
from SuperViewer.superviewer import paths_settings as settings


@pytest.mark.parametrize("frozen", [False, True])
def test_settings_share_user_options_directory(tmp_path, monkeypatch, frozen):
    monkeypatch.setattr(settings.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(options, "get_user_state_dir", lambda: str(tmp_path / "user"))
    monkeypatch.setattr(settings, "get_user_state_dir", options.get_user_state_dir)
    assert Path(settings._get_config_path()).parent == Path(options.get_user_options_path()).parent
    assert Path(settings._get_config_path()) == tmp_path / "user/Config/super_viewer.cfg"


def test_settings_save_to_user_directory_and_reuse_across_builds(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    legacy = app / settings.CONFIG_FILENAME
    legacy.write_text('{"preview_auto_focus_center": true, "label": "中文配置"}', encoding="utf-8")
    original = legacy.read_bytes()
    monkeypatch.setattr(settings, "_get_app_dir", lambda: str(app))
    monkeypatch.setattr(settings, "_get_user_state_dir", lambda: str(tmp_path / "user"))
    assert settings.load_auto_focus_center_from_settings()
    settings.save_auto_focus_center_to_settings(False)
    target = tmp_path / "user/Config/super_viewer.cfg"
    assert json.loads(target.read_text(encoding="utf-8"))["label"] == "中文配置"
    for frozen in (False, True):
        monkeypatch.setattr(settings.sys, "frozen", frozen, raising=False)
        assert not settings.load_auto_focus_center_from_settings()
    assert legacy.read_bytes() == original


def test_packaged_settings_still_read_bundled_defaults(tmp_path, monkeypatch):
    resource_dir = tmp_path / "bundle"
    resource_dir.mkdir()
    (resource_dir / settings.CONFIG_FILENAME).write_text('{"label": "内置配置"}', encoding="utf-8")
    monkeypatch.setattr(settings.sys, "frozen", True, raising=False)
    monkeypatch.setattr(settings.sys, "_MEIPASS", str(resource_dir), raising=False)
    monkeypatch.setattr(settings, "_get_app_dir", lambda: str(tmp_path / "app"))
    monkeypatch.setattr(settings, "_get_user_state_dir", lambda: str(tmp_path / "user"))
    assert settings._load_settings() == {"label": "内置配置"}


def test_directory_scope_defaults_on_and_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "_get_app_dir", lambda: str(tmp_path / "app"))
    monkeypatch.setattr(settings, "_get_user_state_dir", lambda: str(tmp_path / "user"))
    assert settings.load_include_subdirectories_from_settings() is True
    settings._save_settings({"label": "中文配置"})
    for enabled in (False, True):
        settings.save_include_subdirectories_to_settings(enabled)
        assert settings.load_include_subdirectories_from_settings() is enabled
        assert settings._load_settings()["label"] == "中文配置"
