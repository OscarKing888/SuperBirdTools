from __future__ import annotations

import json
from pathlib import Path

import birdstamp
from birdstamp.gui import editor


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
        editor._log,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )

    assert editor._load_about_override_info(cfg_path) == {}
    assert warnings
    assert str(cfg_path) in warnings[0]
    assert "line 1 column" in warnings[0]
