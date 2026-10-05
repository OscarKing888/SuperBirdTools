"""Workspace model prefetch (build_tools/download_models.py): selection, verification, retries."""
from __future__ import annotations

import pytest

from bird_sharpness import model_catalog
from build_tools import download_models as dm


def test_names_resolve_to_catalog_models() -> None:
    assert len(dm.resolve_names([])) == 43  # default: everything
    assert [m.name for m in dm.resolve_names(["yolo11x-seg", "sam2.1_b.pt"])] == ["yolo11x-seg.pt", "sam2.1_b.pt"]
    with pytest.raises(ValueError, match="nosuch.pt"):
        dm.resolve_names(["nosuch"])
    assert dm.workspace_model_dir().parts[-2:] == ("SuperViewer", "models")


def test_fetch_skips_verified_files_and_retries_failures(monkeypatch, tmp_path) -> None:
    model = model_catalog.catalog_model("yolo11n.pt")
    monkeypatch.setattr(model_catalog, "verify", lambda path, name=None: True)
    assert dm.fetch(model, tmp_path, download=lambda *a, **k: pytest.fail("verified: no download")) == "ok"
    monkeypatch.setattr(model_catalog, "verify", lambda path, name=None: False)
    attempts, slept = [], []

    def flaky(name, *, directory, progress):
        attempts.append(name)
        if len(attempts) < 3:
            raise IOError("cut")

    assert dm.fetch(model, tmp_path, download=flaky, sleep=slept.append) == "downloaded"
    assert len(attempts) == 3 and slept == [1, 2]
    with pytest.raises(RuntimeError, match="yolo11n.pt"):
        dm.fetch(model, tmp_path, download=lambda *a, **k: (_ for _ in ()).throw(IOError("down")), sleep=lambda s: None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("tests must not download"))


def test_dry_run_check_only_and_download(monkeypatch, tmp_path, capsys) -> None:
    present = {"yolo11n.pt"}
    monkeypatch.setattr(model_catalog, "verify", lambda path, name=None: path.name in present)
    assert dm.main(["yolo11n.pt", "yolo11s.pt", "--dest", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "已就绪 1 个，需下载 1 个" in out and "需下载 yolo11s.pt" in out
    assert dm.main(["yolo11n.pt", "yolo11s.pt", "--dest", str(tmp_path), "--check-only"]) == 1
    assert dm.main(["yolo11n.pt", "--dest", str(tmp_path), "--check-only"]) == 0
    fetched = []

    def fake_download(name, *, directory, progress):
        fetched.append((name, directory))
        present.add(name)

    monkeypatch.setattr(model_catalog, "download", fake_download)
    assert dm.main(["yolo11n.pt", "yolo11s.pt", "--dest", str(tmp_path)]) == 0
    assert fetched == [("yolo11s.pt", tmp_path.resolve())]  # the verified one is skipped
    assert "完成：2 个模型均已校验" in capsys.readouterr().out
