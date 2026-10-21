"""GIF 并行量化须保留旧编码结果、时间表与失败时的像素所有权。"""
from collections import Counter
import random
import threading

from PIL import Image
import pytest

from birdstamp import gif_export


def _frames(tmp_path, count=5, size=(96, 64)):
    rng = random.Random(42)
    paths = []
    for index in range(count):
        path = tmp_path / f"白鹭-{index}.png"
        shape = (size[0] - index, size[1])
        with Image.frombytes("RGB", shape, rng.randbytes(shape[0] * shape[1] * 3)) as image:
            image.save(path)
        paths.append(path)
    return paths


@pytest.mark.parametrize("optimize", [False, True])
@pytest.mark.parametrize("target_size", [(96, 64), (31, 21)])
@pytest.mark.parametrize("frame_count", [1, 5])
def test_parallel_palette_matches_original_rgb_encoder_byte_for_byte(tmp_path, optimize, target_size, frame_count):
    paths = _frames(tmp_path, frame_count)
    timing = gif_export.build_gif_frame_timing(len(paths), 120, (24, 10))
    sampled = [paths[index] for index in timing.frame_indices]
    rgb = {}
    expected = tmp_path / "before.gif"
    actual = tmp_path / "after.gif"
    try:
        for path in paths:
            with Image.open(path) as image:
                rgb[path] = gif_export.normalize_gif_frame_size(
                    image, target_size, background_color="#123456",
                )
        frames = [rgb[path] for path in sampled]
        frames[0].save(
            expected, format="GIF", save_all=True, append_images=frames[1:],
            duration=list(timing.durations_ms), loop=2, optimize=optimize, disposal=2,
        )
    finally:
        for frame in rgb.values():
            frame.close()
    gif_export._save_gif_variant(
        sampled, actual, durations_ms=timing.durations_ms, loop=2,
        target_size=target_size, background_color="#123456", optimize=optimize,
    )
    assert actual.read_bytes() == expected.read_bytes()


def test_preparation_is_bounded_parallel_and_quantizes_each_unique_frame_once(tmp_path, monkeypatch):
    paths = _frames(tmp_path, 9)
    original = gif_export._prepare_gif_palette_frame
    monkeypatch.setattr(gif_export, "_gif_prepare_workers", lambda *_args: 3)
    barrier = threading.Barrier(3, timeout=5)
    lock = threading.Lock()
    calls = Counter()
    active = maximum = 0
    caller_thread = threading.get_ident()
    progress = []

    def prepare(path, *args):
        nonlocal active, maximum
        with lock:
            calls[path] += 1
            active += 1
            maximum = max(maximum, active)
        try:
            assert threading.get_ident() != caller_thread
            if path in paths[:3]:
                barrier.wait()
            return original(path, *args)
        finally:
            with lock:
                active -= 1

    def on_progress(current):
        assert threading.get_ident() == caller_thread
        progress.append(current)

    monkeypatch.setattr(gif_export, "_prepare_gif_palette_frame", prepare)
    gif_export._save_gif_variant(
        paths * 3, tmp_path / "parallel.gif", durations_ms=[50] * 27, loop=0,
        target_size=(96, 64), background_color="#000000", frame_prepared_callback=on_progress,
    )
    assert calls == Counter(paths)
    assert maximum == 3 and active == 0
    assert progress == list(range(3, 28, 3))


@pytest.mark.parametrize("failure", ["worker", "progress"])
def test_failure_joins_workers_and_closes_all_prepared_frames(tmp_path, monkeypatch, failure):
    paths = _frames(tmp_path, 6)
    monkeypatch.setattr(gif_export, "_gif_prepare_workers", lambda *_args: 2)
    barrier = threading.Barrier(2, timeout=5)
    created = []
    lock = threading.Lock()

    def prepare(path, *_args):
        if path in paths[:2]:
            barrier.wait()
        if failure == "worker" and path == paths[0]:
            raise OSError("broken frame")
        frame = Image.new("P", (10, 10))
        with lock:
            created.append(frame)
        return frame

    def on_progress(_current):
        if failure == "progress":
            raise OSError("progress failed")

    monkeypatch.setattr(gif_export, "_prepare_gif_palette_frame", prepare)
    output = tmp_path / "existing.gif"
    output.write_bytes(b"previous complete GIF")
    with pytest.raises(OSError, match="broken frame|progress failed"):
        gif_export._save_gif_variant(
            paths, output, durations_ms=[50] * 6, loop=0, target_size=(10, 10),
            background_color="#000000", frame_prepared_callback=on_progress,
        )
    assert output.read_bytes() == b"previous complete GIF"
    assert created
    for frame in created:
        with pytest.raises(ValueError, match="closed"):
            frame.getpixel((0, 0))
    assert not any(thread.name.startswith("birdstamp-gif-palette") for thread in threading.enumerate())


def test_worker_budget_counts_source_decode_even_for_small_variant(tmp_path, monkeypatch):
    paths = _frames(tmp_path, 9, size=(400, 300))
    monkeypatch.setattr(gif_export.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(gif_export.os, "process_cpu_count", lambda: 32, raising=False)
    monkeypatch.setattr(gif_export, "_gif_prepare_memory_budget", lambda: 400 * 300 * 24 * 2)
    assert gif_export._gif_prepare_workers(paths, (20, 20)) == 2
    assert gif_export._gif_prepare_workers(paths, (800, 600)) == 1
    monkeypatch.setattr(gif_export, "_gif_prepare_memory_budget", lambda: 100 * 1024**3)
    assert gif_export._gif_prepare_workers(paths, (20, 20)) == 8
    assert gif_export._gif_prepare_workers(paths[:1], (20, 20)) == 1


def test_corrupt_frame_fails_before_any_palette_work(tmp_path, monkeypatch):
    paths = _frames(tmp_path, 1)
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    monkeypatch.setattr(gif_export, "_prepare_gif_palette_frame", lambda *_args: pytest.fail("unexpected decode"))
    with pytest.raises(OSError):
        gif_export._save_gif_variant(
            [*paths, broken], tmp_path / "broken.gif", durations_ms=[50, 50], loop=0,
            target_size=(96, 64), background_color="#000000",
        )
