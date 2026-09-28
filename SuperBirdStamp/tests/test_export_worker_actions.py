from pathlib import Path
import threading

from PIL import Image

from app_common.file_browser._work_pool import BrowserWorkPool
from birdstamp.export_stage import VideoExportOptions, VideoFrameJob, export_video
from birdstamp.gui import editor_exporter
from birdstamp.gui.editor_exporter import _BirdStampExporterMixin


class _Exporter(_BirdStampExporterMixin):
    template_paths = {}

    def _begin_image_export_progress(self, **_kwargs):
        return 1

    def _set_image_export_progress(self, *_args, **_kwargs):
        pass

    def _set_status(self, _message):
        pass

    def _process_image_export_ui_events(self):
        pass

    def _finish_image_export_progress(self, **_kwargs):
        pass


def _jobs(tmp_path, count=2):
    jobs = []
    for index in range(count):
        path = tmp_path / f"照片_{index}.jpg"
        Image.new("RGB", (32, 24), (index * 50, 80, 120)).save(path)
        jobs.append(VideoFrameJob(
            path=path,
            settings={"draw_banner": False, "draw_text": False, "draw_focus": False},
            raw_metadata={"SourceFile": str(path)},
            metadata_context={},
        ))
    return jobs


def test_image_exports_use_parallel_worker_actions(tmp_path, monkeypatch):
    jobs = _jobs(tmp_path)
    exporter = _Exporter()
    barrier = threading.Barrier(2)
    worker_threads = set()
    original = exporter._render_and_save_image_task

    def tracked(task, **kwargs):
        worker_threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        return original(task, **kwargs)

    monkeypatch.setattr(editor_exporter, "resolve_video_render_workers", lambda *_args, **_kwargs: 2)
    monkeypatch.setattr(exporter, "_render_and_save_image_task", tracked)
    targets = [tmp_path / f"output_{index}.png" for index in range(2)]
    ok_paths, failed = exporter._export_render_jobs_to_images(jobs, targets, label="测试")

    assert failed == []
    assert set(ok_paths) == set(targets)
    assert len(worker_threads) == 2
    assert all(path.is_file() for path in targets)


def test_single_image_export_runs_off_gui_thread(tmp_path):
    exporter = _Exporter()
    worker_threads = []
    original = exporter._render_and_save_image_task

    def tracked(task, **kwargs):
        worker_threads.append(threading.get_ident())
        return original(task, **kwargs)

    exporter._render_and_save_image_task = tracked
    target = tmp_path / "single.png"
    ok_paths, failed = exporter._export_render_jobs_to_images(
        _jobs(tmp_path, 1), [target], label="测试",
    )

    assert failed == []
    assert ok_paths == [target]
    assert worker_threads and worker_threads[0] != threading.get_ident()


def test_video_render_and_normalize_use_worker_actions(tmp_path, monkeypatch):
    from birdstamp import export_stage

    jobs = _jobs(tmp_path)
    submitted = []
    worker_threads = set()
    barrier = threading.Barrier(2)
    original_submit = BrowserWorkPool.submit_action

    def tracked_submit(self, action, **kwargs):
        submitted.append(type(action).__name__)
        return original_submit(self, action, **kwargs)

    def render(job, **_kwargs):
        worker_threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        with Image.open(job.path) as image:
            return image.copy()

    def encode(command, **_kwargs):
        Path(command[-1]).write_bytes(b"fake video")

    monkeypatch.setattr(BrowserWorkPool, "submit_action", tracked_submit)
    monkeypatch.setattr(export_stage, "render_video_frame", render)
    monkeypatch.setattr(export_stage, "find_ffmpeg_executable", lambda: tmp_path / "ffmpeg")
    monkeypatch.setattr(export_stage, "_run_ffmpeg_command", encode)

    output = export_video(jobs, VideoExportOptions(
        output_path=tmp_path / "clip.mp4", render_workers=2, preserve_temp_files=False,
    ))

    assert output.is_file()
    assert len(worker_threads) == 2
    assert submitted.count("_SourceFrameRenderAction") == 2
    assert submitted.count("_VideoFrameNormalizeAction") == 2
