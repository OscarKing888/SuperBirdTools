from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from build_tools import download_denoise_model as downloader
from build_tools import viewer_denoise
from image_denoise import models


@pytest.fixture
def payload(monkeypatch):
    data = b"a pinned NAFNet checkpoint fixture"
    monkeypatch.setattr(models, "MODEL_SIZE", len(data))
    monkeypatch.setattr(downloader, "MODEL_SIZE", len(data))
    monkeypatch.setattr(models, "MODEL_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(downloader.time, "sleep", lambda seconds: None)
    return data


def test_verified_existing_model_never_opens_network(tmp_path, payload):
    target = tmp_path / "照片模型" / models.MODEL_NAME
    target.parent.mkdir()
    target.write_bytes(payload)
    def forbidden(url, offset, end):
        raise AssertionError("cached model must not use network")
    assert downloader.download_model(target, opener=forbidden) == target


def test_download_checks_bytes_before_atomic_publish(tmp_path, payload):
    target = tmp_path / "中文 有空格" / models.MODEL_NAME
    urls = []
    def opener(url, offset, end):
        urls.append(url)
        return io.BytesIO(payload[offset:end + 1])
    assert downloader.download_model(target, opener=opener) == target
    assert target.read_bytes() == payload
    assert urls == [models.MODEL_URL]
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("bad_payload", [b"truncated", b"x" * 33])
def test_failed_replacement_preserves_previous_model_and_cleans_parts(tmp_path, payload, bad_payload):
    target = tmp_path / models.MODEL_NAME
    target.write_bytes(payload)
    with pytest.raises(models.DenoiseModelError):
        downloader.download_model(target, force=True, opener=lambda url, offset, end: io.BytesIO(bad_payload[offset:end + 1]))
    assert target.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [target]


def test_network_failure_retries_without_publishing(tmp_path, payload):
    target = tmp_path / models.MODEL_NAME
    calls = []
    def opener(url, offset, end):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("connection reset")
        return io.BytesIO(payload[offset:end + 1])
    downloader.download_model(target, opener=opener)
    assert len(calls) == 3
    assert target.read_bytes() == payload


def test_truncated_response_resumes_from_received_byte_count(tmp_path, payload):
    target = tmp_path / models.MODEL_NAME
    offsets = []
    def opener(url, offset, end):
        offsets.append(offset)
        return io.BytesIO(payload[offset:min(offset + 7, end + 1)])
    downloader.download_model(target, opener=opener)
    assert offsets == list(range(0, len(payload), 7))
    assert target.read_bytes() == payload


@pytest.mark.parametrize("status,content_range,offset,accepted", [
    (206, "bytes 7-13/34", 7, True),
    (206, "bytes 0-6/34", 7, False),
    (200, None, 7, False),
    (200, None, 0, True),
])
def test_range_response_must_match_requested_offset(monkeypatch, payload, status, content_range, offset, accepted):
    class Response(io.BytesIO):
        pass
    response = Response(b"fragment")
    response.status = status
    response.headers = {"Content-Range": content_range}
    monkeypatch.setattr(downloader, "urlopen", lambda *a, **kw: response)
    if accepted:
        assert downloader._open_model(models.MODEL_URL, offset, 13) is response
    else:
        with pytest.raises(models.DenoiseModelError, match="分段"):
            downloader._open_model(models.MODEL_URL, offset, 13)
        assert response.closed


def test_model_hash_is_checked_even_when_size_matches(tmp_path, payload):
    target = tmp_path / models.MODEL_NAME
    target.write_bytes(b"x" * len(payload))
    with pytest.raises(models.DenoiseModelError, match="SHA-256"):
        models.verify_model(target)


def test_dry_run_does_not_create_destination_or_use_network(tmp_path, monkeypatch):
    target = tmp_path / "not created" / models.MODEL_NAME
    monkeypatch.setattr(downloader, "download_model", lambda *a, **kw: pytest.fail("download called"))
    assert downloader.main(["--dest", str(target), "--dry-run"]) == 0
    assert not target.parent.exists()


def test_frozen_model_uses_resource_root_and_explicit_override(tmp_path, monkeypatch, payload):
    monkeypatch.delenv(models.MODEL_DIR_ENV, raising=False)
    monkeypatch.setattr(models.sys, "frozen", True, raising=False)
    monkeypatch.setattr(models.sys, "_MEIPASS", str(tmp_path / "app"), raising=False)
    packaged = tmp_path / "app" / "models" / "denoise" / models.MODEL_NAME
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(payload)
    assert models.resolve_model_path() == packaged
    monkeypatch.setenv(models.MODEL_DIR_ENV, str(tmp_path / "missing override"))
    with pytest.raises(models.DenoiseModelError, match="missing override"):
        models.resolve_model_path()


def test_spec_requires_pinned_model_and_distributes_license(tmp_path, monkeypatch, payload):
    model = models.development_model_path(tmp_path)
    model.parent.mkdir(parents=True)
    model.write_bytes(payload)
    notices = tmp_path / "image_denoise"
    notices.mkdir()
    for name in ("THIRD_PARTY_LICENSE.txt", "NOTICE.txt"):
        (notices / name).write_text("upstream notice", encoding="utf-8")
    monkeypatch.setattr(viewer_denoise.importlib.util, "find_spec", lambda module: SimpleNamespace())
    import PyInstaller.utils.hooks
    monkeypatch.setattr(PyInstaller.utils.hooks, "collect_all", lambda module: ([("codec-data", "imagecodecs")], [("native-codec", "imagecodecs")], ["imagecodecs._cms"]))
    datas, binaries, imports = viewer_denoise.collect_viewer_denoise(tmp_path)
    assert (str(model), "models/denoise") in datas
    assert (str(notices / "THIRD_PARTY_LICENSE.txt"), "licenses/NAFNet") in datas
    assert ("native-codec", "imagecodecs") in binaries
    assert {"torch", "imagecodecs._cms", "tifffile", "psutil"} <= set(imports)
    model.unlink()
    with pytest.raises(models.DenoiseModelError):
        viewer_denoise.collect_viewer_denoise(tmp_path)


def test_spec_missing_dependency_fails_before_model_read(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer_denoise.importlib.util, "find_spec", lambda module: None)
    with pytest.raises(RuntimeError, match="torch"):
        viewer_denoise.collect_viewer_denoise(tmp_path)
