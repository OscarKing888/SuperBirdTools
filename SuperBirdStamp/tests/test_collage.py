"""真实像素、布局边界与失败时数据保留；不联网。"""
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from typer.testing import CliRunner

from birdstamp.collage import CollageCancelled, CollageOptions, PRESETS, export_collage, plan_collage, render_collage
from birdstamp.overlays.assets import import_image
from birdstamp.overlays.model import new_item


@pytest.fixture
def photos(tmp_path):
    paths = []
    for name, size, color in [('红鸟', (120, 60), 'red'), ('绿鸟', (60, 120), 'lime')]:
        path = tmp_path / (name + '.png')
        with Image.new('RGB', size, color) as image:
            image.save(path)
        paths.append(path)
    return paths


@pytest.mark.parametrize('preset,size', [('vertical', (120, 275)), ('horizontal', (275, 120)),
                                       ('grid4', (225, 225)), ('grid6', (330, 225)),
                                       ('grid9', (330, 330)), ('grid16', (435, 435))])
def test_layout_and_pixels(photos, preset, size):
    options = CollageOptions(preset, 100, 5, 10, '#112233')
    image, plan = render_collage(photos, options)
    try:
        assert image.size == plan.size == size
        assert image.getpixel((0, 0)) == (17, 34, 51)
        for box, color in zip(plan.boxes, [(255, 0, 0), (0, 255, 0)]):
            left, top, right, bottom = box
            assert image.getpixel(((left + right) // 2, (top + bottom) // 2)) == color
    finally:
        image.close()


def test_grid_contain_cover_and_preview_geometry(photos):
    options = CollageOptions('grid4', 100, 0, 0)
    contained, plan = render_collage(photos, options)
    covered, _ = render_collage(photos, replace(options, fit='cover'))
    preview, small_plan = render_collage(photos, options, preview_edge=100)
    try:
        assert plan == small_plan
        assert preview.size == (100, 100)
        assert contained.getpixel((50, 5)) == (255, 255, 255)
        assert covered.getpixel((50, 5)) == (255, 0, 0)
        assert covered.getpixel((150, 150)) == (255, 255, 255)  # 空格不重复照片。
        assert preview.getpixel((25, 25)) == contained.getpixel((50, 50))
    finally:
        for image in (contained, covered, preview):
            image.close()


def test_orientation_and_transparency(tmp_path):
    path = tmp_path / '旋转.jpg'
    with Image.new('RGB', (120, 60), 'red') as image:
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, exif=exif)
    image, plan = render_collage([path], CollageOptions(cell_size=100, margin=0))
    assert plan.size == (100, 200)
    image.close()
    path = tmp_path / '透明.png'
    with Image.new('RGBA', (50, 50), (0, 0, 0, 0)) as image:
        image.save(path)
    image, _ = render_collage([path], CollageOptions(background='#123456'))
    assert image.getpixel((50, 50)) == (18, 52, 86)
    image.close()


def test_limits_and_invalid_input():
    with pytest.raises(ValueError, match='最多容纳 4'):
        plan_collage([(100, 100)] * 5, CollageOptions('grid4'))
    with pytest.raises(ValueError, match='拼图过大'):
        plan_collage([(100, 100)] * 64, CollageOptions(cell_size=4096))
    with pytest.raises(ValueError):
        plan_collage([], CollageOptions())
    with pytest.raises(ValueError):
        CollageOptions(preset='unknown')


@pytest.mark.parametrize('suffix', ['.png', '.jpg'])
def test_export_overlay_and_source_preservation(photos, tmp_path, suffix):
    stamp = tmp_path / '蓝色素材.png'
    with Image.new('RGB', (20, 20), 'blue') as image:
        image.save(stamp)
    key, asset = import_image(stamp)
    item = new_item('image')
    item.update(asset_id=key, width=.3, x=.5, y=.5)
    doc = dict(overlays=[item], overlay_assets={key: asset})
    before = [p.read_bytes() for p in photos]
    output = tmp_path / ('中文拼图' + suffix)
    export_collage(photos, CollageOptions('grid4', 100, 0, 0), output, overlays=doc)
    with Image.open(output) as image:
        assert image.size == (200, 200)
        assert image.getpixel((100, 100))[2] >= 250
    assert [p.read_bytes() for p in photos] == before
    assert not list(tmp_path.glob('.birdstamp-collage-*'))


def test_cancel_and_write_failure_keep_existing_output(photos, tmp_path, monkeypatch):
    output = tmp_path / '已有输出.png'
    output.write_bytes(b'previous complete output')
    event = Event()
    with pytest.raises(CollageCancelled):
        export_collage(photos, CollageOptions(), output, cancel=event.is_set,
                       progress=lambda *_: event.set())
    assert output.read_bytes() == b'previous complete output'
    import birdstamp.collage as collage
    def fail(*_args):
        raise OSError('模拟写入失败')
    monkeypatch.setattr(collage.os, 'replace', fail)
    with pytest.raises(OSError, match='模拟写入失败'):
        export_collage(photos, CollageOptions(), output)
    assert output.read_bytes() == b'previous complete output'
    assert not list(tmp_path.glob('.birdstamp-collage-*'))
    with pytest.raises(ValueError, match='不能.*覆盖'):
        export_collage(photos, CollageOptions(), photos[0])


def test_cli(photos, tmp_path):
    from birdstamp.cli import app
    output = tmp_path / '命令行.png'
    result = CliRunner().invoke(app, ['collage', *map(str, photos), '--preset', 'horizontal',
                                    '--cell-size', '100', '--gap', '0', '--margin', '0', '-o', str(output)])
    assert result.exit_code == 0, result.output
    with Image.open(output) as image:
        assert image.size == (250, 100)
