"""测试分块、并发、取消、设备回退和真实 checkpoint 的推理边界。"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from image_denoise.engine import DenoiseEngine, _acquire
from image_denoise.types import DenoiseCancelled


def _fake_engine(monkeypatch, device="cpu"):
    engine = DenoiseEngine(device)
    engine._device = device
    engine._models = {device: object(), "cpu": object()}
    monkeypatch.setattr(engine, "_load", lambda cancelled: None)
    monkeypatch.setattr(engine, "_infer_tile", lambda model, tile, device, cancelled: tile.copy())
    return engine


def test_import_does_not_load_torch():
    result = subprocess.run(
        [sys.executable, "-c", "import image_denoise.engine, sys; assert 'torch' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("shape", [(1, 1), (19, 7), (512, 512), (513, 769), (911, 1093)])
def test_identity_blending_preserves_edges_and_all_pixels(monkeypatch, shape):
    engine = _fake_engine(monkeypatch)
    original = np.random.default_rng(23).random((*shape, 3), dtype=np.float32)
    updates = []
    actual, device, tile_size = engine.denoise(original, progress=lambda done, total: updates.append((done, total)))
    assert actual.shape == original.shape
    assert actual.dtype == np.float32
    assert device == "cpu"
    assert tile_size == 512
    np.testing.assert_allclose(actual, original, atol=2e-7)
    assert updates[0][0] == 0
    assert updates[-1][0] == updates[-1][1]
    assert updates[-1][0] == len(updates) - 1
    engine.close()


def test_fusion_uses_tile_outputs_and_clips_without_mutating_input(monkeypatch):
    engine = _fake_engine(monkeypatch)
    monkeypatch.setattr(engine, "_infer_tile", lambda *args: np.full_like(args[1], 1.3))
    original = np.full((577, 779, 3), 0.1, dtype=np.float32)
    actual, _, _ = engine.denoise(original)
    assert (actual == 1).all()
    assert (original == np.float32(0.1)).all()


def test_retry_discards_partial_image_and_uses_smaller_tiles(monkeypatch):
    engine = _fake_engine(monkeypatch, "mps")
    calls = []
    count_512 = 0

    def infer(model, tile, device, cancelled):
        nonlocal count_512
        size = max(tile.shape[:2])
        calls.append((device, size))
        if size == 512:
            count_512 += 1
            if count_512 > 1:
                raise RuntimeError("MPS backend out of memory")
            return np.full_like(tile, 0.9)
        return np.full_like(tile, 0.2)

    monkeypatch.setattr(engine, "_infer_tile", infer)
    actual, device, tile_size = engine.denoise(np.zeros((530, 680, 3), np.float32))
    assert (device, tile_size) == ("mps", 256)
    np.testing.assert_allclose(actual, 0.2, atol=1e-7)
    assert calls[:2] == [("mps", 512), ("mps", 512)]
    assert all(size == 256 for _, size in calls[2:])


def test_gpu_oom_retries_512_256_128_then_restarts_cpu(monkeypatch):
    engine = _fake_engine(monkeypatch, "mps")
    calls = []

    def infer(model, tile, device, cancelled):
        calls.append((device, max(tile.shape[:2])))
        if device == "mps":
            raise RuntimeError("MPS backend out of memory")
        return np.full_like(tile, 0.4)

    monkeypatch.setattr(engine, "_infer_tile", infer)
    actual, device, tile_size = engine.denoise(np.zeros((600, 601, 3), np.float32))
    assert (device, tile_size) == ("cpu", 128)
    assert calls[:3] == [("mps", 512), ("mps", 256), ("mps", 128)]
    np.testing.assert_allclose(actual, 0.4, atol=1e-7)
    calls.clear()
    engine.denoise(np.zeros((3, 3, 3), np.float32))
    assert calls == [("cpu", 3)]


def test_gpu_non_memory_error_falls_back_directly_and_cpu_errors_propagate(monkeypatch):
    engine = _fake_engine(monkeypatch, "cuda")
    calls = []

    def infer(model, tile, device, cancelled):
        calls.append(device)
        raise RuntimeError("device mismatch" if device == "cuda" else "invalid model")

    monkeypatch.setattr(engine, "_infer_tile", infer)
    with pytest.raises(RuntimeError, match="invalid model"):
        engine.denoise(np.zeros((4, 4, 3), np.float32))
    assert calls == ["cuda", "cpu"]


def test_cancel_between_tiles_prevents_further_inference(monkeypatch):
    engine = _fake_engine(monkeypatch)
    stopped = threading.Event()
    updates = []

    def progress(done, total):
        updates.append(done)
        if done == 1:
            stopped.set()

    with pytest.raises(DenoiseCancelled):
        engine.denoise(np.zeros((700, 700, 3), np.float32), cancelled=stopped.is_set, progress=progress)
    assert updates == [0, 1]


def test_waiting_for_gpu_lock_is_cancellable():
    lock = threading.Lock()
    lock.acquire()
    stopped = threading.Event()
    entered = threading.Event()

    def wait_for_lock():
        entered.set()
        with _acquire(lock, stopped.is_set):
            pytest.fail("Locked GPU must not be entered")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(wait_for_lock)
        assert entered.wait(1)
        stopped.set()
        with pytest.raises(DenoiseCancelled):
            future.result(timeout=1)
    lock.release()


def test_close_waits_for_active_inference_and_is_terminal(monkeypatch):
    engine = _fake_engine(monkeypatch)
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    def infer(*args):
        started.set()
        assert release.wait(2)
        return args[1]

    monkeypatch.setattr(engine, "_infer_tile", infer)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(engine.denoise, np.zeros((3, 3, 3), np.float32))
        assert started.wait(1)
        close_future = executor.submit(lambda: (engine.close(), closed.set()))
        assert not closed.wait(0.05)
        release.set()
        future.result(timeout=2)
        close_future.result(timeout=2)
    assert engine._models == {}
    with pytest.raises(RuntimeError, match="关闭"):
        engine.load()


@pytest.mark.parametrize("device,expected", [("cpu", 2), ("mps", 1)])
def test_cpu_can_overlap_but_gpu_tiles_are_serialized(monkeypatch, device, expected):
    torch = pytest.importorskip("torch")
    engine = _fake_engine(monkeypatch, device)
    active, highest = 0, 0
    mutex = threading.Lock()
    barrier = threading.Barrier(2)

    class Transfer:
        def __init__(self, array):
            self.array = array

        def unsqueeze(self, _axis):
            return self

        def to(self, **kwargs):
            # 测锁而非机器的设备可用性；所有测试张量留在 CPU。
            return torch.from_numpy(self.array).unsqueeze(0)

    class TorchAPI:
        from_numpy = Transfer
        inference_mode = torch.inference_mode
        float32 = torch.float32

    def forward(tensor):
        nonlocal active, highest
        with mutex:
            active += 1
            highest = max(highest, active)
        try:
            if device == "cpu":
                barrier.wait(timeout=2)
            else:
                time.sleep(0.03)
            assert not torch.is_grad_enabled()
            return tensor + 0.1
        finally:
            with mutex:
                active -= 1

    monkeypatch.setattr(engine, "_infer_tile", DenoiseEngine._infer_tile.__get__(engine))
    engine._torch = TorchAPI()
    engine._models[device] = forward
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(engine.denoise, np.zeros((16, 16, 3), np.float32)) for _ in range(2)]
        for future in futures:
            pixels, actual_device, _ = future.result(timeout=3)
            assert actual_device == device
            np.testing.assert_allclose(pixels, 0.1)
    assert highest == expected


@pytest.mark.parametrize("pixels", [
    np.zeros((3, 3, 3), np.uint8), np.zeros((3, 3), np.float32),
    np.zeros((0, 3, 3), np.float32), np.full((3, 3, 3), np.nan, np.float32),
    np.full((3, 3, 3), 1.1, np.float32),
])
def test_invalid_input_fails_before_loading_model(monkeypatch, pixels):
    engine = DenoiseEngine()
    monkeypatch.setattr(engine, "_load", lambda _: pytest.fail("invalid input must not load model"))
    with pytest.raises(ValueError):
        engine.denoise(pixels)


def test_architecture_preserves_odd_dimensions_and_norm_formula():
    torch = pytest.importorskip("torch")
    from image_denoise.architecture import LayerNorm2d, NAFNet

    with torch.inference_mode():
        tensor = torch.rand(1, 3, 19, 29)
        net = NAFNet(width=4, middle_blk_num=1, enc_blk_nums=(1, 1), dec_blk_nums=(1, 1)).eval()
        assert net(tensor).shape == tensor.shape
        norm = LayerNorm2d(3)
        mean = tensor.mean(1, keepdim=True)
        expected = (tensor - mean) / ((tensor - mean).pow(2).mean(1, keepdim=True) + 1e-6).sqrt()
        torch.testing.assert_close(norm(tensor), expected)


def test_checkpoint_load_is_strict_cpu_first_and_weights_only(monkeypatch, tmp_path):
    torch = pytest.importorskip("torch")
    from image_denoise import architecture, models

    checkpoint = tmp_path / "中文模型.pth"
    network = torch.nn.Conv2d(3, 3, 1)
    torch.save({"params": network.state_dict()}, checkpoint)
    calls = []
    real_load = torch.load

    def load(*args, **kwargs):
        calls.append(kwargs)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(architecture, "NAFNet", lambda: torch.nn.Conv2d(3, 3, 1))
    monkeypatch.setattr(models, "verify_model", lambda path: Path(path))
    monkeypatch.setattr(torch, "load", load)
    engine = DenoiseEngine("cpu", checkpoint)
    try:
        engine.load()
        assert calls == [{"map_location": "cpu", "weights_only": True}]
        assert not engine._models["cpu"].training
        assert not any(parameter.requires_grad for parameter in engine._models["cpu"].parameters())
    finally:
        engine.close()
    torch.save({"params": {"unexpected": torch.ones(1)}}, checkpoint)
    with pytest.raises(RuntimeError, match="state_dict"):
        DenoiseEngine("cpu", checkpoint).load()


def test_concurrent_initial_load_creates_one_model(monkeypatch):
    pytest.importorskip("torch")
    engine = DenoiseEngine("cpu")
    started, release = threading.Event(), threading.Event()
    calls = []

    def create(device, cancelled):
        calls.append(device)
        started.set()
        assert release.wait(2)
        return object()

    monkeypatch.setattr(engine, "_create_model", create)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(engine.load)
        assert started.wait(1)
        second = executor.submit(engine.load)
        release.set()
        assert first.result(timeout=2) is engine
        assert second.result(timeout=2) is engine
    assert calls == ["cpu"]
    engine.close()


def test_real_checkpoint_cpu_smoke():
    if os.environ.get("SUPERBIRD_TEST_DENOISE_MODEL") != "1":
        pytest.skip("设置 SUPERBIRD_TEST_DENOISE_MODEL=1 验证已预下载模型")
    pytest.importorskip("torch")
    engine = DenoiseEngine("cpu")
    try:
        engine.load()
        original = np.clip(
            0.4 + np.random.default_rng(19).normal(0, .035, (32, 35, 3)), 0, 1,
        ).astype(np.float32)
        actual, device, tile_size = engine.denoise(original)
        assert actual.shape == original.shape
        assert np.isfinite(actual).all()
        assert actual.min() >= 0 and actual.max() <= 1
        assert .3 < actual.mean() < .5
        assert not np.allclose(actual, original)
        assert (device, tile_size) == ("cpu", 512)
    finally:
        engine.close()
