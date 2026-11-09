"""Real pixels, image-only output, atomic publication and CLI contract."""
from dataclasses import asdict

import pytest
from PIL import Image, ImageChops, ImageOps
from typer.testing import CliRunner

from birdstamp.seamless_split import (
    SplitOptions, SplitCancelled, SplitCanvasStage, export_split, normalized_crop,
    preview_size, render_canvas, slice_boxes, resolve_split_ratio, split_dimensions, XHS_FULLSCREEN_RATIO,
)
from birdstamp.image_pipeline import ImageProcContext


def gradient(size=(601, 403)):
    image = Image.new('RGB', size)
    image.putdata([(x % 256, y % 256, (x * 3 + y * 7) % 256)
                   for y in range(size[1]) for x in range(size[0])])
    return image


@pytest.mark.parametrize('count,width,height', [(2, 65, 87), (3, 108, 144), (3, 108, 180), (3, 1080, 1826), (7, 79, 64), (20, 64, 113)])
@pytest.mark.parametrize('fit', ['cover', 'contain'])
def test_integer_slices_reassemble_exactly(count, width, height, fit):
    options = SplitOptions(count, width, height, fit)
    with gradient() as source, render_canvas(source, options) as total:
        rebuilt = Image.new('RGB', total.size)
        try:
            boxes = slice_boxes(total.size, count)
            for box in boxes:
                with total.crop(box) as tile:
                    assert tile.size == (width, height)
                    rebuilt.paste(tile, box[:2])
            with ImageChops.difference(rebuilt, total) as diff:
                assert diff.getbbox() is None
            assert preview_size(options)[0] % count == 0
        finally:
            rebuilt.close()


def test_crop_keeps_center_and_area_when_possible():
    options = SplitOptions(3, 64, 128)
    box = (.25, .3, .65, .7)
    actual = normalized_crop((600, 400), options, box)
    assert actual == pytest.approx(box)
    changed = normalized_crop((600, 400), SplitOptions(4, 64, 128), box)
    assert (changed[0] + changed[2]) / 2 == pytest.approx(.45)
    assert (changed[1] + changed[3]) / 2 == pytest.approx(.5)
    assert (changed[2] - changed[0]) * (changed[3] - changed[1]) == pytest.approx(.16)
    bounded = normalized_crop((600, 400), options, (-.5, -.5, 1.5, 1.5))
    assert all(0 <= v <= 1 for v in bounded)


def test_transparency_and_padding_share_background():
    options = SplitOptions(2, 100, 100, 'contain', '#123456')
    with Image.new('RGBA', (50, 100), (255, 0, 0, 0)) as source, render_canvas(source, options) as image:
        assert image.getextrema() == ((18, 18), (52, 52), (86, 86))


@pytest.mark.parametrize('values', [dict(count=1), dict(count=21), dict(width=0), dict(height=9000),
                                       dict(count=20, width=8192, height=8192), dict(fit='stretch'), dict(format='gif')])
def test_invalid_options(values):
    with pytest.raises(ValueError):
        SplitOptions(**values)


def stub_metadata(monkeypatch):
    import birdstamp.export_snapshot as snapshot
    import birdstamp.export_metadata as metadata
    monkeypatch.setattr(snapshot, 'capture_export_metadata', lambda *args: pytest.fail('连屏不应读取 XMP 快照'))
    copy = lambda source, target, image: None
    monkeypatch.setattr(metadata, 'copy_export_metadata', copy)
    return metadata, copy


def source_file(tmp_path):
    source = tmp_path / '中文 大图.png'
    with gradient() as image:
        image.save(source)
    return source


def test_group_export_collision_and_exact_png(monkeypatch, tmp_path):
    stub_metadata(monkeypatch)
    source = source_file(tmp_path)
    options = SplitOptions(3, 65, 87, format='png')
    directory = tmp_path / '输出'
    first = export_split(source, options, directory)
    second = export_split(source, options, directory)
    assert first != second
    assert len(list(first.iterdir())) == len(list(first.glob('*.png'))) == 3
    with Image.open(source) as original, render_canvas(original, options) as expected:
        rebuilt = Image.new('RGB', options.size)
        try:
            for index, path in enumerate(sorted(first.glob('*.png'))):
                with Image.open(path) as image:
                    rebuilt.paste(image, (index * options.width, 0))
            with ImageChops.difference(expected, rebuilt) as difference:
                assert difference.getbbox() is None
        finally:
            rebuilt.close()
    assert not list(directory.glob('.*'))


@pytest.mark.parametrize('failure', ['cancel', 'write', 'publish'])
@pytest.mark.parametrize('page_index', [None, 1])
def test_group_failure_retains_previous_results(monkeypatch, tmp_path, failure, page_index):
    metadata, save = stub_metadata(monkeypatch)
    source = source_file(tmp_path)
    directory = tmp_path / '输出'
    previous = directory / f'{source.stem}_连屏'
    previous.mkdir(parents=True)
    (previous / '保留.txt').write_text('之前的完整结果', encoding='utf8')
    cancelled = False
    def progress(done, total):
        nonlocal cancelled
        cancelled = failure == 'cancel' and done == 1
    def broken(source, target, image):
        if target.stem.endswith('02'):
            raise OSError('模拟磁盘失败')
        save(source, target, image)
    if failure == 'write':
        monkeypatch.setattr(metadata, 'copy_export_metadata', broken)
    if failure == 'publish':
        from pathlib import Path
        monkeypatch.setattr(Path, 'rename', lambda *args: (_ for _ in ()).throw(OSError('模拟发布失败')))
    with pytest.raises((OSError, SplitCancelled)):
        export_split(source, SplitOptions(3, 64, 64), directory, page_index=page_index,
                     cancel=lambda: cancelled, progress=progress)
    assert list(directory.iterdir()) == [previous]
    assert (previous / '保留.txt').read_text(encoding='utf8') == '之前的完整结果'


def test_stage_and_cli_share_core(monkeypatch, tmp_path):
    stub_metadata(monkeypatch)
    from birdstamp.cli import app
    source = source_file(tmp_path)
    result = CliRunner().invoke(app, ['split', str(source), '-o', str(tmp_path / 'out'),
                                     '--count', '2', '--width', '64', '--height', '128'])
    assert result.exit_code == 0, result.output
    assert len(list((tmp_path / 'out').rglob('*.png'))) == 2
    result = CliRunner().invoke(app, ['split', str(source), '-o', str(tmp_path / 'single'),
                                     '--count', '2', '--width', '64', '--height', '128', '--page', '2'])
    assert result.exit_code == 0, result.output
    assert [p.name for p in (tmp_path / 'single').rglob('*.png')] == [f'{source.stem}_02.png']
    result = CliRunner().invoke(app, ['split', str(source), '-o', str(tmp_path / 'invalid'),
                                     '--count', '2', '--page', '3'])
    assert result.exit_code != 0
    assert not (tmp_path / 'invalid').exists()
    options = SplitOptions(2, 64, 128)
    with gradient() as image:
        context = ImageProcContext(image, asdict(options))
        SplitCanvasStage().process(context)
        try:
            assert context.image.size == options.size
            assert {o.key for o in SplitCanvasStage().ui_descriptor().parameter_options} >= {'count', 'width', 'height'}
        finally:
            context.image.close()


@pytest.mark.parametrize('cancel_probe', [False, True])
@pytest.mark.parametrize('format', ['png', 'jpeg'])
def test_real_chinese_metadata_and_oriented_source(tmp_path, cancel_probe, format, monkeypatch):
    from test_export_metadata import exiftool, metadata
    from app_common.exif_io import exiftool_path
    from app_common.exif_io.exiftool_runner import exiftool_read_request, exiftool_worker_session
    source = tmp_path / '中文旋转.jpg'
    with gradient() as image:
        image.save(source)
    chinese = tmp_path / '值.txt'
    chinese.write_text('连屏测试，朱鹮飞过山谷', encoding='utf8')
    exiftool('-overwrite_original', '-Orientation#=6', '-ISO=800', f'-XMP-dc:Description<={chinese}', f'-EXIF:UserComment<={chinese}', str(source))
    sidecar = source.with_suffix('.xmp')
    sidecar.write_bytes(b'not valid XMP, deliberately never parsed by split export')
    sidecar_before = sidecar.read_bytes()
    from app_common.exif_io import export_xmp
    monkeypatch.setattr(export_xmp, 'read_export_xmp', lambda *args: pytest.fail('连屏不应读取 XMP'))
    before = source.read_bytes()
    options = SplitOptions(2, 64, 128, format=format)
    if cancel_probe:
        executable = exiftool_path.get_exiftool_executable_path()
        assert executable
        exiftool_path._probe_exiftool.cache_clear()
        with exiftool_worker_session(), exiftool_read_request(lambda: True):
            assert not exiftool_path._is_usable_exiftool(executable)
    # Use the same request/session lifecycle as the editor's split worker.
    with exiftool_worker_session(), exiftool_read_request(lambda: False, timeout=60):
        directory = export_split(source, options, tmp_path / '输出')
    assert len(list(directory.iterdir())) == 2
    assert not list(directory.glob('*.xmp'))
    for path in directory.iterdir():
        tags = metadata(path)
        assert tags['ExifIFD:ISO'] == 800
        assert tags['IFD0:Orientation'] == 1
        assert not any(key.startswith('XMP-') for key in tags)
        assert tags['ExifIFD:UserComment'] == chinese.read_text(encoding='utf8')
        assert tags['ExifIFD:ExifImageWidth'] == 64
        with Image.open(path) as image:
            assert image.size == (64, 128)
    assert source.read_bytes() == before
    assert sidecar.read_bytes() == sidecar_before


@pytest.mark.parametrize('page_index', [0, 1, 2])
def test_single_slice_matches_group_without_overwriting(monkeypatch, tmp_path, page_index):
    stub_metadata(monkeypatch)
    source = source_file(tmp_path)
    options = SplitOptions(3, 65, 87)
    progress = []
    group = export_split(source, options, tmp_path / '输出')
    one = export_split(source, options, tmp_path / '输出', page_index=page_index,
                       progress=lambda done, total: progress.append((done, total)))
    repeated = export_split(source, options, tmp_path / '输出', page_index=page_index)
    name = f'{source.stem}_{page_index + 1:02d}.png'
    assert [p.name for p in one.iterdir()] == [name]
    assert one != group and one != repeated
    assert (one / name).read_bytes() == (group / name).read_bytes() == (repeated / name).read_bytes()
    assert progress == [(1, 1)]


@pytest.mark.parametrize('page_index', [-1, 3, True, 1.5])
def test_invalid_page_does_not_create_output(tmp_path, page_index):
    with pytest.raises(ValueError):
        export_split(source_file(tmp_path), SplitOptions(), tmp_path / '输出', page_index=page_index)
    assert not (tmp_path / '输出').exists()


@pytest.mark.parametrize('width,height', [(1080, 1826), (1440, 2435), (2160, 3652)])
def test_calibrated_dimensions_cover_without_horizontal_loss(width, height):
    from fractions import Fraction
    assert split_dimensions(XHS_FULLSCREEN_RATIO, width, 1) == (width, height)
    assert split_dimensions(XHS_FULLSCREEN_RATIO, 1, height, axis='height') == (width, height)
    # Model aspect-fill into this screenshot's image region, not the whole phone screen.
    scale = max(Fraction(1280, width), Fraction(2164, height))
    assert width * scale == 1280
    assert 0 <= height * scale - 2164 < 1
    # The previous 3:5 tile has ~18.4 px horizontal loss in the same viewport.
    old_scale = max(Fraction(1280, 1080), Fraction(2164, 1800))
    assert float(1080 * old_scale - 1280) == pytest.approx(18.4)


def test_ratio_resolution_and_rounding_direction():
    assert resolve_split_ratio('3:5') == (3, 5)
    assert resolve_split_ratio('custom', custom=(1079, 1825)) == (1079, 1825)
    assert resolve_split_ratio(XHS_FULLSCREEN_RATIO, calibration={}) == (1280, 2164)
    assert resolve_split_ratio(XHS_FULLSCREEN_RATIO, calibration={'width': '1280', 'height': '2164'}) == (1280, 2164)
    assert split_dimensions(XHS_FULLSCREEN_RATIO, 1080, 1825, axis='height') == (1079, 1825)
    assert split_dimensions('9:16', 1080, 1) == (1080, 1920)
    assert split_dimensions('3:5', 1080, 1) == (1080, 1800)
    # Sweep both directions: no floating-point roundoff or width-creep after ceiling.
    for value in range(64, 8193):
        w, h = split_dimensions(XHS_FULLSCREEN_RATIO, value, value)
        assert w == value and 0 <= h * 1280 - w * 2164 < 1280
        w, h = split_dimensions(XHS_FULLSCREEN_RATIO, value, value, axis='height')
        assert h == value and 0 <= h * 1280 - w * 2164 < 2164


@pytest.mark.parametrize('calibration', [
    {'width': 0}, {'width': -1}, {'height': ''}, {'height': '1.5'}, {'height': 2164.5},
    {'width': True}, {'width': 2164, 'height': 1280}, {'height': 1280},
    {'width': None}, {'width': '１２８０'}, [],
])
def test_invalid_fullscreen_calibration(calibration):
    with pytest.raises(ValueError):
        resolve_split_ratio(XHS_FULLSCREEN_RATIO, calibration=calibration)
