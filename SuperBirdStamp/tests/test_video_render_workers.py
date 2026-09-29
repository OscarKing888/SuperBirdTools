"""Video resource budgets and actual frame-action concurrency."""
from pathlib import Path
import threading
import time
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from birdstamp import export_metadata, export_stage
from birdstamp.export_stage import core, video_render_workers as policy
from birdstamp.export_stage import VideoFrameJob, VideoExportOptions


def machine(monkeypatch, cpus=24, available_gib=64):
    monkeypatch.setattr(policy.os, "process_cpu_count", lambda: cpus, raising=False)
    monkeypatch.setattr(policy.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(policy, "available_memory_bytes", lambda: int(available_gib * 1024**3))


def budget(requested=0, pending=100, pixels=21_026_304):
    return policy.resolve_video_frame_budget(requested, pending, max_frame_pixels=pixels, stage="test")


def test_video_uses_24_cpus_without_eight_thread_or_four_gib_cap(monkeypatch):
    machine(monkeypatch)
    assert budget().workers == 24
    assert budget(pending=5).workers == 5
    assert budget(pending=0).workers == 1
    # The legacy public policy is still used by images and sequence analysis.
    assert core.resolve_video_render_workers(0, 100, max_frame_pixels=1_000_000) <= 8


def test_cpu_quota_and_older_python_fallback(monkeypatch):
    machine(monkeypatch, cpus=6)
    assert budget().workers == 6
    monkeypatch.delattr(policy.os, "process_cpu_count")
    assert budget().workers == 32
    monkeypatch.setattr(policy.os, "cpu_count", lambda: None)
    assert budget().workers == 1


@pytest.mark.parametrize("available_gib, expected", [(0, 1), (1, 1), (2, 2)])
def test_half_available_memory_bounds_workers(monkeypatch, available_gib, expected):
    machine(monkeypatch, available_gib=available_gib)
    assert budget().workers == expected


def test_unknown_dimensions_and_missing_memory_probe(monkeypatch):
    machine(monkeypatch)
    monkeypatch.setattr(policy, "available_memory_bytes", lambda: None)
    assert budget(pixels=0).workers == 3
    assert budget(pixels=80_000_000).workers == 1
    assert budget(pixels=1_000_000).workers == 24


@pytest.mark.parametrize("missing", [False, True])
def test_memory_probe_failure_returns_fallback_marker(monkeypatch, missing):
    def fail():
        raise OSError("probe unavailable")
    monkeypatch.setitem(sys.modules, "psutil", None if missing else SimpleNamespace(virtual_memory=fail))
    assert policy.available_memory_bytes() is None


def test_explicit_override_warns_and_is_bounded_by_pending(monkeypatch):
    machine(monkeypatch, available_gib=1)
    result = budget(12, pending=10)
    assert result.workers == 10
    assert result.recommended == 1
    assert "实际使用 10" in result.warning


def job(path, **kwargs):
    return VideoFrameJob(path=path, settings={"ratio": "free", "draw_banner": False,
                         "draw_text": False, "draw_focus": False},
                         raw_metadata={}, metadata_context={}, **kwargs)


def test_source_budget_includes_padding_unknown_jobs_and_header_sizes(tmp_path):
    path = tmp_path / "frame.png"
    with Image.new("RGB", (100, 80)) as image:
        image.save(path)
    source = job(path, crop_plan=(None, (10, 20, 30, 40)))
    assert core.estimate_source_render_pixels([source]) == 170 * 110
    assert core.estimate_source_render_pixels([source, job(tmp_path / "unknown.raw")]) == 24_000_000
    source.crop_plan = None
    source.settings.update(ratio="16:9", crop_box=[-1., -1., 2., 2.], center_mode="custom")
    assert core.estimate_source_render_pixels([source]) >= 300 * 240
    source.settings["ratio"] = "no_crop"
    assert core.estimate_source_render_pixels([source]) == 8000


def test_normalize_budget_reads_source_headers_without_decoding(tmp_path, monkeypatch):
    path = tmp_path / "frame.png"
    with Image.new("RGB", (100, 80)) as image:
        image.save(path)
    def forbidden(*args, **kwargs):
        pytest.fail("budget must not decode image pixels")
    from PIL import PngImagePlugin
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", forbidden)
    assert policy.estimate_normalize_pixels([path], (20, 10)) == 8000
    assert policy.estimate_normalize_pixels([path], (200, 100)) == 20000


@pytest.mark.parametrize("workers", [1, 2, 12])
def test_actual_video_actions_obey_budget_and_reuse_cache(tmp_path, monkeypatch, workers):
    if workers < 3:
        machine(monkeypatch, cpus=24, available_gib=workers)
        monkeypatch.setattr(core, "estimate_source_render_pixels", lambda *a, **kw: 21_026_304)
        monkeypatch.setattr(core, "estimate_normalize_pixels", lambda *a, **kw: 21_026_304)
    else:
        machine(monkeypatch, cpus=workers)
    jobs = []
    for index in range(workers * 2):
        path = tmp_path / f"photo_{index}.png"
        with Image.new("RGB", (32, 24), (index, 80, 120)) as image:
            image.save(path)
        jobs.append(job(path))
    monkeypatch.setattr(export_metadata, "save_export_image",
                        lambda image, target, source_path, **kwargs: image.save(target, **kwargs))
    monkeypatch.setattr(export_stage, "find_ffmpeg_executable", lambda: tmp_path / "ffmpeg")
    monkeypatch.setattr(export_stage, "_run_ffmpeg_command",
                        lambda command, **kwargs: Path(command[-1]).write_bytes(b"video"))
    peaks = {}
    for action_type in (core._SourceFrameRenderAction, core._VideoFrameNormalizeAction):
        original = action_type.execute
        lock = threading.Lock()
        counts = {"active": 0, "peak": 0}
        barrier = threading.Barrier(workers)
        peaks[action_type.__name__] = counts
        def execute(self, original=original, lock=lock, counts=counts, barrier=barrier):
            with lock:
                counts["active"] += 1
                counts["peak"] = max(counts["peak"], counts["active"])
            try:
                barrier.wait(timeout=10)
                time.sleep(.02)
                return original(self)
            finally:
                with lock:
                    counts["active"] -= 1
        monkeypatch.setattr(action_type, "execute", execute)
    records = []
    monkeypatch.setattr(policy._LOG, "info", lambda message, *args: records.append(message % args))
    options = VideoExportOptions(output_path=tmp_path / "clip.mp4", preserve_temp_files=True)
    export_stage.export_video(jobs, options)
    assert all(counts["peak"] == workers for counts in peaks.values())
    assert all(counts["active"] == 0 for counts in peaks.values())
    assert any(f"workers={workers} completed={workers * 2}" in record for record in records)
    monkeypatch.setattr(core, "resolve_video_frame_budget",
                        lambda *args, **kwargs: pytest.fail("cache hit must not schedule frames"))
    export_stage.export_video(jobs, options)
    assert sum(f"workers=0 completed=0 reused={workers * 2}" in record for record in records) == 2


@pytest.mark.parametrize("error, expected", [
    (OSError("write failed"), "failed"),
    (export_stage.VideoExportCancelledError("cancelled"), "cancelled"),
])
def test_stage_diagnostics_include_failure_and_cancellation(monkeypatch, error, expected):
    records = []
    monkeypatch.setattr(policy._LOG, "info", lambda message, *args: records.append(message % args))
    @policy.track_video_stage("source")
    def fail(*, stats):
        raise error
    with pytest.raises(type(error)):
        fail()
    assert len(records) == 1
    assert f"status={expected}" in records[0]
