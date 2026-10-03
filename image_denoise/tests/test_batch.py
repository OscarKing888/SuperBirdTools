"""批处理真实目录、跨平台冲突、共享池取消与内存配额。"""
from dataclasses import replace
from pathlib import Path
import threading
import time
import unicodedata
import subprocess
import sys

import pytest

from image_denoise.batch import (GIB, allocate_destinations, collect_image_paths,
                                 estimate_image_memory, run_batch, validate_options)
from image_denoise.types import DenoiseOptions, DenoiseResult


class Engine:
    def __init__(self):
        self.closed = False

    def load(self, cancelled=None):
        return self

    def close(self):
        self.closed = True


def files(tmp_path, count=4):
    paths = [tmp_path / f"照片{i}.jpg" for i in range(count)]
    for path in paths:
        path.touch()
    return paths


def run(paths, options=None, **kwargs):
    kwargs.setdefault("engine", Engine())
    kwargs.setdefault("probe", lambda _p: (32, 32))
    kwargs.setdefault("memory_provider", lambda: (32 * GIB, 24 * GIB))
    return run_batch(paths, options or DenoiseOptions(), **kwargs)


def test_scan_takes_deduplicated_snapshot_and_excludes_output_and_hidden(tmp_path):
    photo = files(tmp_path, 1)[0]
    sub = tmp_path / "鸟"
    sub.mkdir()
    nested = files(sub, 1)[0]
    for name in ("denoised", "成片", ".superpicky", "fixed"):
        directory = tmp_path / name
        directory.mkdir()
        files(directory, 1)
    (tmp_path / "text.txt").touch()
    previous_output = tmp_path / "照片_denoised_2.tif"
    previous_output.touch()
    options = DenoiseOptions(subdir="成片", output_directory=str(tmp_path / "fixed"))
    result = collect_image_paths([tmp_path, photo], recursive=True, options=options)
    assert set(result) == {str(photo), str(nested)}
    assert collect_image_paths([tmp_path], options=options) == [str(photo)]
    assert collect_image_paths([previous_output], options=options) == [str(previous_output)]


def test_allocate_reserves_image_and_sidecar_across_unicode_and_case(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "CAFÉ_DENOISED.XMP").touch()
    paths = [tmp_path / "Café.jpg", tmp_path / "cafe\u0301.raw", tmp_path / "Café.png"]
    names = [Path(p).name for p in allocate_destinations(paths, DenoiseOptions(
        output_mode="fixed", output_directory=str(out)))]
    keys = [unicodedata.normalize("NFC", name).casefold() for name in names]
    assert keys == ["café_denoised_2.tif", "café_denoised_3.tif", "café_denoised_4.tif"]
    assert list(out.iterdir()) == [out / "CAFÉ_DENOISED.XMP"]  # 预分配不写文件


def test_source_subdir_is_per_source_and_format_is_jpeg(tmp_path):
    paths = [tmp_path / "A" / "a.arw", tmp_path / "B" / "a.arw"]
    outputs = allocate_destinations(paths, DenoiseOptions(format="jpeg", subdir="降噪"))
    assert outputs == [str(p.parent / "降噪" / "a_denoised.jpg") for p in paths]


@pytest.mark.parametrize("name", ["../out", "/tmp", "C:\\out", "CON", "nul.txt", "out.", "a:b", ""])
def test_subdir_cannot_escape_or_use_windows_reserved_names(name):
    with pytest.raises(ValueError):
        validate_options(DenoiseOptions(subdir=name))


def test_parallel_processing_has_bounded_peak_and_input_order(tmp_path):
    lock, barrier = threading.Lock(), threading.Barrier(2)
    active = peak = 0

    def process(source, destination, options, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(5)
        with lock:
            active -= 1
        return DenoiseResult(source, destination)

    paths = files(tmp_path)
    results = run(paths, processor=process)
    assert peak == 2
    assert [r.source for r in results] == list(map(str, paths))
    assert all(r.status == "success" for r in results)


def test_memory_gate_reduces_two_workers_to_one_and_rejects_huge_photo(tmp_path):
    lock = threading.Lock()
    active = peak = 0

    def process(source, destination, options, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)  # 模拟本来可以重叠的计算，不用 sleep 判断测试结果
        with lock:
            active -= 1
        return DenoiseResult(source, destination)

    paths = files(tmp_path)
    result = run(paths, processor=process, probe=lambda _p: (10000, 6000),
                 memory_provider=lambda: (16 * GIB, 12 * GIB))
    assert peak == 1 and all(r.status == "success" for r in result)
    huge = run(paths[:1], processor=process, probe=lambda _p: (30000, 30000))
    assert huge[0].status == "failed" and "内存不足" in huge[0].error


def test_probe_failure_does_not_block_other_images(tmp_path):
    paths = files(tmp_path, 2)

    def probe(path):
        if path == str(paths[0]):
            raise ValueError("损坏图片")
        return 32, 32

    result = run(paths, probe=probe, processor=lambda s, d, o, **kw: DenoiseResult(s, d))
    assert [r.status for r in result] == ["failed", "success"]


def test_output_subdir_is_file_fails_only_its_photo_during_real_export(tmp_path):
    from PIL import Image
    import tifffile

    paths = []
    for directory in (tmp_path / "受阻", tmp_path / "正常"):
        directory.mkdir()
        source = directory / "照片.png"
        Image.new("RGB", (24, 16), "gray").save(source)
        paths.append(source)
    blocker = paths[0].parent / "denoised"
    blocker.write_bytes(b"existing file must survive")
    originals = [path.read_bytes() for path in paths]

    results = run_batch(paths, DenoiseOptions(strength=0, device="cpu"),
                        memory_provider=lambda: (32 * GIB, 24 * GIB))

    assert [result.status for result in results] == ["failed", "success"]
    assert results[0].error
    assert blocker.read_bytes() == b"existing file must survive"
    assert [path.read_bytes() for path in paths] == originals
    destination = Path(results[1].destination)
    pixels = tifffile.imread(destination)
    assert pixels.shape == (16, 24, 3) and pixels.dtype.name == "uint16"
    assert destination.with_suffix(".xmp").is_file()


def test_serial_fallback_executes_on_coordinator_and_callback_failure_is_isolated(tmp_path):
    owner = threading.get_ident()
    threads = []

    def process(source, destination, _options, **_kwargs):
        threads.append(threading.get_ident())
        return DenoiseResult(source, destination)

    def broken_callback(_result):
        raise RuntimeError("UI callback unavailable")

    results = run(files(tmp_path), processor=process, serial=True, on_result=broken_callback)
    assert threads == [owner] * 4
    assert all(result.status == "success" for result in results)


def test_stop_waits_for_running_photos_and_does_not_submit_more(tmp_path):
    stop, gate = threading.Event(), threading.Event()
    started = threading.Barrier(3)
    called, output = [], []

    def process(source, destination, options, cancelled=None, **kwargs):
        called.append(source)
        started.wait(5)
        gate.wait(5)
        return DenoiseResult(source, destination, "cancelled" if cancelled() else "success")

    thread = threading.Thread(target=lambda: output.extend(run(files(tmp_path, 8),
        processor=process, cancelled=stop.is_set)))
    thread.start()
    started.wait(5)
    stop.set()
    assert thread.is_alive()
    gate.set()
    thread.join(5)
    assert not thread.is_alive()
    assert len(called) == 2 and len(output) == 8
    assert all(r.status == "cancelled" for r in output)


def test_pool_queue_is_cancelled_without_owning_or_closing_pool(tmp_path):
    from app_common.file_browser._work_action import CallableAction
    from app_common.file_browser._work_policy import WorkKind
    from app_common.file_browser._work_pool import BrowserWorkPool

    pool = BrowserWorkPool(5, 2, analysis_workers=2)
    blocker, stop = threading.Event(), threading.Event()
    other = [pool.submit_action(CallableAction(blocker.wait, 5), kind=WorkKind.ANALYSIS) for _ in range(2)]
    output = []
    paths = files(tmp_path)
    thread = threading.Thread(target=lambda: output.extend(run(paths, pool=pool,
        processor=lambda *_a, **_kw: pytest.fail("排队动作不应执行"), cancelled=stop.is_set)))
    try:
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and pool.snapshot()["analysis_queued"] < 2:
            time.sleep(0.005)
        assert pool.snapshot()["analysis_queued"] >= 2
        stop.set()
        thread.join(5)
        assert not thread.is_alive()
        assert len(output) == len(paths) and all(r.status == "cancelled" for r in output)
        blocker.set()
        for future in other:
            future.result(timeout=5)
        # 协调器归还 lease，池仍属于浏览器，可继续接收任务。
        assert not pool._producers[WorkKind.ANALYSIS]
        assert pool.submit_action(CallableAction(lambda: 7), kind=WorkKind.ANALYSIS).result(timeout=5) == 7
    finally:
        stop.set()
        blocker.set()
        thread.join(5)
        pool.shutdown(timeout=5)


def test_cli_reports_failure_status_and_uses_same_batch(monkeypatch, tmp_path, capsys):
    from image_denoise import __main__ as cli

    path = files(tmp_path, 1)[0]
    seen = []

    def batch(paths, options, **kwargs):
        seen.append(options)
        result = DenoiseResult(str(path), status="failed", error="测试错误")
        kwargs["on_result"](result)
        return [result]

    monkeypatch.setattr(cli, "run_batch", batch)
    assert cli.main([str(path), "--format", "jpeg", "--json"]) == 1
    assert seen[0].format == "jpeg" and "测试错误" in capsys.readouterr().out


@pytest.mark.parametrize("help_only", [True, False])
def test_cli_does_not_import_qt_even_for_cpu_export(tmp_path, help_only):
    from PIL import Image

    source = tmp_path / "中文照片.png"
    Image.new("RGB", (24, 16), "gray").save(source)
    destination = tmp_path / "导出"
    args = (["--help"] if help_only else [str(source), "--device", "cpu", "--strength", "0",
                                           "--output-dir", str(destination), "--json"])
    script = """
import builtins, runpy, sys
original_import = builtins.__import__
def checked_import(name, *args, **kwargs):
    if name.startswith(('PyQt', 'PySide')):
        raise AssertionError('CLI must not import Qt: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = checked_import
sys.argv = ['image_denoise', *sys.argv[1:]]
runpy.run_module('image_denoise', run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", script, *args], capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    if not help_only:
        assert '"status": "success"' in result.stdout
        assert (destination / "中文照片_denoised.tif").is_file()
        assert (destination / "中文照片_denoised.xmp").is_file()
