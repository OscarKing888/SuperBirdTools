from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from PIL import Image

from birdstamp.gif_export import GifExportOptions, build_gif_frame_timing, export_gif
from birdstamp.gui.editor_exporter import _BirdStampExporterMixin
from birdstamp.gui import editor_exporter


def _frames(tmp_path, count):
    paths = []
    for index in range(count):
        path = tmp_path / f"frame-{index}.png"
        Image.new("RGB", (8, 6), (index * 2, 255 - index * 2, 40)).save(path)
        paths.append(path)
    return paths


def _read_gif(path):
    durations, colors = [], []
    with Image.open(path) as image:
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(image.info.get("duration", 0))
            colors.append(image.convert("RGB").getpixel((0, 0)))
    return durations, colors


@pytest.mark.parametrize("fps,count", [(24, 24), (30, 30), (29.97, 90), (60, 60), (100, 25), (120, 24), (240, 24)])
def test_real_gif_timing_preserves_timeline_and_matches_variants(tmp_path, fps, count):
    paths = _frames(tmp_path, count)
    progress = []
    timing = build_gif_frame_timing(count, fps)
    outputs = export_gif(
        paths, GifExportOptions(output_path=tmp_path / "clip.gif", fps=fps, scale_factors=(0.5,)),
        progress_callback=progress.append,
    )
    results = [_read_gif(path) for path in outputs]

    assert results[0] == results[1]
    durations, colors = results[0]
    assert all(duration >= 10 and duration % 10 == 0 for duration in durations)
    assert abs(sum(durations) - count * 1000.0 / fps) <= 5
    assert durations == list(timing.durations_ms)
    assert colors == [(index * 2, 255 - index * 2, 40) for index in timing.frame_indices]
    assert len(durations) == (count if fps <= 100 else round(count * 100 / fps))
    for event in progress:
        assert event.requested_fps == fps
        assert event.input_frame_count == count
        assert event.encoded_frame_count == len(durations)
        assert event.duration_ms == sum(durations)
        assert event.effective_fps == pytest.approx(len(durations) * 1000 / sum(durations))
        assert 0 <= event.current <= event.total == len(durations)
    for output_index in (1, 2):
        done = [event for event in progress if event.phase == "done" and event.output_index == output_index]
        assert len(done) == 1
        assert done[0].current == done[0].total == len(durations)
        assert done[0].total_outputs == 2
        assert "GIF 实际" in done[0].message
        if fps > 100:
            assert "按时间采样" in done[0].message
            assert done[0].effective_fps == 100


def test_sub_tick_clip_keeps_one_nonzero_frame_and_reports_its_actual_duration(tmp_path):
    events = []
    output = export_gif(
        _frames(tmp_path, 1), GifExportOptions(output_path=tmp_path / "short.gif", fps=240),
        progress_callback=events.append,
    )[0]

    assert _read_gif(output)[0] == [10]
    assert events[-1].duration_ms == 10
    assert events[-1].encoded_frame_count == 1
    assert events[-1].effective_fps == 100


@pytest.mark.parametrize("fps", [math.nan, math.inf, -math.inf, 0])
def test_invalid_fps_fails_before_writing_output(tmp_path, fps):
    target = tmp_path / "invalid.gif"
    with pytest.raises(ValueError, match="FPS"):
        export_gif(_frames(tmp_path, 1), GifExportOptions(output_path=target, fps=fps))
    assert not target.exists()


def test_gui_progress_counts_sampled_frames_and_retains_effective_fps(tmp_path):
    class Harness(_BirdStampExporterMixin):
        def __init__(self):
            self.counts = []
            self.messages = []

        def _begin_image_export_progress(self, **kwargs):
            self.counts.append((0, kwargs["total"]))
            return 1

        def _set_image_export_progress(self, current, total, **kwargs):
            self.counts.append((current, total))

        def _finish_image_export_progress(self, *, current, total, **kwargs):
            self.counts.append((current, total))

        def _set_status(self, message):
            self.messages.append(message)

    harness = Harness()
    harness._export_gif_from_frame_paths(
        _frames(tmp_path, 24), tmp_path / "gui.gif", fps=120, loop=0, scale_factors=[0.5],
    )

    assert harness.counts[0] == (0, 20)
    assert harness.counts[-1] == (20, 20)
    assert all(total == 20 for _, total in harness.counts)
    assert "GIF 实际 100.000 FPS" in harness.messages[-1]


def test_gui_completion_status_reports_temporal_sampling(tmp_path, monkeypatch):
    paths = _frames(tmp_path, 24)

    class Harness(_BirdStampExporterMixin):
        gif_export_panel = SimpleNamespace(current_request=lambda: SimpleNamespace(
            fps=120, loop=0, scale_factors=[], keep_frame_images=False,
        ))

        def __init__(self):
            self.messages = []

        def _begin_image_export_progress(self, **kwargs):
            return 1

        def _set_image_export_progress(self, *args, **kwargs):
            pass

        def _finish_image_export_progress(self, **kwargs):
            pass

        def _set_status(self, message):
            self.messages.append(message)

        def _save_batch_export_last_output_dir(self, directory):
            pass

        def _build_export_render_jobs(self, *args, **kwargs):
            return []

        def _ensure_gif_frame_cache(self, *args, **kwargs):
            return paths, tmp_path

        def _clear_photo_export_dirty(self, paths):
            pass

    monkeypatch.setattr(editor_exporter.QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(tmp_path / "finished.gif"), ""))
    harness = Harness()
    harness._export_all_as_gif(paths)

    assert harness.messages[-1].startswith("GIF 导出完成")
    assert "GIF 实际 100.000 FPS" in harness.messages[-1]
    assert "按时间采样" in harness.messages[-1]
