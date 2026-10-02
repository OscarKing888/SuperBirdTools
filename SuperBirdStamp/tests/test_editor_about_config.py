from __future__ import annotations

import json
from pathlib import Path

import birdstamp
from birdstamp.gui import editor
from app_common.about_dialog import config


def test_birdstamp_about_cfg_is_valid_and_applied(monkeypatch, tmp_path) -> None:
    repo_cfg = Path(__file__).resolve().parents[1] / "about.cfg"
    raw = json.loads(repo_cfg.read_text(encoding="utf-8"))
    assert raw["about"]["作者"] == "追鸟奇遇记(osk.ch)"

    monkeypatch.setattr(editor, "_bundled_about_cfg_path", lambda: repo_cfg)
    monkeypatch.setattr(
        editor,
        "_user_about_cfg_path",
        lambda: tmp_path / "missing-about.cfg",
    )

    info = editor._load_birdstamp_about_info()

    assert info["app_name"] == editor._BIRDSTAMP_DEFAULT_APP_NAME
    assert info["version"] == birdstamp.__version__
    assert info["作者"] == "追鸟奇遇记(osk.ch)"
    assert info["开源地址"] == "https://github.com/OscarKing888/BirdStamp.git"
    assert info["我的小红书"] == "https://xhslink.com/m/A2cowPsYj8P"


def test_invalid_birdstamp_override_logs_parse_location(
    monkeypatch,
    tmp_path,
) -> None:
    cfg_path = tmp_path / "about.cfg"
    cfg_path.write_text('{"about": {"作者": "broken"}', encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(
        config._log,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )

    monkeypatch.setattr(editor, "_user_about_cfg_path", lambda: cfg_path)
    assert editor._load_birdstamp_about_info()["version"] == birdstamp.__version__
    assert warnings
    assert str(cfg_path) in warnings[0]
    assert "line 1 column" in warnings[0]


def test_user_text_override_keeps_bundled_images_and_central_identity(monkeypatch, tmp_path):
    path = tmp_path / 'about.cfg'
    path.write_text(json.dumps({'about': {'作者': '中文作者', 'version': 'old', 'app_name': 'old'}}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(editor, '_user_about_cfg_path', lambda: path)
    info = editor._load_birdstamp_about_info()
    assert info['作者'] == '中文作者'
    assert info['version'] == birdstamp.APP_INFO.version
    assert info['app_name'] == birdstamp.APP_INFO.app_name
    app_title = birdstamp.APP_INFO.window_title(info)
    assert editor._build_birdstamp_main_window_title(info) == f'Untitled* - {app_title}'
    workspace = tmp_path / '工作区' / '白鹭.birdstamp-workspace.json'
    assert editor._build_birdstamp_main_window_title(info, workspace) == f'{workspace} - {app_title}'
    images = editor._load_birdstamp_about_images()
    assert len(images) == 2
    assert all(Path(image['path']).parent == Path(editor._bundled_about_cfg_path()).parent / 'images' for image in images)
    path.write_text('{"images": []}', encoding='utf-8')
    assert editor._load_birdstamp_about_images() == []
