from __future__ import annotations

from pathlib import Path
import threading

import pytest
from PIL import Image

from birdstamp import export_stage
from birdstamp.export_frame_cache import FRAME_CACHE_ROOT_NAME, path_signature
from birdstamp.export_stage import VideoExportOptions, VideoFrameJob, export_video
from birdstamp.export_stage.constants import EXPORT_STAGE_ID_KEY, EXPORT_STAGE_GIF_ID, EXPORT_STAGE_PNG_ID, EXPORT_STAGE_VIDEO_ID
from birdstamp.exported_image_index import ExportedImageIndex
from birdstamp.gui.editor_exporter import _BirdStampExporterMixin


class _Exporter(_BirdStampExporterMixin):
    template_paths = {}

    def _set_status(self, _message):
        pass

    def _begin_image_export_progress(self, **_kwargs):
        return 1

    def _set_image_export_progress(self, *_args, **_kwargs):
        pass

    def _finish_image_export_progress(self, **_kwargs):
        pass

    def _process_image_export_ui_events(self):
        pass


def _jobs(tmp_path: Path, count: int = 2) -> list[VideoFrameJob]:
    jobs = []
    for index in range(count):
        source = tmp_path / f"照片_{index}.jpg"
        Image.new("RGB", (24, 16), (index * 40, 60, 90)).save(source)
        jobs.append(VideoFrameJob(
            path=source,
            settings={"draw_banner": False, "draw_text": False, "draw_focus": False,
                      EXPORT_STAGE_ID_KEY: EXPORT_STAGE_PNG_ID},
            raw_metadata={"ImageWidth": 24, "ImageHeight": 16},
            metadata_context={},
        ))
    return jobs


@pytest.fixture
def image_index(tmp_path, monkeypatch):
    import birdstamp.exported_image_index as module

    index_path = tmp_path / "user-config" / "cache" / "exported_images_v1.json"
    monkeypatch.setattr(module, "default_exported_image_index_path", lambda: index_path)
    return ExportedImageIndex(index_path)


def _fake_video_encoder(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(export_stage, "find_ffmpeg_executable", lambda: tmp_path / "ffmpeg")
    monkeypatch.setattr(export_stage, "_run_ffmpeg_command",
                        lambda command, **_kwargs: Path(command[-1]).write_bytes(b"video"))


def test_exported_png_then_jpg_are_reused_across_directories_with_png_priority(
    tmp_path, monkeypatch, image_index,
):
    jobs = _jobs(tmp_path)
    exporter = _Exporter()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    png = image_dir / "first.png"
    jpg = image_dir / "second.jpg"
    alternate_jpg = image_dir / "first.jpg"
    Image.new("RGB", (24, 16), "#ee0000").save(png)
    Image.new("RGB", (24, 16), "#0000ee").save(jpg, quality=100)
    Image.new("RGB", (24, 16), "#00ee00").save(alternate_jpg, quality=100)
    exporter._remember_exported_images(jobs, [png, jpg], [png, jpg])
    exporter._remember_exported_images([jobs[0]], [alternate_jpg], [alternate_jpg])
    for job in jobs:
        job.settings[EXPORT_STAGE_ID_KEY] = EXPORT_STAGE_VIDEO_ID
    _fake_video_encoder(monkeypatch, tmp_path)
    monkeypatch.setattr(export_stage, "render_video_frame",
                        lambda *_args, **_kwargs: pytest.fail("matching export was rendered again"))

    output = tmp_path / "other-directory" / "clip.mp4"
    export_video(jobs, VideoExportOptions(output_path=output, preserve_temp_files=True))
    source_frames = sorted(output.parent.glob(
        f"{FRAME_CACHE_ROOT_NAME}/rendered_source_frames/*/frames/frame_*.png"
    ))
    video_frames = sorted(output.parent.glob(
        f"{FRAME_CACHE_ROOT_NAME}/video_frames/*/frames/frame_*.png"
    ))
    assert len(source_frames) == 2
    assert len(video_frames) == 2
    assert source_frames[0].read_bytes() == png.read_bytes() == video_frames[0].read_bytes()
    with Image.open(source_frames[0]) as first, Image.open(source_frames[1]) as second:
        assert first.getpixel((0, 0)) == (238, 0, 0)
        assert second.getpixel((0, 0))[2] > 200

    for job in jobs:
        job.settings[EXPORT_STAGE_ID_KEY] = EXPORT_STAGE_GIF_ID
    gif_frames, _directory = exporter._ensure_gif_frame_cache(
        jobs, output_path=tmp_path / "gif-output" / "animation.gif", persistent=True,
    )
    with Image.open(gif_frames[0]) as first, Image.open(gif_frames[1]) as second:
        assert first.getpixel((0, 0)) == (238, 0, 0)
        assert second.getpixel((0, 0))[2] > 200


def test_cached_video_and_gif_frames_upgrade_from_jpg_to_new_png(tmp_path, monkeypatch, image_index):
    job = _jobs(tmp_path, 1)[0]
    exporter = _Exporter()
    jpg = tmp_path / "images" / "first.jpg"
    png = tmp_path / "images" / "first.png"
    jpg.parent.mkdir()
    Image.new("RGB", (24, 16), "blue").save(jpg, quality=100)
    exporter._remember_exported_images([job], [jpg], [jpg])
    _fake_video_encoder(monkeypatch, tmp_path)
    monkeypatch.setattr(export_stage, "render_video_frame",
                        lambda *_args, **_kwargs: pytest.fail("matching export was rendered again"))

    video_output = tmp_path / "video" / "clip.mp4"
    gif_output = tmp_path / "gif" / "animation.gif"

    def video_pixel():
        job.settings[EXPORT_STAGE_ID_KEY] = EXPORT_STAGE_VIDEO_ID
        export_video([job], VideoExportOptions(
            output_path=video_output, preserve_temp_files=True,
        ))
        source_frame, = video_output.parent.glob(
            f"{FRAME_CACHE_ROOT_NAME}/rendered_source_frames/*/frames/frame_*.png"
        )
        with Image.open(source_frame) as image:
            return image.getpixel((0, 0))

    def gif_pixel():
        job.settings[EXPORT_STAGE_ID_KEY] = EXPORT_STAGE_GIF_ID
        frames, _ = exporter._ensure_gif_frame_cache(
            [job], output_path=gif_output, persistent=True,
        )
        with Image.open(frames[0]) as image:
            return image.getpixel((0, 0))

    assert video_pixel()[2] > 200
    assert gif_pixel()[2] > 200

    job.settings[EXPORT_STAGE_ID_KEY] = EXPORT_STAGE_PNG_ID
    Image.new("RGB", (24, 16), "red").save(png)
    exporter._remember_exported_images([job], [png], [png])
    assert video_pixel() == (255, 0, 0)
    assert gif_pixel() == (255, 0, 0)

    png.unlink()
    assert video_pixel() == (0, 0, 254)
    assert gif_pixel() == (0, 0, 254)


def test_index_invalidates_changed_sources_outputs_and_render_settings(tmp_path, image_index):
    job = _jobs(tmp_path, 1)[0]
    from birdstamp.export_stage import source_frame_signature_for_job

    signature = source_frame_signature_for_job(job)
    png = tmp_path / "out.png"
    Image.new("RGB", (24, 16), "red").save(png)
    image_index.add_many([(job.path, path_signature(job.path), signature, png)])
    records = image_index.load()
    assert image_index.find(records, source=job.path, frame_signature=signature) == png
    job.settings[EXPORT_STAGE_ID_KEY] = EXPORT_STAGE_VIDEO_ID
    assert source_frame_signature_for_job(job) == signature
    job.settings["draw_text"] = True
    assert image_index.find(records, source=job.path,
                            frame_signature=source_frame_signature_for_job(job)) is None
    job.settings["draw_text"] = False
    png.write_bytes(b"changed")
    assert image_index.find(records, source=job.path, frame_signature=signature) is None
    Image.new("RGB", (24, 16), "red").save(png)
    image_index.add_many([(job.path, path_signature(job.path), signature, png)])
    job.path.write_bytes(job.path.read_bytes() + b"changed")
    assert image_index.find(image_index.load(), source=job.path, frame_signature=signature) is None


def test_changed_exported_png_falls_back_to_render_action(tmp_path, monkeypatch, image_index):
    job = _jobs(tmp_path, 1)[0]
    from birdstamp.export_stage import source_frame_signature_for_job

    png = tmp_path / "batch.png"
    Image.new("RGB", (24, 16), "red").save(png)
    image_index.add_many([(
        job.path, path_signature(job.path), source_frame_signature_for_job(job), png,
    )])
    Image.new("RGB", (24, 16), "blue").save(png)
    job.settings[EXPORT_STAGE_ID_KEY] = EXPORT_STAGE_VIDEO_ID
    calls = []
    original_render = export_stage.render_video_frame

    def tracked(job, **kwargs):
        calls.append(job.path)
        return original_render(job, **kwargs)

    monkeypatch.setattr(export_stage, "render_video_frame", tracked)
    _fake_video_encoder(monkeypatch, tmp_path)
    export_video([job], VideoExportOptions(
        output_path=tmp_path / "new-output" / "clip.mp4", preserve_temp_files=False,
    ))
    assert calls == [job.path]


def test_single_image_export_registers_completed_output(tmp_path, monkeypatch, image_index):
    from birdstamp.gui import editor_exporter
    from birdstamp.export_stage import source_frame_signature_for_job

    job = _jobs(tmp_path, 1)[0]
    target = tmp_path / "another-place" / "照片.png"

    class SingleExporter(_Exporter):
        current_path = job.path

        def _is_placeholder_active(self):
            return False

        def _selected_output_suffix(self):
            return "png"

        def _build_export_render_jobs(self, *_args, **_kwargs):
            return [job]

        def _clear_photo_export_dirty(self, _paths):
            pass

        def _save_image_export_last_output_dir(self, _path):
            pass

    monkeypatch.setattr(editor_exporter.QFileDialog, "getSaveFileName",
                        lambda *_args, **_kwargs: (str(target), "PNG (*.png)"))
    SingleExporter().export_current()
    assert target.is_file()
    assert image_index.find(
        image_index.load(), source=job.path,
        frame_signature=source_frame_signature_for_job(job),
    ) == target


def test_gif_named_single_image_target_is_normalized_to_real_image_format():
    exporter = _Exporter()
    assert exporter._normalized_image_export_target(Path("out.gif"), default_suffix="png") == Path("out.png")
    with pytest.raises(ValueError, match="不支持"):
        exporter._save_image(Image.new("RGB", (2, 2)), Path("out.gif"), source_path=Path("source.jpg"))


def test_single_video_frame_uses_both_worker_actions(tmp_path, monkeypatch, image_index):
    from app_common.file_browser._work_pool import BrowserWorkPool

    submitted = []
    original_submit = BrowserWorkPool.submit_action

    def tracked(self, action, **kwargs):
        submitted.append((type(action).__name__, self.max_workers))
        return original_submit(self, action, **kwargs)

    monkeypatch.setattr(BrowserWorkPool, "submit_action", tracked)
    _fake_video_encoder(monkeypatch, tmp_path)
    export_video(_jobs(tmp_path, 1), VideoExportOptions(
        output_path=tmp_path / "one.mp4", render_workers=1, preserve_temp_files=False,
    ))
    assert submitted == [
        ("_SourceFrameRenderAction", 1), ("_VideoFrameNormalizeAction", 1),
    ]


def test_video_normalization_starts_before_later_source_finishes(tmp_path, monkeypatch, image_index):
    import birdstamp.export_stage.core as core

    jobs = _jobs(tmp_path)
    _fake_video_encoder(monkeypatch, tmp_path)
    second_started = threading.Event()
    release_second = threading.Event()
    video_started = threading.Event()
    original_source = core._render_and_cache_source_frame
    original_video = core._normalize_and_cache_video_frame

    def source(**kwargs):
        if kwargs["index"] == 2:
            second_started.set()
            assert release_second.wait(5)
        return original_source(**kwargs)

    def video(**kwargs):
        video_started.set()
        return original_video(**kwargs)

    monkeypatch.setattr(core, "_render_and_cache_source_frame", source)
    monkeypatch.setattr(core, "_normalize_and_cache_video_frame", video)
    outcome = []

    def run():
        try:
            outcome.append(export_video(jobs, VideoExportOptions(
                output_path=tmp_path / "pipeline.mp4", render_workers=2, preserve_temp_files=False,
            )))
        except Exception as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert second_started.wait(5)
        assert video_started.wait(5)
    finally:
        release_second.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], Path)
