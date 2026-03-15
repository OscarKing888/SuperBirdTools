import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from birdstamp.gui.editor_core import draw_focus_box_overlay
from birdstamp.video_export import (
    _recommended_auto_render_workers,
    _count_contiguous_rendered_frames,
    _partial_video_output_path,
    VideoFrameJob,
    VideoExportOptions,
    build_ffmpeg_command,
    export_video,
    normalize_frame_size,
    render_video_frame,
    resolve_target_frame_size,
    resolve_video_render_workers,
)


def _build_sample_video_jobs(tmp_dir: Path, *, settings: dict[str, Any] | None = None) -> list[VideoFrameJob]:
    base_settings = {"draw_banner": False, "draw_text": False, "draw_focus": False}
    if settings:
        base_settings.update(settings)

    jobs: list[VideoFrameJob] = []
    for idx in range(2):
        source_path = tmp_dir / f"source_{idx + 1}.jpg"
        color = (idx * 40, 80, 120)
        image = Image.new("RGB", (96, 64), color)
        image.save(source_path)
        jobs.append(
            VideoFrameJob(
                path=source_path,
                settings=dict(base_settings),
                raw_metadata={"SourceFile": str(source_path)},
                metadata_context={},
                source_image=image,
            )
        )
    return jobs


def _close_video_jobs(jobs: list[VideoFrameJob]) -> None:
    for job in jobs:
        if job.source_image is not None:
            job.source_image.close()


def test_resolve_target_frame_size_auto_rounds_to_even() -> None:
    options = VideoExportOptions(output_path=Path("out.mp4"), frame_size_mode="auto")
    assert resolve_target_frame_size(options, (1919, 1081)) == (1920, 1082)


def test_resolve_target_frame_size_custom_uses_requested_size() -> None:
    options = VideoExportOptions(
        output_path=Path("out.mp4"),
        frame_size_mode="custom",
        frame_width=1281,
        frame_height=719,
    )
    assert resolve_target_frame_size(options, (640, 480)) == (1282, 720)


def test_build_ffmpeg_command_h265_mp4_contains_expected_flags(tmp_path) -> None:
    options = VideoExportOptions(
        output_path=tmp_path / "clip.mp4",
        container="mp4",
        codec="h265",
        fps=29.97,
        preset="slow",
        crf=18,
    )
    command = build_ffmpeg_command(Path("/tmp/ffmpeg"), tmp_path / "frames", options)
    assert command[:4] == ["/tmp/ffmpeg", "-hide_banner", "-loglevel", "error"]
    assert "-framerate" in command
    assert "29.97" in command
    assert "libx265" in command
    assert "hvc1" in command
    assert "+faststart" in command
    assert str((tmp_path / "clip.mp4").resolve()) == command[-1]


def test_resolve_video_render_workers_honors_auto_and_manual_limits() -> None:
    assert resolve_video_render_workers(0, 0) == 1
    assert resolve_video_render_workers(3, 2) == 2
    assert resolve_video_render_workers(1, 5) == 1


def test_recommended_auto_render_workers_prefers_physical_cpu_count() -> None:
    assert _recommended_auto_render_workers(physical_cpu_count=12, logical_cpu_count=12) == 20


def test_recommended_auto_render_workers_falls_back_to_logical_cpu_count() -> None:
    assert _recommended_auto_render_workers(physical_cpu_count=None, logical_cpu_count=16) == 12


def test_normalize_frame_size_letterboxes_to_target_canvas() -> None:
    image = Image.new("RGB", (400, 200), "#FF0000")
    normalized = normalize_frame_size(image, (320, 240), background_color="#000000")
    assert normalized.size == (320, 240)
    assert normalized.getpixel((0, 0)) == (0, 0, 0)
    assert normalized.getpixel((160, 120)) == (255, 0, 0)


def test_count_contiguous_rendered_frames_stops_at_gap(tmp_path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for name in ("frame_000001.png", "frame_000002.png", "frame_000004.png"):
        (frames_dir / name).write_bytes(b"png")
    assert _count_contiguous_rendered_frames(frames_dir, 5) == 2


def test_partial_video_output_path_marks_frame_count() -> None:
    output_path = Path("/tmp/video.mp4")
    partial_path = _partial_video_output_path(output_path, 12)
    assert partial_path.name == "video__partial_000012.mp4"


def test_draw_focus_box_overlay_uses_expected_border_colors() -> None:
    image = Image.new("RGB", (100, 100), "#FFFFFF")
    draw_focus_box_overlay(image, (0.2, 0.2, 0.8, 0.8))
    assert image.getpixel((20, 20)) == (0, 0, 0)
    assert image.getpixel((21, 21)) == (46, 255, 85)
    assert image.getpixel((24, 24)) == (46, 255, 85)
    assert image.getpixel((25, 25)) == (0, 0, 0)


def test_render_video_frame_applies_crop_box_override(tmp_path) -> None:
    source_path = tmp_path / "source.jpg"
    source_image = Image.new("RGB", (100, 50), "#FF0000")
    for x in range(50, 100):
        for y in range(50):
            source_image.putpixel((x, y), (0, 0, 255))
    source_image.save(source_path)

    job = VideoFrameJob(
        path=source_path,
        settings={
            "draw_banner": False,
            "draw_text": False,
            "draw_focus": False,
            "crop_box": [0.0, 0.0, 0.5, 1.0],
        },
        raw_metadata={"SourceFile": str(source_path)},
        metadata_context={},
        source_image=source_image,
    )

    rendered = render_video_frame(job)
    try:
        assert rendered.size == (50, 50)
        assert rendered.getpixel((10, 25)) == (255, 0, 0)
    finally:
        rendered.close()
        source_image.close()


def test_export_video_reuses_preserved_temp_frames() -> None:
    import birdstamp.video_export as video_export

    with tempfile.TemporaryDirectory() as tmp_dir_text:
        tmp_dir = Path(tmp_dir_text)
        jobs = _build_sample_video_jobs(tmp_dir)

        options = VideoExportOptions(
            output_path=tmp_dir / "clip.mp4",
            preserve_temp_files=True,
        )

        original_find_ffmpeg = video_export.find_ffmpeg_executable
        original_run_ffmpeg = video_export._run_ffmpeg_command
        original_render_video_frame = video_export.render_video_frame
        render_calls: list[str] = []

        def fake_find_ffmpeg() -> Path:
            return tmp_dir / "ffmpeg"

        def fake_run_ffmpeg(cmd: list[str], *, cancel_event=None, cancel_message: str = "") -> None:
            Path(cmd[-1]).write_bytes(b"fake-video")

        def fake_render_video_frame(job: VideoFrameJob, **_kwargs) -> Image.Image:
            render_calls.append(job.path.name)
            return job.source_image.copy() if job.source_image is not None else Image.new("RGB", (96, 64), "#000000")

        try:
            video_export.find_ffmpeg_executable = fake_find_ffmpeg
            video_export._run_ffmpeg_command = fake_run_ffmpeg
            video_export.render_video_frame = fake_render_video_frame

            first_output = export_video(jobs, options)
            assert first_output == options.output_path.resolve()
            assert render_calls == ["source_1.jpg", "source_2.jpg"]

            render_calls.clear()
            second_output = export_video(jobs, options)
            assert second_output == options.output_path.resolve()
            assert render_calls == []
        finally:
            video_export.find_ffmpeg_executable = original_find_ffmpeg
            video_export._run_ffmpeg_command = original_run_ffmpeg
            video_export.render_video_frame = original_render_video_frame
            _close_video_jobs(jobs)


def test_export_video_reuses_same_cache_dir_when_only_fps_changes() -> None:
    import birdstamp.video_export as video_export

    with tempfile.TemporaryDirectory() as tmp_dir_text:
        tmp_dir = Path(tmp_dir_text)
        jobs = _build_sample_video_jobs(tmp_dir)
        options_24 = VideoExportOptions(
            output_path=tmp_dir / "clip.mp4",
            fps=24.0,
            preserve_temp_files=True,
        )
        options_30 = VideoExportOptions(
            output_path=tmp_dir / "clip.mp4",
            fps=30.0,
            preserve_temp_files=True,
        )

        original_find_ffmpeg = video_export.find_ffmpeg_executable
        original_run_ffmpeg = video_export._run_ffmpeg_command
        original_render_video_frame = video_export.render_video_frame
        render_calls: list[str] = []

        def fake_find_ffmpeg() -> Path:
            return tmp_dir / "ffmpeg"

        def fake_run_ffmpeg(cmd: list[str], *, cancel_event=None, cancel_message: str = "") -> None:
            Path(cmd[-1]).write_bytes(b"fake-video")

        def fake_render_video_frame(job: VideoFrameJob, **_kwargs) -> Image.Image:
            render_calls.append(job.path.name)
            return job.source_image.copy() if job.source_image is not None else Image.new("RGB", (96, 64), "#000000")

        try:
            video_export.find_ffmpeg_executable = fake_find_ffmpeg
            video_export._run_ffmpeg_command = fake_run_ffmpeg
            video_export.render_video_frame = fake_render_video_frame

            first_cache_key = video_export._render_cache_key(jobs, options_24)
            first_work_dir = video_export._create_video_work_dir(
                options_24.normalized_output_path(),
                preserve_temp_files=True,
                cache_key=first_cache_key,
            )
            export_video(jobs, options_24)
            assert render_calls == ["source_1.jpg", "source_2.jpg"]

            render_calls.clear()
            second_cache_key = video_export._render_cache_key(jobs, options_30)
            second_work_dir = video_export._create_video_work_dir(
                options_30.normalized_output_path(),
                preserve_temp_files=True,
                cache_key=second_cache_key,
            )
            export_video(jobs, options_30)
            assert first_work_dir == second_work_dir
            assert render_calls == []
        finally:
            video_export.find_ffmpeg_executable = original_find_ffmpeg
            video_export._run_ffmpeg_command = original_run_ffmpeg
            video_export.render_video_frame = original_render_video_frame
            _close_video_jobs(jobs)


def test_export_video_uses_new_cache_dir_when_draw_focus_changes() -> None:
    import birdstamp.video_export as video_export

    with tempfile.TemporaryDirectory() as tmp_dir_text:
        tmp_dir = Path(tmp_dir_text)
        jobs = _build_sample_video_jobs(tmp_dir)
        options = VideoExportOptions(
            output_path=tmp_dir / "clip.mp4",
            preserve_temp_files=True,
        )

        original_find_ffmpeg = video_export.find_ffmpeg_executable
        original_run_ffmpeg = video_export._run_ffmpeg_command
        original_render_video_frame = video_export.render_video_frame
        render_calls: list[str] = []

        def fake_find_ffmpeg() -> Path:
            return tmp_dir / "ffmpeg"

        def fake_run_ffmpeg(cmd: list[str], *, cancel_event=None, cancel_message: str = "") -> None:
            Path(cmd[-1]).write_bytes(b"fake-video")

        def fake_render_video_frame(job: VideoFrameJob, **_kwargs) -> Image.Image:
            render_calls.append(job.path.name)
            return job.source_image.copy() if job.source_image is not None else Image.new("RGB", (96, 64), "#000000")

        try:
            video_export.find_ffmpeg_executable = fake_find_ffmpeg
            video_export._run_ffmpeg_command = fake_run_ffmpeg
            video_export.render_video_frame = fake_render_video_frame

            first_cache_key = video_export._render_cache_key(jobs, options)
            first_work_dir = video_export._create_video_work_dir(
                options.normalized_output_path(),
                preserve_temp_files=True,
                cache_key=first_cache_key,
            )
            export_video(jobs, options)
            assert render_calls == ["source_1.jpg", "source_2.jpg"]

            for job in jobs:
                job.settings["draw_focus"] = True

            render_calls.clear()
            second_cache_key = video_export._render_cache_key(jobs, options)
            second_work_dir = video_export._create_video_work_dir(
                options.normalized_output_path(),
                preserve_temp_files=True,
                cache_key=second_cache_key,
            )
            export_video(jobs, options)
            assert first_work_dir != second_work_dir
            assert render_calls == ["source_1.jpg", "source_2.jpg"]
        finally:
            video_export.find_ffmpeg_executable = original_find_ffmpeg
            video_export._run_ffmpeg_command = original_run_ffmpeg
            video_export.render_video_frame = original_render_video_frame
            _close_video_jobs(jobs)


def test_render_cache_key_changes_when_crop_box_override_changes() -> None:
    import birdstamp.video_export as video_export

    with tempfile.TemporaryDirectory() as tmp_dir_text:
        tmp_dir = Path(tmp_dir_text)
        jobs = _build_sample_video_jobs(tmp_dir)
        options = VideoExportOptions(
            output_path=tmp_dir / "clip.mp4",
            preserve_temp_files=True,
        )
        try:
            base_key = video_export._render_cache_key(jobs, options)
            for job in jobs:
                job.settings["crop_box"] = [0.0, 0.0, 0.8, 1.0]
            changed_key = video_export._render_cache_key(jobs, options)
            assert changed_key != base_key
        finally:
            _close_video_jobs(jobs)
