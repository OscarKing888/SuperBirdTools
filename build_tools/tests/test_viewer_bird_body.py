from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from build_tools import viewer_bird_body as assets
from build_tools import prepare_bird_body_model as prepare


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "MIN_MODEL_BYTES", 3)
    path = tmp_path / "SuperBirdStamp" / "models" / assets.MODEL_NAME
    path.parent.mkdir(parents=True)
    path.write_bytes(b"yolo")
    return path


def test_collector_reuses_stamp_model_and_precollected_ultralytics(tmp_path, monkeypatch, model):
    monkeypatch.setattr(assets.importlib.util, "find_spec", lambda module: SimpleNamespace())
    datas, binaries, imports = assets.collect_viewer_bird_body(
        tmp_path, ultralytics_assets=([("config", "ultralytics/cfg")], [("native", ".")], ["ultralytics"]),
    )
    assert (str(model), "models") in datas
    assert ("config", "ultralytics/cfg") in datas
    assert binaries == [("native", ".")]
    assert {"torch", "torchvision", "cv2", "ultralytics"} <= set(imports)


def _sparse_catalog(tmp_path, skip=()):
    """Every catalog model as a sparse file of its exact size (no disk space, no download)."""
    from bird_sharpness.model_catalog import DETECTORS, SAM_MODELS

    directory = tmp_path / "SuperViewer" / "models"
    directory.mkdir(parents=True, exist_ok=True)
    for model in (*DETECTORS, *SAM_MODELS):
        if model.name not in skip:
            with open(directory / model.name, "wb") as fh:
                fh.truncate(model.size_bytes)
    return directory


def test_all_catalog_models_are_bundled_only_on_request(tmp_path, monkeypatch, model):
    monkeypatch.setattr(assets.importlib.util, "find_spec", lambda module: SimpleNamespace())
    monkeypatch.delenv(assets.BUNDLE_MODELS_ENV, raising=False)
    directory = _sparse_catalog(tmp_path)
    ultra = ([], [], [])
    datas, _b, _i = assets.collect_viewer_bird_body(tmp_path, ultralytics_assets=ultra)
    assert datas == [(str(directory / "yolo11n.pt"), "models")]  # release builds: yolo11n.pt only (Viewer copy first)
    monkeypatch.setenv(assets.BUNDLE_MODELS_ENV, "all")
    datas, _b, _i = assets.collect_viewer_bird_body(tmp_path, ultralytics_assets=ultra)
    names = [Path(src).name for src, dest in datas]
    assert len(names) == 43 and all(dest == "models" for _src, dest in datas)
    assert names.count("yolo11n.pt") == 1 and (str(directory / "sam2.1_l.pt"), "models") in datas


def test_bundling_all_models_fails_before_analysis_when_one_is_missing(tmp_path, monkeypatch):
    _sparse_catalog(tmp_path, skip=("sam2.1_b.pt",))
    (tmp_path / "SuperViewer" / "models" / "yolo26x.pt").write_bytes(b"truncated")
    with pytest.raises(FileNotFoundError, match="sam2.1_b.pt.*yolo26x.pt|yolo26x.pt.*sam2.1_b.pt"):
        assets.bundled_catalog_models(tmp_path, "all")
    assert assets.bundled_catalog_models(tmp_path, "") == []


def test_collector_fails_before_analysis_for_missing_model_or_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr(assets.importlib.util, "find_spec", lambda module: SimpleNamespace())
    with pytest.raises(FileNotFoundError, match="yolo11n"):
        assets.collect_viewer_bird_body(tmp_path)
    monkeypatch.setattr(assets.importlib.util, "find_spec", lambda module: None)
    with pytest.raises(RuntimeError, match="torch"):
        assets.collect_viewer_bird_body(tmp_path)


def test_existing_birdstamp_asset_never_downloads_or_creates_viewer_copy(tmp_path, monkeypatch, model):
    from SuperBirdStamp.scripts_dev import install_yolo11n
    monkeypatch.setattr(install_yolo11n, "download_yolo11n", lambda **kwargs: pytest.fail("network used"))
    assert prepare.prepare_model(tmp_path) == model
    assert not (tmp_path / "SuperViewer").exists()


def test_missing_asset_uses_existing_official_downloader(tmp_path, monkeypatch):
    from SuperBirdStamp.scripts_dev import install_yolo11n
    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        return kwargs["target_path"]

    monkeypatch.setattr(install_yolo11n, "download_yolo11n", download)
    target = tmp_path / "SuperViewer" / "models" / assets.MODEL_NAME
    assert prepare.prepare_model(tmp_path) == target
    assert calls == [{"target_path": target, "force": False}]


def test_truncated_private_model_is_replaced(tmp_path, monkeypatch):
    from SuperBirdStamp.scripts_dev import install_yolo11n
    target = tmp_path / "SuperViewer" / "models" / assets.MODEL_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"broken")
    calls = []
    monkeypatch.setattr(install_yolo11n, "download_yolo11n", lambda **kwargs: calls.append(kwargs) or target)
    assert prepare.prepare_model(tmp_path) == target
    assert calls == [{"target_path": target, "force": True}]


def test_prepare_dry_run_does_not_create_files(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(prepare, "prepare_model", lambda: pytest.fail("download called"))
    assert prepare.main(["--dry-run"]) == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("spec", ["SuperViewer/SuperViewer_mac.spec", "SuperViewer/SuperViewer_win.spec",
                                  "build_all_win_merged.spec"])
def test_every_viewer_spec_enforces_bird_resources(spec):
    source = (Path(__file__).resolve().parents[2] / spec).read_text(encoding="utf-8")
    assert "collect_viewer_bird_body(" in source
    assert "bird_datas" in source and "bird_binaries" in source and "bird_hiddenimports" in source
    assert 'collect_submodules("superviewer")' in source
