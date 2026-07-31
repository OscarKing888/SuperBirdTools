from __future__ import annotations

import importlib
import json
from pathlib import Path

from SuperViewer.superviewer import DEFAULT_APP_NAME, __version__


main_module = importlib.import_module("SuperViewer.main")


def test_superviewer_about_cfg_is_independent_and_applied(monkeypatch) -> None:
    app_root = Path(__file__).resolve().parents[1]
    about_path = app_root / "about.cfg"
    settings_path = app_root / "super_viewer.cfg"

    raw_about = json.loads(about_path.read_text(encoding="utf-8"))
    raw_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "about" not in raw_settings
    assert raw_about["about"]["作者"] == "追鸟奇遇记(osk.ch)"

    monkeypatch.setattr(
        main_module,
        "_get_about_config_resource_path",
        lambda: str(about_path),
    )
    info = main_module._load_superviewer_about_info()

    assert info["app_name"] == DEFAULT_APP_NAME
    assert info["version"] == __version__
    assert info["开源地址"] == "https://github.com/OscarKing888/SuperBirdTools.git"
    assert info["我的小红书"] == "https://xhslink.com/m/A2cowPsYj8P"


def test_superviewer_about_images_resolve_from_independent_cfg() -> None:
    app_root = Path(__file__).resolve().parents[1]
    images = main_module.load_about_images(str(app_root / "about.cfg"))

    assert [Path(item["path"]).name for item in images] == [
        "download.png",
        "osk.jpg",
    ]
