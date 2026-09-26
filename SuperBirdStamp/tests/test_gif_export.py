from pathlib import Path
import shutil
from types import SimpleNamespace

from PIL import Image

from birdstamp.gui import editor_exporter
from birdstamp.export_frame_cache import FRAME_CACHE_ROOT_NAME
from birdstamp.export_stage import VideoFrameJob
from birdstamp.gif_export import (
    GifExportOptions,
    build_gif_variant_output_paths,
    export_gif,
    normalize_gif_frame_size,
    resolve_gif_target_size,
    validate_gif_export_options,
)
from birdstamp.gui.editor_exporter import _BirdStampExporterMixin


def _build_sample_frame(path: Path, size: tuple[int, int], color: str) -> Path:
    image = Image.new("RGB", size, color)
    image.save(path, format="PNG")
    image.close()
    return path


def test_validate_gif_export_options_filters_invalid_scale_values(tmp_path) -> None:
    validated = validate_gif_export_options(
        GifExportOptions(
            output_path=tmp_path / "demo",
            fps=8,
            loop=-1,
            scale_factors=(0.5, 0.5, 1.0, -0.25, 0.25),
        )
    )
    assert validated.normalized_output_path().suffix == ".gif"
    assert validated.loop == 0
    assert validated.scale_factors == (0.5, 0.25)


def test_resolve_gif_target_size_uses_max_frame_envelope(tmp_path) -> None:
    frame_paths = [
        _build_sample_frame(tmp_path / "frame_1.png", (80, 60), "#FF0000"),
        _build_sample_frame(tmp_path / "frame_2.png", (120, 50), "#00FF00"),
    ]
    assert resolve_gif_target_size(frame_paths) == (120, 60)


def test_normalize_gif_frame_size_letterboxes_to_canvas() -> None:
    image = Image.new("RGB", (120, 60), "#FF0000")
    normalized = normalize_gif_frame_size(image, (120, 120), background_color="#000000")
    try:
        assert normalized.size == (120, 120)
        assert normalized.getpixel((10, 10)) == (0, 0, 0)
        assert normalized.getpixel((60, 60)) == (255, 0, 0)
    finally:
        normalized.close()
        image.close()


def test_build_gif_variant_output_paths_appends_scale_suffixes(tmp_path) -> None:
    variants = build_gif_variant_output_paths(tmp_path / "birdstamp.gif", [0.5, 0.25, 0.5])
    assert variants == [
        (0.5, tmp_path / "birdstamp__1_2.gif"),
        (0.25, tmp_path / "birdstamp__1_4.gif"),
    ]


def test_export_gif_writes_main_and_scaled_outputs(tmp_path) -> None:
    frame_paths = [
        _build_sample_frame(tmp_path / "frame_1.png", (80, 60), "#FF0000"),
        _build_sample_frame(tmp_path / "frame_2.png", (120, 50), "#0000FF"),
    ]
    written = export_gif(
        frame_paths,
        GifExportOptions(
            output_path=tmp_path / "birdstamp.gif",
            fps=5,
            loop=0,
            scale_factors=(0.5,),
            background_color="#101010",
        ),
    )
    assert written == [
        (tmp_path / "birdstamp.gif").resolve(),
        (tmp_path / "birdstamp__1_2.gif").resolve(),
    ]

    with Image.open(written[0]) as image:
        assert image.size == (120, 60)
        assert getattr(image, "n_frames", 1) == 2
        assert image.info.get("duration") == 200
        assert image.info.get("loop") == 0

    with Image.open(written[1]) as image:
        assert image.size == (60, 30)
        assert getattr(image, "n_frames", 1) == 2


def test_gif_frame_cache_persistence_follows_keep_frames_choice(tmp_path) -> None:
    class _Harness(_BirdStampExporterMixin):
        template_paths: dict[str, Path] = {}

        def _dirty_photo_path_keys(self, _paths):
            return set()

        def _set_status(self, _message: str) -> None:
            return None

        def _export_render_jobs_to_images(self, jobs, targets, *, label, fast_png=False):
            for target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (8, 6), "#123456").save(target)
            return list(targets), []

    source_path = _build_sample_frame(tmp_path / "source.png", (8, 6), "#abcdef")
    job = VideoFrameJob(
        path=source_path,
        settings={"draw_banner": False, "draw_text": False, "draw_focus": False},
        raw_metadata={},
        metadata_context={},
    )
    harness = _Harness()

    _paths, transient_frames = harness._ensure_gif_frame_cache(
        [job],
        output_path=tmp_path / "transient.gif",
        persistent=False,
    )
    _paths, persistent_frames = harness._ensure_gif_frame_cache(
        [job],
        output_path=tmp_path / "persistent.gif",
        persistent=True,
    )
    try:
        assert FRAME_CACHE_ROOT_NAME not in transient_frames.parts
        assert FRAME_CACHE_ROOT_NAME in persistent_frames.parts
    finally:
        shutil.rmtree(transient_frames.parent, ignore_errors=True)
        shutil.rmtree(tmp_path / FRAME_CACHE_ROOT_NAME, ignore_errors=True)


def test_static_image_export_uses_full_resolution_memory_budget(
    monkeypatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, int] = {}

    class _Harness(_BirdStampExporterMixin):
        template_paths: dict[str, Path] = {}

        def _begin_image_export_progress(self, **_kwargs):
            return 1

        def _set_image_export_progress(self, *_args, **_kwargs) -> None:
            return None

        def _finish_image_export_progress(self, **_kwargs) -> None:
            return None

        def _set_status(self, _message: str) -> None:
            return None

        def _render_and_save_image_task(self, task, **_kwargs) -> Path:
            return task.target_path

    def resolve_workers(
        render_workers: int,
        pending_jobs: int,
        *,
        max_frame_pixels: int,
    ) -> int:
        observed["render_workers"] = render_workers
        observed["pending_jobs"] = pending_jobs
        observed["max_frame_pixels"] = max_frame_pixels
        return 1

    monkeypatch.setattr(
        editor_exporter,
        "estimate_video_job_max_pixels",
        lambda _jobs: 24_000_000,
    )
    monkeypatch.setattr(
        editor_exporter,
        "resolve_video_render_workers",
        resolve_workers,
    )

    source_path = tmp_path / "source.raw"
    job = VideoFrameJob(
        path=source_path,
        settings={},
        raw_metadata={"ImageWidth": 6000, "ImageHeight": 4000},
        metadata_context={},
    )
    output_path = tmp_path / "output.jpg"

    ok_paths, failures = _Harness()._export_render_jobs_to_images(
        [job],
        [output_path],
        label="静态图片导出",
    )

    assert ok_paths == [output_path]
    assert failures == []
    assert observed == {
        "render_workers": 0,
        "pending_jobs": 1,
        "max_frame_pixels": 24_000_000,
    }


def test_gif_cache_frames_request_fast_lossless_png(tmp_path) -> None:
    requested: list[bool] = []

    class _Harness(_BirdStampExporterMixin):
        template_paths: dict[str, Path] = {}

        def _dirty_photo_path_keys(self, _paths):
            return set()

        def _set_status(self, _message: str) -> None:
            return None

        def _export_render_jobs_to_images(self, jobs, targets, *, label, fast_png=False):
            requested.append(fast_png)
            for target in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (8, 6), "#123456").save(target)
            return list(targets), []

    source_path = _build_sample_frame(tmp_path / "source.png", (8, 6), "#abcdef")
    job = VideoFrameJob(path=source_path, settings={}, raw_metadata={}, metadata_context={})
    _Harness()._ensure_gif_frame_cache([job], output_path=tmp_path / "out.gif", persistent=False)
    assert requested == [True]


def test_fast_png_save_is_lossless_and_user_png_keeps_optimize(tmp_path) -> None:
    image = Image.linear_gradient("L").resize((320, 200)).convert("RGB")
    saver = _BirdStampExporterMixin()
    fast = tmp_path / "fast.png"
    optimized = tmp_path / "optimized.png"
    saver._save_image(image, fast, fast_png=True)
    saver._save_image(image, optimized)
    with Image.open(fast) as a, Image.open(optimized) as b:
        assert a.tobytes() == b.tobytes() == image.tobytes()


def test_gif_encoding_runs_off_caller_thread_and_applies_progress_in_order(tmp_path, monkeypatch) -> None:
    import threading

    from birdstamp.gui import editor_exporter

    caller = threading.get_ident()
    encode_threads: list[int] = []
    applied: list[tuple[int, int]] = []

    def _fake_export_gif(frame_paths, options, *, progress_callback):
        encode_threads.append(threading.get_ident())
        for index in range(1, 4):
            progress_callback(SimpleNamespace(phase="encode", current=index, total=3, output_index=1, message=f"m{index}"))
        return [options.output_path]

    monkeypatch.setattr(editor_exporter, "export_gif", _fake_export_gif)

    class _Harness(_BirdStampExporterMixin):
        def _on_gif_export_progress(self, progress, total_outputs):
            assert threading.get_ident() == caller
            applied.append((progress.current, progress.total))

    result = _Harness()._run_gif_export_off_gui_thread([], SimpleNamespace(output_path=tmp_path / "x.gif"), 1)
    assert result == [tmp_path / "x.gif"]
    assert encode_threads and encode_threads[0] != caller
    assert applied == [(1, 3), (2, 3), (3, 3)]


def test_blocking_task_helper_relays_positional_progress_in_order_and_propagates_errors() -> None:
    import threading

    import pytest

    caller = threading.get_ident()
    worker_threads: list[int] = []
    seen: list[tuple[int, int]] = []

    def _task(values, *, bird_box_cache, progress_callback):
        worker_threads.append(threading.get_ident())
        for index, _value in enumerate(values, start=1):
            progress_callback(index, len(values))
        bird_box_cache["done"] = True
        return len(values)

    def _handler(current, total):
        assert threading.get_ident() == caller
        seen.append((current, total))

    helper = _BirdStampExporterMixin()
    cache: dict = {}
    assert helper._run_blocking_task_off_gui_thread(_task, [1, 2, 3], bird_box_cache=cache, progress_handler=_handler) == 3
    assert worker_threads and worker_threads[0] != caller
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert cache == {"done": True}

    def _failing(*, progress_callback):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        helper._run_blocking_task_off_gui_thread(_failing)
