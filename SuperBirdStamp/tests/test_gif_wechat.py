import random

import pytest
from PIL import Image

from birdstamp import gif_export
from birdstamp.gif_export import GifExportOptions, build_gif_frame_timing, export_gif


def _frames(tmp_path, count=3, size=(60, 40), *, noise=False):
    rng = random.Random(5)
    paths = []
    for index in range(count):
        path = tmp_path / f"{index}.png"
        image = (Image.frombytes("RGB", size, rng.randbytes(size[0] * size[1] * 3))
                 if noise else Image.new("RGB", size, (index * 30, 40, 60)))
        image.save(path)
        image.close()
        paths.append(path)
    return paths


def _timeline(path):
    with Image.open(path) as image:
        size, loop = image.size, image.info["loop"]
        durations = []
        for index in range(image.n_frames):
            image.seek(index)
            durations.append(image.info["duration"])
        return size, loop, durations


@pytest.mark.parametrize("size,expected", [((960, 640), (480, 320)), ((40, 60), (40, 60))])
@pytest.mark.parametrize("fps", [24, 120])
def test_wechat_variant_preserves_timeline_and_regular_outputs(tmp_path, size, expected, fps):
    frames = _frames(tmp_path, size=size)
    events = []
    outputs = export_gif(frames, GifExportOptions(
        tmp_path / "鸟.gif", fps=fps, loop=2, scale_factors=(0.5,), wechat_sticker=True,
    ), progress_callback=events.append)
    assert [p.name for p in outputs] == ["鸟.gif", "鸟__wechat.gif", "鸟__1_2.gif"]
    assert _timeline(outputs[0])[0] == size
    assert _timeline(outputs[1]) == (expected, 2, list(build_gif_frame_timing(3, fps).durations_ms))
    assert outputs[1].stat().st_size <= 5_000_000
    assert [e.output_index for e in events if e.phase == "done"] == [1, 2, 3]
    assert events[-1].total_outputs == 3


def test_real_noisy_gif_over_five_mb_is_shrunk_and_readable(tmp_path):
    frames = _frames(tmp_path, 40, (480, 320), noise=True)
    outputs = export_gif(frames, GifExportOptions(tmp_path / "noise.gif", fps=24, wechat_sticker=True))
    assert outputs[0].stat().st_size > 5_000_000
    assert outputs[1].stat().st_size <= 5_000_000
    size, loop, durations = _timeline(outputs[1])
    assert size[0] < 480
    assert abs(size[0] / size[1] - 1.5) < 0.02
    assert durations == list(build_gif_frame_timing(40, 24).durations_ms)
    assert loop == 0


def test_wechat_exact_limit_is_accepted(tmp_path, monkeypatch):
    frames = _frames(tmp_path)
    options = GifExportOptions(tmp_path / "limit.gif", wechat_sticker=True)
    sticker = export_gif(frames, options)[1]
    original = sticker.read_bytes()
    monkeypatch.setattr(gif_export, "WECHAT_GIF_MAX_BYTES", len(original))
    assert export_gif(frames, options)[1].read_bytes() == original


@pytest.mark.parametrize("failure", ["budget", "encoder"])
def test_wechat_failure_preserves_existing_file_and_removes_temporary_files(tmp_path, monkeypatch, failure):
    frames = _frames(tmp_path, size=(8, 4))
    target = tmp_path / "saved.gif"
    target.write_bytes(b"previous complete file")
    if failure == "budget":
        monkeypatch.setattr(gif_export, "WECHAT_GIF_MAX_BYTES", 1)
        expected_error = ValueError
    else:
        def fail(_paths, output, **kwargs):
            output.write_bytes(b"partial")
            raise OSError("disk full")
        monkeypatch.setattr(gif_export, "_save_gif_variant", fail)
        expected_error = OSError
    with pytest.raises(expected_error):
        gif_export._save_wechat_gif_variant(
            frames, target, durations_ms=(40, 40, 50), loop=0,
            target_size=(8, 4), background_color="#000000",
        )
    assert target.read_bytes() == b"previous complete file"
    assert not list(tmp_path.glob(".birdstamp-wechat-*"))


def test_cli_wechat_option(tmp_path):
    from typer.testing import CliRunner
    from birdstamp.cli import app

    frames = _frames(tmp_path)
    result = CliRunner().invoke(app, ["gif", *map(str, frames), "-o", str(tmp_path / "cli.gif"), "--wechat"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cli__wechat.gif").stat().st_size <= 5_000_000
