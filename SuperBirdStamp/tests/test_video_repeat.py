"""视频「重复播放」：时间线量化、硬链接序列、ffmpeg 命令与面板状态。"""
from __future__ import annotations

import os
from fractions import Fraction
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image

from birdstamp import export_stage
from birdstamp.export_stage import VideoExportOptions, VideoFrameJob, build_ffmpeg_command, export_video
from birdstamp.export_stage.core import validate_video_export_options
from birdstamp.export_stage import video_repeat
from birdstamp.export_stage.video_repeat import (
    build_video_repeat_timeline,
    link_timeline_frames,
    resolve_repeat_output_fps,
)


def _segment_ticks(timeline, frame_count):
    """Split output frames back into per-pass, per-input-frame tick counts."""
    ticks = []
    position = 0
    for _ in timeline.segment_fps:
        counts = []
        for index in range(frame_count):
            count = 0
            while position < len(timeline.frame_indices) and timeline.frame_indices[position] == index:
                count += 1
                position += 1
            counts.append(count)
        ticks.append(counts)
    assert position == len(timeline.frame_indices)
    return ticks


def test_halving_passes_repeat_each_frame_exactly():
    timeline = build_video_repeat_timeline(15, 20, (10, 5))

    assert timeline.output_fps == 20
    assert timeline.segment_fps == (20.0, 10.0, 5.0)
    assert _segment_ticks(timeline, 15) == [[1] * 15, [2] * 15, [4] * 15]
    assert timeline.encoded_frame_count == 105
    assert timeline.duration_seconds == pytest.approx(0.75 + 1.5 + 3.0)
    assert "20 → 10 → 5 FPS" in timeline.summary() and "3 遍" in timeline.summary()


@pytest.mark.parametrize(
    "fps,repeat,expected",
    [
        (30, (24,), 120.0),
        (24, (30,), 120.0),
        (25, (10,), 50.0),
        (29.97, (23.976,), 119.88),
        (25, (7,), 25.0),
        (240, (60,), 240.0),
        (60, (7,), 60.0),
    ],
)
def test_output_fps_prefers_small_exact_multiple(fps, repeat, expected):
    assert resolve_repeat_output_fps((fps, *repeat)) == pytest.approx(expected)


@pytest.mark.parametrize("fps,repeat", [(25, (7, 3)), (60, (7,)), (29.97, (12,)), (10, (30,))])
def test_inexact_passes_keep_each_pass_duration_within_half_output_frame(fps, repeat):
    count = 9
    timeline = build_video_repeat_timeline(count, fps, repeat)
    for rate, ticks in zip(timeline.segment_fps, _segment_ticks(timeline, count)):
        assert min(ticks) >= 1
        expected = Fraction(str(timeline.output_fps)) * count / Fraction(str(rate))
        assert abs(sum(ticks) - expected) <= Fraction(1, 2)


@pytest.mark.parametrize("repeat", [(0,), (float("nan"),), (10, -2), ("x",), (float("inf"),)])
def test_invalid_repeat_fps_is_rejected(tmp_path, repeat):
    with pytest.raises(ValueError, match="第 .* 遍 FPS"):
        validate_video_export_options(VideoExportOptions(output_path=tmp_path / "a.mp4", repeat_fps=repeat))


def _write_frames(directory: Path, count: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        path = directory / f"frame_{index + 1:06d}.png"
        Image.new("RGB", (4, 4), (index * 50, 0, 0)).save(path)
        paths.append(path)
    return paths


def test_link_timeline_frames_hardlinks_in_order_and_replaces_stale_dir(tmp_path):
    frames = _write_frames(tmp_path / "frames", 2)
    target = tmp_path / "timeline"
    target.mkdir()
    (target / "frame_999999.png").write_bytes(b"stale")
    timeline = build_video_repeat_timeline(2, 10, (5,))

    link_timeline_frames(frames, timeline, target)

    outputs = sorted(target.iterdir())
    assert [path.name for path in outputs] == [f"frame_{i:06d}.png" for i in range(1, 7)]
    expected_sources = [frames[i] for i in (0, 1, 0, 0, 1, 1)]
    for output, source in zip(outputs, expected_sources):
        assert os.path.samefile(output, source)


def test_link_timeline_frames_falls_back_to_copy(tmp_path, monkeypatch):
    frames = _write_frames(tmp_path / "frames", 2)
    target = tmp_path / "timeline"
    calls = []

    def refuse_link(*args):
        calls.append(args)
        raise OSError("hardlinks unsupported")

    monkeypatch.setattr(video_repeat.os, "link", refuse_link)
    link_timeline_frames(frames, build_video_repeat_timeline(2, 10, (5,)), target)

    assert len(calls) == 1
    outputs = sorted(target.iterdir())
    assert len(outputs) == 6
    assert outputs[3].read_bytes() == frames[0].read_bytes()
    assert not os.path.samefile(outputs[3], frames[0])


def test_ffmpeg_command_input_fps_override(tmp_path):
    options = VideoExportOptions(output_path=tmp_path / "clip.mp4", fps=29.97)
    command = build_ffmpeg_command(Path("ffmpeg"), tmp_path / "timeline", options, input_fps=119.88)
    assert command[command.index("-framerate") + 1] == "119.88"
    assert command[command.index("-i") + 1] == str(tmp_path / "timeline" / "frame_%06d.png")


def test_export_video_encodes_repeat_timeline_and_keeps_frame_cache(tmp_path, monkeypatch):
    jobs = []
    for index in range(2):
        source = tmp_path / f"source_{index}.png"
        Image.new("RGB", (12, 8), (index * 200, 40, 90)).save(source)
        jobs.append(VideoFrameJob(
            path=source,
            settings={"draw_banner": False, "draw_text": False, "draw_focus": False},
            raw_metadata={}, metadata_context={},
        ))
    captured = {}

    def fake_run(cmd, *, cancel_event=None, cancel_message=""):
        pattern = Path(cmd[cmd.index("-i") + 1])
        captured["fps"] = cmd[cmd.index("-framerate") + 1]
        captured["dir"] = pattern.parent
        captured["colors"] = [
            Image.open(path).convert("RGB").getpixel((0, 0)) for path in sorted(pattern.parent.glob("frame_*.png"))
        ]
        Path(cmd[-1]).write_bytes(b"fake-video")

    monkeypatch.setattr(export_stage, "find_ffmpeg_executable", lambda: tmp_path / "ffmpeg")
    monkeypatch.setattr(export_stage, "_run_ffmpeg_command", fake_run)
    progress = []
    output = export_video(
        jobs,
        VideoExportOptions(output_path=tmp_path / "clip.mp4", fps=20, repeat_fps=(10, 5), preserve_temp_files=True),
        progress_callback=lambda event: progress.append(event.message),
    )

    assert output.read_bytes() == b"fake-video"
    assert captured["fps"] == "20"
    assert captured["dir"].name == "repeat_timeline"
    first, second = (0, 40, 90), (200, 40, 90)
    assert captured["colors"] == [first, second] + [first] * 2 + [second] * 2 + [first] * 4 + [second] * 4
    assert not captured["dir"].exists()
    frames_dir = captured["dir"].parent / "frames"
    assert len(list(frames_dir.glob("frame_*.png"))) == 2
    assert "20 → 10 → 5 FPS" in progress[-1]

    # 去掉重复播放后复用同一渲染缓存，并直接编码原序列。
    captured.clear()
    export_video(jobs, VideoExportOptions(output_path=tmp_path / "clip.mp4", fps=20, preserve_temp_files=True))
    assert captured["dir"] == frames_dir
    assert len(captured["colors"]) == 2


def test_video_panel_repeat_rows_request_state_and_busy():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from birdstamp.gui.editor_video_panel import VideoExportPanel

    panel = VideoExportPanel()
    try:
        panel.set_fps(30)
        assert panel.current_request().repeat_fps == []
        panel.repeat_editor.add_button.click()
        panel.repeat_editor.add_button.click()
        assert panel.current_request().repeat_fps == [15.0, 8.0]
        assert [label.text() for _row, label, _combo in panel.repeat_editor.rows] == ["第 2 遍", "第 3 遍"]

        state = panel.current_state()
        assert state["repeat_fps"] == [15.0, 8.0]
        panel.set_state({"repeat_fps": [12, "bad", 0]})
        assert panel.current_request().repeat_fps == [12.0]
        panel.set_state(state)
        assert panel.current_request().repeat_fps == [15.0, 8.0]
        legacy = dict(state)
        del legacy["repeat_fps"]
        panel.set_state(legacy)
        assert panel.current_request().repeat_fps == []

        panel.set_busy(True)
        assert not panel.repeat_editor.isEnabled()
        panel.set_busy(False)
        assert panel.repeat_editor.add_button.isEnabled()
        app.processEvents()
    finally:
        panel.close()
