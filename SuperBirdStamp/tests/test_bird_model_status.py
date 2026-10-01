"""已安装模型的完整性状态、后台校验与按钮生命周期。"""
import hashlib
import threading
import time

import pytest
from test_editor_dejitter import window, _APP, _finish_recommendation
from birdstamp.image_dejitter.bird_parts import model_store


def _finish_check(panel):
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        _APP.processEvents()
        if panel.model_status.worker is None and panel.model_status.status.state != 'checking':
            return
        time.sleep(.005)
    raise AssertionError('模型校验线程未结束')


@pytest.fixture
def model(monkeypatch, tmp_path):
    data = b'valid test model'
    path = tmp_path / 'models' / 'bird.pth'
    path.parent.mkdir()
    monkeypatch.setattr(model_store, 'MODEL_BYTES', len(data))
    monkeypatch.setattr(model_store, 'MODEL_SHA256', hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(model_store, 'model_path', lambda: path)
    return path, data


def test_existing_model_verified_on_open_blocks_download(window, model, monkeypatch):
    path, data = model
    path.write_bytes(data)
    panel = window.dejitter_recommendation
    panel.model_status.refresh(force=True)
    _finish_check(panel)
    assert panel.model_status.status.state == 'ready'
    assert panel.download.text() == '下载完成 ✅'
    assert not panel.download.isEnabled()
    from birdstamp.gui import region_recommendation_panel as module
    monkeypatch.setattr(module, 'install_model', lambda **kw: pytest.fail('已安装时不应重复下载'))
    panel.install()
    assert panel.worker is None
    path.unlink()
    panel.model_status.refresh()
    _finish_check(panel)
    assert panel.model_status.status.state == 'missing'
    assert panel.download.isEnabled()


def test_install_completion_and_same_size_corruption(window, model, monkeypatch):
    path, data = model
    panel = window.dejitter_recommendation
    panel.model_status.refresh(force=True)
    _finish_check(panel)
    from birdstamp.gui import region_recommendation_panel as module
    def install(source, **kwargs):
        path.write_bytes(data)
        return path
    monkeypatch.setattr(module, 'install_model', install)
    panel.install()
    _finish_recommendation(window)
    _finish_check(panel)
    assert panel.download.text() == '下载完成 ✅'
    assert not panel.download.isEnabled()
    path.write_bytes(b'x' * len(data))
    panel.model_status.refresh()
    _finish_check(panel)
    assert panel.model_status.status.state == 'invalid'
    assert panel.download.isEnabled()
    assert '重新下载' in panel.download.text()
    assert '校验失败' in panel.download.toolTip()


def test_cancelled_install_never_shows_complete(window, model, monkeypatch):
    panel = window.dejitter_recommendation
    panel.model_status.refresh(force=True)
    _finish_check(panel)
    from birdstamp.gui import region_recommendation_panel as module
    def cancelled_install(*a, **kw):
        raise InterruptedError('已取消')
    monkeypatch.setattr(module, 'install_model', cancelled_install)
    panel.install()
    _finish_recommendation(window)
    _finish_check(panel)
    assert panel.model_status.status.state == 'missing'
    assert panel.download.isEnabled()


def test_verification_drops_replaced_file_and_is_owned_during_close(window, model, monkeypatch):
    path, data = model
    path.write_bytes(data)
    started, release = threading.Event(), threading.Event()
    original = model_store.inspect_model
    def inspect(*args, **kwargs):
        result = original(*args, **kwargs)
        started.set()
        release.wait(5)
        return result
    monkeypatch.setattr(model_store, 'inspect_model', inspect)
    panel = window.dejitter_recommendation
    panel.model_status.refresh(force=True)
    assert started.wait(2)
    path.unlink()
    release.set()
    _finish_check(panel)
    assert panel.model_status.status.state == 'missing'
    started.clear();release.clear()
    path.write_bytes(data)
    panel.model_status.refresh(force=True)
    assert started.wait(2)
    worker = panel.model_status.worker
    assert not panel.shutdown()
    assert panel.model_status.worker is worker
    release.set()
    limit = time.monotonic() + 5
    while panel.model_status.worker is not None and time.monotonic() < limit:
        _APP.processEvents();time.sleep(.005)
    assert panel.shutdown()
    assert not panel.download.isEnabled()
