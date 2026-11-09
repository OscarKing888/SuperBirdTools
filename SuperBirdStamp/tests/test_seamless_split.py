"""Real pixels, metadata pairs, group publication, CLI and independent stage contract."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageOps
from typer.testing import CliRunner

from birdstamp.seamless_split import (
    SplitOptions, SplitCancelled, SplitCanvasStage, export_split, normalized_crop,
    preview_size, render_canvas, slice_boxes,
)
from birdstamp.image_pipeline import ImageProcContext


def gradient(size=(601, 403)):
    image = Image.new('RGB', size)
    image.putdata([(x % 256, y % 256, (x * 3 + y * 7) % 256)
                   for y in range(size[1]) for x in range(size[0])])
    return image


@pytest.mark.parametrize('count,width,height', [(2, 65, 87), (3, 108, 144), (7, 79, 64), (20, 64, 113)])
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
    monkeypatch.setattr(snapshot, 'capture_export_metadata', lambda path: SimpleNamespace(xmp=b'<xmp/>'))
    def save(image, target, **kwargs):
        image.save(target, format=kwargs['format'])
        target.with_suffix('.xmp').write_bytes(kwargs['xmp_snapshot'])
    monkeypatch.setattr(metadata, 'save_export_image', save)
    return metadata, save


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
    assert len(list(first.glob('*.png'))) == len(list(first.glob('*.xmp'))) == 3
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
def test_group_failure_retains_previous_results(monkeypatch, tmp_path, failure):
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
    def broken(image, target, **kwargs):
        if target.stem.endswith('02'):
            raise OSError('模拟磁盘失败')
        save(image, target, **kwargs)
    if failure == 'write':
        monkeypatch.setattr(metadata, 'save_export_image', broken)
    if failure == 'publish':
        from pathlib import Path
        monkeypatch.setattr(Path, 'rename', lambda *args: (_ for _ in ()).throw(OSError('模拟发布失败')))
    with pytest.raises((OSError, SplitCancelled)):
        export_split(source, SplitOptions(3, 64, 64), directory, cancel=lambda: cancelled, progress=progress)
    assert list(directory.iterdir()) == [previous]
    assert (previous / '保留.txt').read_text(encoding='utf8') == '之前的完整结果'


def test_stage_and_cli_share_core(monkeypatch, tmp_path):
    stub_metadata(monkeypatch)
    from birdstamp.cli import app
    source = source_file(tmp_path)
    result = CliRunner().invoke(app, ['split', str(source), '-o', str(tmp_path / 'out'),
                                     '--count', '2', '--width', '64', '--height', '128', '--format', 'png'])
    assert result.exit_code == 0, result.output
    assert len(list((tmp_path / 'out').rglob('*.png'))) == 2
    options = SplitOptions(2, 64, 128)
    with gradient() as image:
        context = ImageProcContext(image, asdict(options))
        SplitCanvasStage().process(context)
        try:
            assert context.image.size == options.size
            assert {o.key for o in SplitCanvasStage().ui_descriptor().parameter_options} >= {'count', 'width', 'height'}
        finally:
            context.image.close()


def test_real_chinese_metadata_and_oriented_source(tmp_path):
    from test_export_metadata import exiftool, metadata
    source = tmp_path / '中文旋转.jpg'
    with gradient() as image:
        image.save(source)
    chinese = tmp_path / '值.txt'
    chinese.write_text('连屏测试，朱鹮飞过山谷', encoding='utf8')
    exiftool('-overwrite_original', '-Orientation#=6', '-ISO=800', f'-XMP-dc:Description<={chinese}', str(source))
    before = source.read_bytes()
    options = SplitOptions(2, 64, 128, format='png')
    directory = export_split(source, options, tmp_path / '输出')
    for path in directory.glob('*.png'):
        tags = metadata(path)
        sidecar = metadata(path.with_suffix('.xmp'))
        assert tags['ExifIFD:ISO'] == 800
        assert tags['IFD0:Orientation'] == 1
        assert not any(key.startswith('XMP-') for key in tags)
        assert sidecar['XMP-dc:Description'] == chinese.read_text(encoding='utf8')
        assert sidecar['XMP-tiff:ImageWidth'] == 64
        with Image.open(path) as image:
            assert image.size == (64, 128)
    assert source.read_bytes() == before
