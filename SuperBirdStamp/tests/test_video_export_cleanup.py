from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from birdstamp import export_stage
from birdstamp.export_frame_cache import FRAME_CACHE_ROOT_NAME
from birdstamp.export_stage import VideoExportCancelledError, VideoExportOptions, VideoFrameJob
from birdstamp.export_stage import core


@pytest.fixture
def export_job(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGB", (12, 8), "blue").save(source)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output = output_dir / "existing.mp4"
    output.write_bytes(b"previous successful export")
    monkeypatch.setattr(export_stage, "find_ffmpeg_executable", lambda: tmp_path / "ffmpeg")
    job = VideoFrameJob(
        path=source,
        settings={"draw_banner": False, "draw_text": False, "draw_focus": False},
        raw_metadata={}, metadata_context={},
    )
    return job, output


def _fail_after_frame_write(monkeypatch, stage, error):
    function_name = "_render_and_cache_source_frame" if stage == "source" else "_normalize_and_cache_video_frame"
    original = getattr(core, function_name)

    def fail_after_write(**kwargs):
        original(**kwargs)
        raise error

    monkeypatch.setattr(core, function_name, fail_after_write)


@pytest.mark.parametrize("stage", ["source", "video"])
@pytest.mark.parametrize("preserve", [False, True])
def test_failure_cleans_only_nonpersistent_caches(export_job, monkeypatch, stage, preserve):
    job, output = export_job
    _fail_after_frame_write(monkeypatch, stage, OSError("injected write failure"))
    options = VideoExportOptions(output_path=output, preserve_temp_files=preserve)

    with pytest.raises(OSError, match="injected write failure"):
        export_stage.export_video([job], options)

    assert output.read_bytes() == b"previous successful export"
    if preserve:
        assert list((output.parent / FRAME_CACHE_ROOT_NAME).rglob("*.png"))
    else:
        assert list(output.parent.iterdir()) == [output]


def test_missing_source_cleans_cache_created_before_decode(export_job):
    job, output = export_job
    job.path.unlink()

    with pytest.raises(FileNotFoundError):
        export_stage.export_video([job], VideoExportOptions(output_path=output, preserve_temp_files=False))

    assert list(output.parent.iterdir()) == [output]


@pytest.mark.parametrize("stage", ["source", "video"])
def test_cancel_returns_actual_preserved_frames_from_incomplete_helper(export_job, monkeypatch, stage):
    job, output = export_job
    _fail_after_frame_write(monkeypatch, stage, VideoExportCancelledError("cancel after first frame"))
    commands = []

    def encode_partial(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"partial export")

    monkeypatch.setattr(export_stage, "_run_ffmpeg_command", encode_partial)
    with pytest.raises(VideoExportCancelledError) as error:
        export_stage.export_video([job], VideoExportOptions(output_path=output, preserve_temp_files=False))

    frames_dir = error.value.preserved_frames_dir
    assert frames_dir is not None
    frames = list(frames_dir.glob("*.png"))
    assert len(frames) == 1
    with Image.open(frames[0]) as frame:
        assert frame.size == (12, 8)
    assert str(frames_dir.parent) in str(error.value)
    assert output.read_bytes() == b"previous successful export"
    if stage == "video":
        assert len(commands) == 1
        assert error.value.partial_output_path is not None
        assert error.value.partial_output_path.read_bytes() == b"partial export"
    else:
        assert not commands
        assert error.value.partial_output_path is None
