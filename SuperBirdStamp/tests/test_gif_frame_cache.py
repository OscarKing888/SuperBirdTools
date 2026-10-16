"""重复导出必须真实跳过解码，且不能把被改写的缓存当成成片。"""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from birdstamp import export_frame_cache
from birdstamp.export_stage import VideoFrameJob
from birdstamp.export_stage import core
from birdstamp.gui import editor_exporter
from birdstamp.gui.editor_exporter import _BirdStampExporterMixin


class Exporter(_BirdStampExporterMixin):
    template_paths = {}

    def __init__(self, jobs):
        self.jobs = jobs
        self.dirty = set()
        self.messages = []
        self.request = SimpleNamespace(
            keep_frame_images=True, fps=24, loop=0, scale_factors=(),
            wechat_sticker=False, repeat_fps=(),
        )
        self.gif_export_panel = SimpleNamespace(current_request=lambda: self.request)

    def _set_status(self, message):
        self.messages.append(message)

    def _begin_image_export_progress(self, **_kwargs):
        return 1

    def _set_image_export_progress(self, *_args, **_kwargs):
        pass

    def _finish_image_export_progress(self, **_kwargs):
        pass

    def _reset_image_export_progress(self, **_kwargs):
        pass

    def _process_image_export_ui_events(self):
        pass

    def _save_batch_export_last_output_dir(self, _path):
        pass

    def _build_export_render_jobs(self, _paths, **_kwargs):
        return [replace(job, settings=dict(job.settings), raw_metadata=dict(job.raw_metadata)) for job in self.jobs]

    def _dirty_photo_path_keys(self, paths):
        return {str(path.resolve()).casefold() for path in paths} & self.dirty

    def _clear_photo_export_dirty(self, paths):
        self.dirty.difference_update(str(path.resolve()).casefold() for path in paths)

    def _save_image(self, image, path, **_kwargs):
        # 元数据写入已有独立集成测试；此处保留真实解码/渲染和 PNG 文件读写。
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")


@pytest.fixture
def cache_case(tmp_path, monkeypatch):
    from birdstamp import exported_image_index

    monkeypatch.setattr(exported_image_index, "default_exported_image_index_path", lambda: tmp_path / "index.json")
    jobs = []
    for index, color in enumerate(["red", "blue"]):
        source = tmp_path / f"白鹭-{index}.png"
        with Image.new("RGB", (32, 24), color) as image:
            image.save(source)
        jobs.append(VideoFrameJob(
            path=source, settings={"ratio": "no_crop", "draw_text": False, "draw_banner": False,
                                   "draw_images": False, "draw_focus": False, "max_long_edge": 32},
            raw_metadata={"ImageWidth": 32, "ImageHeight": 24}, metadata_context={},
        ))
    decoded = []
    decode = core.decode_image

    def tracked(path, **kwargs):
        decoded.append(path)
        return decode(path, **kwargs)

    monkeypatch.setattr(core, "decode_image", tracked)
    return Exporter(jobs), decoded, tmp_path / "output" / "birds.gif"


def _ensure(exporter, output):
    return exporter._ensure_gif_frame_cache(exporter.jobs, output_path=output, persistent=True)[0]


def test_complete_repeat_export_reuses_frames_when_only_encoding_options_change(cache_case, monkeypatch):
    exporter, decoded, output = cache_case
    chosen = [output]
    monkeypatch.setattr(editor_exporter.QFileDialog, "getSaveFileName", lambda *_args: (str(chosen[0]), "GIF"))
    paths = [job.path for job in exporter.jobs]
    exporter.dirty = {str(path.resolve()).casefold() for path in paths}
    exporter._export_all_as_gif(paths)
    assert sorted(decoded) == sorted(paths)
    assert not exporter.dirty
    frames = list(output.parent.glob("birdstamp_export_cache/rendered_source_frames/*/frames/*.png"))
    signatures = {path: export_frame_cache.path_signature(path) for path in frames}
    assert len(signatures) == 2

    decoded.clear()
    exporter.request.fps = 30
    exporter.request.repeat_fps = (12,)
    exporter.request.scale_factors = (0.5,)
    exporter.request.wechat_sticker = True
    chosen[0] = output.with_name("renamed.gif")
    exporter._export_all_as_gif(paths)
    assert decoded == []
    assert signatures == {path: export_frame_cache.path_signature(path) for path in frames}
    assert any("复用缓存帧 2/2" in message for message in exporter.messages)
    assert chosen[0].is_file()
    assert chosen[0].with_stem("renamed__wechat").is_file()
    assert chosen[0].with_stem("renamed__1_2").is_file()


@pytest.mark.parametrize("change", ["deleted", "truncated", "replaced", "settings", "source", "xmp", "dirty"])
def test_only_invalidated_frame_is_rendered_again(cache_case, change):
    exporter, decoded, output = cache_case
    frames = _ensure(exporter, output)
    unchanged = export_frame_cache.path_signature(frames[1])
    job = exporter.jobs[0]
    if change == "deleted":
        frames[0].unlink()
    elif change == "truncated":
        frames[0].write_bytes(b"broken PNG")
    elif change == "replaced":
        with Image.new("RGB", (32, 24), "green") as image:
            image.save(frames[0])
    elif change == "settings":
        job.settings["text_scale"] = 1.25
    elif change == "source":
        with Image.new("RGB", (32, 24), "green") as image:
            image.save(job.path)
    elif change == "xmp":
        job.path.with_suffix(".xmp").write_text('<x:xmpmeta xmlns:x="adobe:ns:meta/"/>', encoding="utf-8")
    elif change == "dirty":
        exporter.dirty.add(str(job.path.resolve()).casefold())
    decoded.clear()
    _ensure(exporter, output)
    assert decoded == [job.path]
    assert export_frame_cache.path_signature(frames[1]) == unchanged
    with Image.open(frames[0]) as image:
        assert image.getpixel((0, 0)) == ((0, 128, 0) if change == "source" else (255, 0, 0))


def test_legacy_manifest_is_verified_and_upgraded_without_source_decode(cache_case, monkeypatch):
    exporter, decoded, output = cache_case
    frames = _ensure(exporter, output)
    manifest_path = frames[0].parent.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["frames"].values():
        record.pop("file_signature", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    decoded.clear()
    _ensure(exporter, output)
    assert decoded == []
    upgraded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert all(record.get("file_signature") for record in upgraded["frames"].values())
    # 升级后的热缓存只做 stat 校验，不再打开/解压 PNG。
    monkeypatch.setattr(Image, "open", lambda *_args, **_kwargs: pytest.fail("hot cache reopened image"))
    _ensure(exporter, output)
    assert decoded == []


def test_malformed_manifest_version_is_a_cache_miss(cache_case):
    exporter, decoded, output = cache_case
    frames = _ensure(exporter, output)
    manifest_path = frames[0].parent.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "broken"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    decoded.clear()
    _ensure(exporter, output)
    assert sorted(decoded) == sorted(job.path for job in exporter.jobs)


@pytest.mark.parametrize("change", ["truncated_legacy", "outside_bucket"])
def test_unverifiable_manifest_frame_is_rebuilt(cache_case, change):
    exporter, decoded, output = cache_case
    frames = _ensure(exporter, output)
    manifest_path = frames[0].parent.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["frames"]["1"]
    record.pop("file_signature", None)
    if change == "truncated_legacy":
        frames[0].write_bytes(frames[0].read_bytes()[:40])
    else:
        record["relative_path"] = str(exporter.jobs[1].path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    decoded.clear()
    _ensure(exporter, output)
    assert decoded == [exporter.jobs[0].path]


def test_video_reuses_gif_source_frames_and_repairs_only_its_damaged_normalized_frame(cache_case, monkeypatch):
    from birdstamp import export_stage
    from birdstamp.export_stage import VideoExportOptions, export_video

    exporter, decoded, output = cache_case
    source_frames = _ensure(exporter, output)
    source_signatures = [export_frame_cache.path_signature(path) for path in source_frames]
    monkeypatch.setattr(export_stage, "find_ffmpeg_executable", lambda: output.parent / "ffmpeg")
    monkeypatch.setattr(export_stage, "_run_ffmpeg_command", lambda args, **_kwargs: Path(args[-1]).write_bytes(b"video"))
    options = VideoExportOptions(output_path=output.with_suffix(".mp4"), preserve_temp_files=True)
    decoded.clear()
    export_video(exporter.jobs, options)
    assert decoded == []
    frames = sorted(output.parent.glob("birdstamp_export_cache/video_frames/*/frames/*.png"))
    assert len(frames) == 2
    unchanged = export_frame_cache.path_signature(frames[1])
    frames[0].write_bytes(b"broken PNG")
    export_video(exporter.jobs, options)
    assert decoded == []
    assert [export_frame_cache.path_signature(path) for path in source_frames] == source_signatures
    assert export_frame_cache.path_signature(frames[1]) == unchanged
    with Image.open(frames[0]) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
